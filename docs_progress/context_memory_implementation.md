# 会话记忆系统实现总结

## 功能概述

为 Bibliometrics Agent 的智能体（TuningAgent 和 GuardianSoul）实现了完整的会话记忆系统，支持：
1. 对话历史持久化
2. 自动压缩与历史轮转
3. 会话续接（中断后恢复对话）
4. 灵活的配置选项

---

## 核心修改

### 1. ConversationContext 增强 (`core/context.py`)

#### 新增参数
```python
def __init__(
    self,
    context_file: Path,
    max_history_files: int = 10,           # 最多保留多少个 .old 文件
    preserve_recent_messages: int = 50,    # 压缩时保留最近多少条消息
    ...
)
```

#### 历史轮转机制
- **递增编号**：每次压缩时，`.jsonl` → `.jsonl.old{N}`（N 递增）
- **自动清理**：超过 `max_history_files` 时删除最旧的文件
- **完整历史**：用户可以查看 `.old1`、`.old2` 等文件恢复早期对话

```python
def _rotate_history_files(self):
    """轮转历史文件 — 当前文件 → .old{count}"""
    # 扫描现有历史文件，确定下一个编号
    next_num = self._compaction_count + 1

    # 重命名当前文件
    self.context_file.rename(f"{stem}.jsonl.old{next_num}")

    # 清理超过上限的旧文件
    # ...
```

#### 智能压缩
```python
def compact(self, llm_provider, preserve_recent=None):
    """压缩上下文 — 保留最近 N 条消息，其余用摘要替代"""
    if preserve_recent is None:
        preserve_recent = self.preserve_recent_messages  # 使用配置值

    # 轮转历史文件
    self._rotate_history_files()

    # 保留最近 N 条消息
    recent_messages = self._history[-preserve_recent:]

    # 用 LLM 生成摘要替代早期对话
    summary = self._generate_compaction_summary(llm_provider, to_compact)

    # ...
```

---

### 2. TuningAgent 会话续接 (`core/tuning_agent.py`)

#### 新增 `new_session` 参数
```python
def activate(self, initial_message: str = "", new_session: bool = False):
    """启动调优会话

    Args:
        initial_message: 初始用户消息
        new_session: True = 新会话（清空历史），False = 续接历史
    """
    if new_session or len(self.context) == 0:
        # 清空历史，重新开始
        self.context.clear()
        self.context.append_message(Message(role="system", content=system_prompt))
    else:
        # 续接历史
        logger.info("Continuing existing session with %d messages", len(self.context))
        # 更新系统提示词（保留历史消息）
        # ...
```

#### 用户交互流程
1. **首次调优**：用户点击"调优" → 选择"新会话" → 创建 `tuning_context.jsonl`
2. **中断后续接**：用户再次点击"调优" → 选择"续接会话" → 加载历史继续对话
3. **自动压缩**：当上下文接近 token 限制时，自动压缩并轮转历史文件

---

### 3. 配置文件 (`configs/default.yaml`)

```yaml
modules:
  tuning_agent:
    max_steps: 30                          # Agent 最大步数（-1 = 无限）
    max_history_files: 10                  # 最多保留多少个 .old 文件
    preserve_recent_messages: 20           # 压缩时保留最近多少条消息

  chat_agent:
    max_steps: -1
    max_history_files: 10
    preserve_recent_messages: 50           # Chat Agent 默认保留更多消息（50）

pipeline:
  automated:
    guardian_max_steps: 50                 # GuardianSoul 最大步数
```

---

### 4. 前端交互 (`static/index.html`)

```javascript
async function startTuning(projectId) {
    // 弹窗询问用户
    const shouldStartNew = confirm(
        '是否开启新的调优会话？\n\n' +
        '确定 = 新会话（清空历史）\n' +
        '取消 = 续接上次会话'
    );

    const res = await fetch(`${API_BASE}/projects/${projectId}/tune`, {
        method: 'POST',
        body: JSON.stringify({
            message: '',
            new_session: shouldStartNew
        })
    });

    // 显示会话类型
    const sessionType = shouldStartNew ? '新会话' : '续接会话';
    // ...
}
```

---

## 工作流程示例

### 场景 1：首次调优
```
用户：点击"调优" → 选择"新会话"
系统：
  1. 创建 tuning_context.jsonl
  2. 添加系统提示词和初始用户消息
  3. Agent 开始分析管道输出

AI：正在分析 state.json 和输出文件...
用户：请检查 topic_modeler 的结果
AI：已检查，发现主题质量良好...
```

### 场景 2：中断后续接
```
（上次会话在第 15 步中断）

用户：点击"调优" → 选择"续接会话"
系统：
  1. 加载 tuning_context.jsonl
  2. 恢复 15 条历史消息
  3. 继续对话

AI：（知道之前的对话内容）之前我们在检查 topic_modeler...
用户：继续分析其他模块
AI：好的，接下来检查 burst_detector...
```

