# Orchestrator State
_最后同步：2026-04-14T04:30:00Z_

## 全局进度看板

| 阶段 | 状态 | 完成/总计 | 备注 |
|------|------|----------|------|
| 基础架构 | done | 12/12 | Pipeline + Guardian + 调优 Agent |
| 核心模块 | done | 12/12 | 含 paper_generator v2.0.0 |
| 论文生成器 | done | 2/2 | Markdown+LaTeX 双输出 + fpdf2 PDF |
| 状态徽章 | done | 3/3 | tuning_count, paper_status, 前端渲染 |
| 调优 Agent 增强 | pending | 0/5 | 参考 AI-Scientist-v2 BFTS 架构 |
| 工作流模式 | active | 1/3 | Automated ✅ HITL/Pluggable ⚠️ |
| 端到端测试 | active | 5/8 | 部分模块失败，需修复 |

## 当前活跃任务

### 任务 1: 调优 Agent 架构增强（参考 AI-Scientist-v2）
- **状态**: pending
- **参考**: `D:/文件综述智能体/AI-Scientist-v2/`
- **待实现**: Journal 系统、分阶段调优、图表分析、自评机制、参数快照
- **优先级**: 高

### 任务 2: topic_modeler 模块修复
- **状态**: in-progress（长期）
- **问题**: 数据稀疏时 "cannot infer dimensions from zero sized index arrays"
- **影响**: tsr_ranker 下游模块

## 最近完成任务（最近 5 条）

| 时间 | 任务 | 状态 | 备注 |
|------|------|------|------|
| 2026-04-14 | 论文生成器 v2.0.0 | done | Markdown+LaTeX 双输出 + fpdf2 PDF |
| 2026-04-14 | 状态徽章系统 | done | 调优次数 + 论文状态 + 前端渲染 |
| 2026-04-13 | 调优 Agent 基础版 | done | 13 工具 + agent loop + WebSocket |
| 2026-04-13 | 论文生成器 v1.0.0 | done | LaTeX 章节 + LLM 生成 |
| 2026-04-09 | Guardian Agent 系统 | done | 12/12 测试通过 |

## 决策点

### 决策: 调优 Agent 增强方案

**背景**: 当前调优 Agent 是简单的 agent loop，缺少 AI-Scientist-v2 的智能调优特性。

**AI-Scientist-v2 核心参考**:
- 四阶段 BFTS：初始实现(20 iter) → 基线调优(12) → 创造性研究(12) → 消融研究(18)
- Journal 记忆：LLM 摘要所有成功/失败经验
- VLM 图表分析：直接看图表返回质量反馈
- LLM 自评完成度：判断"调好了没"
- 子阶段目标：LLM 根据进度生成下一步目标
- 最佳节点传递：保留最优结果继续优化

**建议方案**: 渐进式增强
1. 先添加 Journal 系统（记录调优历史）
2. 添加图表分析工具（evaluate_figure）
3. 添加自评循环（每轮结束后评估）
4. 分阶段流程（分析→调参→验证→报告）

**待决策者**: 用户确认增强优先级

## 关键指标

### 新增功能统计
- **paper_generator**: v2.0.0, Markdown+LaTeX+PDF 三输出
- **tuning_agent**: 13 工具, 最大 30 步
- **build_pdf.py**: 独立脚本, CJK 支持, fpdf2 渲染
- **状态徽章**: 2 个字段 (tuning_count, paper_status)

### 依赖变更
- +fpdf2>=2.8（PDF 生成）

## 风险项

### 中风险
1. **调优 Agent 缺少记忆** — 可能重复尝试相同参数
2. **fpdf2 表格渲染** — 复杂表格可能溢出列宽
3. **CJK 字体** — Windows 系统字体可能在不同机器上表现不同

### 低风险
4. **LaTeX 转换精度** — Markdown→LaTeX 最佳努力，复杂格式可能丢失

## 下一步建议

### 立即（调优 Agent 增强）

1. **添加 Journal 系统**
   - `workspace/tuning_journal.json` 记录每次调优操作
   - 字段：timestamp, action, params_before, params_after, result, assessment

2. **添加 evaluate_figure 工具**
   - LLM 分析 PNG 图表质量
   - 返回：清晰度、数据完整性、是否需要重新生成

3. **添加自评机制**
   - 每轮结束 LLM 评估："结果是否改善？是否继续？"
   - 自动决定继续或结束调优

4. **分阶段调优流程**
   - Stage 1: 质量评估（读输出、分析图表）
   - Stage 2: 参数调整（确定调整策略）
   - Stage 3: 重跑验证（执行并对比）
   - Stage 4: 报告生成（总结调优效果）

### 本周

5. **端到端测试** — 完整 pipeline + 调优 + 论文
6. **HITL UI 完善**
