"""State manager for pipeline runs — checkpoint, resume, and persistence."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from modules.base import ModuleStatus

logger = logging.getLogger(__name__)


class StateManager:
    """Manages pipeline run state with JSON file persistence.

    Each run has:
    - A workspace directory at workspace_dir/
    - State tracking at workspace_dir/checkpoints/state.json
    - Module outputs at workspace_dir/outputs/{module_name}/
    - Module checkpoints at workspace_dir/checkpoints/{module_name}/
    """

    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir
        self.checkpoint_dir = workspace_dir / "checkpoints"
        self.outputs_dir = workspace_dir / "outputs"
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)

    def create_run(self, run_id: str, pipeline_config: dict) -> Path:
        """Create state.json and initialize run state."""
        state = {
            "run_id": run_id,
            "status": "created",
            "mode": pipeline_config.get("pipeline_mode", "automated"),
            "pipeline_order": pipeline_config.get("pipeline_order", []),
            "hitl_checkpoints": pipeline_config.get("hitl", {}).get("checkpoints", []),
            "created_at": time.time(),
            "updated_at": time.time(),
            "pipeline_config": pipeline_config,
            "modules": {},
            "checkpoint_reviews": {},
            "tuning_count": 0,
            "paper_status": "",  # "" | "draft" | "pdf_ready"
            "llm_config": {},  # {"provider": "openai", "api_key": "...", "base_url": "...", "model": "..."}
        }
        self._write_state(state)
        logger.info("Created run: %s", run_id)
        return self.workspace_dir

    def get_run_state(self, run_id: str) -> dict:
        """Read the current state of a run."""
        state_file = self.checkpoint_dir / "state.json"
        if not state_file.exists():
            raise FileNotFoundError(f"No state found for run: {run_id}")
        return json.loads(state_file.read_text(encoding="utf-8"))

    def update_module_status(
        self,
        run_id: str,
        module_name: str,
        status: ModuleStatus,
        output_path: str | None = None,
        error: str | None = None,
    ) -> None:
        """Update the status of a specific module in a run."""
        state = self.get_run_state(run_id)
        state["modules"][module_name] = {
            "status": status.value,
            "updated_at": time.time(),
            "output_path": output_path,
            "error": error,
        }
        state["updated_at"] = time.time()
        self._write_state(state)

    def get_module_status(self, run_id: str, module_name: str) -> ModuleStatus:
        """Get the current status of a module."""
        state = self.get_run_state(run_id)
        mod_state = state.get("modules", {}).get(module_name, {})
        return ModuleStatus(mod_state.get("status", "pending"))

    def get_next_pending_module(self, run_id: str, module_order: list[str]) -> str | None:
        """Find the next module that hasn't completed yet."""
        state = self.get_run_state(run_id)
        modules = state.get("modules", {})
        for name in module_order:
            status = modules.get(name, {}).get("status", "pending")
            if status in ("pending", "paused"):
                return name
        return None

    def save_module_output(self, run_id: str, module_name: str, output_data: dict) -> Path:
        """Save a module's output data to a JSON file."""
        output_dir = self.outputs_dir / module_name
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "output.json"
        output_file.write_text(
            json.dumps(output_data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return output_file

    def load_module_output(self, run_id: str, module_name: str) -> dict:
        """Load a module's previously saved output."""
        output_file = self.outputs_dir / module_name / "output.json"
        if not output_file.exists():
            raise FileNotFoundError(f"No output for {module_name} in run {run_id}")
        return json.loads(output_file.read_text(encoding="utf-8"))

    def save_checkpoint(self, run_id: str, module_name: str, checkpoint: dict) -> None:
        """Save a module checkpoint for resume."""
        cp_file = self.checkpoint_dir / module_name / "checkpoint.json"
        cp_file.parent.mkdir(parents=True, exist_ok=True)
        cp_file.write_text(
            json.dumps(checkpoint, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def load_checkpoint(self, run_id: str, module_name: str) -> dict | None:
        """Load a module checkpoint if available."""
        cp_file = self.checkpoint_dir / module_name / "checkpoint.json"
        if not cp_file.exists():
            return None
        return json.loads(cp_file.read_text(encoding="utf-8"))

    def detect_user_changes(self, run_id: str) -> list[str]:
        """Detect modules whose output files have been modified since last state update.

        Returns list of module names whose downstream modules need re-running.
        """
        state = self.get_run_state(run_id)
        changed = []
        for mod_name, mod_state in state.get("modules", {}).items():
            if mod_state.get("status") != "completed":
                continue
            output_path = mod_state.get("output_path")
            if output_path:
                p = Path(output_path)
                if p.exists() and p.stat().st_mtime > mod_state.get("updated_at", 0):
                    changed.append(mod_name)
                    logger.info("Detected change in module output: %s", mod_name)
        return changed

    def pause(self, run_id: str) -> None:
        """Mark a run as paused."""
        state = self.get_run_state(run_id)
        state["status"] = "paused"
        state["updated_at"] = time.time()
        self._write_state(state)
        logger.info("Paused run: %s", run_id)

    def resume(self, run_id: str) -> None:
        """Mark a run as running (resume from pause)."""
        state = self.get_run_state(run_id)
        state["status"] = "running"
        state["updated_at"] = time.time()
        self._write_state(state)
        logger.info("Resumed run: %s", run_id)

    def get_status(self, run_id: str) -> str:
        """Get the current status of a run."""
        state = self.get_run_state(run_id)
        return state.get("status", "created")

    def set_status(self, run_id: str, status: str) -> None:
        """Set the status of a run."""
        state = self.get_run_state(run_id)
        state["status"] = status
        self._write_state(state)
        logger.info("Set run %s status to: %s", run_id, status)

    def increment_tuning_count(self, run_id: str) -> int:
        """Increment tuning_count in state.json and return new value."""
        state = self.get_run_state(run_id)
        count = state.get("tuning_count", 0) + 1
        state["tuning_count"] = count
        state["updated_at"] = time.time()
        self._write_state(state)
        return count

    def set_paper_status(self, run_id: str, paper_status: str) -> None:
        """Set paper_status in state.json. Values: '' | 'draft' | 'pdf_ready'."""
        state = self.get_run_state(run_id)
        state["paper_status"] = paper_status
        state["updated_at"] = time.time()
        self._write_state(state)

    def get_llm_config(self, run_id: str) -> dict:
        """Get LLM config from state.json. Returns empty dict if not set."""
        state = self.get_run_state(run_id)
        return state.get("llm_config", {})

    def set_llm_config(self, run_id: str, llm_config: dict) -> None:
        """Set LLM config in state.json.

        Args:
            llm_config: {"provider": "openai", "api_key": "...", "base_url": "...", "model": "..."}
        """
        state = self.get_run_state(run_id)
        state["llm_config"] = llm_config
        state["updated_at"] = time.time()
        self._write_state(state)
        logger.info("Updated LLM config for run %s", run_id)

    def _write_state(self, state: dict) -> None:
        state_file = self.checkpoint_dir / "state.json"
        state_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    # --- HITL Checkpoint Review ---

    def set_checkpoint_review(self, run_id: str, module_name: str, output: dict) -> None:
        """Store module output at a HITL checkpoint for user review."""
        state = self.get_run_state(run_id)
        state.setdefault("checkpoint_reviews", {})[module_name] = {
            "output": output,
            "status": "pending_review",
            "timestamp": time.time(),
        }
        self._write_state(state)

    def resolve_checkpoint(
        self,
        run_id: str,
        module_name: str,
        decision: str,
        adjusted_params: dict | None = None,
    ) -> None:
        """Record user's decision on a HITL checkpoint.

        Args:
            decision: "approved", "rejected", or "adjusted"
            adjusted_params: New parameters if decision is "adjusted"
        """
        state = self.get_run_state(run_id)
        review = state.get("checkpoint_reviews", {}).get(module_name, {})
        review["status"] = decision
        review["adjusted_params"] = adjusted_params
        self._write_state(state)
        logger.info("Checkpoint %s resolved: %s", module_name, decision)

    def get_checkpoint_review(self, run_id: str, module_name: str) -> dict | None:
        """Get the current review state for a HITL checkpoint."""
        state = self.get_run_state(run_id)
        return state.get("checkpoint_reviews", {}).get(module_name)

    # --- Dynamic Module Injection ---

    def update_pipeline_order(self, run_id: str, pipeline_order: list[str]) -> None:
        """Update state.json pipeline_order (for runtime module injection)."""
        state = self.get_run_state(run_id)
        state["pipeline_order"] = pipeline_order
        state["updated_at"] = time.time()
        state.setdefault("injections", []).append({
            "pipeline_order": list(pipeline_order),
            "timestamp": time.time(),
        })
        self._write_state(state)
        logger.info("Updated pipeline_order for run %s: %s", run_id, pipeline_order)

    # --- Preset Management (Pluggable Mode) ---

    def save_preset(
        self, name: str, pipeline_order: list, module_configs: dict | None = None
    ) -> Path:
        """Save a module configuration preset."""
        presets_dir = self.workspace_dir / "presets"
        presets_dir.mkdir(parents=True, exist_ok=True)
        preset_file = presets_dir / f"{name}.json"
        preset_file.write_text(
            json.dumps({
                "name": name,
                "pipeline_order": pipeline_order,
                "module_configs": module_configs or {},
                "saved_at": time.time(),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Saved preset: %s", name)
        return preset_file

    def load_preset(self, name: str) -> dict | None:
        """Load a module configuration preset."""
        preset_file = self.workspace_dir / "presets" / f"{name}.json"
        if not preset_file.exists():
            return None
        return json.loads(preset_file.read_text(encoding="utf-8"))

    def list_presets(self) -> list[str]:
        """List available presets."""
        presets_dir = self.workspace_dir / "presets"
        if not presets_dir.exists():
            return []
        return [f.stem for f in presets_dir.glob("*.json")]
