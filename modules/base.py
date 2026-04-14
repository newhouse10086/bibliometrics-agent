"""Base module interface for the bibliometrics-agent pluggable architecture.

Every analysis module (Preprocessor, TopicModeler, BurstDetector, etc.)
must inherit from BaseModule and implement its abstract methods.

The interface defines:
- Schema declarations (input, output, config) via JSON Schema
- A process() method that transforms input → output
- Checkpoint/resume support for HITL workflows
- Hardware requirement estimation for adaptive parameter tuning
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import jsonschema


class ModuleStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"        # waiting for HITL confirmation
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class HardwareSpec:
    """Estimated hardware requirements for a given module configuration."""
    min_memory_gb: float = 0.0
    recommended_memory_gb: float = 0.0
    cpu_cores: int = 1
    gpu_required: bool = False
    estimated_runtime_seconds: float = 0.0


@dataclass
class HITLCheckpoint:
    """A human-in-the-loop checkpoint within a module."""
    module: str
    name: str
    display_data: dict = field(default_factory=dict)
    options: list[str] = field(default_factory=lambda: ["continue", "adjust", "skip"])
    auto_timeout: int | None = None  # seconds; None = must confirm manually


@dataclass
class RunContext:
    """Runtime context passed to every module.process() call."""
    project_dir: Path
    run_id: str
    checkpoint_dir: Path
    hardware_info: dict = field(default_factory=dict)
    previous_outputs: dict[str, dict] = field(default_factory=dict)

    def get_output_path(self, module_name: str, filename: str) -> Path:
        """Get a standardized output path for a module's artifact.

        Uses outputs/{module_name}/ directory under project_dir.
        """
        p = self.project_dir / "outputs" / module_name
        p.mkdir(parents=True, exist_ok=True)
        return p / filename


class BaseModule(ABC):
    """Abstract base class for all bibliometric analysis modules.

    Subclasses must implement:
        - name, version properties
        - input_schema, output_schema, config_schema
        - process()

    Optionally override:
        - get_hardware_requirements()
        - get_checkpoint() / restore_checkpoint()
        - get_hitl_checkpoints()
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this module (e.g. 'preprocessor')."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Semantic version string."""

    @abstractmethod
    def input_schema(self) -> dict:
        """JSON Schema describing the expected input data format."""

    @abstractmethod
    def output_schema(self) -> dict:
        """JSON Schema describing the output data format."""

    @abstractmethod
    def config_schema(self) -> dict:
        """JSON Schema describing configurable parameters."""

    @abstractmethod
    def process(self, input_data: dict, config: dict, context: RunContext) -> dict:
        """Execute the module's analysis logic.

        Args:
            input_data: Data conforming to input_schema().
            config: Parameters conforming to config_schema().
            context: Runtime context with paths, hardware info, etc.

        Returns:
            Output data conforming to output_schema().
        """

    # --- Validation ---

    def validate_input(self, data: dict) -> bool:
        """Validate input data against input_schema."""
        try:
            jsonschema.validate(data, self.input_schema())
            return True
        except jsonschema.ValidationError:
            return False

    def validate_config(self, config: dict) -> bool:
        """Validate config against config_schema."""
        try:
            jsonschema.validate(config, self.config_schema())
            return True
        except jsonschema.ValidationError:
            return False

    def validate_output(self, data: dict) -> bool:
        """Validate output data against output_schema."""
        try:
            jsonschema.validate(data, self.output_schema())
            return True
        except jsonschema.ValidationError:
            return False

    # --- Hardware ---

    def get_hardware_requirements(self, config: dict) -> HardwareSpec:
        """Estimate hardware needs for the given configuration.

        Override in subclasses that have significant resource requirements.
        """
        return HardwareSpec()

    # --- Checkpoint / Resume ---

    def get_checkpoint(self) -> dict | None:
        """Return current checkpoint state for resume.

        Override if the module supports incremental processing.
        Returns None if no checkpoint is available.
        """
        return None

    def restore_checkpoint(self, checkpoint: dict) -> None:
        """Restore module state from a checkpoint.

        Override if the module supports incremental processing.
        """
        pass

    # --- HITL ---

    def get_hitl_checkpoints(self) -> list[HITLCheckpoint]:
        """Define HITL checkpoints within this module.

        Override to add human review points.
        """
        return []

    # --- Metadata ---

    def describe(self) -> dict:
        """Return a full description of this module for the registry."""
        return {
            "name": self.name,
            "version": self.version,
            "input_schema": self.input_schema(),
            "output_schema": self.output_schema(),
            "config_schema": self.config_schema(),
            "hardware_requirements": self.get_hardware_requirements({}).__dict__,
            "hitl_checkpoints": [
                {"name": c.name, "auto_timeout": c.auto_timeout}
                for c in self.get_hitl_checkpoints()
            ],
        }
