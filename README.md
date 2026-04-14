# 文献计量智能体 (Bibliometrics Agent)

**Fully automated bibliometric analysis powered by LLM — 用户输入研究领域，系统自动生成查询、抓取论文、执行分析、生成可视化和报告**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## ✨ 核心特性

### 🤖 全自动分析流程
- **输入研究领域** → 系统自动完成所有步骤
- 12 个模块顺序执行：查询生成 → 论文抓取 → 国家分析 → 计量分析 → 预处理 → 频次分析 → 主题建模 → 突发检测 → TSR 排名 → 网络分析 → 可视化 → 报告生成
- 支持 3 种工作模式：自动模式、人机交互（HITL）、可插拔模式

### 🧠 LLM 驱动的智能体
- **GuardianSoul** — 错误自修复 Agent，遇错自动分析并修复
- **TuningAgent** — 后优化 Agent，支持用户交互式调优
- **Chat Agent** — 实时 AI 助手，随时回答问题、执行任务
- 完整的会话记忆系统，支持历史轮转和续接

### 📊 丰富的分析能力
- 多数据源抓取：PubMed、OpenAlex、Crossref、Semantic Scholar
- 5 种网络分析：作者合作、机构合作、国家合作、共引、文献耦合
- LDA 主题建模 + 突发检测 + TSR 排名
- 发表级可视化（matplotlib + plotly）

### 📝 论文生成
- LLM 自动撰写综述论文（Markdown + LaTeX + PDF）
- CJK 字体支持（NotoSansSC + Windows 系统字体）
- fpdf2 纯 Python 渲染，无需 xelatex/pdflatex

### 🌐 现代 Web 界面
- 三栏布局：项目管理 | 模块时间线 | AI 工作流面板
- 实时 WebSocket 进度更新
- AI 思考过程可视化（类似 Cursor IDE）
- 用户可随时与 AI 对话、干预分析流程

---

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -e .
python -m spacy download en_core_web_sm
```

### 2. 配置 API Key

#### 配置优先级

系统使用**三层配置优先级**（从高到低）：

1. **项目特定配置** — `state.json` 的 `llm_config` 字段（高级用法）
2. **环境变量** — `.env` 文件（推荐方式）✅
3. **默认配置** — `configs/default.yaml` 的默认值

#### 快速配置（推荐）

创建 `.env` 文件（复制 `.env.example`）：

```bash
# 必需：设置 OpenRouter API Key（支持多种模型）
OPENAI_API_KEY="sk-or-v1-your-key-here"
OPENAI_BASE_URL="https://openrouter.ai/api/v1"

# 可选：指定模型（默认 qwen/qwen3.6-plus）
LLM_MODEL="qwen/qwen3.6-plus"

# 其他模型选项：
# LLM_MODEL="anthropic/claude-3.5-sonnet"
# LLM_MODEL="openai/gpt-4o"
# LLM_MODEL="deepseek/deepseek-chat"
```

**获取 API Key**：访问 https://openrouter.ai/keys

#### 高级配置

**调整模型参数**（编辑 `configs/default.yaml`）：

```yaml
llm:
  model: "qwen/qwen3.6-plus"  # 默认模型
  temperature: 0.3             # 生成温度（0-1）
  max_tokens: 4096             # 最大输出长度
```

**项目级别覆盖**（通过 API 创建项目时）：

```json
{
  "name": "My Project",
  "llm_config": {
    "model": "anthropic/claude-3.5-sonnet",
    "temperature": 0.7
  }
}
```

#### 可选：论文抓取 API Keys

提高论文抓取速率限制（可选）：

```bash
# Semantic Scholar（更高速率限制）
SEMANTIC_SCHOLAR_API_KEY="your-key-here"

# PubMed（速率限制从 3/s 提升到 10/s）
PUBMED_API_KEY="your-ncbi-key"
PUBMED_EMAIL="your-email@example.com"

