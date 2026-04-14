# Agent 架构增强设计 (学习 kimi-cli)

## 背景与目标

基于对 kimi-cli 的深入研究，识别出当前 bibliometrics-agent 的关键缺陷：

1. **记忆系统缺失** — 无持久化对话历史，无检查点/回滚能力
2. **上下文压缩空白** — 无 token 限制感知，无自动压缩机制
3. **模式单一** — Web Chat 只有简单 chat，缺少 agent/chat 模式切换
4. **上下文管理粗糙** — 无动态注入，无消息规范化

**目标**：参考 kimi-cli，增强当前的 GuardianSoul/TuningAgent/WebChat，使其具备生产级 Agent 能力。

---

## 核心架构对比

### kimi-cli 的三层架构

```
┌─────────────────────────────────────────────────────────────┐
│                    KimiSoul (主控循环)                       │
│  - 检查点/回滚 (_checkpoint, revert_to)                     │
│  - 自动压缩 (should_auto_compact)                           │
│  - 动态注入 (collect_injections)                            │
│  - Flow 控制 (ralph_loop, skills)                           │
└─────────────────────────────────────────────────────────────┘
         │                │                  │
         ▼                ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐
│  kosong.step │  │   Context    │  │   KimiToolset         │
│  (LLM抽象层) │  │  (记忆系统)  │  │   (工具注册+MCP)      │
└──────────────┘  └──────────────┘  └───────────────────────┘
         │                │
         ▼                ▼
┌──────────────┐  ┌────────────────────────────────────────┐
│ ChatProvider │  │         File Backend                   │
│ (OpenAI API) │  │  - context.jsonl (消息历史)            │
│              │  │  - wire.jsonl (调试日志)               │
│              │  │  - state.json (会话状态)               │
└──────────────┘  └────────────────────────────────────────┘
```

### 当前 bibliometrics-agent 架构

```
┌─────────────────────────────────────────────────────────────┐
│              GuardianSoul / TuningAgent                     │
│  - 简单 for 循环 (max_steps)                                │
│  - 无检查点机制                                             │
│  - 无自动压缩                                               │
│  - 无动态注入                                               │
└─────────────────────────────────────────────────────────────┘
         │                │
         ▼                ▼
┌──────────────┐  ┌────────────────────────────────────────┐
│  LLM.chat()  │  │     self.messages: list[Message]       │
│  (直接调用)  │  │     (内存中，无持久化)                  │
└──────────────┘  └────────────────────────────────────────┘
```

---

## 设计方案

### Phase 1: 记忆系统 (Context Management)

#### 1.1 新建 `core/context.py` — 消息历史持久化

**核心类 `ConversationContext`**:

