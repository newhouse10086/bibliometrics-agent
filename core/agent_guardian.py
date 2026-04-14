"""Guardian Agent System for automatic error recovery.

This module provides a framework for per-module guardian agents that can
automatically generate and apply fixes when modules fail.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import json
import logging
import traceback

logger = logging.getLogger(__name__)


@dataclass
class ErrorAnalysis:
    """Analysis of a module execution error."""
    error_type: str
    error_message: str
    root_cause: str
    suggested_fix: str
    confidence: float  # 0-1
    context: dict = field(default_factory=dict)


@dataclass
class FixCode:
    """Generated fix code."""
    module_name: str
    code: str
    description: str
    timestamp: str
    error_type: str
    metadata: dict = field(default_factory=dict)


@dataclass
class GuardianDecision:
    """Record of guardian agent decision."""
    module: str
    error: dict
    analysis: ErrorAnalysis
    fix_generated: bool
    fix_path: Optional[str]
    test_passed: bool
    applied: bool
    outcome: str  # "success", "failed", "escalated"
    timestamp: str


class GuardianAgent(ABC):
    """Base class for all guardian agents.

    Each module has a specialized guardian agent that can:
    1. Analyze module execution errors
    2. Generate project-specific fix code
    3. Test fixes before applying
    4. Save fixes to workspace (does NOT modify core code)
    """

    def __init__(self, module_name: str):
        self.module_name = module_name
        self.logger = logging.getLogger(f"guardian.{module_name}")

    @abstractmethod
    def analyze_error(self, error: Exception, context: dict) -> ErrorAnalysis:
        """Analyze module execution error.

        Args:
            error: The exception that occurred
            context: Execution context (input_data, config, etc.)

        Returns:
            ErrorAnalysis with root cause and suggested fix
        """
        pass

    @abstractmethod
    def generate_fix(self, analysis: ErrorAnalysis) -> Optional[FixCode]:
        """Generate fix code based on error analysis.

        Args:
            analysis: Error analysis from analyze_error()

        Returns:
            FixCode if fix can be generated, None otherwise
        """
        pass

    def test_fix(self, fix: FixCode, context: dict) -> bool:
        """Test if fix code is valid.

        Default implementation checks syntax.

        Args:
            fix: Generated fix code
            context: Execution context

        Returns:
            True if fix passes tests
        """
        try:
            compile(fix.code, '<string>', 'exec')
            self.logger.info(f"Fix syntax check passed for {self.module_name}")
            return True
        except SyntaxError as e:
            self.logger.warning(f"Fix syntax error: {e}")
            return False

    def handle_error(
        self,
        error: Exception,
        context: dict,
        workspace_dir: Path
    ) -> GuardianDecision:
        """Main error handling flow.

        Args:
            error: The exception that occurred
            context: Execution context
            workspace_dir: Path to workspace directory

        Returns:
            GuardianDecision with outcome
        """
        timestamp = datetime.now().isoformat()

        self.logger.info(f"Handling error in {self.module_name}: {error}")

        # Step 1: Analyze error
        analysis = self.analyze_error(error, context)
        self.logger.info(f"Root cause: {analysis.root_cause}")

        # Step 2: Generate fix
        fix = self.generate_fix(analysis)

        if not fix:
            self.logger.warning(f"Could not generate fix for {self.module_name}")
            decision = GuardianDecision(
                module=self.module_name,
                error={
                    "type": type(error).__name__,
                    "message": str(error),
                    "traceback": traceback.format_exc(),
                },
                analysis=analysis,
                fix_generated=False,
                fix_path=None,
                test_passed=False,
                applied=False,
                outcome="failed",
                timestamp=timestamp,
            )
            self._log_decision(decision, workspace_dir)
            return decision

        # Step 3: Test fix
        test_passed = self.test_fix(fix, context)

        if not test_passed:
            self.logger.warning(f"Fix test failed for {self.module_name}")
            decision = GuardianDecision(
                module=self.module_name,
                error={
                    "type": type(error).__name__,
                    "message": str(error),
                    "traceback": traceback.format_exc(),
                },
                analysis=analysis,
                fix_generated=True,
                fix_path=None,
                test_passed=False,
                applied=False,
                outcome="failed",
                timestamp=timestamp,
            )
            self._log_decision(decision, workspace_dir)
            return decision

        # Step 4: Save fix to workspace
        fix_path = self._save_fix(fix, workspace_dir)
        self.logger.info(f"Saved fix to {fix_path}")

        # Step 5: Log decision
        decision = GuardianDecision(
            module=self.module_name,
            error={
                "type": type(error).__name__,
                "message": str(error),
                "traceback": traceback.format_exc(),
            },
            analysis=analysis,
            fix_generated=True,
            fix_path=str(fix_path),
            test_passed=True,
            applied=False,  # Will be set to True by orchestrator if applied successfully
            outcome="success",
            timestamp=timestamp,
        )
        self._log_decision(decision, workspace_dir)

        return decision

    def _save_fix(self, fix: FixCode, workspace_dir: Path) -> Path:
        """Save fix code to workspace.

        Args:
            fix: Fix code to save
            workspace_dir: Workspace directory

        Returns:
            Path to saved fix file
        """
        fixes_dir = workspace_dir / "fixes"
        fixes_dir.mkdir(parents=True, exist_ok=True)

        timestamp_clean = fix.timestamp.replace(":", "-").replace(".", "-")
        filename = f"{self.module_name}_fix_{timestamp_clean}.py"
        fix_path = fixes_dir / filename

        # Create fix file with metadata header
        header = f'''"""
