"""GuardianSoul — 持续交互式 Guardian Agent 主循环.

参考 kimi-cli 的 KimiSoul 架构，实现一个 LLM 驱动的 Agent Loop：
  模块错误 → 自动激活 → LLM 分析(可多轮) → 工具调用 → 生成修复 → 测试 → 决策

核心流程:
  1. 收到模块错误，构建 error context
  2. 调用 LLM 分析错误，LLM 可以选择使用工具（读文件、搜索、执行命令）
  3. LLM 工具调用 → 执行工具 → 结果反馈给 LLM → 继续分析
  4. LLM 生成修复代码 → 写入 workspace → 测试
  5. 如果失败 → 继续循环（最多 max_steps 轮）
  6. 返回 GuardianDecision
"""

from __future__ import annotations

import json
import logging
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from core.agent_guardian import (
    ErrorAnalysis,
    FixCode,
    GuardianAgent,
    GuardianDecision,
)
from core.context import ConversationContext
from core.context_injector import DynamicInjector
from core.llm import (
    BaseLLMProvider,
    LLMResponse,
    Message,
    MockProvider,
    ToolCall,
    ToolDef,
    create_provider,
)
from core.tools import ToolRegistry, ToolResult, create_default_registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Guardian 工具定义（传给 LLM 的 schema）
# ---------------------------------------------------------------------------