```python
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.llm import Message

logger = logging.getLogger(__name__)


@dataclass
class Checkpoint:
    """检查点 — 支持回滚到历史状态"""
    id: int
    timestamp: str
    message_count: int
    token_count: int
    metadata: dict = field(default_factory=dict)  # {"step": 3, "phase": "analyzing"}


class ConversationContext:
    """会话上下文 — 持久化消息历史 + 检查点管理

    参考 kimi-cli 的 Context 类，提供：
    1. JSONL 持久化 (每条消息一行，追加写入)
    2. 检查点/回滚 (用于多步 Agent 循环中的状态恢复)
    3. Token 计数 (用于自动压缩决策)
    4. 消息规范化 (合并相邻 user 消息)
    """

    def __init__(self, context_file: Path, system_prompt: str = ""):
        self.context_file = context_file
        self.system_prompt = system_prompt
        self._history: list[Message] = []
        self._token_count = 0
        self._pending_token_estimate = 0
        self._next_checkpoint_id = 1
        self._checkpoints: dict[int, int] = {}  # checkpoint_id -> message_index

        # 加载已有历史
        if context_file.exists():
            self._load_from_file()

    def _load_from_file(self):
        """从 JSONL 加载历史"""
        try:
            with open(self.context_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        # Checkpoint marker
                        if data.get("type") == "checkpoint":
                            cp = Checkpoint(**data["checkpoint"])
                            self._checkpoints[cp.id] = data["message_index"]
                            self._next_checkpoint_id = max(self._next_checkpoint_id, cp.id + 1)
                        # Message
                        elif "role" in data:
                            self._history.append(Message(**data))
            logger.info(f"Loaded {len(self._history)} messages from {self.context_file}")
        except Exception as e:
            logger.error(f"Failed to load context: {e}")

    def append_message(self, message: Message, token_estimate: int = 0):
        """追加消息并持久化"""
        self._history.append(message)
        self._pending_token_estimate += token_estimate

        # 追加写入 JSONL
        try:
            with open(self.context_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "role": message.role,
                    "content": message.content,
                    "name": message.name,
                    "tool_call_id": message.tool_call_id,
                    "tool_calls": [
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in (message.tool_calls or [])
                    ],
                }, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to append message: {e}")

    def create_checkpoint(self, metadata: dict = None) -> int:
        """创建检查点"""
        cp_id = self._next_checkpoint_id
        self._checkpoints[cp_id] = len(self._history)

        # 写入检查点标记
        try:
            with open(self.context_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "type": "checkpoint",
                    "checkpoint": {
                        "id": cp_id,
                        "timestamp": datetime.now().isoformat(),
                        "message_count": len(self._history),
                        "token_count": self._token_count,
                        "metadata": metadata or {},
                    },
                    "message_index": len(self._history),
                }, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to create checkpoint: {e}")

        self._next_checkpoint_id += 1
        logger.debug(f"Created checkpoint {cp_id} at message {len(self._history)}")
        return cp_id

    def revert_to(self, checkpoint_id: int):
        """回滚到检查点"""
        if checkpoint_id not in self._checkpoints:
            raise ValueError(f"Checkpoint {checkpoint_id} not found")

        target_index = self._checkpoints[checkpoint_id]
        self._history = self._history[:target_index]
        self._pending_token_estimate = 0

        logger.info(f"Reverted to checkpoint {checkpoint_id}, kept {len(self._history)} messages")

    def get_normalized_history(self) -> list[Message]:
        """规范化历史 — 合并相邻 user 消息

        OpenAI API 要求不能有相邻的同角色消息，需要合并。
        """
        normalized = []
        for msg in self._history:
            if normalized and normalized[-1].role == "user" and msg.role == "user":
                # 合并 user 消息
                merged_content = f"{normalized[-1].content}\n\n{msg.content}"
                normalized[-1] = Message(role="user", content=merged_content)
            else:
                normalized.append(msg)
        return normalized

    def update_token_count(self, count: int):
        """更新 token 计数 (从 LLM API 响应中获取)"""
        self._token_count = count
        self._pending_token_estimate = 0

    @property
    def estimated_tokens(self) -> int:
        """估算当前总 token 数"""
        return self._token_count + self._pending_token_estimate

    @property
    def history(self) -> list[Message]:
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
            self.context_file.unlink()
```

#### 1.2 集成到 GuardianSoul

**修改 `core/guardian_soul.py`**:

```python
# 在 __init__ 中
self.context = ConversationContext(
    context_file=self.checkpoint_dir / "guardian_context.jsonl",
    system_prompt=self._build_system_prompt()
)

# 替换 self.messages.append() 为
self.context.append_message(message, token_estimate=...)

# 在关键决策点创建检查点
checkpoint_id = self.context.create_checkpoint({"step": step, "phase": "analyzing"})

# 如果需要回滚 (如用户拒绝修复)
self.context.revert_to(checkpoint_id)
```

---

### Phase 2: 上下文压缩 (Compaction)

#### 2.1 新建 `core/compaction.py` — 自动压缩系统

