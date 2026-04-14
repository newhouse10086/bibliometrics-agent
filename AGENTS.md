# Bibliometrics Agent - 智能体编排系统

## 快速命令

```bash
# 运行完整流水线
python run_pipeline.py --domain "machine learning" --interactive

# 运行单个模块
python run_pipeline.py --module preprocessor --resume run_001

# 测试 Guardian Agent 系统
python test_guardian_system.py

# 查看模块状态
python run_pipeline.py --status run_001
```

## 项目概述

Bibliometrics Agent 是一个文献计量分析流水线系统，采用**固定流水线 + Guardian Agent** 混合架构：

- **固定流水线**：预定义的分析流程（query_generator → paper_fetcher → preprocessor → ...）
- **Guardian Agent**：每个模块配备专门的守护智能体，监控错误并自动生成修复代码
- **工作区隔离**：生成的修复代码保存在 workspace/，不修改核心代码

## 技术栈

- Python 3.10+
- 配置管理：OmegaConf
- 日志系统：loguru
- 智能体框架：自定义 Guardian Agent 系统
- LLM 集成：支持 OpenAI API / 本地模型（可选）

## 架构概览

### 核心组件

1. **Pipeline Orchestrator** (`core/orchestrator.py`)
   - 管理流水线执行流程
   - 调用 Guardian Agent 处理错误
   - 支持 HITL（Human-in-the-Loop）检查点

2. **Guardian Agent System** (`core/agent_guardian.py`)
   - 基类：`GuardianAgent` - 定义错误分析、修复生成、测试流程
   - 全局协调器：`GlobalCoordinatorAgent` - 跨模块错误升级
   - 注册表：按模块名查找对应 Guardian

3. **Module System** (`modules/`)
   - 基类：`BaseModule` - 定义模块接口
   - 硬件需求声明：`HardwareSpec`
   - 运行上下文：`RunContext`

4. **State Manager** (`core/state_manager.py`)
   - 运行状态持久化
   - 检查点保存/加载
   - 模块输出缓存

### 智能体工具系统

参考 kimi-cli 的工具编排架构，本项目的 Guardian Agent 拥有以下工具能力：

**文件操作**：
- `ReadFile` - 读取分析结果、日志文件
- `WriteFile` - 生成修复代码到 workspace/fixes/
- `Glob` - 搜索相关文件
- `Grep` - 搜索代码模式

**Shell 执行**：
- `Shell` - 运行测试、诊断命令
- `BackgroundTask` - 长时间运行的修复验证任务

**智能体协作**：
- `SpawnGuardian` - 为特定错误类型创建专门的修复智能体
- `AskUser` - 请求用户确认（通过 GlobalCoordinator）

**Web 搜索**（可选）：
- `SearchWeb` - 搜索错误解决方案
- `FetchURL` - 获取文档、示例代码

### 智能体配置文件

位于 `.agents/configs/` 目录，YAML 格式：

```yaml
version: 1
agent:
  name: preprocessor_guardian
  module: preprocessor
  tools:
    - "core.tools.file:ReadFile"
    - "core.tools.file:WriteFile"
    - "core.tools.shell:Shell"
    - "core.tools.agent:SpawnGuardian"
  error_patterns:
    - encoding
    - memory
    - spacy_model
    - dtm_vocabulary
  fix_templates_dir: modules/guardians/templates/
  workspace_dir: workspace/
```

### 技能系统

位于 `.agents/skills/` 目录，每个技能是一个独立的 `SKILL.md` 文件：

```
.agents/skills/
├── error-recovery/
│   └── SKILL.md         # 错误恢复通用技能
├── code-generation/
│   └── SKILL.md         # 代码生成最佳实践
├── encoding-fix/
│   └── SKILL.md         # 编码错误专项修复
└── memory-optimization/
    └── SKILL.md         # 内存优化技能
```

## 智能体编排流程

### 1. 正常流程

```
User Input → Orchestrator → Module.process()
              ↓
         State Manager (save checkpoint)
              ↓
         Next Module ...
```

### 2. 错误处理流程

```
Module.process() raises Exception
              ↓
    Orchestrator catches error
              ↓
    Get Guardian for module
              ↓
    Guardian.analyze_error()
       - Classify error type
       - Identify root cause
       - Generate fix strategy
              ↓
    Guardian.generate_fix()
       - Create fix code
       - Add documentation
              ↓
    Guardian.test_fix()
       - Syntax check
       - Dry-run test (optional)
              ↓
    Save fix to workspace/fixes/
              ↓
    Log decision to workspace/agent_logs/
              ↓
    Return GuardianDecision
              ↓
    If success:
       - Notify user (option to apply fix)
       - Resume pipeline
    If failed:
       - Escalate to GlobalCoordinator
       - Request HITL intervention
```

### 3. Guardian Agent 工作流程

