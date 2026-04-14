# 记忆与上下文管理系统实现总结

## 概述

本项目成功实现了完整的记忆系统和上下文管理功能，参考 kimi-cli 架构，为 GuardianSoul 和 TuningAgent 提供了持久化、自动压缩和动态注入能力。

---

## 已完成功能

### 1. 记忆系统基础 (core/context.py)

**ConversationContext 类**提供：

- **JSONL 持久化**：每条消息追加写入，支持崩溃恢复
- **检查点机制**：可回滚到历史状态
- **Token 计数**：实时估算上下文使用量
- **消息规范化**：合并相邻同角色消息（OpenAI API 要求）

**关键方法**：
```python
context = ConversationContext(
    context_file=Path("guardian_context.jsonl"),
    max_context_tokens=128000,  # 128K token 限制
    compaction_trigger_ratio=0.85  # 85% 时触发压缩
)

context.append_message(message, token_estimate)
checkpoint_id = context.create_checkpoint(metadata)
context.revert_to(checkpoint_id)
messages = context.get_normalized_history()
```

### 2. 自动上下文压缩

**触发条件**：当 token 使用量达到 128K 的 85% (约 109K tokens)

**压缩流程**：
1. 保留 system 消息和最近 15 条消息
2. 将中间消息发送给 LLM 生成摘要
3. 摘要作为 user 消息插入："Context Summary - Earlier Conversation"
4. 写入压缩标记到 JSONL（记录节省的 token 数）

**LLM 摘要提示词**：
```
Summarize the conversation history concisely. Focus on:
1. Key decisions made
2. Important findings from tool calls
3. Errors encountered and resolutions
4. Current progress and next steps
```

### 3. 动态上下文注入 (core/context_injector.py)

**DynamicInjector 类**每 10 次 LLM 调用自动注入：

- **项目进度**：`X/Y modules completed, Z failed`
- **最近文件**：最近 10 个修改的输出文件
- **错误摘要**：最近 5 条 ERROR 日志

**注入时机**：在 `should_compact()` 检查之前

**代码示例**：
```python
injector = DynamicInjector(
    workspace_dir=workspace_dir,
    project_id=project_id,
    mode="agent",
    update_interval=10
)

update_msg = injector.inject_context_update()
if update_msg:
    context.append_message(update_msg)
```

### 4. Web Chat 模式切换

**前端 UI** (`static/index.html`)：
- 三个模式按钮：**自动** / **对话** / **Agent**
- 当前模式高亮显示
- 不同模式下 placeholder 提示不同

**后端逻辑** (`web_api.py`)：
- 接收 `mode` 参数："auto" | "chat" | "agent"
- "auto" 模式根据关键词自动判断
- "chat" 模式：纯对话，不调用工具
- "agent" 模式：主动使用工具执行任务

**自动检测关键词**：
```python
agent_keywords = [
    "读取", "查看", "分析", "执行", "运行", "文件", "目录", "输出", "日志",
    "read", "execute", "run", "file", "output", "analyze", "analysis"
]
```

### 5. GuardianSoul 集成

**修改文件**：`core/guardian_soul.py`

**关键改动**：
- `self.messages` → `self.context: ConversationContext`
- 激活时创建初始检查点
- Agent loop 中集成压缩和注入
- 持久化到 `workspace/guardian_context.jsonl`

**Agent Loop 流程**：
```python
for step in range(self.max_steps):
    # 1. 动态注入（每 10 次）
    update_msg = self.injector.inject_context_update()
    if update_msg:
        self.context.append_message(update_msg)

    # 2. 检查是否需要压缩
    if self.context.should_compact():
        self.context.compact(self.llm, preserve_recent=15)

    # 3. 调用 LLM
    response = self.llm.chat(
        messages=self.context.get_normalized_history(),
        tools=GUARDIAN_TOOL_DEFS,
        temperature=0.2
    )
```

### 6. TuningAgent 集成

**修改文件**：`core/tuning_agent.py`

**相同改动**：
- 替换消息列表为 `ConversationContext`
- 集成压缩和注入
- 持久化到 `workspace/tuning_context.jsonl`

---

## 技术细节

### Token 估算策略

**粗略估算**：`len(text.split()) * 2`