# OpenAlex（速率限制从 1/s 提升到 10/s）
OPENALEX_EMAIL="your-email@example.com"
```

### 3. 启动服务

```bash
python run_web.py
```

访问 http://localhost:8001

### 4. 创建项目

1. 点击"新建项目"
2. 输入研究领域（如 "machine learning in healthcare"）
3. 选择工作模式（自动/人机交互/可插拔）
4. 设置论文数量限制
5. 点击"开始" → 系统自动运行 12 个模块

---

## 📖 文档

- **[快速开始指南](docs_content/QUICK_START_GUIDE.md)** — 详细安装和使用教程
- **[LLM 配置说明](docs_content/LLM_CONFIGURATION.md)** — API Key、模型选择、配置优先级详解
- **[架构设计](CLAUDE.md)** — 系统架构、模块开发、API 端点
- **[会话记忆实现](docs_progress/context_memory_implementation.md)** — Agent 记忆系统技术文档

---

## 🏗️ 架构设计

### 核心组件

| 组件 | 文件 | 功能 |
|------|------|------|
| **Orchestrator** | `core/orchestrator.py` | 顺序执行模块，Guardian 激活，HITL 检查点 |
| **GuardianSoul** | `core/guardian_soul.py` | LLM Agent 错误恢复（最多 50 步） |
| **TuningAgent** | `core/tuning_agent.py` | LLM Agent 后优化（13 个工具，最多 30 步） |
| **Communication Hub** | `core/communication_hub.py` | WebSocket 消息路由 |
| **State Manager** | `core/state_manager.py` | 持久化运行状态到 `state.json` |
| **Pipeline Runner** | `core/pipeline_runner.py` | 异步管道执行管理 |
| **LLM Provider** | `core/llm/__init__.py` | OpenAI 兼容 API 抽象层 |

### 12 个分析模块

```
query_generator → paper_fetcher → country_analyzer → bibliometrics_analyzer
→ preprocessor → frequency_analyzer → topic_modeler → burst_detector
→ tsr_ranker → network_analyzer → visualizer → report_generator
```

每个模块继承 `BaseModule`，支持自动发现和热插拔。

### 工作空间隔离

```
workspaces/{project_name}_{run_id}/
├── checkpoints/
│   └── state.json          # 管道状态（模块状态、调优次数、论文状态）
├── outputs/
│   └── {module_name}/      # 每个模块的输出
└── workspace/              # Guardian 生成的修复
    ├── tuning_context.jsonl    # 调优 Agent 会话历史
    ├── chat_context.jsonl      # 聊天 Agent 会话历史
    └── guardian_context.jsonl  # Guardian Agent 会话历史
```

---

## 🛠️ 开发

### 运行测试

```bash
# 快速集成测试（需要 API Key）
python testscript/test_integration_quick.py

# 完整集成测试
python testscript/test_integration.py

# 使用缓存数据测试（模块 3+ 无需 API 调用）
python testscript/test_pipeline_cached.py

# 单个模块测试
python testscript/test_tsr_ranker.py
python testscript/test_network_analyzer.py
python testscript/test_metadata_normalizer.py

# Agent 测试
python testscript/test_guardian_soul.py
python testscript/test_llm_integration.py
python testscript/test_all_tools.py
```

### 开发自定义模块

```python
from modules.base import BaseModule, RunContext

class MyModule(BaseModule):
    @property
    def name(self) -> str:
        return "my_module"

    def process(self, input_data: dict, config: dict, context: RunContext) -> dict:
        # 访问前驱输出
        data = input_data.get("key")

        # 访问任意早期模块
        if "preprocessor" in context.previous_outputs:
            vocab = context.previous_outputs["preprocessor"]["vocab_path"]

        return {"result": "..."}

    def input_schema(self) -> dict: ...
    def output_schema(self) -> dict: ...
    def config_schema(self) -> dict: ...
