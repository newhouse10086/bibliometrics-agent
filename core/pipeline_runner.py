"""
Pipeline Runner - Connects Web API to actual pipeline execution.

Runs pipeline in background tasks and tracks progress.
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
import traceback

from core.orchestrator import PipelineOrchestrator
from core.state_manager import StateManager
from core.workspace_manager import WorkspaceManager, get_default_workspace_dir
from core.project_logger import get_log_manager
from modules.registry import ModuleRegistry

logger = logging.getLogger(__name__)


class PipelineRunner:
    """Manages pipeline execution for web API projects."""

    def __init__(self):
        """Initialize pipeline runner."""
        self.workspace_manager = WorkspaceManager(get_default_workspace_dir())
        self.registry = ModuleRegistry()

        # Track active runs
        self.active_runs: Dict[str, asyncio.Task] = {}

        # Track active orchestrators (for module injection)
        self.active_orchestrators: Dict[str, PipelineOrchestrator] = {}

        # Per-project state managers (keyed by project_id)
        self.state_managers: Dict[str, StateManager] = {}

        # Progress callbacks
        self.progress_callbacks: Dict[str, list] = {}

        # Initialize modules
        self._register_modules()

        logger.info("PipelineRunner initialized")

    def _register_modules(self):
        """Register all available modules."""
        # Auto-discover and register modules from modules/ package
        self.registry.auto_discover()

        logger.info(f"Registered {len(self.registry.list_modules())} modules: {self.registry.list_modules()}")

    async def start_pipeline(
        self,
        project_id: str,
        project_name: str,
        research_domain: str,
        config: Dict[str, Any],
    ) -> bool:
        """Start pipeline execution for a project.

        Args:
            project_id: Unique project identifier
            project_name: Human-readable project name
            research_domain: Research domain/topic
            config: Configuration dict (max_papers, etc.)

        Returns:
            True if started successfully, False otherwise
        """
        # Check if already running
        if project_id in self.active_runs:
            logger.warning(f"Project {project_id} is already running")
            return False

        try:
            # Create workspace
            workspace_dir = self.workspace_manager.create_workspace(
                name=f"{project_name}_{project_id}",
                description=f"Workspace for {project_name} - {research_domain}",
                metadata={
                    "project_id": project_id,
                    "research_domain": research_domain,
                    "config": config,
                }
            )

            logger.info(f"Created workspace at: {workspace_dir}")

            # Create project-specific state manager
            state_manager = StateManager(workspace_dir)
            self.state_managers[project_id] = state_manager

            # Setup project-specific logger
            log_manager = get_log_manager()
            log_manager.setup_project_logger(project_id, workspace_dir)
            logger.info(f"Project logger initialized for {project_id}")

            # Add LLM configuration to config
            import os
            api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
            base_url = os.environ.get("OPENROUTER_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
            if os.environ.get("OPENROUTER_API_KEY"):
                base_url = base_url or "https://openrouter.ai/api/v1"
            model = os.environ.get("DEFAULT_LLM_MODEL", "qwen/qwen3.6-plus")
            llm_config = {
                "provider": "openai",
                "api_key": api_key,
                "base_url": base_url,
                "model": model,
            }

            # Only add if API key is available
            if llm_config["api_key"]:
                config["llm"] = llm_config
                logger.info(f"LLM provider configured: {llm_config.get('model')} via {llm_config.get('base_url', 'OpenAI')}")
            else:
                logger.warning("No LLM API key found in environment - GuardianSoul will use template fallback")

            # Create orchestrator with mode and custom pipeline order
            mode = config.get("pipeline_mode", "automated")
            pipeline_order = config.get("pipeline_order")  # None = DEFAULT_PIPELINE

            # Load plugins for pluggable mode
            if mode == "pluggable" and config.get("plugin_dir"):
                plugin_dir = Path(config["plugin_dir"])
                if plugin_dir.exists():
                    self.registry.load_plugins(plugin_dir)
                    logger.info(f"Loaded plugins from {plugin_dir}")

            orchestrator = PipelineOrchestrator(
                registry=self.registry,
                state_manager=state_manager,
                config=config,
                pipeline_order=pipeline_order,
                mode=mode,
            )

            # Pass HITL checkpoints if configured
            if mode == "hitl" and config.get("hitl", {}).get("checkpoints"):
                orchestrator.hitl_checkpoints = config["hitl"]["checkpoints"]

            # Prepare initial input
            initial_input = {
                "research_domain": research_domain,
                "max_papers": config.get("max_papers", 100),
                "require_abstract": config.get("require_abstract", True),
            }

            # Start background task
            task = asyncio.create_task(
                self._run_pipeline_async(
                    project_id=project_id,
                    orchestrator=orchestrator,
                    initial_input=initial_input,
                )
            )

            self.active_runs[project_id] = task
            self.active_orchestrators[project_id] = orchestrator

            logger.info(f"Started pipeline for project {project_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to start pipeline: {e}")
            logger.error(traceback.format_exc())
            return False

    async def _run_pipeline_async(
        self,
        project_id: str,
        orchestrator: PipelineOrchestrator,
        initial_input: Dict[str, Any],
    ):
        """Execute pipeline in background.

        Args:
            project_id: Project identifier
            orchestrator: Pipeline orchestrator instance
            initial_input: Initial input data
        """
        try:
            logger.info(f"Pipeline execution started for {project_id}")

            # Run pipeline in executor (to avoid blocking)
            loop = asyncio.get_event_loop()
            orchestrator.set_event_loop(loop)
            result = await loop.run_in_executor(
                None,
                orchestrator.run,
                project_id,
                initial_input,
                orchestrator.mode,  # Dynamic mode: "automated", "hitl", or "pluggable"
                False,  # Don't resume
                None,   # start_from
                None,   # end_at
            )

            logger.info(f"Pipeline completed for {project_id}: {result}")

            # Notify completion via callback
            await self._notify_progress(project_id, {
                "type": "pipeline_complete",
                "project_id": project_id,
                "result": result,
                "timestamp": datetime.now().isoformat(),
            })

        except Exception as e:
            logger.error(f"Pipeline failed for {project_id}: {e}")
            logger.error(traceback.format_exc())

            # Notify failure
            await self._notify_progress(project_id, {
                "type": "pipeline_error",
                "project_id": project_id,
                "error": str(e),
                "traceback": traceback.format_exc(),
                "timestamp": datetime.now().isoformat(),
            })

        finally:
            # Remove from active runs
            if project_id in self.active_runs:
                del self.active_runs[project_id]
            if project_id in self.active_orchestrators:
                del self.active_orchestrators[project_id]

    async def pause_pipeline(self, project_id: str) -> bool:
        """Pause a running pipeline via steer command.

        The orchestrator checks the steer queue before each module and will
        pause gracefully when it sees a PAUSE command. This is safer than
        cancelling the asyncio task, which cannot stop a thread running
        in run_in_executor.

        Args:
            project_id: Project identifier

        Returns:
            True if pause command was sent successfully
        """
        if project_id not in self.active_runs:
            logger.warning(f"Project {project_id} is not running")
            return False

        # Send PAUSE steer command — orchestrator picks it up at next module boundary
        from core.communication_hub import get_communication_hub
        comm_hub = get_communication_hub()
        comm_hub.steer(project_id, "PAUSE")

        logger.info(f"Sent PAUSE steer command for project {project_id}")
        return True

    async def resume_pipeline(self, project_id: str, config: Dict[str, Any] = None) -> bool:
        """Resume a paused pipeline from last checkpoint.

        Args:
            project_id: Project identifier
            config: Optional updated config (uses saved state if not provided)

        Returns:
            True if resumed successfully
        """
        if project_id in self.active_runs:
            logger.warning(f"Project {project_id} is already running")
            return False

        try:
            # Get workspace directory for this project
            workspace_dir = self.workspace_manager.get_workspace(project_id)
            if not workspace_dir:
                # Fallback: scan workspace folders for one ending with _{project_id}
                workspace_base = self.workspace_manager.base_dir
                for ws_dir in workspace_base.iterdir():
                    if ws_dir.is_dir() and ws_dir.name.endswith(f"_{project_id}"):
                        workspace_dir = ws_dir
                        break
            if not workspace_dir:
                logger.error(f"Workspace not found for project {project_id}")
                return False

            # Create state manager for this workspace
            state_manager = StateManager(workspace_dir)
            self.state_managers[project_id] = state_manager

            # Setup project-specific logger for resume
            log_manager = get_log_manager()
            log_manager.setup_project_logger(project_id, workspace_dir)
            logger.info(f"Project logger initialized for resume of {project_id}")

            # Load state to get mode and pipeline order
            state = state_manager.get_run_state(project_id)
            mode = state.get("mode", "automated")
            pipeline_order = state.get("pipeline_order") or None
            saved_config = state.get("pipeline_config", {})
            if config:
                saved_config.update(config)

            # Add LLM config if not present
            import os
            if "llm" not in saved_config:
                api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
                base_url = os.environ.get("OPENROUTER_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
                if os.environ.get("OPENROUTER_API_KEY"):
                    base_url = base_url or "https://openrouter.ai/api/v1"
                model = os.environ.get("DEFAULT_LLM_MODEL", "qwen/qwen3.6-plus")
                llm_config = {
                    "provider": "openai",
                    "api_key": api_key,
                    "base_url": base_url,
                    "model": model,
                }
                if llm_config["api_key"]:
                    saved_config["llm"] = llm_config

            # Set HITL checkpoints if applicable
            if mode == "hitl" and state.get("hitl_checkpoints"):
                saved_config.setdefault("hitl", {})["checkpoints"] = state["hitl_checkpoints"]

            orchestrator = PipelineOrchestrator(
                registry=self.registry,
                state_manager=state_manager,
                config=saved_config,
                pipeline_order=pipeline_order,
                mode=mode,
            )

            # Set HITL checkpoints from saved state
            if mode == "hitl" and state.get("hitl_checkpoints"):
                orchestrator.hitl_checkpoints = state["hitl_checkpoints"]

            # Resume in background
            task = asyncio.create_task(
                self._resume_pipeline_async(project_id, orchestrator, mode)
            )
            self.active_runs[project_id] = task
            self.active_orchestrators[project_id] = orchestrator

            logger.info(f"Resumed pipeline for {project_id} (mode={mode})")
            return True

        except FileNotFoundError:
            logger.error(f"No state found for project {project_id}")
            return False
        except Exception as e:
            logger.error(f"Failed to resume pipeline: {e}")
            return False

    async def _resume_pipeline_async(
        self,
        project_id: str,
        orchestrator: PipelineOrchestrator,
        mode: str,
    ):
        """Resume pipeline execution in background."""
        try:
            loop = asyncio.get_event_loop()
            orchestrator.set_event_loop(loop)
            result = await loop.run_in_executor(
                None,
                orchestrator.run,
                project_id,
                {},       # Empty input (loaded from checkpoint)
                mode,
                True,     # resume=True
                None,
                None,
            )

            logger.info(f"Pipeline resumed and completed for {project_id}: {result}")
            await self._notify_progress(project_id, {
                "type": "pipeline_complete",
                "project_id": project_id,
                "result": result,
                "timestamp": datetime.now().isoformat(),
            })

        except Exception as e:
            logger.error(f"Pipeline resume failed for {project_id}: {e}")
            logger.error(traceback.format_exc())
            await self._notify_progress(project_id, {
                "type": "pipeline_error",
                "project_id": project_id,
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            })

        finally:
            if project_id in self.active_runs:
                del self.active_runs[project_id]
            if project_id in self.active_orchestrators:
                del self.active_orchestrators[project_id]

    async def reset_pipeline(self, project_id: str) -> bool:
        """Reset pipeline and clear workspace.

        Args:
            project_id: Project identifier

        Returns:
            True if reset successfully
        """
        # Stop running pipeline
        if project_id in self.active_runs:
            task = self.active_runs[project_id]
            task.cancel()
            del self.active_runs[project_id]

        # Clear state manager
        if project_id in self.state_managers:
            del self.state_managers[project_id]

        # Clear workspace
        try:
            self.workspace_manager.delete_workspace(project_id)
            logger.info(f"Reset workspace for {project_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to reset workspace: {e}")
            return False

    def get_pipeline_status(self, project_id: str) -> Dict[str, Any]:
        """Get current status of a pipeline run.

        Args:
            project_id: Project identifier

        Returns:
            Status dict
        """
        # Check if running
        is_running = project_id in self.active_runs

        # Get workspace stats
        workspace = self.workspace_manager.get_workspace(project_id)
        stats = {}
        if workspace:
            stats = self.workspace_manager.get_workspace_stats(project_id)

        return {
            "is_running": is_running,
            "workspace_path": str(workspace) if workspace else None,
            "stats": stats,
        }

    def register_progress_callback(
        self,
        project_id: str,
        callback: Any
    ):
        """Register a callback for progress updates.

        Args:
            project_id: Project identifier
            callback: Async callback function
        """
        if project_id not in self.progress_callbacks:
            self.progress_callbacks[project_id] = []

        self.progress_callbacks[project_id].append(callback)

    async def _notify_progress(self, project_id: str, update: Dict[str, Any]):
        """Notify all registered callbacks of progress update.

        Args:
            project_id: Project identifier
            update: Progress update dict
        """
        callbacks = self.progress_callbacks.get(project_id, [])

        for callback in callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(update)
                else:
                    callback(update)
            except Exception as e:
                logger.error(f"Progress callback error: {e}")


# Global runner instance
_runner: Optional[PipelineRunner] = None


def get_runner() -> PipelineRunner:
    """Get or create global pipeline runner.

    Returns:
        PipelineRunner instance
    """
    global _runner

    if _runner is None:
        _runner = PipelineRunner()

    return _runner
