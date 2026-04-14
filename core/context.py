"""会话上下文管理 — 消息历史持久化 + 检查点机制 + 自动压缩.

参考 kimi-cli 的 Context 类，为 GuardianSoul/TuningAgent 提供:
  1. JSONL 持久化 (每条消息一行，追加写入)
  2. 检查点/回滚 (用于多步 Agent 循环中的状态恢复)
  3. Token 计数 (用于自动压缩决策)
  4. 消息规范化 (合并相邻 user 消息)
  5. 自动压缩 (当接近上下文限制时自动摘要)

用法:
    context = ConversationContext(
        context_file=Path("guardian_context.jsonl"),
        system_prompt="You are a helpful assistant...",
        max_context_tokens=128000,
        compaction_trigger_ratio=0.85
    )

    # 追加消息
    context.append_message(Message(role="user", content="Hello"))

    # 创建检查点
    cp_id = context.create_checkpoint({"step": 3, "phase": "analyzing"})

    # 回滚到检查点
    context.revert_to(cp_id)

    # 获取规范化历史
    messages = context.get_normalized_history()

    # 检查是否需要压缩
    if context.should_compact():
        context.compact(llm_provider)  # 使用 LLM 生成摘要
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from core.llm import Message

logger = logging.getLogger(__name__)


@dataclass
class Checkpoint:
    """检查点 — 支持回滚到历史状态"""

    id: int
    timestamp: str
    message_count: int
    token_count: int
    metadata: dict[str, Any] = field(default_factory=dict)  # {"step": 3, "phase": "analyzing"}


class ConversationContext:
    """会话上下文 — 持久化消息历史 + 检查点管理

    参考 kimi-cli 的 Context 类，提供：
    1. JSONL 持久化 (每条消息一行，追加写入)
    2. 检查点/回滚 (用于多步 Agent 循环中的状态恢复)
    3. Token 计数 (用于自动压缩决策)
    4. 消息规范化 (合并相邻 user 消息)
    """

    def __init__(
        self,
        context_file: Path,
        system_prompt: str = "",
        max_context_tokens: int = 128000,
        compaction_trigger_ratio: float = 0.85,
        max_history_files: int = 10,
        preserve_recent_messages: int = 50,
    ):
        """初始化上下文

        Args:
            context_file: 持久化文件路径（JSONL 格式）
            system_prompt: 系统提示词（可选）
            max_context_tokens: 最大上下文 token 数（默认 128K）
            compaction_trigger_ratio: 触发压缩的阈值比例（默认 0.85，即达到 85% 时触发）
            max_history_files: 历史文件保留数量（默认 10，超过后删除最旧的）
            preserve_recent_messages: 压缩时保留最近 N 条消息（默认 50）
        """
        self.context_file = context_file
        self.system_prompt = system_prompt
        self.max_context_tokens = max_context_tokens
        self.compaction_trigger_ratio = compaction_trigger_ratio
        self.max_history_files = max_history_files
        self.preserve_recent_messages = preserve_recent_messages
        self._history: list[Message] = []
        self._token_count = 0
        self._pending_token_estimate = 0
        self._next_checkpoint_id = 1
        self._checkpoints: dict[int, int] = {}  # checkpoint_id -> message_index
        self._compaction_count = 0  # 压缩次数计数器

        # 加载已有历史
        if context_file.exists():
            self._load_from_file()

    def _load_from_file(self):
        """从 JSONL 加载历史"""
        try:
            with open(self.context_file, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue

                    data = json.loads(line)

                    # Checkpoint marker
                    if data.get("type") == "checkpoint":
                        cp = Checkpoint(**data["checkpoint"])
                        self._checkpoints[cp.id] = data["message_index"]
                        self._next_checkpoint_id = max(self._next_checkpoint_id, cp.id + 1)
                        continue

                    # Message
                    if "role" in data:
                        # Import here to avoid circular dependency
                        from core.llm import Message, ToolCall

                        # Reconstruct tool_calls
                        tool_calls = None
                        if data.get("tool_calls"):
                            tool_calls = [
                                ToolCall(
                                    id=tc["id"],
                                    name=tc["name"],
                                    arguments=tc["arguments"],
                                )
                                for tc in data["tool_calls"]
                            ]

                        msg = Message(
                            role=data["role"],
                            content=data.get("content"),
                            name=data.get("name"),
                            tool_call_id=data.get("tool_call_id"),
                            tool_calls=tool_calls,
                        )
                        self._history.append(msg)

            logger.info(f"Loaded {len(self._history)} messages from {self.context_file}")

        except Exception as e:
            logger.error(f"Failed to load context from {self.context_file}: {e}")
            # Don't raise - start fresh
            self._history.clear()

    def append_message(self, message: Message, token_estimate: int = 0):
        """追加消息并持久化

        Args:
            message: 要追加的消息
            token_estimate: 预估 token 数（可选）
        """
        self._history.append(message)
        self._pending_token_estimate += token_estimate

        # 追加写入 JSONL
        try:
            # Ensure parent directory exists
            self.context_file.parent.mkdir(parents=True, exist_ok=True)

            # Prepare message data
            msg_data: dict[str, Any] = {
                "role": message.role,
            }

            if message.content is not None:
                msg_data["content"] = message.content
            if message.name:
                msg_data["name"] = message.name
            if message.tool_call_id:
                msg_data["tool_call_id"] = message.tool_call_id
            if message.tool_calls:
                msg_data["tool_calls"] = [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in message.tool_calls
                ]

            with open(self.context_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(msg_data, ensure_ascii=False) + "\n")

        except Exception as e:
            logger.error(f"Failed to append message to {self.context_file}: {e}")

    def create_checkpoint(self, metadata: Optional[dict[str, Any]] = None) -> int:
        """创建检查点

        Args:
            metadata: 检查点元数据（如 {"step": 3, "phase": "analyzing"}）

        Returns:
            检查点 ID
        """
        cp_id = self._next_checkpoint_id
        self._checkpoints[cp_id] = len(self._history)

        # 写入检查点标记
        try:
            # Ensure parent directory exists
            self.context_file.parent.mkdir(parents=True, exist_ok=True)

            cp_data = {
                "type": "checkpoint",
                "checkpoint": {
                    "id": cp_id,
                    "timestamp": datetime.now().isoformat(),
                    "message_count": len(self._history),
                    "token_count": self._token_count,
                    "metadata": metadata or {},
                },
                "message_index": len(self._history),
            }

            with open(self.context_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(cp_data, ensure_ascii=False) + "\n")

        except Exception as e:
            logger.error(f"Failed to create checkpoint: {e}")

        self._next_checkpoint_id += 1
        logger.debug(f"Created checkpoint {cp_id} at message {len(self._history)}")
        return cp_id

    def revert_to(self, checkpoint_id: int):
        """回滚到检查点

        Args:
            checkpoint_id: 检查点 ID

        Raises:
            ValueError: 检查点不存在
        """
        if checkpoint_id not in self._checkpoints:
            raise ValueError(f"Checkpoint {checkpoint_id} not found")

        target_index = self._checkpoints[checkpoint_id]
        removed_count = len(self._history) - target_index

        self._history = self._history[:target_index]
        self._pending_token_estimate = 0

        # 移除后续检查点
        self._checkpoints = {
            cid: idx for cid, idx in self._checkpoints.items() if idx <= target_index
        }

        logger.info(
            f"Reverted to checkpoint {checkpoint_id}, "
            f"kept {len(self._history)} messages, removed {removed_count}"
        )

    def get_normalized_history(self) -> list[Message]:
        """规范化历史 — 合并相邻 user 消息

        OpenAI API 要求不能有相邻的同角色消息，需要合并。

        Returns:
            规范化后的消息列表
        """
        if not self._history:
            return []

        # Import here to avoid circular dependency
        from core.llm import Message as MessageClass

        normalized = []
        for msg in self._history:
            if (
                normalized
                and normalized[-1].role == "user"
                and msg.role == "user"
                and normalized[-1].content
                and msg.content
            ):
                # 合并 user 消息
                merged_content = f"{normalized[-1].content}\n\n{msg.content}"
                normalized[-1] = MessageClass(role="user", content=merged_content)
            else:
                normalized.append(msg)

        return normalized

    def update_token_count(self, count: int):
        """更新 token 计数 (从 LLM API 响应中获取)

        Args:
            count: 确认的 token 数
        """
        self._token_count = count
        self._pending_token_estimate = 0

    @property
    def estimated_tokens(self) -> int:
        """估算当前总 token 数"""
        return self._token_count + self._pending_token_estimate

    def should_compact(self) -> bool:
        """检查是否需要压缩上下文

        Returns:
            True 如果当前 token 数超过阈值的 85%
        """
        threshold = int(self.max_context_tokens * self.compaction_trigger_ratio)
        return self.estimated_tokens >= threshold

    def _rotate_history_files(self):
        """轮转历史文件 — 当前文件 → .old{count}

        每次压缩时重命名为 .old{N}，N 递增。
        超过 max_history_files 时删除最旧的。
        """
        if not self.context_file.exists():
            return

        # 扫描现有的历史文件，确定下一个编号
        next_num = 1
        existing_nums = []
        for file in self.context_file.parent.glob(f"{self.context_file.stem}.jsonl.old*"):
            try:
                num = int(file.suffix.replace(".old", ""))
                existing_nums.append(num)
                next_num = max(next_num, num + 1)
            except ValueError:
                continue

        # 如果有压缩计数器，使用它
        if self._compaction_count > 0:
            next_num = self._compaction_count + 1

        # 新的历史文件名
        new_file = self.context_file.with_suffix(f".jsonl.old{next_num}")

        # 重命名当前文件
        try:
            self.context_file.rename(new_file)
            logger.info(f"Rotated history: {self.context_file} → {new_file}")
            self._compaction_count = next_num
        except Exception as e:
            logger.error(f"Failed to rotate history file: {e}")
            return

        # 清理超过上限的旧文件
        if len(existing_nums) >= self.max_history_files:
            # 删除最旧的文件
            existing_nums.sort()
            for num in existing_nums[:len(existing_nums) - self.max_history_files + 1]:
                old_file = self.context_file.with_suffix(f".jsonl.old{num}")
                try:
                    old_file.unlink()
                    logger.debug(f"Deleted old history: {old_file}")
                except Exception as e:
                    logger.error(f"Failed to delete {old_file}: {e}")

    def compact(self, llm_provider: "BaseLLMProvider", preserve_recent: Optional[int] = None) -> int:
        """压缩上下文 — 使用 LLM 生成摘要替代早期对话

        Args:
            llm_provider: LLM provider 用于生成摘要
            preserve_recent: 保留最近 N 条消息不压缩（None 则使用配置的 preserve_recent_messages）

        Returns:
            压缩后节省的 token 数
        """
        if preserve_recent is None:
            preserve_recent = self.preserve_recent_messages

        if len(self._history) <= preserve_recent + 2:  # system + recent
            logger.info("Not enough messages to compact")
            return 0

        # 轮转历史文件
        self._rotate_history_files()

        # 保留 system 消息和最近的消息
        system_msg = self._history[0] if self._history and self._history[0].role == "system" else None
        recent_messages = self._history[-preserve_recent:]

        # 需要压缩的消息
        to_compact = self._history[1:-preserve_recent] if system_msg else self._history[:-preserve_recent]

        if not to_compact:
            return 0

        logger.info(f"Compacting {len(to_compact)} messages, preserving {preserve_recent} recent messages")

        # 构建摘要请求
        compact_summary = self._generate_compaction_summary(llm_provider, to_compact)

        # 重建历史
        new_history = []
        if system_msg:
            new_history.append(system_msg)

        # 添加摘要作为 user 消息
        new_history.append(
            Message(
                role="user",
                content=f"[Context Summary - Earlier Conversation]:\n{compact_summary}"
            )
        )

        # 添加最近的消息
        new_history.extend(recent_messages)

        # 计算节省的 token
        old_tokens = self.estimated_tokens
        self._history = new_history
        self._token_count = 0
        self._pending_token_estimate = sum(
            len(msg.content.split()) * 2 if msg.content else 0
            for msg in new_history
        )

        saved = old_tokens - self.estimated_tokens
        logger.info(f"Compaction saved ~{saved} tokens")

        # 写入压缩标记到文件
        self._write_compaction_marker(len(to_compact), saved)

        return saved

    def _generate_compaction_summary(
        self, llm_provider: "BaseLLMProvider", messages: list[Message]
    ) -> str:
        """使用 LLM 生成对话摘要

        Args:
            llm_provider: LLM provider
            messages: 需要摘要的消息列表

        Returns:
            摘要文本
        """
        # Import here to avoid circular dependency
        from core.llm import Message as MessageClass

        # 构建摘要 prompt
        conversation_text = []
        for msg in messages:
            role = msg.role.upper()
            content = msg.content or ""
            if msg.tool_calls:
                tool_names = [tc.name for tc in msg.tool_calls]
                content = f"[Tools: {', '.join(tool_names)}] {content}"
            elif msg.role == "tool" and msg.name:
                content = f"[Tool Result: {msg.name}] {content[:200]}..."
            conversation_text.append(f"{role}: {content[:500]}")

        prompt = f"""Summarize the following conversation history concisely. Focus on:
