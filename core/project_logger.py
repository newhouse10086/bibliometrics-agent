"""Project-specific logging system.

Manages separate log files for each project workspace.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


class ProjectLogManager:
    """Manages project-specific log files."""

    _instance: Optional["ProjectLogManager"] = None
    _handlers: Dict[str, logging.FileHandler] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def setup_project_logger(self, project_id: str, workspace_dir: Path) -> None:
        """Setup a logger for a specific project.

        Args:
            project_id: Unique project identifier
            workspace_dir: Path to project workspace
        """
        # Create logs directory
        logs_dir = workspace_dir / "logs"
        logs_dir.mkdir(exist_ok=True)

        # Create log file with timestamp
        log_file = logs_dir / f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

        # Create file handler
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setLevel(logging.DEBUG)

        # Create formatter
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)

        # Store handler
        self._handlers[project_id] = handler

        # Add to root logger so all modules log to this file
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)

        # Also ensure we're logging to console
        if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(logging.INFO)
            console_formatter = logging.Formatter(
                "%(levelname)s:%(name)s:%(message)s"
            )
            console_handler.setFormatter(console_formatter)
            root_logger.addHandler(console_handler)

    def remove_project_logger(self, project_id: str) -> None:
        """Remove a project's log handler.

        Args:
            project_id: Project identifier
        """
        if project_id in self._handlers:
            handler = self._handlers.pop(project_id)
            logging.getLogger().removeHandler(handler)
            handler.close()

    def get_log_file_path(self, project_id: str) -> Optional[Path]:
        """Get the log file path for a project.

        Args:
            project_id: Project identifier

        Returns:
            Path to log file or None if not found
        """
        if project_id not in self._handlers:
            return None

        return Path(self._handlers[project_id].baseFilename)

    def read_recent_logs(self, project_id: str, lines: int = 100) -> list[str]:
        """Read recent log entries for a project.

        Args:
            project_id: Project identifier
            lines: Number of lines to read from end

        Returns:
            List of log lines (newest first)
        """
        log_file = self.get_log_file_path(project_id)

        # Fallback: find log file from workspace if handler not registered
        if not log_file or not log_file.exists():
            log_file = self._find_log_file(project_id)
            if not log_file:
                return []

        try:
            with open(log_file, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
                # Get last N lines
                recent = all_lines[-lines:] if len(all_lines) > lines else all_lines
                return [line.rstrip("\n") for line in recent]
        except Exception as e:
            logging.error(f"Failed to read log file for {project_id}: {e}")
            return [f"Error reading log file: {e}"]

    def read_error_logs(self, project_id: str) -> list[str]:
        """Read error-level log entries for a project.

        Args:
            project_id: Project identifier

        Returns:
            List of error log lines
        """
        log_file = self.get_log_file_path(project_id)

        # Fallback: find log file from workspace if handler not registered
        if not log_file or not log_file.exists():
            log_file = self._find_log_file(project_id)
            if not log_file:
                return []

        try:
            with open(log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
                # Filter for ERROR and CRITICAL levels
                error_lines = [
                    line.rstrip("\n")
                    for line in lines
                    if " - ERROR - " in line or " - CRITICAL - " in line
                ]
                return error_lines
        except Exception as e:
            logging.error(f"Failed to read error log for {project_id}: {e}")
            return [f"Error reading error log: {e}"]

    def _find_log_file(self, project_id: str) -> Optional[Path]:
        """Find the latest log file for a project by scanning workspace directories.

        Args:
            project_id: Project identifier

        Returns:
            Path to the latest log file, or None if not found
        """
        from core.workspace_manager import WorkspaceManager, get_default_workspace_dir
        try:
            base_dir = get_default_workspace_dir()
            # Look for workspace directory matching this project
            workspace_dir = None
            for ws_dir in base_dir.iterdir():
                if ws_dir.is_dir() and ws_dir.name.endswith(f"_{project_id}"):
                    workspace_dir = ws_dir
                    break
            if not workspace_dir:
                return None

            logs_dir = workspace_dir / "logs"
            if not logs_dir.exists():
                return None

            # Find the most recent log file
            log_files = sorted(logs_dir.glob("pipeline_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            return log_files[0] if log_files else None
        except Exception:
            return None


# Global singleton instance
_log_manager: Optional[ProjectLogManager] = None


def get_log_manager() -> ProjectLogManager:
    """Get the global log manager instance."""
    global _log_manager
    if _log_manager is None:
        _log_manager = ProjectLogManager()
    return _log_manager