**保守策略**：
- 英文单词约 1.3 tokens
- 中文约 2-3 tokens
- 使用 2x 安全系数

### JSONL 持久化格式

**消息格式**：
```json
{"role": "user", "content": "Hello"}
{"role": "assistant", "content": "Hi!", "tool_calls": [...]}
{"role": "tool", "content": "...", "name": "read_file", "tool_call_id": "call_123"}
```

**检查点格式**：
```json
{
  "type": "checkpoint",
  "checkpoint": {
    "id": 1,
    "timestamp": "2026-04-14T12:00:00",
    "message_count": 10,
    "token_count": 5000,
    "metadata": {"phase": "initial"}
  },
  "message_index": 10
}
```

**压缩标记格式**：
```json
{
  "type": "compaction",
  "timestamp": "2026-04-14T12:30:00",
  "messages_removed": 25,
  "tokens_saved": 15000
}
```

### 压缩效果预估

**假设场景**：
- Guardian 激活 50 步，每步 3 条消息（assistant + tool + result）
- 总消息数：~150 条
- 平均每条消息 500 tokens
- 总 tokens：~75K

**压缩触发**：
- 首次达到 109K tokens 时触发
- 压缩前 120 条消息，保留最近 15 条
- 生成摘要约 200 tokens
- 节省：~52K tokens

**结果**：上下文从 109K 降至 ~57K，避免达到 128K 限制

---

## 文件清单

### 新建文件

| 文件 | 行数 | 说明 |
|------|------|------|
| `core/context.py` | 315 | 记忆系统核心类 |
| `core/context_injector.py` | 190 | 动态注入器 |

### 修改文件

| 文件 | 修改内容 |
|------|----------|
| `core/guardian_soul.py` | 集成 ConversationContext + 压缩 + 注入 |
| `core/tuning_agent.py` | 集成 ConversationContext + 压缩 + 注入 |
| `web_api.py` | 添加 mode 参数支持 |
| `static/index.html` | 添加模式切换 UI |

---

## 测试建议

### 单元测试

```python
def test_context_persistence():
    context = ConversationContext(context_file=Path("test.jsonl"))
    context.append_message(Message(role="user", content="Hello"))
    context.append_message(Message(role="assistant", content="Hi"))

    # 重新加载
    context2 = ConversationContext(context_file=Path("test.jsonl"))
    assert len(context2.history) == 2

def test_compaction():
    context = ConversationContext(max_context_tokens=1000)
    # 添加大量消息...
    assert context.should_compact()
    saved = context.compact(mock_llm_provider)
    assert saved > 0
```

### 集成测试

1. 启动项目，运行 pipeline 直到 Guardian 激活
2. 检查 `workspace/guardian_context.jsonl` 是否生成
3. 观察 agent 是否在接近 128K 时自动压缩
4. 查看 JSONL 中的 compaction 标记

---

## 后续优化方向

### 短期优化

1. **精确 Token 计数**：使用 tiktoken 库替代估算
2. **分层压缩**：保留关键决策点，而非仅保留最近消息
3. **压缩策略可配置**：允许用户自定义保留策略

### 长期优化

1. **向量化检索**：将历史消息向量化，按需检索相关上下文
2. **记忆重要性评分**：LLM 标注消息重要性，压缩时优先保留
3. **跨会话记忆**：项目级别的长期记忆，跨多次运行共享

---

## 总结

本项目成功实现了生产级的记忆和上下文管理系统：

✅ **持久化**：JSONL 追加写入，支持崩溃恢复
✅ **检查点**：可回滚到任意历史状态
✅ **自动压缩**：128K token 限制，85% 触发压缩
✅ **动态注入**：每 10 次 LLM 调用更新项目状态
✅ **模式切换**：Web Chat 支持 chat/agent/auto 三种模式

**技术亮点**：
- 参考了 kimi-cli 的成熟架构
- 压缩摘要由 LLM 生成，保留关键信息
- 动态注入确保 LLM 始终掌握最新项目状态
- 完全集成到 GuardianSoul 和 TuningAgent

**影响范围**：
- GuardianSoul：错误诊断更可靠（不丢失上下文）
- TuningAgent：优化分析更连贯（记住历史决策）
- Web Chat：用户体验更灵活（模式切换）