Guardian Agent Generated Fix
Module: {fix.module_name}
Error Type: {fix.error_type}
Description: {fix.description}
Timestamp: {fix.timestamp}
"""

'''
        full_code = header + fix.code
        fix_path.write_text(full_code, encoding="utf-8")

        return fix_path

    def _log_decision(self, decision: GuardianDecision, workspace_dir: Path):
        """Log guardian decision to workspace.

        Args:
            decision: Guardian decision to log
            workspace_dir: Workspace directory
        """
        logs_dir = workspace_dir / "agent_logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        log_file = logs_dir / f"{self.module_name}_guardian.json"

        # Append to log file
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
        }

        # Load existing log or create new
        if log_file.exists():
            with open(log_file, "r", encoding="utf-8") as f:
                logs = json.load(f)
        else:
            logs = []

        logs.append(log_entry)

        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=2, ensure_ascii=False)


class GlobalCoordinatorAgent:
    """Global coordinator for cross-module error handling."""

    def __init__(self):
        self.logger = logging.getLogger("guardian.global_coordinator")

    def should_escalate_to_hitl(
        self,
        error: Exception,
        module_name: str,
        guardian_decision: Optional[GuardianDecision]
    ) -> bool:
        """Determine if error should be escalated to human-in-the-loop.

        Args:
            error: The exception
            module_name: Module that failed
            guardian_decision: Guardian's decision (if any)

        Returns:
            True if should escalate to HITL
        """
        if not guardian_decision:
            return True

        if guardian_decision.outcome == "failed":
            # Guardian couldn't fix it
            return True

        # Specific escalation rules
        error_str = str(error)

        # API keys, authentication issues
        if "api key" in error_str.lower() or "authentication" in error_str.lower():
            return True

        # File not found, missing resources
        if "not found" in error_str.lower() or "does not exist" in error_str.lower():
            return True

        return False

    def create_hitl_request(
        self,
        error: Exception,
        module_name: str,
        guardian_decision: Optional[GuardianDecision]
    ) -> dict:
        """Create HITL request for user intervention.

        Args:
            error: The exception
            module_name: Module that failed
            guardian_decision: Guardian's decision (if any)

        Returns:
            HITL request dict
        """
        return {
            "type": "error_intervention",
            "module": module_name,
            "error": {
                "type": type(error).__name__,
                "message": str(error),
                "traceback": traceback.format_exc(),
            },
            "guardian_attempted": guardian_decision is not None,
            "guardian_outcome": guardian_decision.outcome if guardian_decision else None,
            "message": f"Module '{module_name}' failed and could not be auto-fixed. Please intervene.",
            "actions": [
                "retry",
                "skip_module",
                "abort_pipeline",
                "provide_input",
            ],
        }


# Registry of module guardians
_guardian_registry: dict[str, type[GuardianAgent]] = {}


def register_guardian(module_name: str, guardian_class: type[GuardianAgent]):
    """Register a guardian agent for a module.

    Args:
        module_name: Name of the module
        guardian_class: Guardian agent class
    """
    _guardian_registry[module_name] = guardian_class
    logger.info(f"Registered guardian for module: {module_name}")


def get_guardian(module_name: str) -> Optional[GuardianAgent]:
    """Get guardian agent for a module.

    Args:
        module_name: Name of the module

    Returns:
        Guardian agent instance, or None if not registered
    """
    guardian_class = _guardian_registry.get(module_name)
    if guardian_class:
        return guardian_class(module_name)
    return None


def load_fix_from_workspace(workspace_dir: Path, module_name: str) -> Optional[str]:
    """Load most recent fix for a module from workspace.

    Args:
        workspace_dir: Workspace directory
        module_name: Name of the module

    Returns:
        Fix code if found, None otherwise
    """
    fixes_dir = workspace_dir / "fixes"
    if not fixes_dir.exists():
        return None

    # Find all fixes for this module
    fix_files = sorted(fixes_dir.glob(f"{module_name}_fix_*.py"), reverse=True)

    if not fix_files:
        return None

    # Load most recent fix
    latest_fix = fix_files[0]
    return latest_fix.read_text(encoding="utf-8")