GUARDIAN_TOOL_DEFS: list[ToolDef] = [
    ToolDef(
        name="read_file",
        description="读取文件内容。用于读取错误日志、配置文件、源代码等。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "encoding": {"type": "string", "description": "编码，默认 utf-8", "default": "utf-8"},
            },
            "required": ["path"],
        },
    ),
    ToolDef(
        name="read_project_file",
        description="读取项目checkpoint目录中的文件（相对于checkpoint根目录）。例如 '../state.json', '../query_generator/output.json'",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对于checkpoint根目录的路径，如 '../query_generator/output.json'"},
                "encoding": {"type": "string", "description": "编码，默认 utf-8", "default": "utf-8"},
            },
            "required": ["path"],
        },
    ),
    ToolDef(
        name="write_file",
        description="写入文件。用于保存生成的修复代码到 workspace。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径（相对于 workspace）"},
                "content": {"type": "string", "description": "文件内容"},
            },
            "required": ["path", "content"],
        },
    ),
    ToolDef(
        name="search_files",
        description="搜索匹配 glob 模式的文件列表。",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 模式，如 *.py, **/*.log"},
                "directory": {"type": "string", "description": "搜索目录（可选）"},
            },
            "required": ["pattern"],
        },
    ),
    ToolDef(
        name="grep_content",
        description="在文件中搜索匹配正则表达式的内容行。",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "正则表达式"},
                "directory": {"type": "string", "description": "搜索目录（可选）"},
                "file_pattern": {"type": "string", "description": "文件模式，默认 *.py", "default": "*.py"},
            },
            "required": ["pattern"],
        },
    ),
    ToolDef(
        name="run_command",
        description="执行 Shell 命令。用于运行诊断命令、测试修复代码等。",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的命令"},
                "timeout": {"type": "integer", "description": "超时秒数，默认 30", "default": 30},
            },
            "required": ["command"],
        },
    ),
    ToolDef(
        name="generate_fix",
        description="生成修复代码。当你完成了错误分析，准备好修复方案时调用此工具。代码将保存到 workspace。",
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "完整的 Python 修复代码"},
                "description": {"type": "string", "description": "修复方案的简要描述"},
                "error_type": {"type": "string", "description": "错误类型标签"},
            },
            "required": ["code", "description", "error_type"],
        },
    ),
    ToolDef(
        name="finish",
        description="结束错误分析，提交最终决策。当你认为分析完成（无论是否成功）时调用。",
        parameters={
            "type": "object",
            "properties": {
                "outcome": {
                    "type": "string",
                    "enum": ["success", "failed", "escalated"],
                    "description": "分析结果",
                },
                "summary": {"type": "string", "description": "分析和修复的总结"},
                "confidence": {"type": "number", "description": "置信度 0-1", "default": 0.8},
            },
            "required": ["outcome", "summary"],
        },
    ),
    ToolDef(
        name="create_module",
        description="创建新的分析模块并写入 workspace/modules/。模块必须继承 BaseModule。用于响应用户添加新分析方法的需求。",
        parameters={
            "type": "object",
            "properties": {
                "module_name": {"type": "string", "description": "snake_case 模块名，不能与系统模块重名"},
                "code": {"type": "string", "description": "完整的 BaseModule 子类 Python 代码"},
                "description": {"type": "string", "description": "模块功能描述"},
                "insert_after": {"type": "string", "description": "插入到哪个已有模块之后（可选）"},
                "insert_before": {"type": "string", "description": "插入到哪个已有模块之前（可选）"},
            },
            "required": ["module_name", "code", "description"],
        },
    ),
    ToolDef(
        name="add_to_pipeline",
        description="将已创建的模块提交注入到运行中的 pipeline，需要用户确认。必须先调用 create_module。",
        parameters={
            "type": "object",
            "properties": {
                "module_name": {"type": "string", "description": "模块名（须先 create_module）"},
                "insert_after": {"type": "string", "description": "插入到哪个已有模块之后"},
                "insert_before": {"type": "string", "description": "插入到哪个已有模块之前"},
                "rationale": {"type": "string", "description": "为什么添加此模块"},
            },
            "required": ["module_name", "rationale"],
        },
    ),
]

# ---------------------------------------------------------------------------
#  System Prompt 模板
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_TEMPLATE = """\
You are a **Guardian Agent** for the bibliometrics analysis pipeline.
Your role: when a pipeline module fails, you diagnose the root cause and generate fix code.

## Project Context
- **Project ID**: {run_id}
- **Research Domain**: {research_domain}
- **Project Status**: {project_status} ({completed_modules}/{total_modules} modules completed)
- **Execution Mode**: {execution_mode}
- **Max Papers**: {max_papers}
- **Failed Modules**: {failed_modules}
- **Date**: {current_date}

## Module Context
- **Current Module**: {module_name}
- **Workspace**: {workspace_dir}
- **Pipeline Stage**: {pipeline_stage}

## Your Capabilities
1. **Read project files** — logs, configs, source code, previous module outputs from this project
2. **Search files** — find related files and patterns in the project workspace
3. **Run commands** — diagnostic commands, test fixes
4. **Write files** — write fixes to workspace (NOT system source code)
5. **Generate fix code** — generate complete Python code
6. **Finish** — submit your final decision

## Workflow
1. **Understand the project context** — Review research domain, progress, and what has been done
2. **Analyze** the error using the provided context
3. **Investigate** by reading relevant files and running diagnostics
4. **Generate** a fix:
   - Use `generate_fix` to create timestamped fix in `workspace/fixes/` (for logging)
   - Use `write_file` to write the complete module code to `workspace/modules/{module_name}.py` (for execution)
5. **Verify** by running syntax check
6. **Submit** your final decision with `finish`

## Workspace Structure
```
{workspace_dir}/
├── fixes/               # Timestamped fix logs (generated by `generate_fix`)
│   └── guardian_fix_*.py
└── modules/             # Fixed module code (for next execution)
    └── {module_name}.py  # Complete module class (use `write_file`)
```

## Available Project Files
You can read files from the project checkpoint directory using `read_project_file`:
- `read_project_file("state.json")` — Project state and module progress
- `read_project_file("query_generator/output.json")` — Research domain and search queries
- `read_project_file("paper_fetcher/papers.csv")` — Fetched papers metadata
- `read_project_file("preprocessor/output.json")` — Preprocessed corpus data
- `read_project_file("{module_name}/logs/error.log")` — Module execution logs

The `read_project_file` tool accepts paths relative to the checkpoint root directory (checkpoints/{run_id}/).

## Rules
- Understand the **research domain** and **project progress** before diagnosing errors
- Always investigate before generating a fix
- Fix code must be **complete and runnable** — no placeholders
- Use `write_file` to save fixed module code to `workspace/modules/{module_name}.py`
- The orchestrator will load `workspace/modules/{module_name}.py` instead of system module
- If you cannot fix the error, escalate with `finish(outcome="escalated")`
- Maximum {max_steps} analysis steps — be efficient
- Respond in the same language as the error context (Chinese or English)

## Module Creation
When the user requests adding a new analysis method or computation module:
1. **Understand the need** — What data does it need? What output should it produce?
2. **Check data availability** — Confirm context.previous_outputs has the required inputs
3. **Generate module** — Call `create_module` with complete, runnable code
4. **Specify insertion point** — Use `insert_after` to place it after its input dependency
5. **Submit for approval** — Call `add_to_pipeline` with a rationale

Module code requirements:
- Inherit from `BaseModule` (from modules.base import BaseModule, HardwareSpec, RunContext)
- Define: `name`, `version`, `input_schema()`, `output_schema()`, `config_schema()`
- Implement `process(input_data, config, context) -> dict`
- Use `context.previous_outputs` to access non-immediate-predecessor module data
- Use `context.get_output_path()` to save output files
- Do NOT use the same name as any of the 10 system modules
"""

ERROR_PROMPT_TEMPLATE = """\
## Error Report

**Module**: {module_name}
**Error Type**: `{error_type}`
**Error Message**: ```
{error_message}
```

**Traceback**:
```
{traceback_str}
```

## Execution Context
{context_json}

## Available Previous Outputs
{previous_outputs}

Please analyze this error and determine the root cause. Use tools to investigate if needed.
"""

# ---------------------------------------------------------------------------
#  Tool Executor
# ---------------------------------------------------------------------------


class GuardianToolExecutor:
    """执行 LLM 请求的工具调用."""

    def __init__(self, workspace_dir: Path, tool_registry: ToolRegistry):
        self.workspace_dir = workspace_dir
        self.registry = tool_registry
        self.logger = logging.getLogger("guardian.tool_executor")

        # 生成的修复（由 generate_fix 工具填充）
        self.generated_fix: Optional[FixCode] = None
        self.finish_decision: Optional[dict] = None

        # 模块注入（由 create_module / add_to_pipeline 工具填充）
        self.pending_injections: list[dict] = []
        self.injection_request: Optional[dict] = None

    def execute(self, tool_call: ToolCall) -> str:
        """执行一个工具调用，返回结果文本."""
        try:
            args = json.loads(tool_call.arguments)
        except json.JSONDecodeError as e:
            return f"Error: Invalid JSON arguments: {e}"

        self.logger.info(f"Tool call: {tool_call.name}({list(args.keys())})")

        if tool_call.name == "read_file":
            return self._read_file(args)
        elif tool_call.name == "read_project_file":
            return self._read_project_file(args)
        elif tool_call.name == "write_file":
            return self._write_file(args)
        elif tool_call.name == "search_files":
            return self._search_files(args)
        elif tool_call.name == "grep_content":
            return self._grep_content(args)
        elif tool_call.name == "run_command":
            return self._run_command(args)
        elif tool_call.name == "generate_fix":
            return self._generate_fix(args)
        elif tool_call.name == "finish":
            return self._finish(args)
        elif tool_call.name == "create_module":
            return self._create_module(args)
        elif tool_call.name == "add_to_pipeline":
            return self._add_to_pipeline(args)
        else:
            return f"Error: Unknown tool '{tool_call.name}'"

    def _read_file(self, args: dict) -> str:
        tool = self.registry.get("ReadFile")
        if not tool:
            return "Error: ReadFile tool not available"
        result: ToolResult = tool.run(args.get("path", ""), args.get("encoding", "utf-8"))
        if result.success:
            content = result.output
            # 截断过长的内容
            if len(content) > 8000:
                content = content[:8000] + f"\n... [truncated, total {len(result.output)} chars]"
            return content
        return f"Error reading file: {result.error}"

    def _read_project_file(self, args: dict) -> str:
        """Read file from project checkpoint directory (relative to workspace parent)."""
        path = args.get("path", "")
        if not path:
            return "Error: No path specified"

        # Resolve to checkpoint root (workspace_dir is checkpoints/{run_id}/workspace)
        # Use resolve() to get absolute path
        checkpoint_root = self.workspace_dir.parent.resolve()
        target_path = (checkpoint_root / path).resolve()

        # Debug logging
        self.logger.debug(f"read_project_file: checkpoint_root={checkpoint_root}")
        self.logger.debug(f"read_project_file: target_path={target_path}")
        self.logger.debug(f"read_project_file: path={path}")

        # Security check: ensure path doesn't escape checkpoint root
        checkpoint_root_str = str(checkpoint_root)
        target_path_str = str(target_path)

        if not target_path_str.startswith(checkpoint_root_str):
            return f"Error: Path must be under checkpoint directory.\nCheckpoint: {checkpoint_root}\nTarget: {target_path}"

        # Read file
        try:
            encoding = args.get("encoding", "utf-8")
            content = target_path.read_text(encoding=encoding)
            # Truncate long content
            if len(content) > 8000:
                content = content[:8000] + f"\n... [truncated, total {len(content)} chars]"
            return content
        except FileNotFoundError:
            return f"Error: File not found: {target_path}"
        except Exception as e:
            return f"Error reading file: {e}"

    def _write_file(self, args: dict) -> str:
        """Write file to workspace directory only (security constraint)."""
        tool = self.registry.get("WriteFile")
        if not tool:
            return "Error: WriteFile tool not available"

        # Force path to be under workspace_dir
        path = args.get("path", "")
        if not path:
            return "Error: No path specified"

        # Resolve to absolute path under workspace
        target_path = (self.workspace_dir / path).resolve()

        # Ensure target is still under workspace (prevent path traversal)
        try:
            target_path.relative_to(self.workspace_dir.resolve())
        except ValueError:
            return f"Error: Path must be under workspace directory. Workspace: {self.workspace_dir}"

        result: ToolResult = tool.run(str(target_path), args.get("content", ""))
        if result.success:
            return f"File written to workspace: {target_path}"
        return f"Error writing file: {result.error}"

    def _search_files(self, args: dict) -> str:
        tool = self.registry.get("Glob")
        if not tool:
            return "Error: Glob tool not available"
        result: ToolResult = tool.run(args.get("pattern", "*"), args.get("directory"))
        if result.success:
            files = result.output
            if not files:
                return "No files found matching the pattern."
            return "Files found:\n" + "\n".join(f"  - {f}" for f in files[:50])
        return f"Error searching files: {result.error}"

    def _grep_content(self, args: dict) -> str:
        tool = self.registry.get("Grep")
        if not tool:
            return "Error: Grep tool not available"
        result: ToolResult = tool.run(
            args.get("pattern", ""),
            args.get("directory"),
            args.get("file_pattern", "*.py"),
        )
        if result.success:
            matches = result.output
            if not matches:
                return "No matches found."
            lines = []
            for m in matches[:30]:
                lines.append(f"  {m['file']}:{m['line']}: {m['content']}")
            return "Matches:\n" + "\n".join(lines)
        return f"Error searching content: {result.error}"

    def _run_command(self, args: dict) -> str:
        tool = self.registry.get("Shell")
        if not tool:
            return "Error: Shell tool not available"
        result: ToolResult = tool.run(args.get("command", ""), args.get("timeout", 30))
        output = ""
        if result.output:
            output = result.output[:4000]
        if result.error:
            output += f"\nSTDERR: {result.error[:2000]}"
        if not result.success:
            output += f"\n[Exit code: {result.metadata.get('returncode', 'unknown')}]"
        return output or "(no output)"

    def _generate_fix(self, args: dict) -> str:
        """处理 generate_fix 工具调用."""
        code = args.get("code", "")
        description = args.get("description", "")
        error_type = args.get("error_type", "unknown")

        if not code.strip():
            return "Error: Fix code cannot be empty."

        # 语法检查
        try:
            compile(code, "<string>", "exec")
        except SyntaxError as e:
            return f"Syntax error in generated code: {e}\nPlease fix the syntax and try again."

        # 保存修复
        self.generated_fix = FixCode(
            module_name="",  # 由 GuardianSoul 填充
            code=code,
            description=description,
            timestamp=datetime.now().isoformat(),
            error_type=error_type,
            metadata={"source": "llm_generated"},
        )

        # 同时写入文件（使用绝对路径）
        fixes_dir = self.workspace_dir / "fixes"
        fixes_dir.mkdir(parents=True, exist_ok=True)
        ts_clean = datetime.now().isoformat().replace(":", "-").replace(".", "-")
        fix_filename = f"guardian_fix_{ts_clean}.py"
        fix_path = fixes_dir / fix_filename

        header = f'"""\nGuardian Agent LLM-Generated Fix\nDescription: {description}\nError Type: {error_type}\nTimestamp: {self.generated_fix.timestamp}\n"""\n\n'
        fix_path.write_text(header + code, encoding="utf-8")

        abs_path = fix_path.resolve()
        return f"Fix generated and saved to: {abs_path}\n\nPlease verify with `run_command` and then call `finish`."

    def _create_module(self, args: dict) -> str:
        """Create a new analysis module in workspace/modules/."""
        import re
        import importlib.util

        module_name = args.get("module_name", "")
        code = args.get("code", "")
        description = args.get("description", "")
        insert_after = args.get("insert_after")
        insert_before = args.get("insert_before")

        if not module_name or not code:
            return "Error: module_name and code are required."

        # Validate snake_case
        if not re.match(r"^[a-z][a-z0-9_]*$", module_name):
            return f"Error: module_name must be snake_case (got '{module_name}')"

        # Block system module names
        system_modules = {
            "query_generator", "paper_fetcher", "preprocessor",
            "frequency_analyzer", "topic_modeler", "burst_detector",
            "tsr_ranker", "network_analyzer", "visualizer", "report_generator",
        }
        if module_name in system_modules:
            return f"Error: Cannot use system module name '{module_name}'. Choose a different name."

        # Syntax check
        try:
            compile(code, f"<{module_name}>", "exec")
        except SyntaxError as e:
            return f"Syntax error in module code: {e}\nPlease fix the syntax and try again."

        # Validate it contains a BaseModule subclass
        try:
            spec = importlib.util.spec_from_file_location(
                module_name,
                str(self.workspace_dir / "modules" / f"{module_name}.py"),
                submodule_search_locations=[],
            )
            # We can't actually import without writing first, so do a text-based check
            if "BaseModule" not in code:
                return "Error: Module code must inherit from BaseModule."
            if "def process(" not in code:
                return "Error: Module must implement process() method."
        except Exception as e:
            return f"Error validating module: {e}"

        # Write to workspace/modules/ (reuses _write_file security logic)
        modules_dir = self.workspace_dir / "modules"
        modules_dir.mkdir(parents=True, exist_ok=True)
        module_path = modules_dir / f"{module_name}.py"
        module_path.write_text(code, encoding="utf-8")

        # Extract schemas via simple text parsing (avoid import side effects)
        input_schema = {"type": "object", "properties": {}}
        output_schema = {"type": "object", "properties": {}}
        # Best-effort: note that actual schema is resolved at runtime

        # Store in pending injections
        injection_entry = {
            "module_name": module_name,
            "description": description,
            "insert_after": insert_after,
            "insert_before": insert_before,
            "input_schema": input_schema,
            "output_schema": output_schema,
            "path": str(module_path),
        }
        self.pending_injections.append(injection_entry)

        self.logger.info("Created module '%s' at %s", module_name, module_path)
        return (
            f"Module '{module_name}' created at workspace/modules/{module_name}.py\n"
            f"Description: {description}\n"
            f"Next: call `add_to_pipeline` to submit for user approval."
        )

    def _add_to_pipeline(self, args: dict) -> str:
        """Submit a previously created module for pipeline injection (requires user approval)."""
        module_name = args.get("module_name", "")
        rationale = args.get("rationale", "")
        insert_after = args.get("insert_after")
        insert_before = args.get("insert_before")

        if not module_name:
            return "Error: module_name is required."

        # Verify module was created first
        matching = [p for p in self.pending_injections if p["module_name"] == module_name]
        if not matching:
            return f"Error: Module '{module_name}' not found. Call `create_module` first."

        injection = matching[0]

        # Override insert positions if provided here
        if insert_after:
            injection["insert_after"] = insert_after
        if insert_before:
            injection["insert_before"] = insert_before
        injection["rationale"] = rationale

        # Store injection request for the orchestrator to process
        self.injection_request = injection

        self.logger.info("Injection request for '%s': %s", module_name, rationale)
        return (
            f"Module injection request submitted for '{module_name}'.\n"
            f"Rationale: {rationale}\n"
            f"Insert after: {injection.get('insert_after') or 'end'}\n"
            f"Insert before: {injection.get('insert_before') or 'N/A'}\n"
            f"Waiting for user approval..."
        )

    def _finish(self, args: dict) -> str:
        """处理 finish 工具调用."""
        self.finish_decision = {
            "outcome": args.get("outcome", "failed"),
            "summary": args.get("summary", ""),
            "confidence": args.get("confidence", 0.8),
        }
        outcome = self.finish_decision["outcome"]
        return f"Decision recorded: {outcome}. Analysis complete."


# ---------------------------------------------------------------------------
#  GuardianSoul
# ---------------------------------------------------------------------------


class GuardianSoul:
    """持续交互式 Guardian Agent 主循环.

    类似 kimi-cli 的 KimiSoul：
    - 收到错误后自动激活
    - 通过 LLM 多轮对话分析问题
    - LLM 可以调用工具（读文件、搜索、执行命令）
    - 生成修复代码到 workspace
    - 返回结构化的 GuardianDecision

    使用方式:
        soul = GuardianSoul(module_name="preprocessor", llm=provider, workspace_dir=Path(...))
        decision = soul.activate(error, context)
    """

    def __init__(
        self,
        module_name: str,
        llm: BaseLLMProvider,
        workspace_dir: Path,
        max_steps: int = 50,
        pipeline_stage: str = "",
        guardian: Optional[GuardianAgent] = None,
        project_id: Optional[str] = None,
        event_loop: Optional[Any] = None,
        run_id: Optional[str] = None,
        max_history_files: int = 10,
        preserve_recent_messages: int = 20,
    ):
        self.module_name = module_name
        self.llm = llm
        self.workspace_dir = workspace_dir
        self.max_steps = max_steps
        self.pipeline_stage = pipeline_stage
        self.guardian = guardian  # 可选的模板 Guardian 作为后备
        self.project_id = project_id  # For real-time communication
        self.event_loop = event_loop  # Main event loop for async calls
        self.run_id = run_id or workspace_dir.parent.name  # Extract run_id from workspace path
        self._stop_requested = False  # Stop signal flag

        self.tool_registry = create_default_registry(workspace_dir)
        self.logger = logging.getLogger(f"guardian.soul.{module_name}")

        # Load project context
        self.project_context = self._load_project_context()

        # 初始化 ConversationContext（持久化对话历史）
        context_file = workspace_dir / "guardian_context.jsonl"
        self.context = ConversationContext(
            context_file=context_file,
            max_history_files=max_history_files,
            preserve_recent_messages=preserve_recent_messages,
        )

        # 动态上下文注入器
        self.injector = DynamicInjector(
            workspace_dir=workspace_dir,
            project_id=project_id or "unknown",
            mode="agent",
            update_interval=10,
        )

        # Communication hub for real-time interaction
        if project_id:
            from core.communication_hub import get_communication_hub
            self.comm_hub = get_communication_hub()
        else:
            self.comm_hub = None

    def _load_project_context(self) -> dict:
        """Load project context from state.json and query_generator output.

        Returns project metadata including:
        - run_id, status, mode, created_at
        - research_domain (from query_generator keywords)
        - pipeline_config (max_papers, etc.)
        - module progress (completed/failed counts)
        """
        context = {
            "run_id": self.run_id,
            "research_domain": "Unknown",
            "status": "unknown",
            "mode": "automated",
            "created_at": None,
            "max_papers": 100,
            "completed_modules": 0,
            "total_modules": 0,
            "failed_modules": [],
        }

        try:
            # Load state.json
            state_path = self.workspace_dir.parent / "state.json"
            if state_path.exists():
                with open(state_path, encoding="utf-8") as f:
                    state = json.load(f)

                context["status"] = state.get("status", "unknown")
                context["mode"] = state.get("mode", "automated")
                context["created_at"] = state.get("created_at")

                # Extract pipeline config
                pipeline_config = state.get("pipeline_config", {})
                context["max_papers"] = pipeline_config.get("max_papers", 100)

                # Count module progress
                modules = state.get("modules", {})
                context["total_modules"] = len(modules)
                context["completed_modules"] = sum(
                    1 for m in modules.values() if m.get("status") == "completed"
                )
                context["failed_modules"] = [
                    name for name, m in modules.items() if m.get("status") == "failed"
                ]

            # Load research domain from query_generator output
            query_output_path = self.workspace_dir.parent / "query_generator" / "output.json"
            if query_output_path.exists():
                with open(query_output_path, encoding="utf-8") as f:
                    query_output = json.load(f)
                    keywords = query_output.get("keywords", [])
                    if keywords:
                        context["research_domain"] = ", ".join(keywords[:5])

        except Exception as e:
            self.logger.warning(f"Failed to load project context: {e}")

        return context

    def request_stop(self):
        """Request GuardianSoul to stop processing."""
        self._stop_requested = True
        self.logger.info(f"Stop requested for GuardianSoul ({self.module_name})")

    def _check_stop_requested(self) -> bool:
        """Check if stop was requested via communication hub."""
        # Check internal flag
        if self._stop_requested:
            return True

        # Check for STOP command from communication hub
        if self.comm_hub and self.project_id:
            try:
                steer_cmd = self.comm_hub.get_steer(self.project_id)
                if steer_cmd and steer_cmd.upper() == "STOP_GUARDIAN":
                    self.logger.info("Stop command received via communication hub")
                    self._stop_requested = True
                    return True
            except Exception as e:
                self.logger.debug(f"Could not check steer queue: {e}")

        return False

    def activate(
        self,
        error: Exception,
        context: dict,
        user_message: Optional[str] = None,
    ) -> GuardianDecision:
        """激活 Guardian Agent 处理错误.

        这是主入口。错误发生后由 Orchestrator 调用。

        Args:
            error: 模块抛出的异常
            context: 执行上下文（input_data, config, previous_outputs 等）
            user_message: 可选的用户附加说明

        Returns:
            GuardianDecision 包含最终决策
        """
        timestamp = datetime.now().isoformat()
        self.logger.info(f"GuardianSoul activated for {self.module_name}: {error}")

        # 清空上下文，构建初始消息
        self.context.clear()
        self._build_initial_messages(error, context, user_message)

        # 创建初始检查点
        initial_checkpoint = self.context.create_checkpoint({
            "phase": "initial",
            "module": self.module_name,
        })

        # 创建工具执行器
        executor = GuardianToolExecutor(self.workspace_dir, self.tool_registry)

        # Agent Loop
        for step in range(self.max_steps):
            self.logger.info(f"Step {step + 1}/{self.max_steps}")

            # Check if stop requested
            if self._check_stop_requested():
                self.logger.info("Stop requested, terminating GuardianSoul")
                return self._make_decision(
                    error=error,
                    context=context,
                    executor=executor,
                    outcome="stopped",
                    summary="Guardian stopped by user request",
                    confidence=0.0,
                    timestamp=timestamp,
                )

            # Broadcast step start
            if self.comm_hub and self.project_id and self.event_loop:
                import asyncio
                try:
                    if self.event_loop.is_running():
                        # Schedule coroutine in main event loop from thread
                        future = asyncio.run_coroutine_threadsafe(
                            self.comm_hub.ai_thinking(
                                self.project_id,
                                f"Step {step + 1}/{self.max_steps}: Analyzing..."
                            ),
                            self.event_loop
                        )
                except Exception as e:
                    self.logger.warning(f"Failed to broadcast step: {e}")

            # Check for user messages
            if self.comm_hub and self.project_id and self.event_loop:
                try:
                    if self.event_loop.is_running():
                        future = asyncio.run_coroutine_threadsafe(
                            self.comm_hub.get_user_message(self.project_id, timeout=0.1),
                            self.event_loop
                        )
                        user_msg = future.result(timeout=0.2)
                        if user_msg:
                            # User sent a message, add to conversation
                            self.context.append_message(
                                Message(role="user", content=f"[User input]: {user_msg}")
                            )
                            self.logger.info(f"User message received: {user_msg[:100]}")
                except Exception:
                    pass

            try:
                # 动态注入上下文更新
                update_msg = self.injector.inject_context_update()
                if update_msg:
                    self.context.append_message(update_msg)
                    self.logger.debug("Injected context update")

                # 检查是否需要压缩
                if self.context.should_compact():
                    self.logger.info("Context approaching token limit, compacting...")
                    saved = self.context.compact(self.llm, preserve_recent=15)
                    self.logger.info(f"Compaction saved {saved} tokens")

                # 调用 LLM（使用规范化后的历史）
                response = self.llm.chat(
                    messages=self.context.get_normalized_history(),
                    tools=GUARDIAN_TOOL_DEFS,
                    temperature=0.2,  # 低温度，确保分析的确定性
                )
            except Exception as e:
                self.logger.error(f"LLM call failed: {e}")
                return self._make_decision(
                    error=error,
                    context=context,
                    executor=executor,
                    outcome="failed",
                    summary=f"LLM 调用失败: {e}",
                    confidence=0.0,
                    timestamp=timestamp,
                )

            # 处理 LLM 响应
            if response.content:
                if response.has_tool_calls:
                    # Assistant message must carry tool_calls for OpenAI API
                    self.context.append_message(
                        Message(role="assistant", content=response.content, tool_calls=response.tool_calls),
                        token_estimate=len(response.content.split()) * 2  # 粗略估算
                    )
                else:
                    self.context.append_message(
                        Message(role="assistant", content=response.content),
                        token_estimate=len(response.content.split()) * 2
                    )
                self.logger.debug(f"LLM: {response.content[:200]}")

                # Broadcast AI thinking
                if self.comm_hub and self.project_id and self.event_loop:
                    try:
                        if self.event_loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                self.comm_hub.ai_thinking(self.project_id, response.content[:500]),
                                self.event_loop
                            )
                    except Exception:
                        pass

            if response.has_tool_calls:
                # 处理工具调用
                for tc in response.tool_calls:
                    self.logger.info(f"  Tool: {tc.name}")

                    # Broadcast tool call
                    if self.comm_hub and self.project_id and self.event_loop:
                        try:
                            if self.event_loop.is_running():
                                args = json.loads(tc.arguments) if tc.arguments else {}
                                asyncio.run_coroutine_threadsafe(
                                    self.comm_hub.ai_tool_call(self.project_id, tc.name, args),
                                    self.event_loop
                                )
                        except Exception:
                            pass

                    # 执行工具
                    result_text = executor.execute(tc)

                    # Broadcast tool result
                    if self.comm_hub and self.project_id and self.event_loop:
                        try:
                            if self.event_loop.is_running():
                                asyncio.run_coroutine_threadsafe(
                                    self.comm_hub.ai_tool_result(self.project_id, tc.name, result_text),
                                    self.event_loop
                                )
                        except Exception:
                            pass

                    # 追加工具调用和结果到消息
                    self.context.append_message(
                        Message(
                            role="assistant",
                            content="",  # OpenRouter/某些 provider 要求 content 必须是字符串
                        )
                    )
                    self.context.append_message(
                        Message(
                            role="tool",
                            content=result_text,
                            name=tc.name,
                            tool_call_id=tc.id,
                        ),
                        token_estimate=len(result_text.split()) * 2
                    )

                    self.logger.debug(f"  Result: {result_text[:200]}")

                # 检查是否已 finish
                if executor.finish_decision:
                    return self._make_decision_from_executor(
                        error=error,
                        context=context,
                        executor=executor,
                        timestamp=timestamp,
                    )
            else:
                # 没有工具调用，LLM 只给了文本回复
                # 如果没有 finish，检查是否应该提示继续
                if not executor.finish_decision and not executor.generated_fix:
                    # 追加提示，引导 LLM 继续行动
                    self.context.append_message(Message(
                        role="user",
                        content="Please continue your analysis. Use tools to investigate or call `generate_fix` when ready.",
                    ))
                elif executor.generated_fix and not executor.finish_decision:
                    self.context.append_message(Message(
                        role="user",
                        content="Fix generated. Please verify it and call `finish` to submit your decision.",
                    ))

        # 达到最大步数
        self.logger.warning(f"Max steps ({self.max_steps}) reached")
        return self._make_decision(
            error=error,
            context=context,
            executor=executor,
            outcome="failed" if not executor.generated_fix else "success",
            summary=f"达到最大分析步数 ({self.max_steps})",
            confidence=0.5,
            timestamp=timestamp,
        )

    def continue_dialogue(self, user_message: str) -> GuardianDecision:
        """用户追加指令，继续对话.

        用于交互模式：Guardian 完成分析后，用户可以追问或调整方向。

        Args:
            user_message: 用户追加的消息

        Returns:
            更新的 GuardianDecision
        """
        self.context.append_message(Message(role="user", content=user_message))

        executor = GuardianToolExecutor(self.workspace_dir, self.tool_registry)

        # 继续循环
        for step in range(self.max_steps):
            # Check if stop requested
            if self._check_stop_requested():
                self.logger.info("Stop requested during dialogue continuation")
                return self._make_decision(
                    error=None,
                    context={},
                    executor=executor,
                    outcome="stopped",
                    summary="Guardian stopped by user request during dialogue",
                    confidence=0.0,
                    timestamp=datetime.now().isoformat(),
                )

            try:
                response = self.llm.chat(
                    messages=self.context.get_normalized_history(),
                    tools=GUARDIAN_TOOL_DEFS,
                    temperature=0.2,
                )
            except Exception as e:
                self.logger.error(f"LLM call failed in dialogue: {e}")
                break

            if response.content:
                self.context.append_message(
                    Message(role="assistant", content=response.content),
                    token_estimate=len(response.content.split()) * 2
                )

            if response.has_tool_calls:
                for tc in response.tool_calls:
                    result_text = executor.execute(tc)
                    self.context.append_message(
                        Message(role="tool", content=result_text, name=tc.name, tool_call_id=tc.id),
                        token_estimate=len(result_text.split()) * 2
                    )

                if executor.finish_decision:
                    return self._make_decision_from_executor(
                        error=Exception("User-initiated dialogue"),
                        context={},
                        executor=executor,
                        timestamp=datetime.now().isoformat(),
                    )
            else:
                if not executor.finish_decision:
                    self.context.append_message(Message(
                        role="user",
                        content="Please continue. Call `finish` when ready.",
                    ))

        return self._make_decision(
            error=Exception("User-initiated dialogue"),
            context={},
            executor=executor,
            outcome="failed",
            summary="Dialogue ended without resolution",
            confidence=0.0,
            timestamp=datetime.now().isoformat(),
        )

    # ------------------------------------------------------------------
    #  Internal helpers
    # ------------------------------------------------------------------

    def _build_initial_messages(
        self,
        error: Exception,
        context: dict,
        user_message: Optional[str],
    ) -> None:
        """构建初始消息并追加到 context."""

        # System prompt with project context
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            run_id=self.project_context.get("run_id", "unknown"),
            research_domain=self.project_context.get("research_domain", "Unknown"),
            project_status=self.project_context.get("status", "unknown"),
            completed_modules=self.project_context.get("completed_modules", 0),
            total_modules=self.project_context.get("total_modules", 0),
            execution_mode=self.project_context.get("mode", "automated"),
            max_papers=self.project_context.get("max_papers", 100),
            failed_modules=", ".join(self.project_context.get("failed_modules", [])),
            current_date=datetime.now().strftime("%Y-%m-%d"),
            module_name=self.module_name,
            workspace_dir=str(self.workspace_dir),
            pipeline_stage=self.pipeline_stage or self.module_name,
            max_steps=self.max_steps,
        )
        self.context.append_message(Message(role="system", content=system_prompt))

        # Error prompt
        previous_outputs = ""
        if "previous_outputs" in context:
            prev = context["previous_outputs"]
            if isinstance(prev, dict):
                previous_outputs = "\n".join(f"- {k}: {type(v).__name__}" for k, v in prev.items())

        # 安全序列化 context
        safe_context = {}
        for k, v in context.items():
            try:
                json.dumps({k: v})  # 测试可序列化
                safe_context[k] = v
            except (TypeError, ValueError):
                safe_context[k] = str(type(v).__name__)

        error_prompt = ERROR_PROMPT_TEMPLATE.format(
            module_name=self.module_name,
            error_type=type(error).__name__,
            error_message=str(error),
            traceback_str=traceback.format_exc(),
            context_json=json.dumps(safe_context, indent=2, ensure_ascii=False, default=str)[:3000],
            previous_outputs=previous_outputs or "(none)",
        )
        self.context.append_message(Message(role="user", content=error_prompt))

        # 用户附加说明
        if user_message:
            self.context.append_message(Message(role="user", content=user_message))

    def _make_decision(
        self,
        error: Exception,
        context: dict,
        executor: GuardianToolExecutor,
        outcome: str,
        summary: str,
        confidence: float,
        timestamp: str,
    ) -> GuardianDecision:
        """构建 GuardianDecision."""
        fix = executor.generated_fix

        return GuardianDecision(
            module=self.module_name,
            error={
                "type": type(error).__name__,
                "message": str(error),
                "traceback": traceback.format_exc(),
            },
            analysis=ErrorAnalysis(
                error_type=fix.error_type if fix else "unknown",
                error_message=str(error),
                root_cause=summary,
                suggested_fix=fix.description if fix else "No fix generated",
                confidence=confidence,
                context=context,
            ),
            fix_generated=fix is not None,
            fix_path=None,
            test_passed=False,
            applied=False,
            outcome=outcome,
            timestamp=timestamp,
        )

    def _make_decision_from_executor(
        self,
        error: Exception,
        context: dict,
        executor: GuardianToolExecutor,
        timestamp: str,
    ) -> GuardianDecision:
        """从 executor 的 finish 决策构建 GuardianDecision."""
        fd = executor.finish_decision
        fix = executor.generated_fix

        # 如果有 fix，尝试测试
        test_passed = False
        fix_path = None
        if fix:
            fix.module_name = self.module_name
            test_passed = self._test_fix_code(fix.code)

            # 记录 fix path
            fixes_dir = self.workspace_dir / "fixes"
            fix_files = sorted(fixes_dir.glob("guardian_fix_*.py"), reverse=True)
            if fix_files:
                fix_path = str(fix_files[0].resolve())

        # 记录日志
        self._log_interaction(executor, fd)

        decision = GuardianDecision(
            module=self.module_name,
            error={
                "type": type(error).__name__,
                "message": str(error),
                "traceback": traceback.format_exc(),
            },
            analysis=ErrorAnalysis(
                error_type=fix.error_type if fix else "unknown",
                error_message=str(error),
                root_cause=fd.get("summary", ""),
                suggested_fix=fix.description if fix else "No fix generated",
                confidence=fd.get("confidence", 0.5),
                context=context,
            ),
            fix_generated=fix is not None,
            fix_path=fix_path,
            test_passed=test_passed,
            applied=False,
            outcome=fd.get("outcome", "failed"),
            timestamp=timestamp,
        )

        # 写入 agent_logs
        self._write_decision_log(decision)

        return decision

    def _test_fix_code(self, code: str) -> bool:
        """测试修复代码语法."""
        try:
            compile(code, "<string>", "exec")
            return True
        except SyntaxError:
            return False

    def _log_interaction(self, executor: GuardianToolExecutor, finish_decision: dict):
        """记录交互日志（供后续审计）."""
        logs_dir = self.workspace_dir / "agent_logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        log_file = logs_dir / f"{self.module_name}_soul.json"

        interaction = {
            "module": self.module_name,
            "timestamp": datetime.now().isoformat(),
            "steps": len([m for m in self.messages if m.role == "assistant"]),
            "tools_used": [m.name for m in self.messages if m.role == "tool" and m.name],
            "fix_generated": executor.generated_fix is not None,
            "finish_decision": finish_decision,
            "message_count": len(self.messages),
        }

        if log_file.exists():
            with open(log_file, "r", encoding="utf-8") as f:
                logs = json.load(f)
        else:
            logs = []

        logs.append(interaction)

        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=2, ensure_ascii=False)

    def _write_decision_log(self, decision: GuardianDecision):
        """写入决策日志."""
        logs_dir = self.workspace_dir / "agent_logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        log_file = logs_dir / f"{self.module_name}_guardian.json"

        log_entry = {
            "module": decision.module,
            "error": decision.error,
            "analysis": {
                "error_type": decision.analysis.error_type,
                "error_message": decision.analysis.error_message,
                "root_cause": decision.analysis.root_cause,
                "suggested_fix": decision.analysis.suggested_fix,
                "confidence": decision.analysis.confidence,
            },
            "fix_generated": decision.fix_generated,
            "fix_path": decision.fix_path,
            "test_passed": decision.test_passed,
            "applied": decision.applied,
            "outcome": decision.outcome,
            "timestamp": decision.timestamp,
            "source": "guardian_soul",
        }

        if log_file.exists():
            with open(log_file, "r", encoding="utf-8") as f:
                logs = json.load(f)
        else:
            logs = []

        logs.append(log_entry)

        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=2, ensure_ascii=False)