1. Key decisions made
2. Important findings from tool calls
3. Errors encountered and how they were resolved
4. Current progress and next steps

Conversation:
{chr(10).join(conversation_text)}

Provide a brief summary (3-5 sentences):"""

        try:
            response = llm_provider.chat(
                messages=[MessageClass(role="user", content=prompt)],
                temperature=0.3,
                max_tokens=500,
            )
            return response.content or "Earlier conversation summarized."
        except Exception as e:
            logger.error(f"Failed to generate compaction summary: {e}")
            return "Earlier conversation compressed (summary generation failed)."

    def _write_compaction_marker(self, messages_removed: int, tokens_saved: int):
        """写入压缩标记到 JSONL"""
        try:
            self.context_file.parent.mkdir(parents=True, exist_ok=True)

            marker = {
                "type": "compaction",
                "timestamp": datetime.now().isoformat(),
                "messages_removed": messages_removed,
                "tokens_saved": tokens_saved,
            }

            with open(self.context_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(marker, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to write compaction marker: {e}")

    @property
    def history(self) -> list[Message]:
        """获取历史消息列表"""
        return self._history

    def clear(self):
        """清空历史"""
        self._history.clear()
        self._token_count = 0
        self._pending_token_estimate = 0
        self._checkpoints.clear()
        self._next_checkpoint_id = 1

        # 清空文件
        if self.context_file.exists():
            try:
                self.context_file.unlink()
                logger.info(f"Cleared context file {self.context_file}")
            except Exception as e:
                logger.error(f"Failed to clear context file: {e}")

    def __len__(self) -> int:
        """返回历史消息数量"""
        return len(self._history)

    def __repr__(self) -> str:
        return (
            f"ConversationContext("
            f"messages={len(self._history)}, "
            f"tokens={self.estimated_tokens}, "
            f"checkpoints={len(self._checkpoints)})"
        )