```python
from __future__ import annotations

import logging
from dataclasses import dataclass

from core.context import ConversationContext
from core.llm import BaseLLMProvider, Message

logger = logging.getLogger(__name__)


@dataclass
class CompactionResult:
    """压缩结果"""
    original_messages: int
    preserved_messages: int
    tokens_saved: int
    summary: str


class SimpleCompaction:
    """简单压缩策略 — 保留最近 N 条消息，其余压缩为摘要

    参考 kimi-cli 的 SimpleCompaction:
    1. 保留最近 2 条消息 (可配置)
    2. 其余消息 → LLM 摘要
    3. 替换历史为: 摘要消息 + 保留消息
    """

    def __init__(self, llm: BaseLLMProvider, max_preserved: int = 2):
        self.llm = llm
        self.max_preserved = max_preserved

    def compact(self, context: ConversationContext) -> CompactionResult:
        """执行压缩"""
        history = context.history
        if len(history) <= self.max_preserved:
            return CompactionResult(
                original_messages=len(history),
                preserved_messages=len(history),
                tokens_saved=0,
                summary="No compaction needed"
            )

        # 1. 分割：待压缩 vs 保留
        to_compact = history[:-self.max_preserved]
        preserved = history[-self.max_preserved:]

        # 2. 格式化待压缩消息
        compact_text = self._format_messages_for_compaction(to_compact)

        # 3. 调用 LLM 生成摘要
        summary = self._generate_summary(compact_text)

        # 4. 替换历史
        context.clear()
        context.append_message(Message(
            role="system",
            content=f"<context_summary>\n{summary}\n</context_summary>"
        ))
        for msg in preserved:
            context.append_message(msg)

        return CompactionResult(
            original_messages=len(history),
            preserved_messages=len(preserved) + 1,  # +1 for summary
            tokens_saved=len(compact_text) // 4,  # Rough estimate
            summary=summary
        )

    def _format_messages_for_compaction(self, messages: list[Message]) -> str:
        """格式化消息列表为压缩提示词"""
        lines = []
        for i, msg in enumerate(messages):
            lines.append(f"## Message {i+1} ({msg.role})")
            if msg.content:
                lines.append(msg.content[:500])  # Truncate long content
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    lines.append(f"Tool: {tc.name}({tc.arguments[:100]})")
            lines.append("")
        return "\n".join(lines)

    def _generate_summary(self, compact_text: str) -> str:
        """调用 LLM 生成摘要"""
        prompt = f"""Summarize the following conversation history for context preservation.

Focus on:
- Current task state and progress
- Key decisions made
- Errors encountered and solutions attempted
- Important context for continuing the task

Remove:
- Redundant explanations
- Failed attempts (unless they provide learning)
- Verbose code blocks (keep signatures and key logic only)

Conversation history:
{compact_text}

Output a concise summary (max 500 tokens):"""

        response = self.llm.chat(
            messages=[Message(role="user", content=prompt)],
            temperature=0.3,
            max_tokens=600
        )

        return response.content or "Summary generation failed"


def should_auto_compact(
    token_count: int,
    max_context_size: int,
    trigger_ratio: float = 0.85,
    reserved_context_size: int = 20000,
) -> bool:
    """判断是否需要自动压缩

    参考 kimi-cli 的触发条件:
    1. 当前 token 数 >= 最大值 * 触发比例 (85%)
    2. 或 当前 token + 预留空间 >= 最大值

    Args:
        token_count: 当前 token 数
        max_context_size: 模型最大上下文长度
        trigger_ratio: 触发比例 (默认 0.85)
        reserved_context_size: 预留空间 (默认 20000，用于工具结果等)

    Returns:
        是否需要压缩
    """
    return (
        token_count >= max_context_size * trigger_ratio
        or token_count + reserved_context_size >= max_context_size
    )
```

#### 2.2 集成到 Agent 循环

**在 GuardianSoul/TuningAgent 循环开始处**:

```python
# 每个 step 开始前检查
if should_auto_compact(
    token_count=self.context.estimated_tokens,
    max_context_size=128000,  # qwen3.6-plus context window
    trigger_ratio=0.85
):
    compactor = SimpleCompaction(self.llm, max_preserved=3)
    result = compactor.compact(self.context)
    self._broadcast(ai_decision=f"Context compacted: {result.original_messages} → {result.preserved_messages} messages")
```

---

### Phase 3: Web Chat 模式切换

#### 3.1 模式设计

**三种模式**:

| 模式 | 说明 | 工具调用 | 适用场景 |
|------|------|---------|---------|
| `chat` | 简单对话 | ❌ 无 | 快速问答，不涉及项目文件 |
| `agent` | 智能体 | ✅ 5个工具 | 需要读取项目、执行命令 |
| `auto` | 自动判断 | 动态 | 根据用户消息内容自动选择 |

