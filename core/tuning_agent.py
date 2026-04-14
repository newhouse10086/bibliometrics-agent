"""TuningAgent — LLM-driven pipeline optimization agent.

Similar to GuardianSoul but focused on quality optimization rather than error recovery.
Analyzes pipeline outputs, identifies quality issues, adjusts parameters, and re-runs modules.

Workflow:
  1. User clicks "Tune" → TuningAgent activated
  2. Agent reads all module outputs (CSVs, JSONs, figures)
  3. Agent analyzes quality issues (sparse data, poor topics, etc.)
  4. Agent writes optimization scripts to workspace/scripts/
  5. Agent adjusts parameters and re-runs modules
  6. Agent writes analysis report
  7. User can chat with agent throughout the process
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from core.context import ConversationContext
from core.context_injector import DynamicInjector
from core.llm import (
    BaseLLMProvider,
    LLMResponse,
    Message,
    ToolCall,
    ToolDef,
    create_provider,
)
from core.tools import ToolRegistry, ToolResult, create_default_registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Tuning result
# ---------------------------------------------------------------------------

@dataclass
class TuningResult:
    """Result of a tuning session."""
    success: bool = False
    summary: str = ""
    modules_rerun: list[str] = field(default_factory=list)
    config_changes: dict = field(default_factory=dict)
    report_path: str = ""
    steps_taken: int = 0


# ---------------------------------------------------------------------------
#  Tuning tool definitions
# ---------------------------------------------------------------------------

TUNING_TOOL_DEFS: list[ToolDef] = [
    ToolDef(
        name="read_file",
        description="Read any file content. Use to inspect outputs, configs, logs.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "encoding": {"type": "string", "description": "Encoding, default utf-8", "default": "utf-8"},
            },
            "required": ["path"],
        },
    ),
    ToolDef(
        name="read_project_file",
        description="Read file from project workspace (outputs, state, etc.). Path relative to workspace root.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path, e.g. 'outputs/topic_modeler/output.json'"},
                "encoding": {"type": "string", "description": "Encoding, default utf-8", "default": "utf-8"},
            },
            "required": ["path"],
        },
    ),
    ToolDef(
        name="write_file",
        description="Write file to workspace (scripts, reports, etc.).",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to workspace"},
                "content": {"type": "string", "description": "File content"},
            },
            "required": ["path", "content"],
        },
    ),
    ToolDef(
        name="search_files",
        description="Search files matching glob pattern.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. *.csv, **/*.json"},
                "directory": {"type": "string", "description": "Search directory (optional)"},
            },
            "required": ["pattern"],
        },
    ),
    ToolDef(
        name="grep_content",
        description="Search file contents with regex.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern"},
                "directory": {"type": "string", "description": "Search directory (optional)"},
                "file_pattern": {"type": "string", "description": "File pattern, default *", "default": "*"},
            },
            "required": ["pattern"],
        },
    ),
    ToolDef(
        name="run_command",
        description="Execute a shell command. Use for running analysis scripts.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Command to execute"},
                "timeout": {"type": "integer", "description": "Timeout in seconds, default 60", "default": 60},
            },
            "required": ["command"],
        },
    ),
    ToolDef(
        name="list_project_outputs",
        description="List all output files in the project workspace with sizes.",
        parameters={
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "Subdirectory to list (default: outputs/)", "default": "outputs"},
            },
        },
    ),
    ToolDef(
        name="read_module_output",
        description="Read the output.json of a specific module.",
        parameters={
            "type": "object",
            "properties": {
                "module_name": {"type": "string", "description": "Module name, e.g. 'topic_modeler'"},
            },
            "required": ["module_name"],
        },
    ),
    ToolDef(
        name="get_module_config",
        description="Get the current configuration for a module.",
        parameters={
            "type": "object",
            "properties": {
                "module_name": {"type": "string", "description": "Module name"},
            },
            "required": ["module_name"],
        },
    ),
    ToolDef(
        name="adjust_config",
        description="Adjust a module's configuration parameters. Changes are saved to state.json.",
        parameters={
            "type": "object",
            "properties": {
                "module_name": {"type": "string", "description": "Module name"},
                "params": {
                    "type": "object",
                    "description": "Parameters to change, e.g. {\"max_topics\": 20, \"n_iter\": 500}",
                },
            },
            "required": ["module_name", "params"],
        },
    ),
    ToolDef(
        name="rerun_module",
        description="Re-run a specific module with updated config. Backs up current output first. "
                     "Note: re-running a module may invalidate downstream outputs.",
        parameters={
            "type": "object",
            "properties": {
                "module_name": {"type": "string", "description": "Module to re-run"},
                "from_module": {
                    "type": "string",
                    "description": "Re-run from this module onward (optional, for cascading re-runs)",
                },
            },
            "required": ["module_name"],
        },
    ),
    ToolDef(
        name="write_analysis_report",
        description="Write a tuning analysis report in markdown.",
        parameters={
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Filename (default: tuning_report.md)", "default": "tuning_report.md"},
                "content": {"type": "string", "description": "Report content in markdown"},
            },
            "required": ["content"],
        },
    ),
    ToolDef(
        name="finish_tuning",
        description="End the tuning session. Call when you are satisfied with the optimization.",
        parameters={
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Summary of tuning actions and improvements"},
                "modules_rerun": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of modules that were re-run",
                },
            },
            "required": ["summary"],
        },
    ),
]


# ---------------------------------------------------------------------------
#  System Prompt
# ---------------------------------------------------------------------------

TUNING_SYSTEM_PROMPT = """\
You are a **Tuning Agent** for the bibliometrics analysis pipeline.
Your role: analyze pipeline outputs for quality issues and optimize parameters to improve results.

