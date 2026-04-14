"""Pipeline orchestrator — state machine that drives the analysis workflow."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
import requests  # For calling broadcast-progress API

from omegaconf import OmegaConf

from core.state_manager import StateManager
from modules.base import BaseModule, HITLCheckpoint, ModuleStatus, RunContext
from modules.registry import ModuleRegistry
from core.communication_hub import WorkflowMessage

logger = logging.getLogger(__name__)

# Default module execution order for the fixed bibliometric pipeline
DEFAULT_PIPELINE = [
    "query_generator",
    "paper_fetcher",
    "country_analyzer",
    "bibliometrics_analyzer",
    "preprocessor",
    "frequency_analyzer",
    "topic_modeler",
    "burst_detector",
    "tsr_ranker",
    "network_analyzer",
    "visualizer",
    "report_generator",
]


class PipelineOrchestrator:
    """Orchestrates the execution of analysis modules in sequence.

    Supports:
    - Sequential execution of the fixed pipeline
    - Per-module HITL checkpoints
    - Automatic resume from last completed module
    - **GuardianSoul**: LLM-driven interactive error recovery
    - Fallback to template-based GuardianAgent when LLM unavailable
    """

    def __init__(
        self,
        registry: ModuleRegistry,
        state_manager: StateManager,
        config: dict,
        pipeline_order: list[str] | None = None,
        mode: str = "automated",
    ) -> None:
        self.registry = registry
        self.state = state_manager
        self.config = OmegaConf.merge(
            OmegaConf.structured({}),
            OmegaConf.create(config),
        )
        self.pipeline_order = pipeline_order or DEFAULT_PIPELINE
        self.mode = mode

        # Track current execution position for injection safety
        self._current_execution_index = -1

        # Mode-specific options
        mode_cfg = OmegaConf.to_container(
            OmegaConf.select(self.config, f"pipeline.{mode}", default=OmegaConf.create()),
            resolve=True,
        )
        if not isinstance(mode_cfg, dict):
            mode_cfg = {}
        self.skip_on_failure = mode_cfg.get("skip_on_guardian_failure", True) if mode == "automated" else False
        self.hitl_checkpoints = list(mode_cfg.get("default_checkpoints", [])) if mode == "hitl" else []

        # --- LLM Provider 初始化（用于 GuardianSoul） ---
        self._llm = None
        self._event_loop = None
        self._init_llm()

    def set_event_loop(self, loop):
        """Set the event loop from the async context for GuardianSoul."""
        self._event_loop = loop

    def run(
        self,
        run_id: str,
        initial_input: dict,
        mode: str = "interactive",
        resume: bool = False,
        start_from: str | None = None,
        end_at: str | None = None,
    ) -> dict:
        """Execute the pipeline.

        Args:
            run_id: Unique identifier for this run.
            initial_input: Starting input data (e.g. {"domain": "machine learning"}).
            mode: "batch" (fully automatic) or "interactive" (HITL).
            resume: If True, resume from last checkpoint.
            start_from: Module name to start from (overrides checkpoint).
            end_at: Module name to stop after.

        Returns:
            Final output from the last completed module.
        """
        # --- Start Error Monitoring (captures ALL errors including multiprocessing) ---
        from core.error_monitor import start_error_monitoring, stop_error_monitoring, get_detected_errors

        error_monitor = start_error_monitoring()
        logger.info("Error monitoring started - will capture all errors for Guardian intervention")

        try:
            # Create or load run state
            if resume:
                run_state = self.state.get_run_state(run_id)
                self.state.set_status(run_id, "running")
            else:
                self.state.create_run(run_id, OmegaConf.to_container(self.config, resolve=True))
                run_state = self.state.get_run_state(run_id)
                self.state.set_status(run_id, "running")

            # Project directory is the workspace root
            project_dir = self.state.workspace_dir
            context = RunContext(
                project_dir=project_dir,
                run_id=run_id,
                checkpoint_dir=self.state.checkpoint_dir,
                hardware_info=self._get_hardware_info(),
            )

            # Determine starting point
            if start_from:
                start_idx = self.pipeline_order.index(start_from)
            elif resume:
                next_mod = self.state.get_next_pending_module(run_id, self.pipeline_order)
                start_idx = self.pipeline_order.index(next_mod) if next_mod else 0
            else:
                start_idx = 0

            end_idx = (
                self.pipeline_order.index(end_at) + 1
                if end_at
                else len(self.pipeline_order)
            )

            # Accumulate outputs
            current_input = initial_input
            if resume and start_idx > 0:
                # Load ALL previous module outputs and merge them for input_data
                merged = {}
                for prev_idx in range(start_idx):
                    prev_mod = self.pipeline_order[prev_idx]
                    try:
                        prev_output = self.state.load_module_output(run_id, prev_mod)
                        context.previous_outputs[prev_mod] = prev_output
                        if isinstance(prev_output, dict):
                            merged.update(prev_output)
                    except FileNotFoundError:
                        logger.warning("Could not load output from %s", prev_mod)
                current_input = merged

            # Execute modules in sequence
            for i in range(start_idx, end_idx):
                mod_name = self.pipeline_order[i]
                self._current_execution_index = i
                if mod_name not in self.registry.list_modules():
                    logger.warning("Module '%s' not registered, skipping", mod_name)
                    continue

                # --- Check for user messages and steer commands ---
                from core.communication_hub import get_communication_hub
                comm_hub = get_communication_hub()

                # Check for steer commands (PAUSE, SKIP, etc.)
                steer_cmd = comm_hub.get_steer(run_id)
                if steer_cmd:
                    logger.info(f"Received steer command: {steer_cmd}")

                    if steer_cmd == "PAUSE":
                        logger.info(f"Pipeline paused by user at module {mod_name}")
                        self.state.pause(run_id)
                        self._broadcast_progress(run_id)  # Notify frontend
                        return {"status": "paused", "completed_modules": i}

                    elif steer_cmd.startswith("SKIP:"):
                        target_module = steer_cmd.split(":", 1)[1]
                        if target_module == mod_name:
                            logger.info(f"Skipping module {mod_name} per user request")
                            # Mark as completed with skip note
                            self.state.update_module_status(
                                run_id, mod_name, ModuleStatus.COMPLETED,
                                output_path="skipped_by_user"
                            )
                            continue  # Skip to next module

                # Check for user chat messages and respond
                if self._event_loop and self._event_loop.is_running():
                    try:
                        future = asyncio.run_coroutine_threadsafe(
                            comm_hub.get_user_message(run_id, timeout=0.01),
                            self._event_loop
                        )
                        user_msg = future.result(timeout=0.05)

                        if user_msg and self._llm:
                            logger.info(f"Received user message: {user_msg}")
                            # Respond to user using LLM (in thread pool to not block)
                            import threading
                            respond_thread = threading.Thread(
                                target=self._respond_to_user,
                                args=(run_id, user_msg, mod_name, context),
                                daemon=True
                            )
                            respond_thread.start()
                    except Exception as e:
                        logger.debug(f"No user message or error checking: {e}")

                # Get module - prioritize workspace version if available
                module = self._get_module_with_workspace_override(mod_name, project_dir)
                mod_config = self._get_module_config(mod_name)

                # Check hardware requirements
                hw = module.get_hardware_requirements(mod_config)
                self._log_hardware_check(mod_name, hw, context.hardware_info)

                logger.info("Running module: %s (%d/%d)", mod_name, i + 1, end_idx)
                self.state.update_module_status(run_id, mod_name, ModuleStatus.RUNNING)
                self._broadcast_progress(run_id)  # Broadcast progress update

                try:
                    output = module.process(current_input, mod_config, context)

                    # --- Check for detected errors even if no exception was raised ---
                    detected_errors = get_detected_errors(clear=False)
                    # Only consider errors that are clearly from THIS module.
                    # Errors without a module_name are ambiguous — they could come
                    # from logging of any earlier module, so we ignore them to avoid
                    # false-positive Guardian triggers (which was causing earlier
                    # modules like query_generator to get stuck in "running" state).
                    module_errors = [e for e in detected_errors
                                     if e.module_name == mod_name]

                    if module_errors:
                        logger.warning(
                            f"Module {mod_name} completed but errors were detected in logs: "
                            f"{len(module_errors)} error(s) found"
                        )

                        # Create a synthetic exception for Guardian intervention
                        error_summary = "\n".join([f"- [{e.error_type}] {e.error_message}"
                                                   for e in module_errors[:3]])

                        error = RuntimeError(
                            f"Errors detected in module execution:\n{error_summary}\n\n"
                            f"Full logs:\n{error_monitor.get_error_summary()}"
                        )

                        # Trigger Guardian intervention
                        # Use project workspace so fixes are isolated to this project
                        workspace_dir = project_dir / "workspace"
                        workspace_dir.mkdir(parents=True, exist_ok=True)
                        decision = self._handle_module_error(
                            mod_name=mod_name,
                            error=error,
                            context=context,
                            workspace_dir=workspace_dir,
                            step_info=f"{i + 1}/{end_idx}",
                            run_id=run_id,
                        )

                        # If Guardian successfully fixed, continue; otherwise fail
                        if not (decision and decision.outcome == "success"):
                            self.state.update_module_status(
                                run_id, mod_name, ModuleStatus.FAILED, error=str(error)
                            )
                            self._broadcast_progress(run_id)  # Broadcast progress update
                            raise error

                    # Validate output
                    if not module.validate_output(output):
                        logger.warning("Module %s output failed validation", mod_name)

                    # Save output
                    output_path = self.state.save_module_output(run_id, mod_name, output)
                    self.state.update_module_status(
                        run_id, mod_name, ModuleStatus.COMPLETED, str(output_path)
                    )
                    self._broadcast_progress(run_id)  # Broadcast progress update

                    # Clear accumulated errors after successful module completion
                    # to prevent false-positive Guardian triggers on later modules
                    get_detected_errors(clear=True)

                    # Update previous_outputs for downstream modules
                    context.previous_outputs[mod_name] = output

                    # Build merged input from all previous outputs
                    # This allows each module to access data from any earlier module,
                    # not just the immediate predecessor (e.g. burst_detector needs
                    # frequency_analyzer's keyword_year_matrix_path even though it
                    # comes after topic_modeler)
                    merged_input = {}
                    for prev_mod_name in self.pipeline_order:
                        if prev_mod_name in context.previous_outputs:
                            prev_out = context.previous_outputs[prev_mod_name]
                            if isinstance(prev_out, dict):
                                merged_input.update(prev_out)
                    current_input = merged_input

                    logger.info("Module %s completed successfully", mod_name)

                    # --- HITL Checkpoint: pause for user review ---
                    if self.mode == "hitl" and mod_name in self.hitl_checkpoints:
                        logger.info("HITL checkpoint reached: %s", mod_name)
                        review_decision = self._handle_hitl_checkpoint(
                            run_id, mod_name, output, context
                        )
                        if review_decision == "reject":
                            raise RuntimeError(f"Module {mod_name} rejected by user at HITL checkpoint")
                        elif review_decision == "adjust":
                            # Re-run with adjusted parameters
                            adjusted_config = self._get_adjusted_config(run_id, mod_name, mod_config)
                            logger.info("Re-running %s with adjusted parameters", mod_name)
                            output = module.process(current_input, adjusted_config, context)
                            output_path = self.state.save_module_output(run_id, mod_name, output)
                            self.state.update_module_status(
                                run_id, mod_name, ModuleStatus.COMPLETED, str(output_path)
                            )
                            context.previous_outputs[mod_name] = output
                            # Rebuild merged input after HITL re-run
                            merged_input = {}
                            for prev_mod_name in self.pipeline_order:
                                if prev_mod_name in context.previous_outputs:
                                    prev_out = context.previous_outputs[prev_mod_name]
                                    if isinstance(prev_out, dict):
                                        merged_input.update(prev_out)
                            current_input = merged_input

                except Exception as e:
                    logger.error("Module %s failed: %s", mod_name, e)

                    # --- Guardian Agent Integration ---
                    workspace_dir = project_dir / "workspace"
                    workspace_dir.mkdir(parents=True, exist_ok=True)
                    decision = self._handle_module_error(
                        mod_name=mod_name,
                        error=e,
                        context=context,
                        workspace_dir=workspace_dir,
                        step_info=f"{i + 1}/{end_idx}",
                        run_id=run_id,
                    )

                    self.state.update_module_status(
                        run_id, mod_name, ModuleStatus.FAILED, error=str(e)
                    )
                    self._broadcast_progress(run_id)  # Broadcast progress update

                    # Automated mode: skip module and continue
                    if self.mode == "automated" and self.skip_on_failure:
                        logger.warning(
                            "Guardian failed for %s, skipping module (automated mode)",
                            mod_name,
                        )
                        continue
                    else:
                        raise

            # All modules completed successfully
            # Reconcile any modules stuck in "running" (safety net)
            state = self.state.get_run_state(run_id)
            for mod_name, mod_state in state.get("modules", {}).items():
                if mod_state.get("status") == "running":
                    logger.warning(
                        "Module %s stuck in 'running' at pipeline end, "
                        "setting to 'completed' (output exists: %s)",
                        mod_name,
                        mod_state.get("output_path") is not None,
                    )
                    # If it has an output_path, it completed successfully
                    if mod_state.get("output_path"):
                        self.state.update_module_status(
                            run_id, mod_name, ModuleStatus.COMPLETED,
                            output_path=mod_state["output_path"]
                        )
                    else:
                        self.state.update_module_status(
                            run_id, mod_name, ModuleStatus.FAILED,
                            error="Module stuck in running state with no output"
                        )

            self.state.set_status(run_id, "completed")
            self._broadcast_progress(run_id)  # Broadcast final status to frontend
            logger.info("Pipeline completed successfully for run %s", run_id)
            return current_input

        except Exception as pipeline_error:
            # Pipeline failed - set status to failed
            # Also reconcile any modules stuck in "running"
            try:
                state = self.state.get_run_state(run_id)
                for mod_name, mod_state in state.get("modules", {}).items():
                    if mod_state.get("status") == "running":
                        if mod_state.get("output_path"):
                            self.state.update_module_status(
                                run_id, mod_name, ModuleStatus.COMPLETED,
                                output_path=mod_state["output_path"]
                            )
                        else:
                            self.state.update_module_status(
                                run_id, mod_name, ModuleStatus.FAILED,
                                error="Module stuck in running state at pipeline failure"
                            )
            except Exception as reconcile_err:
                logger.warning("Failed to reconcile module statuses: %s", reconcile_err)

            self.state.set_status(run_id, "failed")
            self._broadcast_progress(run_id)  # Broadcast failure status to frontend
            logger.error("Pipeline failed for run %s: %s", run_id, pipeline_error)
            raise

        finally:
            # --- Stop Error Monitoring ---
            stop_error_monitoring()
            logger.info("Error monitoring stopped")

    def run_single_module(
        self,
        run_id: str,
        module_name: str,
        config_overrides: dict | None = None,
    ) -> dict:
        """Execute a single module using existing upstream outputs.

        Used by TuningAgent to re-run a specific module with adjusted parameters.
        Backs up the current output before re-running.

        Args:
            run_id: Run identifier.
            module_name: Module to re-execute.
            config_overrides: Optional config params to override.

        Returns:
            Module output dict.
        """
        if module_name not in self.registry.list_modules():
            raise ValueError(f"Module '{module_name}' not registered")

        # Load state and all previous outputs
        run_state = self.state.get_run_state(run_id)
        project_dir = self.state.workspace_dir

        context = RunContext(
            project_dir=project_dir,
            run_id=run_id,
            checkpoint_dir=self.state.checkpoint_dir,
            hardware_info=self._get_hardware_info(),
            previous_outputs={},
        )

        # Load all completed module outputs
        modules_state = run_state.get("modules", {})
        for mod_name, mod_data in modules_state.items():
            if mod_data.get("status") == "completed" and mod_data.get("output_path"):
                try:
                    prev_output = self.state.load_module_output(run_id, mod_name)
                    context.previous_outputs[mod_name] = prev_output
                except FileNotFoundError:
                    pass

        # Backup current output if exists
        if module_name in modules_state and modules_state[module_name].get("output_path"):
            import shutil
            output_path = Path(modules_state[module_name]["output_path"])
            if output_path.exists():
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_dir = output_path.parent / f"{module_name}_backup_{timestamp}"
                shutil.copytree(output_path.parent / module_name, backup_dir, dirs_exist_ok=True)
                logger.info("Backed up %s output to %s", module_name, backup_dir)

        # Build merged input from all previous outputs
        merged = {}
        for prev_name, prev_output in context.previous_outputs.items():
            if isinstance(prev_output, dict):
                merged.update(prev_output)

        # Get module and config
        module = self._get_module_with_workspace_override(module_name, project_dir)
        mod_config = self._get_module_config(module_name)

        # Apply config overrides
        if config_overrides:
            mod_config.update(config_overrides)

        logger.info("Re-running module: %s (single module execution)", module_name)
        self.state.update_module_status(run_id, module_name, ModuleStatus.RUNNING)

        try:
            output = module.process(merged, mod_config, context)
            self.state.save_module_output(run_id, module_name, output)
            self.state.update_module_status(run_id, module_name, ModuleStatus.COMPLETED,
                                             output_path=str(
                                                 self.state.outputs_dir / module_name / "output.json"))
            logger.info("Module %s re-run completed successfully", module_name)
            return output
        except Exception as e:
            self.state.update_module_status(run_id, module_name, ModuleStatus.FAILED, error=str(e))
            logger.error("Module %s re-run failed: %s", module_name, e)
            raise

    def _init_llm(self):
        """初始化 LLM Provider（用于 GuardianSoul）."""
        try:
            # Check for project-specific LLM config first
            llm_config_from_state = None
            if self.project_id:
                try:
                    state_manager = StateManager(self.project_dir)
                    llm_config_from_state = state_manager.get_llm_config(self.project_id)
                except Exception as e:
                    logger.debug(f"Could not load project-specific LLM config: {e}")

            # Fallback to global config
            llm_cfg = OmegaConf.to_container(
                OmegaConf.select(self.config, "llm", default=OmegaConf.create()),
                resolve=True,
            )

            from core.llm import create_provider

            # Priority: project-specific config > global config
            if llm_config_from_state:
                self._llm = create_provider(llm_config_from_state=llm_config_from_state)
                logger.info(f"LLM provider initialized from project config: {llm_config_from_state.get('provider', 'openai')}")
            elif isinstance(llm_cfg, dict) and llm_cfg.get("provider"):
                self._llm = create_provider(llm_cfg)
                logger.info(f"LLM provider initialized: {llm_cfg.get('provider')}")
            else:
                logger.info("No LLM provider configured, GuardianSoul will use template fallback")
        except Exception as e:
            logger.warning(f"Failed to initialize LLM provider: {e}")

    def _get_module_with_workspace_override(self, mod_name: str, project_dir: Path | None) -> BaseModule:
        """Get module instance, prioritizing workspace version if available.

        This enables GuardianSoul fixes to be used in subsequent runs.

        Args:
            mod_name: Module name
            project_dir: Project directory (contains workspace/)

        Returns:
            Module instance (either from workspace or registry)
        """
        if project_dir:
            workspace_mod_path = project_dir / "workspace" / "modules" / f"{mod_name}.py"
        else:
            workspace_mod_path = None

        if workspace_mod_path and workspace_mod_path.exists():
            logger.info(f"Loading workspace override for module: {mod_name}")
            try:
                # Dynamic import of workspace module
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    f"_workspace_module_{mod_name}",
                    workspace_mod_path
                )
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)

                    # Find BaseModule subclass
                    for attr_name in dir(mod):
                        attr = getattr(mod, attr_name)
                        if (
                            isinstance(attr, type)
                            and issubclass(attr, BaseModule)
                            and attr is not BaseModule
                        ):
                            instance = attr()
                            logger.info(f"Successfully loaded workspace module: {mod_name}")
                            return instance

                    logger.warning(f"No BaseModule subclass found in workspace module: {mod_name}")

            except Exception as e:
                logger.error(f"Failed to load workspace module {mod_name}: {e}")
                logger.info("Falling back to registry version")

        # Fall back to registry
        return self.registry.get(mod_name)

    def inject_module(
        self,
        module_name: str,
        insert_after: str | None = None,
        insert_before: str | None = None,
        run_id: str | None = None,
    ) -> bool:
        """Inject a new module into the pipeline at runtime.

        Args:
            module_name: Name of the module to inject (must exist in workspace/modules/).
            insert_after: Insert after this module in the pipeline order.
            insert_before: Insert before this module in the pipeline order.
            run_id: Optional run_id for persisting the updated order.

        Returns:
            True if injection succeeded, False otherwise.
        """
        # 1. Determine insertion index
        try:
            if insert_after:
                idx = self.pipeline_order.index(insert_after) + 1
            elif insert_before:
                idx = self.pipeline_order.index(insert_before)
            else:
                idx = len(self.pipeline_order)
        except ValueError as e:
            logger.error("Inject: invalid insertion point — %s", e)
            return False

        # 2. Cannot inject before current execution point
        if idx <= self._current_execution_index:
            logger.warning(
                "Inject: cannot insert '%s' at position %d — already past that point (current=%d)",
                module_name, idx, self._current_execution_index,
            )
            return False

        # 3. Validate data compatibility
        project_dir = self.state.workspace_dir if run_id else None
        module = self._get_module_with_workspace_override(module_name, project_dir)
        warnings = self._validate_data_compatibility(module, insert_after, insert_before)
        if warnings:
            for w in warnings:
                logger.warning("Inject compatibility: %s", w)

        # 4. Modify pipeline order
        self.pipeline_order.insert(idx, module_name)
        logger.info("Injected module '%s' at position %d (pipeline=%s)", module_name, idx, self.pipeline_order)

        # 5. Register the module
        try:
            self.registry.register(module)
            logger.info("Registered injected module: %s", module_name)
        except Exception as e:
            logger.warning("Module registration failed (may already exist): %s", e)

        # 6. Persist to state.json
        if run_id:
            self.state.update_pipeline_order(run_id, self.pipeline_order)

        return True

    def _validate_data_compatibility(
        self,
        new_module: BaseModule,
        insert_after: str | None,
        insert_before: str | None,
    ) -> list[str]:
        """Validate data compatibility between new module and its neighbors.

        Returns a list of warning strings (empty = fully compatible).
        """
        warnings = []
        try:
            new_required = new_module.input_schema().get("required", [])
        except Exception:
            new_required = []

        if insert_after:
            try:
                pred = self._get_module_with_workspace_override(insert_after, None)
                pred_output_props = pred.output_schema().get("properties", {})
                for field in new_required:
                    if field not in pred_output_props:
                        warnings.append(
                            f"新模块需要 '{field}'，但前驱 '{insert_after}' 不产生此字段"
                        )
            except Exception as e:
                warnings.append(f"无法验证前驱 '{insert_after}' 的输出 schema: {e}")

        if insert_before:
            try:
                succ = self._get_module_with_workspace_override(insert_before, None)
                succ_required = succ.input_schema().get("required", [])
                new_output_props = new_module.output_schema().get("properties", {})
                for field in succ_required:
                    if field not in new_output_props:
                        warnings.append(
                            f"后继 '{insert_before}' 需要 '{field}'，但新模块不产生此字段"
                        )
            except Exception as e:
                warnings.append(f"无法验证后继 '{insert_before}' 的输入 schema: {e}")

        return warnings

    def _handle_module_error(
        self,
        mod_name: str,
        error: Exception,
        context: RunContext,
        workspace_dir: Path,
        step_info: str = "",
        run_id: str = None,
    ) -> Any:
        """处理模块错误：优先 GuardianSoul，回退模板 Guardian.

        Args:
            mod_name: 模块名称
            error: 模块抛出的异常
            context: 运行上下文
            workspace_dir: 工作区目录
            step_info: 流水线步骤信息（如 "3/10"）

        Returns:
            GuardianDecision（如果 Guardian 成功处理）
        """
        # --- 策略 1: GuardianSoul（LLM 驱动持续交互） ---
        if self._llm is not None:
            try:
                from core.guardian_soul import GuardianSoul
                from core.agent_guardian import get_guardian

                # 获取模板 Guardian 作为后备知识源
                template_guardian = get_guardian(mod_name)

                # Read guardian_max_steps and max_history_files from config
                guardian_max_steps = OmegaConf.to_container(
                    OmegaConf.select(self.config, "pipeline.automated.guardian_max_steps", default=50),
                    resolve=True,
                )
                if not isinstance(guardian_max_steps, int):
                    guardian_max_steps = 50
                if guardian_max_steps == -1:
                    guardian_max_steps = 999999  # Unlimited

                max_history_files = OmegaConf.to_container(
                    OmegaConf.select(self.config, "modules.tuning_agent.max_history_files", default=10),
                    resolve=True,
                )
                if not isinstance(max_history_files, int):
                    max_history_files = 10

                preserve_recent_messages = OmegaConf.to_container(
                    OmegaConf.select(self.config, "modules.tuning_agent.preserve_recent_messages", default=20),
                    resolve=True,
                )
                if not isinstance(preserve_recent_messages, int):
                    preserve_recent_messages = 20

                soul = GuardianSoul(
                    module_name=mod_name,
                    llm=self._llm,
                    workspace_dir=workspace_dir,
                    max_steps=guardian_max_steps,
                    pipeline_stage=step_info,
                    guardian=template_guardian,
                    project_id=run_id,  # Pass project_id for real-time communication
                    event_loop=self._event_loop,  # Pass main event loop
                    run_id=run_id,  # Pass run_id for project context loading
                    max_history_files=max_history_files,
                    preserve_recent_messages=preserve_recent_messages,
                )

                logger.info(f"Activating GuardianSoul for {mod_name}")
                decision = soul.activate(error, context.__dict__)

                if decision.outcome == "success" and decision.fix_path:
                    logger.info(f"GuardianSoul generated fix: {decision.fix_path}")
                    logger.info(f"Fix description: {decision.analysis.suggested_fix}")
                else:
                    logger.warning(f"GuardianSoul outcome: {decision.outcome}")

                return decision

            except Exception as soul_error:
                logger.error(f"GuardianSoul failed, falling back to template: {soul_error}")

        # --- 策略 2: 模板 GuardianAgent（无 LLM 时） ---
        try:
            from core.agent_guardian import get_guardian

            guardian = get_guardian(mod_name)
            if guardian:
                logger.info(f"Using template Guardian for {mod_name}")
                decision = guardian.handle_error(error, context.__dict__, workspace_dir)

                if decision.outcome == "success" and decision.fix_path:
                    logger.info(f"Template Guardian generated fix: {decision.fix_path}")
                else:
                    logger.warning(f"Template Guardian outcome: {decision.outcome}")

                return decision
            else:
                logger.info(f"No guardian registered for module: {mod_name}")

        except Exception as guardian_error:
            logger.error(f"Template Guardian error: {guardian_error}")

        return None

    def _respond_to_user(
        self,
        run_id: str,
        user_message: str,
        current_module: str,
        context: RunContext,
    ):
        """Respond to user message using LLM during pipeline execution.

        If the user requests adding a new module, the LLM may use create_module
        and add_to_pipeline tools, and we process the injection request here.

        Args:
            run_id: Project ID for communication
            user_message: User's question or comment
            current_module: Name of the currently running module
            context: Pipeline execution context
        """
        if not self._llm:
            logger.warning("No LLM available to respond to user message")
            return

        from core.communication_hub import get_communication_hub
        from core.llm import Message
        from core.guardian_soul import GuardianSoul, GUARDIAN_TOOL_DEFS, GuardianToolExecutor

        comm_hub = get_communication_hub()
        workspace_dir = context.project_dir / "workspace"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Broadcast that AI is thinking
            if self._event_loop and self._event_loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    comm_hub.ai_thinking(
                        run_id,
                        f"Processing user message while running {current_module}..."
                    ),
                    self._event_loop
                ).result(timeout=2)

            # Use GuardianSoul's tool system for module creation capability
            from core.tools import create_default_registry
            tool_registry = create_default_registry(workspace_dir)
            executor = GuardianToolExecutor(workspace_dir, tool_registry)

            # Build context for LLM
            system_prompt = f"""You are an AI assistant helping with bibliometric analysis.
