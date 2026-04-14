# Execution Context
_最后同步：2026-04-14T04:30:00Z_

## 当前任务

**ID:** task_tuning_agent_enhancement
**标题:** 调优 Agent 架构增强（参考 AI-Scientist-v2）
**状态:** pending
**优先级:** 高

## 任务背景

### 已完成工作

1. **调优 Agent 基础版** (`core/tuning_agent.py`, 789 行)
   - 13 个工具定义 + agent loop
   - 用户聊天干预 + WebSocket 广播
   - 调优次数持久化

2. **论文生成器** (`modules/paper_generator.py`, v2.0.0)
   - LLM 生成 Markdown → 自动转 LaTeX → fpdf2 生成 PDF
   - 双输出：main.tex + main.pdf

3. **状态徽章系统**
   - state.json: tuning_count, paper_status
   - 前端项目卡片和详情页徽章

### AI-Scientist-v2 调优架构参考

**来源**: `D:/文件综述智能体/AI-Scientist-v2/`

**核心设计**:

| 组件 | AI-Scientist-v2 | 文件位置 |
|------|-----------------|---------|
| 四阶段 BFTS | 初始实现→基线调优→创造性研究→消融研究 | `ai_scientist/treesearch/agent_manager.py` |
| Journal 记忆 | 每次迭代摘要所有成功/失败经验 | `ai_scientist/treesearch/journal.py` |
| VLM 图表分析 | GPT-4o 分析实验图表返回反馈 | `parallel_agent.py:_analyze_plots_with_vlm()` |
| LLM 自评 | 判断阶段是否完成 | `parallel_agent.py:_check_stage_completion()` |
| 子阶段目标 | LLM 生成下一步具体目标 | `agent_manager.py:_generate_substage_goal()` |
| 最佳节点传递 | 每阶段最优结果传给下一阶段 | `agent_manager.py:run()` |

**关键代码模式**:

```python
# Journal 系统 — 记忆摘要
class Journal:
    def generate_summary(self):
        # LLM 总结所有成功/失败实验
        # 返回 key_successes, common_failures, recommendations

# VLM 图表分析
def _analyze_plots_with_vlm(plot_paths):
    # 将图表图片 base64 编码发送给 VLM
    # 返回 plot_analyses + vlm_feedback_summary

# 自评完成度
def _check_stage_completion(journal, metrics):
    # LLM 评估: 数据覆盖度、收敛稳定性、图表质量
    # 返回 {is_complete: bool, reason: str, next_goals: list}

# 子阶段目标生成
def _generate_substage_goal(journal_summary, current_metrics):
    # LLM 根据进度和问题生成下一步目标
    # 返回 {goal: str, focus_areas: list, max_iters: int}
```

## 增强方案（渐进式）

### Phase 1: Journal 系统

**修改文件**: `core/tuning_agent.py`

在 `TuningAgent` 中添加：

```python
class TuningJournal:
    """调优操作记录，类似 AI-Scientist-v2 的 Journal"""
    entries: list[TuningEntry]  # 每次工具调用/决策记录

    def generate_summary(self, llm) -> str:
        """LLM 摘要所有调优操作：尝试了什么、什么有效、什么失败"""

    def get_best_config(self) -> dict:
        """返回效果最好的参数配置"""
```

**持久化**: `workspace/tuning_journal.json`

### Phase 2: 分阶段调优流程

在 `TuningAgent.activate()` 中实现四阶段：

| 阶段 | 目标 | 工具使用 | 最大步数 |
|------|------|---------|---------|
| 评估 | 读输出、分析图表质量 | read_module_output, list_project_outputs, evaluate_figure | 8 |
| 调整 | 确定参数调整策略 | get_module_config, analyze_quality(新增) | 5 |
| 验证 | 重跑模块、对比结果 | adjust_config, rerun_module, compare_results(新增) | 10 |
| 报告 | 总结调优效果 | write_analysis_report | 5 |

### Phase 3: 图表分析工具

新增工具 `evaluate_figure`：
- 读取 PNG 图片
- 将图片描述/特征发送给 LLM
- LLM 评估：图表是否清晰、数据是否完整、是否需要改进

### Phase 4: 自评机制

每轮调优结束后，LLM 自评：
- 参数调整是否改善了输出？
- 图表质量是否提升？
- 是否需要继续调优？
- 下一步应该关注什么？

## 相关文件

### 需要修改的文件
- `core/tuning_agent.py` — 主要修改（添加 Journal + 分阶段 + 自评）
- `configs/default.yaml` — tuning_agent 配置更新

### 参考文件
- `D:/文件综述智能体/AI-Scientist-v2/ai_scientist/treesearch/agent_manager.py` — 四阶段架构
- `D:/文件综述智能体/AI-Scientist-v2/ai_scientist/treesearch/journal.py` — Journal 系统
- `D:/文件综述智能体/AI-Scientist-v2/ai_scientist/treesearch/parallel_agent.py` — VLM 图表分析

## 成功标准

### 最低标准
- ✅ 调优 Agent 维护 Journal，不重复尝试
- ✅ 分阶段执行（至少评估→调整→验证）
- ✅ 每轮自评

### 理想标准
- ✅ 图表分析工具可用
- ✅ 参数快照和回滚
- ✅ 调优报告自动生成
