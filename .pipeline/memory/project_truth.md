# Project Truth
_最后同步：2026-04-14T04:30:00Z_

## 项目概况

**项目类型**: Bibliometrics Agent - 文献计量智能体开发项目

**核心功能**: 基于 LLM 的全自动文献计量分析系统，用户输入研究领域，系统自动生成查询、抓取论文、执行分析、生成可视化和论文。

**技术栈**: Python, FastAPI, WebSocket, LLM (OpenRouter/Qwen 3.6-plus), fpdf2, spaCy, scikit-learn

## 当前阶段: `feature_development`

**总体进度**: 核心功能 + 论文生成 + 调优 Agent 已实现，准备优化调优架构

### 主要里程碑

| 阶段 | 状态 | 完成度 | 备注 |
|------|------|--------|------|
| 基础架构设计 | ✅ 完成 | 100% | Pipeline + Guardian Agent 混合架构 |
| 12 个核心模块 | ✅ 完成 | 100% | 含 paper_generator (v2.0.0) |
| Guardian Agent 系统 | ✅ 完成 | 100% | 10 个 Guardian + GuardianSoul LLM 代理 |
| 调优 Agent | ✅ 基础完成 | 70% | 13 工具 + agent loop，待增强（见下文） |
| 论文生成器 | ✅ 完成 | 100% | Markdown + LaTeX 双输出 + fpdf2 PDF |
| 状态徽章系统 | ✅ 完成 | 100% | 调优次数 + 论文状态持久化 |
| 前端界面 | ✅ 完成 | 100% | 项目显示、AI 可视化、徽章、调优/论文按钮 |

## 已确认决策

1. **2026-04-08** - 采用固定 Pipeline + Guardian Agent 混合架构
2. **2026-04-08** - GuardianSoul 使用 LLM 进行错误分析和修复
3. **2026-04-09** - 实现三种工作流模式（Automated/HITL/Pluggable）
4. **2026-04-13** - 调优 Agent 复用 GuardianSoul agent loop 架构，独立工具集
5. **2026-04-13** - 论文生成器 LLM 输出 Markdown，同时转换为 LaTeX + PDF
6. **2026-04-13** - PDF 生成使用 fpdf2（纯 Python），不依赖 xelatex
7. **2026-04-14** - 调优次数和论文状态持久化到 state.json，前端显示徽章
8. **2026-04-14** - 参考 AI-Scientist-v2 的四阶段 BFTS 架构增强调优 Agent

## 核心功能进展

### 1. 调优 Agent (core/tuning_agent.py, 789 行)

**当前能力**:
- ✅ 13 个工具：read_file, write_file, run_command, list_project_outputs, read_module_output, get_module_config, adjust_config, rerun_module 等
- ✅ Agent loop：LLM 驱动，最大 30 步
- ✅ 用户聊天干预（WebSocket 双向通信）
- ✅ 调优次数持久化到 state.json
- ✅ 前端实时显示调优过程

**待增强（参考 AI-Scientist-v2）**:
- ⚠️ 缺少记忆/Journal 系统 — 无法总结"尝试了什么、什么有效"
- ⚠️ 缺少分阶段目标 — 没有"分析→调参→验证→报告"的明确流程
- ⚠️ 缺少自评机制 — LLM 不判断结果是否够好
- ⚠️ 缺少图表分析 — 不分析输出图表质量

### 2. 论文生成器 (modules/paper_generator.py, v2.0.0)

**输出格式**:
- `sections/*.md` — LLM 生成的 Markdown 原始输出
- `main.tex` — 从 Markdown 自动转换的 LaTeX（可编辑）
- `main.pdf` — fpdf2 生成的 PDF（无需 xelatex）
- `refs/references.bib` — BibTeX 参考文献源文件
- `refs/references.txt` — 纯文本参考文献（PDF 用）
- `figures/*.png` — 从 visualizer 复制的图表

**Markdown → LaTeX 转换支持**:
- 标题 (# → \section), 表格 (pipe → tabular), 图片 (![] → \includegraphics)
- 列表 (- → \item), 行内格式 (**bold**, *italic*, [citation])

### 3. 状态徽章系统

- `state.json` 新增 `tuning_count` 和 `paper_status` 字段
- 前端项目卡片显示：🔧 已调优 N 次 | 📄 PDF已生成 | 📄 已生成初稿
- 项目详情头部同步显示徽章

### 4. PDF 生成脚本 (scripts/build_pdf.py)

- 独立脚本：`python scripts/build_pdf.py <input_dir> <output.pdf> [zh|en]`
- fpdf2 渲染 Markdown（标题、段落、表格、图片、列表）
- CJK 支持：优先 scripts/fonts/NotoSansSC，fallback 到 Windows SimHei/SimSun
- 120 秒超时保护

## AI-Scientist-v2 调优架构参考

### 核心设计（待借鉴到调优 Agent）

| 机制 | AI-Scientist-v2 做法 | 对我们的启示 |
|------|----------------------|-------------|
| **四阶段流水线** | 初始实现 → 基线调优 → 创造性研究 → 消融研究 | 调优可分阶段：质量评估 → 参数调整 → 重跑验证 → 报告生成 |
| **记忆系统 (Journal)** | 每次迭代生成摘要，记录成功/失败经验 | 调优 Agent 应维护 `tuning_journal.md`，避免重复尝试 |
| **VLM 图表分析** | GPT-4o 直接看图表返回反馈 | 让调优 Agent 分析 pipeline 输出的 PNG 图表质量 |
| **LLM 自评完成度** | LLM 判断阶段是否完成 | 调优 Agent 应自评"结果是否够好，是否继续调" |
| **子阶段目标生成** | LLM 根据进度生成下一步具体目标 | 每轮调优开始时 LLM 生成具体目标 |
| **最佳节点传递** | 每阶段最佳结果传给下一阶段 | 保留最佳参数配置，在之上继续优化 |

### 建议的增强方向

1. **Journal 系统**: 每次调优操作记录到 `workspace/tuning_journal.json`
2. **分阶段执行**: 调优 Agent 内部分 4 个阶段（分析→调参→重跑→报告）
3. **图表质量评估**: 添加 `analyze_figure` 工具，让 LLM 评估图表质量
4. **自评循环**: 每轮结束 LLM 评估是否需要继续
5. **参数快照**: 每次调优前保存当前配置快照，支持回滚

## 技术栈变更

| 变更 | 说明 |
|------|------|
| +fpdf2>=2.8 | PDF 生成（替代 xelatex 依赖） |
| +scripts/build_pdf.py | 独立 PDF 生成脚本 |
| state.json 扩展 | tuning_count, paper_status 字段 |
| Project 模型扩展 | tuning_count, paper_status 字段 |

## 当前阻塞项

无高优先级阻塞。主要工作是增强调优 Agent 的智能性。

## 下一步计划

### 短期（基于 AI-Scientist-v2 参考增强调优）

1. **添加 Journal 系统** — 调优记录持久化
2. **分阶段调优流程** — 分析→调参→验证→报告
3. **图表分析工具** — evaluate_figure 工具
4. **自评机制** — LLM 判断调优效果

### 中期

5. **端到端测试** — 完整 pipeline + 调优 + 论文生成
6. **HITL/Pluggable UI 完善**