### 场景 3：历史压缩
```
上下文达到 85% token 限制（~108K tokens）

系统：
  1. 轮转：tuning_context.jsonl → tuning_context.jsonl.old1
  2. 生成摘要："早期对话：检查了 topic_modeler、burst_detector，调整了参数..."
  3. 保留最近 20 条消息
  4. 重建上下文：系统消息 + 摘要 + 最近 20 条消息

结果：上下文从 108K tokens 压缩到 ~30K tokens
```

### 场景 4：历史轮转
```
第1次压缩：tuning_context.jsonl → .old1
第2次压缩：tuning_context.jsonl → .old2
...
第11次压缩：
  - 删除 .old1（最旧）
  - 保留 .old2 ~ .old10（最新 10 个）
  - 创建 .old11
```

---

## 技术细节

### 1. JSONL 持久化格式
```
{"role": "system", "content": "You are a helpful assistant..."}
{"role": "user", "content": "Hello, my name is Alice."}
{"role": "assistant", "content": "Hello Alice! How can I help you?"}
{"type": "checkpoint", "checkpoint": {"id": 1, "timestamp": "2026-04-14T..."}}
{"type": "compaction", "messages_removed": 30, "tokens_saved": 50000}
```

### 2. 文件结构
```
workspaces/project_abc123/
├── checkpoints/
│   └── state.json
├── outputs/
│   └── ...
└── workspace/
    ├── tuning_context.jsonl          # 当前会话
    ├── tuning_context.jsonl.old1     # 第1次压缩的历史
    ├── tuning_context.jsonl.old2     # 第2次压缩的历史
    └── guardian_context.jsonl        # GuardianSoul 会话
```

### 3. GuardianSoul vs TuningAgent
| 特性 | GuardianSoul | TuningAgent |
|------|-------------|-------------|
| 会话性质 | 每次错误重新开始 | 用户交互式，需记忆 |
| `clear()` 调用 | 每次激活都清空 | 仅 `new_session=True` 时清空 |
| 历史续接 | 不需要 | 核心功能 |
| 历史轮转 | 支持（备用） | 完整支持 |

---

## 测试验证

已通过 `test_context_memory.py` 测试：
- ✅ 历史轮转：`.old1`、`.old2` 递增创建，超过上限自动删除
- ✅ 会话续接：重新加载后正确恢复历史消息
- ✅ 配置读取：`preserve_recent_messages` 从 YAML 正确读取
- ✅ 边界情况：空会话、单个消息、大量消息

---

## 配置说明

### 调优场景推荐配置

**快速迭代**（对话较少）：
```yaml
tuning_agent:
  preserve_recent_messages: 10
  max_history_files: 5
```

**深度优化**（长对话）：
```yaml
tuning_agent:
  preserve_recent_messages: 50
  max_history_files: 20
```

**无限记忆**（不压缩）：
```yaml
tuning_agent:
  preserve_recent_messages: 999999  # 保留所有消息
```

---

## 用户可见改进

1. **对话连贯性**：AI 记得之前的对话内容，可以引用之前提到的问题
2. **中断恢复**：会话意外中断后，可以继续之前的优化工作
3. **历史可追溯**：`.old` 文件完整保留，可以回溯早期对话
4. **灵活选择**：用户可以选择"新会话"或"续接会话"
5. **透明提示**：前端显示"新会话"或"续接会话"标签

---

## 实现文件清单

| 文件 | 修改内容 |
|------|---------|
| `core/context.py` | 添加 `max_history_files`、`preserve_recent_messages` 参数，实现 `_rotate_history_files()` |
| `core/tuning_agent.py` | 添加 `new_session` 参数，实现会话续接逻辑 |
| `core/guardian_soul.py` | 添加配置参数支持 |
| `web_api.py` | 读取配置，传递 `preserve_recent_messages` |
| `core/orchestrator.py` | 读取配置，传递给 GuardianSoul |
| `static/index.html` | 添加用户选择弹窗，显示会话类型 |
| `configs/default.yaml` | 添加 `max_history_files`、`preserve_recent_messages` 配置 |
| `test_context_memory.py` | 测试历史轮转、会话续接、配置读取 |

---

## 后续优化建议

1. **压缩策略优化**：可以根据消息重要性（如包含决策、错误修复）智能保留，而不仅仅是时间顺序
2. **摘要质量**：使用更强的 LLM 模型生成摘要，确保关键信息不丢失
3. **用户可见历史**：前端添加"查看历史会话"功能，浏览 `.old` 文件
4. **自动恢复**：检测到中断会话时，自动提示用户"继续上次会话？"
5. **Token 监控**：前端显示当前上下文 token 使用量，提前预警即将压缩

---

**实现时间**：2026-04-14
**版本**：v1.0
**状态**：✅ 已完成并测试通过