```

放置在 `modules/` 目录即可自动发现。配置项添加到 `configs/default.yaml` 的 `modules.my_module` 节点。

---

## 📂 目录结构

```
bibliometrics-agent/
├── core/                   # 核心引擎
│   ├── orchestrator.py     # 管道编排
│   ├── guardian_soul.py    # 错误自修复 Agent
│   ├── tuning_agent.py     # 后优化 Agent
│   ├── context.py          # 会话上下文管理
│   └── llm/                # LLM 提供者抽象
├── modules/                # 分析模块（12 个）
├── web_api.py              # FastAPI Web 服务
├── run_web.py              # 启动脚本
├── configs/                # 配置文件
├── static/                 # 前端单页应用
├── testscript/             # 测试脚本（20 个）
├── docs_content/           # 项目内容文档
│   ├── QUICK_START_GUIDE.md
│   └── LLM_CONFIGURATION.md
├── docs_progress/          # 项目进展文档
├── docs_archive/           # 归档文档
└── scripts_archive/        # 归档脚本
```

---

## 🔌 API 端点

### REST API

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/` | Web 界面 |
| GET | `/api/projects` | 列出所有项目 |
| POST | `/api/projects` | 创建项目 |
| GET | `/api/projects/{id}` | 获取项目详情 + 进度 |
| POST | `/api/projects/{id}/start` | 启动管道 |
| POST | `/api/projects/{id}/pause` | 暂停（异步） |
| POST | `/api/projects/{id}/resume` | 恢复 |
| POST | `/api/projects/{id}/reset` | 重置项目 |
| POST | `/api/projects/{id}/tune` | 启动调优会话 |
| POST | `/api/projects/{id}/generate-paper` | 生成论文 |
| GET | `/api/projects/{id}/chat-history` | 获取 AI 对话历史 |
| POST | `/api/projects/{id}/checkpoint-review` | HITL 检查点审核 |
| GET | `/api/modules` | 列出可用模块 |
| GET | `/api/presets` | 列出保存的预设 |

### WebSocket

`WS /ws/{project_id}` — 实时更新

消息类型：
- `progress_update` — 模块进度
- `project_status_update` — 项目状态变化
- `ai_thinking` — AI 思考消息
- `ai_tool_call` — AI 工具调用
- `ai_tool_result` — 工具执行结果
- `ai_decision` — AI 决策建议
- `user_message` — 用户消息

---

## ⚙️ 配置

### 配置优先级

系统使用三层配置架构：

| 优先级 | 来源 | 用途 | 示例 |
|--------|------|------|------|
| **1** | 项目配置 (`state.json`) | 项目特定模型设置 | 临时切换模型 |
| **2** | 环境变量 (`.env`) | 主要配置方式 ✅ | API Key、模型选择 |
| **3** | 默认配置 (`default.yaml`) | 全局默认值 | temperature、max_tokens |

### 环境变量配置

编辑 `.env` 文件（推荐方式）：

```bash
# 必需：LLM API Key
OPENAI_API_KEY="sk-or-v1-your-key-here"        # OpenRouter Key
OPENAI_BASE_URL="https://openrouter.ai/api/v1"  # API 端点

# 可选：模型选择
LLM_MODEL="qwen/qwen3.6-plus"                   # 默认模型

# 可选：论文抓取 API Keys（提高速率限制）
SEMANTIC_SCHOLAR_API_KEY="your-key-here"
PUBMED_API_KEY="your-ncbi-key"
PUBMED_EMAIL="your-email@example.com"
OPENALEX_EMAIL="your-email@example.com"
```

### YAML 配置

编辑 `configs/default.yaml` 调整模型参数：