The pipeline is currently running the '{current_module}' module.

Current run ID: {run_id}
Working directory: {context.project_dir}

You can:
- Answer questions about the pipeline progress
- Explain what the current module is doing
- Help interpret results from completed modules
- Suggest adjustments to parameters
- **Create new analysis modules** if the user asks for additional analysis methods
  - Use `create_module` to generate a complete BaseModule subclass
  - Use `add_to_pipeline` to submit it for user approval

When the user asks to add a new analysis method:
1. Understand what data it needs and what output it should produce
2. Check that the required data is available in previous module outputs
3. Call `create_module` with complete, runnable code
4. Call `add_to_pipeline` to request user approval for injection

Be concise and helpful. Respond in the same language as the user."""

            # Get recent module outputs for context
            recent_outputs = {}
            for mod_name in list(context.previous_outputs.keys())[-3:]:
                output = context.previous_outputs[mod_name]
                if isinstance(output, dict):
                    recent_outputs[mod_name] = str(output)[:200]

            user_prompt = f"User message: {user_message}\n\n"
            if recent_outputs:
                user_prompt += f"Recent module outputs:\n"
                for mod_name, output in recent_outputs.items():
                    user_prompt += f"- {mod_name}: {output}\n"

            messages = [
                Message(role="system", content=system_prompt),
                Message(role="user", content=user_prompt),
            ]

            # Agent loop with tool calls (max 10 steps)
            for step in range(10):
                response = self._llm.chat(
                    messages=messages,
                    tools=GUARDIAN_TOOL_DEFS,
                    temperature=0.2,
                )

                if response.content:
                    messages.append(Message(role="assistant", content=response.content))
                    # Broadcast AI response
                    if self._event_loop and self._event_loop.is_running():
                        asyncio.run_coroutine_threadsafe(
                            comm_hub.ai_thinking(run_id, response.content[:500]),
                            self._event_loop
                        ).result(timeout=2)

                if response.has_tool_calls:
                    for tc in response.tool_calls:
                        result_text = executor.execute(tc)
                        messages.append(Message(
                            role="tool",
                            content=result_text,
                            name=tc.name,
                            tool_call_id=tc.id,
                        ))

                        # Broadcast tool call/result
                        if self._event_loop and self._event_loop.is_running():
                            args = json.loads(tc.arguments) if tc.arguments else {}
                            asyncio.run_coroutine_threadsafe(
                                comm_hub.ai_tool_call(run_id, tc.name, args),
                                self._event_loop
                            ).result(timeout=2)
                            asyncio.run_coroutine_threadsafe(
                                comm_hub.ai_tool_result(run_id, tc.name, result_text),
                                self._event_loop
                            ).result(timeout=2)

                    # Check for injection request
                    if executor.injection_request:
                        inj = executor.injection_request
                        logger.info("Processing injection request for '%s'", inj["module_name"])

                        # Request user confirmation via CommunicationHub
                        if self._event_loop and self._event_loop.is_running():
                            result = asyncio.run_coroutine_threadsafe(
                                comm_hub.request_module_injection(
                                    project_id=run_id,
                                    module_name=inj["module_name"],
                                    description=inj.get("description", ""),
                                    input_schema=inj.get("input_schema", {}),
                                    output_schema=inj.get("output_schema", {}),
                                    insert_after=inj.get("insert_after"),
                                    insert_before=inj.get("insert_before"),
                                    compatibility_warnings=inj.get("compatibility_warnings", []),
                                ),
                                self._event_loop
                            ).result(timeout=310)

                            if result == "approved":
                                success = self.inject_module(
                                    module_name=inj["module_name"],
                                    insert_after=inj.get("insert_after"),
                                    insert_before=inj.get("insert_before"),
                                    run_id=run_id,
                                )
                                status_msg = f"Module '{inj['module_name']}' {'injected successfully' if success else 'injection failed'}"
                                logger.info(status_msg)
                                messages.append(Message(role="user", content=status_msg))
                            else:
                                logger.info("Module injection %s by user", result)
                                messages.append(Message(role="user", content=f"Injection {result} by user"))

                        executor.injection_request = None  # Reset
                        break  # End conversation after injection handling
                else:
                    break  # No tool calls, conversation done

            logger.info(f"Responded to user message: {messages[-1].content[:100] if messages else 'no response'}...")

        except Exception as e:
            logger.error(f"Failed to respond to user message: {e}")
            if self._event_loop and self._event_loop.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(
                        comm_hub.ai_error(run_id, f"Failed to respond: {str(e)}"),
                        self._event_loop
                    ).result(timeout=2)
                except:
                    pass

    def _handle_hitl_checkpoint(
        self,
        run_id: str,
        mod_name: str,
        output: dict,
        context: RunContext,
    ) -> str:
        """Handle HITL checkpoint: pause and wait for user review.

        Returns:
            "approve", "reject", or "adjust"
        """
        from core.communication_hub import get_communication_hub, MessageType

        comm_hub = get_communication_hub()

        # Store checkpoint for review
        self.state.set_checkpoint_review(run_id, mod_name, output)

        # Broadcast checkpoint to frontend
        if self._event_loop and self._event_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                comm_hub.broadcast(run_id, WorkflowMessage(
                    type=MessageType.AI_DECISION,
                    content=f"Checkpoint: {mod_name} awaiting your review",
                    metadata={"checkpoint": mod_name, "review_required": True}
                )),
                self._event_loop
            ).result(timeout=5)

        # Poll for user decision via steer queue
        logger.info("HITL: Waiting for user review of %s ...", mod_name)
        while True:
            import time
            time.sleep(1)  # Poll every second

            steer_cmd = comm_hub.get_steer(run_id)
            if not steer_cmd:
                continue

            # Parse: "APPROVE:module_name", "REJECT:module_name", "ADJUST:module_name"
            parts = steer_cmd.split(":", 1)
            action = parts[0].upper()
            target = parts[1] if len(parts) > 1 else ""

            if target != mod_name:
                # Not for this checkpoint, put it back
                comm_hub.steer(run_id, steer_cmd)
                continue

            if action in ("APPROVE", "REJECT", "ADJUST"):
                self.state.resolve_checkpoint(run_id, mod_name, action.lower())
                logger.info("HITL: User %s %s", action.lower(), mod_name)
                return action.lower()

    def _broadcast_progress(self, run_id: str):
        """Broadcast progress update via API."""
        try:
            # Read web API port from config (default 8001)
            web_cfg = OmegaConf.select(self.config, "web_api", default=OmegaConf.create())
            host = OmegaConf.select(web_cfg, "host", default="localhost")
            port = OmegaConf.select(web_cfg, "port", default=8001)
            # Call the broadcast-progress endpoint
            response = requests.post(
                f"http://{host}:{port}/api/projects/{run_id}/broadcast-progress",
                timeout=2.0
            )
            if response.status_code == 200:
                logger.debug(f"Broadcasted progress for {run_id}")
            else:
                logger.warning(f"Failed to broadcast progress: {response.status_code}")
        except Exception as e:
            logger.debug(f"Could not broadcast progress (API not available?): {e}")

    def _get_adjusted_config(self, run_id: str, mod_name: str, default_config: dict) -> dict:
        """Get user-adjusted config for a module after HITL review."""
        review = self.state.get_checkpoint_review(run_id, mod_name)
        if review and review.get("adjusted_params"):
            merged = dict(default_config)
            merged.update(review["adjusted_params"])
            return merged
        return default_config

    def _get_module_config(self, module_name: str) -> dict:
        """Extract module-specific config from the main config."""
        modules_cfg = OmegaConf.select(self.config, "modules", default=OmegaConf.create())
        mod_cfg = OmegaConf.to_container(
            OmegaConf.select(modules_cfg, module_name, default=OmegaConf.create()),
            resolve=True,
        )
        return mod_cfg if isinstance(mod_cfg, dict) else {}

    def _get_hardware_info(self) -> dict:
        """Detect available hardware resources."""
        import os

        import psutil

        return {
            "cpu_cores": os.cpu_count() or 1,
            "memory_gb": psutil.virtual_memory().total / (1024**3),
            "memory_available_gb": psutil.virtual_memory().available / (1024**3),
        }

    def _log_hardware_check(self, mod_name: str, hw: Any, hardware_info: dict) -> None:
        """Log a warning if estimated requirements exceed available resources."""
        available_gb = hardware_info.get("memory_available_gb", float("inf"))
        if hw.recommended_memory_gb > available_gb:
            logger.warning(
                "Module '%s' recommends %.1f GB but only %.1f GB available. "
                "Consider reducing parameters.",
                mod_name,
                hw.recommended_memory_gb,
                available_gb,
            )