**自动模式判断逻辑** (参考 kimi-cli 的 Plan Mode 动态注入):

```python
def detect_mode(user_message: str) -> str:
    """自动检测应该使用哪种模式"""
    agent_keywords = [
        "读取", "查看", "分析", "执行", "运行",
        "文件", "目录", "输出", "日志",
        "read", "execute", "run", "file", "output"
    ]
    if any(kw in user_message.lower() for kw in agent_keywords):
        return "agent"
    return "chat"
```

#### 3.2 前端设计

**UI 元素**:

```html
<div class="chat-mode-selector">
    <button class="mode-btn active" data-mode="auto">🤖 自动</button>
    <button class="mode-btn" data-mode="chat">💬 纯聊</button>
    <button class="mode-btn" data-mode="agent">🔧 Agent</button>
</div>

<div class="chat-input-wrapper">
    <textarea id="chatInput" placeholder="输入消息..."></textarea>
    <button id="sendBtn">发送</button>
</div>
```

**模式切换时显示提示**:

```javascript
// Agent 模式提示
if (currentMode === 'agent') {
    showInfo("Agent 模式已启用。我可以读取项目文件、执行命令、分析输出。");
}
```

#### 3.3 后端实现

**修改 `web_api.py` 的 `_respond_directly()`**:

```python
async def _respond_directly(
    project_id: str,
    user_message: str,
    mode: str = "auto",  # 新增参数
    image_data: str = None,
    image_name: str = ""
):
    """响应 Web Chat 消息"""

    # 自动模式检测
    if mode == "auto":
        mode = detect_mode(user_message)

    # Chat 模式: 简单对话，不调用工具
    if mode == "chat":
        response = llm.chat(
            messages=[Message(role="user", content=user_message)],
            temperature=0.7,
            max_tokens=2000
        )
        await hub.ai_decision(project_id, response.content, confidence=0.8)
        return

    # Agent 模式: 完整 Agent Loop
    # ... (现有代码，包含 5 个工具)
```

**WebSocket 消息格式更新**:

```typescript
// 前端发送
{
    type: "user_message",
    content: "读取最新的论文生成日志",
    mode: "auto"  // 新增字段
}
```

---

### Phase 4: 动态注入 (Dynamic Injection)

#### 4.1 动态注入系统

**新建 `core/dynamic_injection.py`**:

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class DynamicInjection(ABC):
    """动态注入 — 在每次 LLM 调用前注入额外上下文

    参考 kimi-cli:
    - Plan Mode Provider: 周期性提醒模型当前处于规划模式
    - YOLO Mode Provider: 一次性注入说明非交互模式
    """

    @abstractmethod
    def get_injection(self, context: dict) -> Optional[str]:
        """返回要注入的内容，或 None 表示不注入"""
        pass

    @property
    @abstractmethod
    def is_one_time(self) -> bool:
        """是否一次性注入（注入后自动禁用）"""
        pass


class ProjectContextInjection(DynamicInjection):
    """项目上下文注入 — 在 Agent 循环中周期性提醒当前项目状态"""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self._injected = False

    def get_injection(self, context: dict) -> Optional[str]:
        # 只在前 3 步注入，避免重复
        if context.get("step", 0) >= 3:
            return None

        return f"""<project_context>
Project ID: {self.project_id}
Current module: {context.get('current_module', 'unknown')}
Pipeline status: {context.get('pipeline_status', 'unknown')}
Step: {context.get('step', 0)}
</project_context>"""

    @property
    def is_one_time(self) -> bool:
        return False


class AgentModeInjection(DynamicInjection):
    """Agent 模式注入 — 提醒模型当前处于 Agent 模式，可以调用工具"""

    def __init__(self):
        self._injected = False

    def get_injection(self, context: dict) -> Optional[str]:
        if self._injected:
            return None

        self._injected = True
        return """<agent_mode>
You are in AGENT mode with tool-calling capabilities.
You can read files, execute commands, and analyze project outputs.
When the user asks "can you execute code", answer YES and demonstrate it.
</agent_mode>"""

    @property
    def is_one_time(self) -> bool:
        return True
