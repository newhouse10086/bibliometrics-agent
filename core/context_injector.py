"""动态上下文注入 — 在每次 LLM 调用前注入最新状态.

参考 kimi-cli 的动态注入机制，提供：
  1. 项目状态更新（最新模块进度、文件列表）
  2. 模式提醒（chat/agent 模式的行为提示）
  3. 工作区摘要（最近修改的文件、错误日志）
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.llm import Message

logger = logging.getLogger(__name__)


class DynamicInjector:
    """动态上下文注入器.

    在每次 LLM 调用前，根据当前状态生成上下文消息。
    """

    def __init__(
        self,
        workspace_dir: Path,
        project_id: str,
        mode: str = "agent",  # "chat" | "agent"
        update_interval: int = 10,  # 每隔 N 次 LLM 调用更新一次
    ):
        self.workspace_dir = workspace_dir
        self.project_id = project_id
        self.mode = mode
        self.update_interval = update_interval
        self._call_count = 0
        self._last_update = None

    def should_inject(self) -> bool:
        """检查是否需要注入更新.

        Returns:
            True 如果距离上次更新已超过 update_interval 次调用
        """
        self._call_count += 1
        return self._call_count % self.update_interval == 0

    def inject_context_update(self) -> Optional["Message"]:
        """生成上下文更新消息.

        Returns:
            包含最新状态的消息，或 None 如果无需更新
        """
        if not self.should_inject():
            return None

        from core.llm import Message

        # 收集最新状态
        updates = []

        # 1. 项目进度
        state_update = self._get_state_update()
        if state_update:
            updates.append(state_update)

        # 2. 最近修改的文件
        recent_files = self._get_recent_files()
        if recent_files:
            updates.append(f"**Recent Files**:\n{recent_files}")

        # 3. 错误日志摘要
        error_summary = self._get_error_summary()
        if error_summary:
            updates.append(f"**Recent Errors**:\n{error_summary}")

        if not updates:
            return None

        # 构建消息
        content = "[Context Update]\n\n" + "\n\n".join(updates)
        self._last_update = datetime.now().isoformat()

        return Message(role="user", content=content)

    def _get_state_update(self) -> Optional[str]:
        """获取项目状态更新."""
        try:
            state_path = self.workspace_dir / "checkpoints" / "state.json"
            if not state_path.exists():
                return None

            with open(state_path, encoding="utf-8") as f:
                state = json.load(f)

            modules = state.get("modules", {})
            completed = sum(1 for m in modules.values() if m.get("status") == "completed")
            failed = sum(1 for m in modules.values() if m.get("status") == "failed")

            return (
                f"**Pipeline Progress**: {completed}/{len(modules)} modules completed, {failed} failed\n"
                f"Status: {state.get('status', 'unknown')}"
            )
        except Exception as e:
            logger.debug(f"Failed to get state update: {e}")
            return None

    def _get_recent_files(self, limit: int = 10) -> Optional[str]:
        """获取最近修改的文件."""
        try:
            outputs_dir = self.workspace_dir / "outputs"
            if not outputs_dir.exists():
                return None

            # 收集所有文件及其修改时间
            files = []
            for f in outputs_dir.rglob("*"):
                if f.is_file() and not f.name.endswith(".log"):
                    files.append((f, f.stat().st_mtime))

            # 按修改时间排序
            files.sort(key=lambda x: x[1], reverse=True)
            recent = files[:limit]

            if not recent:
                return None

            lines = []
            for f, _ in recent:
                rel_path = f.relative_to(outputs_dir)
                lines.append(f"- {rel_path}")

            return "\n".join(lines)
        except Exception as e:
            logger.debug(f"Failed to get recent files: {e}")
            return None

    def _get_error_summary(self, limit: int = 5) -> Optional[str]:
        """获取最近的错误摘要."""
        try:
            error_log_path = self.workspace_dir / "error.log"
            if not error_log_path.exists():
                return None

            with open(error_log_path, encoding="utf-8") as f:
                lines = f.readlines()

            # 获取最后几条错误
            error_lines = [line.strip() for line in lines if line.strip() and "ERROR" in line]
            recent_errors = error_lines[-limit:]

            if not recent_errors:
                return None

            return "\n".join(f"- {line[:200]}" for line in recent_errors)
        except Exception as e:
            logger.debug(f"Failed to get error summary: {e}")
            return None

    def get_mode_reminder(self) -> Optional["Message"]:
        """获取模式提醒消息.

        Returns:
            模式相关的提醒消息
        """
        from core.llm import Message

        if self.mode == "chat":
            return Message(
                role="user",
                content="[Mode Reminder] You are in chat mode. Focus on conversational assistance, "
                        "answering questions, and discussing ideas. Do not execute tools unless "
                        "explicitly requested.",
            )
        else:  # agent mode
            return Message(
                role="user",
                content="[Mode Reminder] You are in agent mode. Proactively use tools to read files, "
                        "execute commands, and analyze data to accomplish tasks.",
            )