```python
# 伪代码示例
class PreprocessorGuardianAgent(GuardianAgent):
    def analyze_error(self, error, context):
        if "UnicodeDecodeError" in str(error):
            return ErrorAnalysis(
                error_type="encoding",
                root_cause="File encoding not UTF-8",
                suggested_fix="Add encoding detection",
                confidence=0.9
            )
        # ... other patterns

    def generate_fix(self, analysis):
        if analysis.error_type == "encoding":
            return FixCode(
                code=load_template("encoding_fix.py"),
                description="Auto-detect file encoding",
                ...
            )
```

## 模块与 Guardian 映射

| Module | Guardian | 主要错误类型 |
|--------|----------|-------------|
| `query_generator` | `QueryGeneratorGuardian` | API 错误、JSON 解析错误 |
| `paper_fetcher` | `PaperFetcherGuardian` | 网络错误、API 限流、数据格式错误 |
| `preprocessor` | `PreprocessorGuardian` | 编码错误、内存不足、spaCy 模型缺失 |
| `frequency_analyzer` | `FrequencyAnalyzerGuardian` | DTM 计算错误、内存不足 |
| `topic_modeler` | `TopicModelerGuardian` | LDA 收敛错误、维度不匹配 |
| `burst_detector` | `BurstDetectorGuardian` | 数据格式错误、参数范围错误 |
| `tsr_ranker` | `TSRRankerGuardian` | 数学计算错误、指标缺失 |
| `network_analyzer` | `NetworkAnalyzerGuardian` | 图构建错误、内存不足 |
| `visualizer` | `VisualizerGuardian` | 渲染错误、文件路径错误 |
| `report_generator` | `ReportGeneratorGuardian` | 模板错误、LaTeX 编译错误 |

## 扩展 Guardian Agent

### 步骤 1：创建 Guardian 类

```python
# modules/guardians/paper_fetcher_guardian.py
from core.agent_guardian import GuardianAgent, ErrorAnalysis, FixCode

class PaperFetcherGuardianAgent(GuardianAgent):
    def analyze_error(self, error, context):
        # 分析 API 错误、网络错误等
        pass

    def generate_fix(self, analysis):
        # 生成重试逻辑、切换 API 源等
        pass
```

### 步骤 2：注册 Guardian

```python
# modules/guardians/__init__.py
from core.agent_guardian import register_guardian
from .paper_fetcher_guardian import PaperFetcherGuardianAgent

register_guardian("paper_fetcher", PaperFetcherGuardianAgent)
```

### 步骤 3：创建修复模板

```python
# modules/guardians/templates/api_retry.py
RETRY_TEMPLATE = '''
def fetch_with_retry(api_func, max_retries=3, backoff_factor=2):
    """Auto-generated fix: Retry API calls with exponential backoff."""
    import time
    for attempt in range(max_retries):
        try:
            return api_func()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(backoff_factor ** attempt)
'''
```

### 步骤 4：配置智能体

```yaml
# .agents/configs/paper_fetcher_guardian.yaml
version: 1
agent:
  name: paper_fetcher_guardian
  module: paper_fetcher
  tools:
    - "core.tools.shell:Shell"
    - "core.tools.web:SearchWeb"
  error_patterns:
    - api_rate_limit
    - network_timeout
    - invalid_response
```

## 质量保证

### 测试

- 单元测试：`tests/test_guardian_*.py`
- 集成测试：`tests/test_pipeline_integration.py`
- 端到端测试：`tests_e2e/test_full_pipeline.py`

### 代码规范

- Python >= 3.10
- 代码风格：black + ruff
- 类型注解：pyright
- 提交信息：Conventional Commits

### 日志与监控

- Guardian 决策日志：`workspace/agent_logs/*.json`
- 修复代码：`workspace/fixes/*.py`
- 运行状态：`workspace/runs/*/state.json`

## 与 kimi-cli 的对比

| 特性 | kimi-cli | bibliometrics-agent |
|------|----------|---------------------|
| 智能体类型 | 通用编程助手 | 领域专用 Guardian Agent |
| 工具系统 | 丰富的内置工具 | 精简的错误处理工具集 |
| 子智能体 | 灵活的任务分解 | 固定的模块 Guardian |
| 技能系统 | 用户可定义技能 | 预定义的错误修复技能 |
| 执行模式 | 交互式对话 | 流水线驱动 + 自动恢复 |
| 代码修改 | 直接修改源文件 | 工作区隔离，不修改核心 |

## 未来扩展

1. **LLM 增强**：使用 GPT-4/Claude 生成更智能的修复方案
2. **知识库**：积累历史修复案例，提供相似错误解决方案
3. **Web UI**：可视化 Guardian 决策过程，支持用户确认/拒绝修复
4. **多智能体协作**：多个 Guardian 协同处理复杂错误链
5. **自动回滚**：修复失败时自动恢复到上一个稳定状态

## 参考资源

- [kimi-cli 架构文档](https://github.com/anthropics/kimi-cli)
- [Guardian Agent 设计文档](./docs/guardian_design.md)
- [模块开发指南](./docs/module_development.md)