```

#### 4.2 集成到 Agent 循环

**在 GuardianSoul/TuningAgent 中**:

```python
def __init__(self, ...):
    self.dynamic_injections: list[DynamicInjection] = [
        ProjectContextInjection(project_id),
        AgentModeInjection(),
    ]

async def _step(self):
    # 1. 收集动态注入
    injections = []
    for injection in self.dynamic_injections:
        content = injection.get_injection({
            "step": self.step,
            "current_module": self.current_module,
            "pipeline_status": self.pipeline_status,
        })
        if content:
            injections.append(Message(role="system", content=content))

    # 2. 构建最终消息列表
    messages = [
        Message(role="system", content=self.system_prompt),
        *injections,  # 动态注入
        *self.context.get_normalized_history()
    ]

    # 3. 调用 LLM
    response = self.llm.chat(messages, tools=self.tools)

    # 4. 清理一次性注入
    self.dynamic_injections = [
        inj for inj in self.dynamic_injections
        if not inj.is_one_time
    ]
```

---

## 实施计划

| 阶段 | 文件 | 工作量 | 优先级 |
|------|------|--------|--------|
| **Phase 1: 记忆系统** | `core/context.py` (新建) | 中 | 🔴 高 |
| | `core/guardian_soul.py` (修改) | 中 | 🔴 高 |
| | `core/tuning_agent.py` (修改) | 中 | 🟡 中 |
| **Phase 2: 上下文压缩** | `core/compaction.py` (新建) | 高 | 🟡 中 |
| | 集成到 Agent 循环 | 中 | 🟡 中 |
| **Phase 3: Web Chat 模式** | `web_api.py` (修改) | 低 | 🟢 低 |
| | `static/index.html` (修改) | 低 | 🟢 低 |
| **Phase 4: 动态注入** | `core/dynamic_injection.py` (新建) | 低 | 🟢 低 |

**建议顺序**:
1. Phase 1 (记忆系统) — 最关键，解决持久化问题
2. Phase 3 (Web Chat 模式) — 用户直接可感知的改进
3. Phase 2 (上下文压缩) — 长对话场景必备
4. Phase 4 (动态注入) — 锦上添花

---

## 测试计划

### 记忆系统测试

```python
def test_context_persistence():
    """测试消息持久化和加载"""
    ctx = ConversationContext(Path("test_context.jsonl"))

    # 追加消息
    ctx.append_message(Message(role="user", content="Hello"))
    ctx.append_message(Message(role="assistant", content="Hi there"))

    # 创建检查点
    cp_id = ctx.create_checkpoint({"phase": "test"})

    # 重新加载
    ctx2 = ConversationContext(Path("test_context.jsonl"))
    assert len(ctx2.history) == 2
    assert cp_id in ctx2._checkpoints

    # 回滚
    ctx2.revert_to(cp_id)
    assert len(ctx2.history) == 0
```

### 压缩系统测试

```python
def test_auto_compaction():
    """测试自动压缩触发"""
    assert should_auto_compact(
        token_count=100000,
        max_context_size=128000,
        trigger_ratio=0.85
    ) == True  # 100k > 128k * 0.85

    assert should_auto_compact(
        token_count=80000,
        max_context_size=128000,
        trigger_ratio=0.85
    ) == False  # 80k < 128k * 0.85
```

### Web Chat 模式测试

```python
def test_mode_detection():
    """测试自动模式检测"""
    assert detect_mode("读取最新的日志文件") == "agent"
    assert detect_mode("今天天气怎么样") == "chat"
    assert detect_mode("analyze the output files") == "agent"
```

---

## 参考文件

- `D:/文件综述智能体/kimi-cli/src/kimi_cli/soul/context.py` — Context 类
- `D:/文件综述智能体/kimi-cli/src/kimi_cli/soul/compaction.py` — SimpleCompaction
- `D:/文件综述智能体/kimi-cli/src/kimi_cli/soul/dynamic_injection.py` — DynamicInjection
- `D:/文件综述智能体/kimi-cli/src/kimi_cli/soul/kimisoul.py` — Agent 主循环
- `D:/文件综述智能体/kimi-cli/packages/kosong/src/kosong/message.py` — Message 类型