## Project Context
- **Project ID**: {run_id}
- **Research Domain**: {research_domain}
- **Project Status**: {project_status}
- **Completed Modules**: {completed_modules}/{total_modules}
- **Date**: {current_date}

## Your Task
1. **Assess** the current pipeline outputs for quality issues
2. **Identify** specific problems (sparse data, poor topic separation, missing keywords, etc.)
3. **Write** optimization analysis scripts to workspace/scripts/
4. **Adjust** parameters (topic numbers, thresholds, etc.) using `adjust_config`
5. **Re-run** specific modules with new parameters using `rerun_module`
6. **Generate** an analysis report using `write_analysis_report`
7. User can guide you at any time via chat messages

## Quality Issues to Check
- **Paper retrieval**: Was the retrieval sufficient? Check paper count vs target.
- **Keyword distribution**: Too sparse? Dominated by stop words? Missing MeSH coverage?
- **Topic model**: Good coherence and separation? Too many/few topics?
- **Burst detection**: Too sensitive (many bursts) or too insensitive (no bursts)?
- **Network analysis**: Reasonable density? Good community structure?
- **Geographic coverage**: Are key countries represented?
- **Frequency analysis**: Balanced keyword-year distribution?

## Workspace Structure
```
{workspace_dir}/
├── outputs/           # Module outputs (CSV, JSON, figures)
├── checkpoints/       # Pipeline state (state.json)
├── workspace/
│   ├── scripts/       # Write analysis scripts here
│   ├── fixes/         # Generated fixes
│   └── modules/       # Modified module code
└── tuning_report.md   # Write final analysis report here
```

## Key Files to Read
- `read_project_file("checkpoints/state.json")` — Full pipeline state
- `read_module_output("topic_modeler")` — Topic modeling results
- `read_module_output("frequency_analyzer")` — Keyword frequencies
- `read_module_output("bibliometrics_analyzer")` — Statistical summaries
- `read_module_output("network_analyzer")` — Network statistics

