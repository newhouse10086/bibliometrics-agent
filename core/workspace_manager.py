"""Workspace manager for organizing project data and results.

Manages workspace directories with proper permissions for:
- Uploaded data files
- Intermediate results
- Final outputs
- Checkpoints
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """Manages project workspaces with isolated storage."""

    def __init__(self, base_dir: Path | str):
        """Initialize workspace manager.

        Args:
            base_dir: Base directory for all workspaces
        """
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Workspace manager initialized at: {self.base_dir}")

    def create_workspace(
        self,
        name: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        """Create a new workspace.

        Args:
            name: Workspace name (will be sanitized for filesystem)
            description: Optional description
            metadata: Optional metadata dict

        Returns:
            Path to created workspace directory
        """
        # Sanitize name for filesystem
        safe_name = self._sanitize_name(name)

        # Check if workspace already exists
        workspace_dir = self.base_dir / safe_name
        if workspace_dir.exists():
            logger.warning(f"Workspace '{safe_name}' already exists, using existing")
            return workspace_dir

        # Create workspace directory structure
        workspace_dir.mkdir(parents=True, exist_ok=True)

        # Create data directory for user uploads
        (workspace_dir / "data").mkdir(exist_ok=True)

        # Note: outputs/, checkpoints/, logs/, fixes/, reports/ will be created
        # by StateManager when the pipeline runs

        # Create workspace metadata
        workspace_meta = {
            "name": name,
            "safe_name": safe_name,
            "description": description,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "metadata": metadata or {},
            "status": "created",
        }

        meta_path = workspace_dir / "workspace.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(workspace_meta, f, indent=2, ensure_ascii=False)

        logger.info(f"Created workspace: {safe_name} at {workspace_dir}")
        return workspace_dir

    def get_workspace(self, name: str) -> Path | None:
        """Get workspace directory by name.

        Args:
            name: Workspace name (safe name or original name)

        Returns:
            Path to workspace or None if not found
        """
        safe_name = self._sanitize_name(name)
        workspace_dir = self.base_dir / safe_name

        if not workspace_dir.exists():
            logger.warning(f"Workspace '{safe_name}' not found")
            return None

        return workspace_dir

    def list_workspaces(self) -> list[dict[str, Any]]:
        """List all available workspaces.

        Returns:
            List of workspace metadata dicts
        """
        workspaces = []

        for workspace_dir in self.base_dir.iterdir():
            if not workspace_dir.is_dir():
                continue

            meta_path = workspace_dir / "workspace.json"
            if meta_path.exists():
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    workspaces.append(meta)
                except Exception as e:
                    logger.error(f"Failed to load workspace metadata: {e}")
                    # Add basic info
                    workspaces.append({
                        "name": workspace_dir.name,
                        "safe_name": workspace_dir.name,
                        "description": "Metadata unavailable",
                        "created_at": "Unknown",
                    })
            else:
                # Legacy workspace without metadata
                workspaces.append({
                    "name": workspace_dir.name,
                    "safe_name": workspace_dir.name,
                    "description": "Legacy workspace",
                    "created_at": "Unknown",
                })

        # Sort by creation date (newest first)
        workspaces.sort(
            key=lambda w: w.get("created_at", ""),
            reverse=True
        )

        return workspaces

    def update_workspace_metadata(
        self,
        name: str,
        updates: dict[str, Any],
    ) -> bool:
        """Update workspace metadata.

        Args:
            name: Workspace name
            updates: Dict of fields to update

        Returns:
            True if successful, False otherwise
        """
        workspace_dir = self.get_workspace(name)
        if not workspace_dir:
            return False

        meta_path = workspace_dir / "workspace.json"

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)

            # Update fields
            meta.update(updates)
            meta["updated_at"] = datetime.now().isoformat()

            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)

            logger.info(f"Updated workspace metadata: {name}")
            return True

        except Exception as e:
            logger.error(f"Failed to update workspace metadata: {e}")
            return False

    def delete_workspace(self, name: str) -> bool:
        """Delete a workspace and all its data.

        Args:
            name: Workspace name

        Returns:
            True if successful, False otherwise
        """
        workspace_dir = self.get_workspace(name)
        if not workspace_dir:
            logger.warning(f"Workspace '{name}' not found for deletion")
            return False

        try:
            shutil.rmtree(workspace_dir)
            logger.info(f"Deleted workspace: {name}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete workspace: {e}")
            return False

    def get_workspace_stats(self, name: str) -> dict[str, Any]:
        """Get workspace statistics.

        Args:
            name: Workspace name

        Returns:
            Dict with stats like file count, total size, etc.
        """
        workspace_dir = self.get_workspace(name)
        if not workspace_dir:
            return {}

        stats = {
            "total_files": 0,
            "total_size_mb": 0,
            "data_files": 0,
            "output_files": 0,
            "visualization_files": 0,
        }

        try:
            for file_path in workspace_dir.rglob("*"):
                if file_path.is_file():
                    stats["total_files"] += 1
                    stats["total_size_mb"] += file_path.stat().st_size / (1024 * 1024)

                    # Categorize files
                    if "data" in file_path.parts:
                        stats["data_files"] += 1
                    elif "outputs" in file_path.parts:
                        stats["output_files"] += 1
                    elif "visualizations" in file_path.parts:
                        stats["visualization_files"] += 1

            stats["total_size_mb"] = round(stats["total_size_mb"], 2)

        except Exception as e:
            logger.error(f"Failed to calculate workspace stats: {e}")

        return stats

    def _sanitize_name(self, name: str) -> str:
        """Sanitize workspace name for filesystem.

        Args:
            name: Original name

        Returns:
            Safe filesystem name
        """
        import re

        # Replace spaces with underscores
        safe = name.strip().replace(" ", "_")

        # Remove special characters
        safe = re.sub(r"[^\w\-_]", "", safe)

        # Limit length
        if len(safe) > 64:
            safe = safe[:64]

        # Ensure not empty
        if not safe:
            safe = f"workspace_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        return safe


def get_default_workspace_dir() -> Path:
    """Get default workspace directory.

    Returns:
        Path to default workspaces directory
    """
    from pathlib import Path

    # Use project-relative path
    project_root = Path(__file__).parent.parent
    workspaces_dir = project_root / "workspaces"

    return workspaces_dir