```yaml
llm:
  model: "qwen/qwen3.6-plus"    # 默认模型
  temperature: 0.3               # 生成温度（0-1，越低越确定）
  max_tokens: 4096               # 最大输出 token 数
  base_url: "https://openrouter.ai/api/v1"

modules:
  query_generator:
    max_queries: 10              # 最大查询数
  paper_fetcher:
    max_papers: 500              # 最大论文数
  topic_modeler:
    min_topics: 5
    max_topics: 50
  tuning_agent:
    max_steps: 30                # Agent 最大步数
    max_history_files: 10        # 历史文件保留数量
    preserve_recent_messages: 20 # 压缩时保留消息数

pipeline:
  automated:
    guardian_max_steps: 50       # Guardian 最大步数
```

### 项目级别配置

创建项目时通过 API 覆盖配置：

```bash
POST /api/projects
{
  "name": "My Project",
  "domain": "machine learning",
  "llm_config": {
    "model": "anthropic/claude-3.5-sonnet",
    "temperature": 0.7,
    "max_tokens": 8192
  }
}
```

### 支持的模型

通过 OpenRouter 支持多种模型：

- `qwen/qwen3.6-plus` — 默认，性价比高
- `anthropic/claude-3.5-sonnet` — 高质量推理
- `openai/gpt-4o` — OpenAI 旗舰模型
- `deepseek/deepseek-chat` — DeepSeek 模型
- `meta-llama/llama-3.1-70b-instruct` — 开源模型

完整列表：https://openrouter.ai/models

---

## 🧪 技术栈

- **后端**：FastAPI、Uvicorn、asyncio
- **LLM**：OpenAI SDK（兼容 OpenRouter）
- **数据分析**：pandas、numpy、scikit-learn、gensim、spacy
- **可视化**：matplotlib、plotly、pyLDAvis
- **PDF 生成**：fpdf2（纯 Python，CJK 支持）
- **前端**：原生 HTML/CSS/JavaScript（无框架）
- **实时通信**：WebSocket

---

## 📝 已实现功能

- ✅ 全自动 12 模块管道
- ✅ GuardianSoul 错误自修复
- ✅ TuningAgent 后优化交互
- ✅ Chat Agent 实时对话
- ✅ 会话记忆系统（历史轮转、续接）
- ✅ 多数据源论文抓取（PubMed、OpenAlex、Crossref、SS）
- ✅ 5 种网络分析
- ✅ 论文自动生成（Markdown + LaTeX + PDF）
- ✅ 实时 WebSocket 进度更新
- ✅ AI 工作流可视化
- ✅ 状态徽章系统（调优次数、论文状态）
- ✅ HITL 人机交互模式
- ✅ 可插拔管道模式

---

## 🚧 已知限制

- **论文抓取速率限制**：Semantic Scholar API 限制（无 API Key: 100 请求/5 分钟）
- **摘要可用性**：部分论文无摘要，默认跳过
- **LLM API 成本**：查询生成、解释、论文撰写需要 LLM API 调用
- **语言支持**：当前优化为英文文本

---

## 📄 许可证

**MIT License with Non-Commercial and Attribution Requirements**

- ✅ **允许**：个人学习、研究、非商业用途
- ❌ **禁止**：未经许可的商业使用
- 📝 **要求**：修改需注明原作者 **Liu Dingrui (刘丁瑞)** 并引用原项目链接

详见 [LICENSE](LICENSE) 文件。

### 商业使用

如需商业使用许可，请联系原作者：
- GitHub: https://github.com/newhouse10086
- 项目地址: https://github.com/newhouse10086/bibliometrics-agent

---

## 📚 相关资源

- [快速开始指南](docs_content/QUICK_START_GUIDE.md)
- [LLM 配置说明](docs_content/LLM_CONFIGURATION.md)
- [架构设计文档](CLAUDE.md)
- [会话记忆系统实现](docs_progress/context_memory_implementation.md)
- [项目进展记录](docs_progress/)
- [API 文档](docs_content/)

---

**最后更新**：2026-04-14
**版本**：v2.0
**状态**：✅ 生产就绪