## Rules
- Always **investigate** before making changes — read outputs first
- Use `adjust_config` to change parameters, then `rerun_module` to apply
- When re-running a module, downstream outputs may become stale — inform the user
- Backups are created automatically before re-running
- Maximum {max_steps} steps — be efficient
- Respond in the same language as the user
"""


# ---------------------------------------------------------------------------
#  Tuning Tool Executor
# ---------------------------------------------------------------------------

class TuningToolExecutor:
    """Execute tool calls from the tuning agent LLM."""

    def __init__(self, workspace_dir: Path, tool_registry: ToolRegistry,
                 project_id: str = ""):
        self.workspace_dir = workspace_dir
        self.registry = tool_registry
        self.project_id = project_id
        self.logger = logging.getLogger("tuning.tool_executor")

        self.finish_result: Optional[TuningResult] = None
        self.config_changes: dict = {}
        self.modules_rerun: list[str] = []

    def execute(self, tool_call: ToolCall) -> str:
        try:
            args = json.loads(tool_call.arguments)
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON arguments: {e}"

        self.logger.info("Tool call: %s(%s)", tool_call.name, list(args.keys()))

        dispatch = {
            "read_file": self._read_file,
            "read_project_file": self._read_project_file,
            "write_file": self._write_file,
            "search_files": self._search_files,
            "grep_content": self._grep_content,
            "run_command": self._run_command,
            "list_project_outputs": self._list_project_outputs,
            "read_module_output": self._read_module_output,
            "get_module_config": self._get_module_config,
            "adjust_config": self._adjust_config,
            "rerun_module": self._rerun_module,
            "write_analysis_report": self._write_analysis_report,
            "finish_tuning": self._finish_tuning,
        }

        handler = dispatch.get(tool_call.name)
        if handler:
            return handler(args)
        return f"Error: Unknown tool '{tool_call.name}'"

    def _read_file(self, args: dict) -> str:
        tool = self.registry.get("ReadFile")
        if not tool:
            return "Error: ReadFile tool not available"
        result: ToolResult = tool.run(args.get("path", ""), args.get("encoding", "utf-8"))
        if result.success:
            content = result.output
            if len(content) > 8000:
                content = content[:8000] + f"\n... [truncated, total {len(result.output)} chars]"
            return content
        return f"Error reading file: {result.error}"

    def _read_project_file(self, args: dict) -> str:
        path = args.get("path", "")
        if not path:
            return "Error: No path specified"
        target_path = (self.workspace_dir / path).resolve()
        if not str(target_path).startswith(str(self.workspace_dir.resolve())):
            return "Error: Path must be under workspace directory"
        try:
            content = target_path.read_text(encoding=args.get("encoding", "utf-8"))
            if len(content) > 8000:
                content = content[:8000] + f"\n... [truncated, total {len(content)} chars]"
            return content
        except FileNotFoundError:
            return f"Error: File not found: {target_path}"
        except Exception as e:
            return f"Error reading file: {e}"

    def _write_file(self, args: dict) -> str:
        tool = self.registry.get("WriteFile")
        if not tool:
            return "Error: WriteFile tool not available"
        path = args.get("path", "")
        target_path = (self.workspace_dir / "workspace" / path).resolve()
        try:
            target_path.relative_to((self.workspace_dir / "workspace").resolve())
        except ValueError:
            return "Error: Path must be under workspace directory"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        result: ToolResult = tool.run(str(target_path), args.get("content", ""))
        if result.success:
            return f"File written: {target_path}"
        return f"Error writing file: {result.error}"

    def _search_files(self, args: dict) -> str:
        tool = self.registry.get("Glob")
        if not tool:
            return "Error: Glob tool not available"
        directory = args.get("directory") or str(self.workspace_dir / "outputs")
        result: ToolResult = tool.run(args.get("pattern", "*"), directory)
        if result.success:
            files = result.output
            if not files:
                return "No files found."
            return "Files found:\n" + "\n".join(f"  - {f}" for f in files[:50])
        return f"Error: {result.error}"

    def _grep_content(self, args: dict) -> str:
        tool = self.registry.get("Grep")
        if not tool:
            return "Error: Grep tool not available"
        result: ToolResult = tool.run(
            args.get("pattern", ""),
            args.get("directory") or str(self.workspace_dir / "outputs"),
            args.get("file_pattern", "*"),
        )
        if result.success:
            matches = result.output
            if not matches:
                return "No matches found."
            lines = [f"  {m['file']}:{m['line']}: {m['content']}" for m in matches[:30]]
            return "Matches:\n" + "\n".join(lines)
        return f"Error: {result.error}"

    def _run_command(self, args: dict) -> str:
        tool = self.registry.get("Shell")
        if not tool:
            return "Error: Shell tool not available"
        result: ToolResult = tool.run(args.get("command", ""), args.get("timeout", 60))
        output = ""
        if result.output:
            output = result.output[:4000]
        if result.error:
            output += f"\nSTDERR: {result.error[:2000]}"
        if not result.success:
            output += f"\n[Exit code: {result.metadata.get('returncode', 'unknown')}]"
        return output or "(no output)"

    def _list_project_outputs(self, args: dict) -> str:
        subdir = args.get("directory", "outputs")
        target_dir = self.workspace_dir / subdir
        if not target_dir.exists():
            return f"Directory not found: {target_dir}"
        lines = []
        for f in sorted(target_dir.rglob("*")):
            if f.is_file():
                rel = f.relative_to(self.workspace_dir)
                size = f.stat().st_size
                if size > 1024 * 1024:
                    size_str = f"{size / 1024 / 1024:.1f} MB"
                elif size > 1024:
                    size_str = f"{size / 1024:.1f} KB"
                else:
                    size_str = f"{size} B"
                lines.append(f"  {rel} ({size_str})")
        if not lines:
            return "No output files found."
        return f"Output files ({len(lines)} total):\n" + "\n".join(lines[:80])

    def _read_module_output(self, args: dict) -> str:
        module_name = args.get("module_name", "")
        output_path = self.workspace_dir / "outputs" / module_name / "output.json"
        if not output_path.exists():
            return f"Error: No output found for module '{module_name}' at {output_path}"
        try:
            content = output_path.read_text(encoding="utf-8")
            if len(content) > 8000:
                content = content[:8000] + "\n... [truncated]"
            return content
        except Exception as e:
            return f"Error reading output: {e}"

    def _get_module_config(self, args: dict) -> str:
        module_name = args.get("module_name", "")
        state_path = self.workspace_dir / "checkpoints" / "state.json"
        if not state_path.exists():
            return "Error: state.json not found"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            pipeline_config = state.get("pipeline_config", {})
            modules_config = pipeline_config.get("modules", {})
            mod_cfg = modules_config.get(module_name, {})
            if not mod_cfg:
                return f"No specific config for '{module_name}'. Using defaults."
            return json.dumps(mod_cfg, indent=2, ensure_ascii=False)
        except Exception as e:
            return f"Error reading config: {e}"

    def _adjust_config(self, args: dict) -> str:
        module_name = args.get("module_name", "")
        params = args.get("params", {})
        if not module_name or not params:
            return "Error: module_name and params are required"

        state_path = self.workspace_dir / "checkpoints" / "state.json"
        if not state_path.exists():
            return "Error: state.json not found"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            pipeline_config = state.setdefault("pipeline_config", {})
            modules_config = pipeline_config.setdefault("modules", {})
            mod_cfg = modules_config.setdefault(module_name, {})
            mod_cfg.update(params)
            state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

            self.config_changes[module_name] = params
            return f"Config updated for '{module_name}': {json.dumps(params, ensure_ascii=False)}\nUse rerun_module to apply changes."
        except Exception as e:
            return f"Error adjusting config: {e}"

    def _rerun_module(self, args: dict) -> str:
        module_name = args.get("module_name", "")
        if not module_name:
            return "Error: module_name is required"

        # Delegate to pipeline_runner for actual module re-execution
        try:
            from core.pipeline_runner import get_runner
            runner = get_runner()
            state_manager = runner.state_managers.get(self.project_id)
            if not state_manager:
                return f"Error: No state manager for project {self.project_id}"

            from core.orchestrator import PipelineOrchestrator
            from modules.registry import ModuleRegistry
            from omegaconf import OmegaConf

            # Get current state config
            state = state_manager.get_run_state(self.project_id)
            pipeline_config = state.get("pipeline_config", {})

            orchestrator = PipelineOrchestrator(
                registry=runner.registry,
                state_manager=state_manager,
                config=pipeline_config,
            )

            output = orchestrator.run_single_module(
                run_id=self.project_id,
                module_name=module_name,
            )

            self.modules_rerun.append(module_name)
            return (
                f"Module '{module_name}' re-run completed successfully.\n"
                f"Output keys: {list(output.keys()) if isinstance(output, dict) else 'N/A'}"
            )
        except Exception as e:
            return f"Error re-running module: {e}\n{traceback.format_exc()}"

    def _write_analysis_report(self, args: dict) -> str:
        content = args.get("content", "")
        filename = args.get("filename", "tuning_report.md")
        if not content:
            return "Error: Report content is required"

        report_path = self.workspace_dir / "workspace" / filename
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(content, encoding="utf-8")

        return f"Analysis report written to: {report_path}"

    def _finish_tuning(self, args: dict) -> str:
        summary = args.get("summary", "")
        modules = args.get("modules_rerun", [])
        self.finish_result = TuningResult(
            success=True,
            summary=summary,
            modules_rerun=modules or self.modules_rerun,
            config_changes=dict(self.config_changes),
        )
        return "Tuning session complete. Summary recorded."


# ---------------------------------------------------------------------------
#  TuningAgent
# ---------------------------------------------------------------------------

class TuningAgent:
    """LLM-driven pipeline tuning agent.

    Similar to GuardianSoul but focused on quality optimization:
    - Reads all module outputs
    - Analyzes quality issues
    - Adjusts parameters and re-runs modules
    - Generates analysis report
    - User can chat throughout the process
    """

    def __init__(
        self,
        llm: BaseLLMProvider,
        workspace_dir: Path,
        project_id: str,
        event_loop: Optional[Any] = None,
        max_steps: int = 30,
        temperature: float = 0.5,
        max_history_files: int = 10,
        preserve_recent_messages: int = 20,
    ):
        self.llm = llm
        self.workspace_dir = workspace_dir
        self.project_id = project_id
        self.event_loop = event_loop
        self.max_steps = max_steps
        self.temperature = temperature
        self._stop_requested = False

        self.tool_registry = create_default_registry(workspace_dir)
        self.logger = logging.getLogger(f"tuning.agent.{project_id}")

        # Communication hub
        from core.communication_hub import get_communication_hub
        self.comm_hub = get_communication_hub()

        # Conversation context with persistence
        context_file = workspace_dir / "tuning_context.jsonl"
        self.context = ConversationContext(
            context_file=context_file,
            max_history_files=max_history_files,
            preserve_recent_messages=preserve_recent_messages,
        )

        # Dynamic context injector
        self.injector = DynamicInjector(
            workspace_dir=workspace_dir,
            project_id=project_id,
            mode="agent",
            update_interval=10,
        )

    def request_stop(self):
        self._stop_requested = True
        self.logger.info("Stop requested for TuningAgent")

    def inject_user_message(self, message: str):
        """Inject a user message into the agent's conversation."""
        self.context.append_message(Message(role="user", content=message))
        self.logger.info("User message injected: %s", message[:100])

    def activate(self, initial_message: str = "", new_session: bool = False) -> TuningResult:
        """Start the tuning session. Main entry point.

        Args:
            initial_message: Initial user message (optional)
            new_session: If True, clear history and start fresh. If False, continue from existing history.
        """
        self.logger.info("TuningAgent activated for project %s (new_session=%s)", self.project_id, new_session)

        # Build system prompt
        project_ctx = self._load_project_context()
        system_prompt = TUNING_SYSTEM_PROMPT.format(
            run_id=self.project_id,
            research_domain=project_ctx.get("research_domain", "Unknown"),
            project_status=project_ctx.get("status", "unknown"),
            completed_modules=project_ctx.get("completed_modules", 0),
            total_modules=project_ctx.get("total_modules", 0),
            current_date=datetime.now().strftime("%Y-%m-%d"),
            workspace_dir=self.workspace_dir,
            max_steps=self.max_steps,
        )

        # Only clear history if this is a new session
        if new_session or len(self.context) == 0:
            self.logger.info("Starting new session, clearing history")
            self.context.clear()
            self.context.append_message(Message(role="system", content=system_prompt))

            # Initial user prompt
            if initial_message:
                self.context.append_message(Message(role="user", content=initial_message))
            else:
                self.context.append_message(Message(
                    role="user",
                    content=(
                        "Please analyze the current pipeline outputs for quality issues. "
                        "Start by listing all output files and reading the state.json to understand "
                        "the current pipeline status. Then identify any quality problems and suggest "
                        "optimizations. Use the same language as the research domain context."
                    ),
                ))
        else:
            self.logger.info("Continuing existing session with %d messages", len(self.context))

            # Inject system prompt update if needed
            if self.context.history and self.context.history[0].role == "system":
                # Update system message with current context
                self.context.history[0] = Message(role="system", content=system_prompt)
            else:
                # Prepend system message
                self.context.history.insert(0, Message(role="system", content=system_prompt))

            # Add user message if provided
            if initial_message:
                self.context.append_message(Message(role="user", content=initial_message))

        # Create checkpoint
        self.context.create_checkpoint({"phase": "initial"})

        # Create tool executor
        executor = TuningToolExecutor(
            workspace_dir=self.workspace_dir,
            tool_registry=self.tool_registry,
            project_id=self.project_id,
        )

        # Agent loop
        for step in range(self.max_steps):
            self.logger.info("Tuning step %d/%d", step + 1, self.max_steps)

            if self._stop_requested:
                self.logger.info("Tuning stopped by user")
                break

            # Broadcast step
            self._broadcast(ai_thinking=f"Step {step + 1}/{self.max_steps}: Analyzing...")

            # Check for injected user messages (already in context)

            # Dynamic context injection
            update_msg = self.injector.inject_context_update()
            if update_msg:
                self.context.append_message(update_msg)
                self.logger.debug("Injected context update")

            # Check if compression needed
            if self.context.should_compact():
                self.logger.info("Context approaching token limit, compacting...")
                saved = self.context.compact(self.llm, preserve_recent=15)
                self.logger.info(f"Compaction saved {saved} tokens")

            # Call LLM
            try:
                response = self.llm.chat(
                    self.context.get_normalized_history(),
                    tools=TUNING_TOOL_DEFS,
                    temperature=self.temperature,
                    max_tokens=4096,
                )
            except Exception as e:
                self.logger.error("LLM call failed: %s", e)
                self._broadcast(ai_decision=f"LLM error: {str(e)[:200]}", confidence=0.0)
                break

            # Process response
            if response.content:
                if response.has_tool_calls:
                    # Assistant message must carry tool_calls for OpenAI API
                    self.context.append_message(
                        Message(role="assistant", content=response.content, tool_calls=response.tool_calls),
                        token_estimate=len(response.content.split()) * 2
                    )
                else:
                    self.context.append_message(
                        Message(role="assistant", content=response.content),
                        token_estimate=len(response.content.split()) * 2
                    )
                self._broadcast(ai_thinking=response.content[:500])

            # Execute tool calls
            if response.has_tool_calls:
                for tc in response.tool_calls:
                    self._broadcast(ai_tool_call=f"{tc.name}({tc.arguments[:100]}...)")

                    result = executor.execute(tc)
                    self._broadcast(ai_tool_result=f"{tc.name}: {result[:300]}")

                    # Add tool result as tool message
                    self.context.append_message(
                        Message(
                            role="tool",
                            content=result,
                            name=tc.name,
                            tool_call_id=tc.id,
                        ),
                        token_estimate=len(result.split()) * 2
                    )

                    # Check for finish
                    if tc.name == "finish_tuning" and executor.finish_result:
                        self._broadcast(ai_decision=executor.finish_result.summary, confidence=0.9)
                        return executor.finish_result

            elif not response.content:
                # No content and no tool calls — stop
                break

            if response.finish_reason == "stop":
                # LLM chose to stop without calling finish_tuning
                if response.content:
                    self._broadcast(ai_decision=response.content[:500], confidence=0.7)
                break

        # If we exited without finish_tuning
        result = executor.finish_result or TuningResult(
            success=False,
            summary="Tuning session ended without explicit finish.",
            modules_rerun=executor.modules_rerun,
            config_changes=dict(executor.config_changes),
            steps_taken=step + 1,
        )
        self._broadcast(ai_decision=result.summary, confidence=0.6)
        return result

    def _load_project_context(self) -> dict:
        """Load project context from state.json."""
        context = {
            "run_id": self.project_id,
            "research_domain": "Unknown",
            "status": "unknown",
            "completed_modules": 0,
            "total_modules": 0,
        }
        try:
            state_path = self.workspace_dir / "checkpoints" / "state.json"
            if state_path.exists():
                state = json.loads(state_path.read_text(encoding="utf-8"))
                context["status"] = state.get("status", "unknown")
                modules = state.get("modules", {})
                context["total_modules"] = len(modules)
                context["completed_modules"] = sum(
                    1 for m in modules.values() if m.get("status") == "completed"
                )
        except Exception as e:
            self.logger.warning("Failed to load project context: %s", e)

        try:
            qg_path = self.workspace_dir / "outputs" / "query_generator" / "output.json"
            if qg_path.exists():
                qg = json.loads(qg_path.read_text(encoding="utf-8"))
                kw = qg.get("keywords", [])
                if kw:
                    context["research_domain"] = ", ".join(kw[:5])
        except Exception:
            pass

        return context

    def _broadcast(self, ai_thinking: str = "", ai_tool_call: str = "",
                   ai_tool_result: str = "", ai_decision: str = "",
                   confidence: float = 0.8):
        """Broadcast messages to the frontend via communication hub."""
        if not self.comm_hub or not self.project_id or not self.event_loop:
            return
        if not self.event_loop.is_running():
            return
        try:
            if ai_thinking:
                asyncio.run_coroutine_threadsafe(
                    self.comm_hub.ai_thinking(self.project_id, ai_thinking),
                    self.event_loop,
                )
            if ai_tool_call:
                asyncio.run_coroutine_threadsafe(
                    self.comm_hub.ai_tool_call(self.project_id, ai_tool_call),
                    self.event_loop,
                )
            if ai_tool_result:
                asyncio.run_coroutine_threadsafe(
                    self.comm_hub.ai_tool_result(self.project_id, ai_tool_result),
                    self.event_loop,
                )
            if ai_decision:
                asyncio.run_coroutine_threadsafe(
                    self.comm_hub.ai_decision(self.project_id, ai_decision, confidence=confidence),
                    self.event_loop,
                )
        except Exception as e:
            self.logger.debug("Broadcast failed: %s", e)
