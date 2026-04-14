# GuardianSoul 项目上下文注入功能

## 概述

GuardianSoul 现在支持自动加载和注入项目上下文信息,使其能够更好地理解当前项目的状态、进度和研究主题。

## 功能特性

### 1. 自动加载项目上下文

当 GuardianSoul 初始化时,会自动从以下文件加载项目信息:

- `checkpoints/{run_id}/state.json` - 项目状态、模块进度、执行模式
- `checkpoints/{run_id}/query_generator/output.json` - 研究主题和关键词

### 2. 注入到系统提示词

加载的项目上下文会自动注入到 LLM 的系统提示词中,包括:

- **Project ID** (run_id)
- **Research Domain** (研究主题关键词)
- **Project Status** (项目状态和进度)
- **Execution Mode** (执行模式: automated/HITL/pluggable)
- **Max Papers** (最大论文数量)
- **Failed Modules** (失败模块列表)
- **Current Date** (当前日期)

示例系统提示词:

```
## Project Context
- **Project ID**: 24286b2d
- **Research Domain**: machine learning, healthcare, AI, diagnosis
- **Project Status**: running (2/4 modules completed)
- **Execution Mode**: automated
- **Max Papers**: 100
- **Failed Modules**: preprocessor
- **Date**: 2026-04-14

## Module Context
- **Current Module**: preprocessor
- **Workspace**: checkpoints/24286b2d/workspace
- **Pipeline Stage**: preprocessor
```

### 3. 新工具: read_project_file

新增 `read_project_file` 工具,允许 Guardian 读取项目 checkpoint 目录中的文件:

```python
# 读取项目状态
read_project_file("state.json")

# 读取查询生成器输出
read_project_file("query_generator/output.json")

# 读取论文元数据
read_project_file("paper_fetcher/papers.csv")

# 读取预处理输出
read_project_file("preprocessor/output.json")

# 读取模块日志
read_project_file("preprocessor/logs/error.log")
```

**安全限制**: 该工具只能读取 checkpoint 目录 (checkpoints/{run_id}/) 下的文件,不能读取系统文件或其他项目文件。

## 使用示例

### 场景 1: 理解研究主题

当 Guardian 分析错误时,它会知道当前项目的研究主题:

```
Research Domain: machine learning, healthcare, AI, diagnosis
```

这有助于 Guardian 更好地理解数据处理逻辑和可能的错误原因。

### 场景 2: 了解项目进度

Guardian 可以看到项目的整体进度:

```
Project Status: running (2/4 modules completed)
Failed Modules: preprocessor
```

这帮助 Guardian 判断错误的影响范围和优先级。

### 场景 3: 读取相关文件

Guardian 可以使用 `read_project_file` 工具深入了解项目数据:

```python
# 查看查询生成器的输出,了解搜索策略
query_output = read_project_file("query_generator/output.json")

# 查看已获取的论文数量
papers = read_project_file("paper_fetcher/papers.csv")

# 检查上一个模块的输出
preprocessor_output = read_project_file("preprocessor/output.json")
```

## 技术实现

### 代码位置

- `core/guardian_soul.py`
  - `_load_project_context()` - 加载项目上下文
  - `_build_initial_messages()` - 注入到系统提示词
  - `_read_project_file()` - 新工具实现

### 初始化参数

GuardianSoul 新增 `run_id` 参数:

```python
soul = GuardianSoul(
    module_name="preprocessor",
    llm=provider,
    workspace_dir=Path("checkpoints/24286b2d/workspace"),
    run_id="24286b2d",  # 新增参数
)
```

如果不提供 `run_id`,会自动从 `workspace_dir` 中提取。

### Orchestrator 集成

`core/orchestrator.py` 已更新,会自动传递 `run_id` 给 GuardianSoul:

```python
soul = GuardianSoul(
    module_name=mod_name,
    llm=self._llm,
    workspace_dir=workspace_dir,
    run_id=run_id,  # 自动传递
    ...
)
```

## 测试

运行测试脚本验证功能:

```bash
python test_guardian_context.py
```

测试内容:
1. 加载项目上下文 (state.json, query_generator/output.json)
2. 读取项目文件 (read_project_file 工具)
3. 验证系统提示词注入

## 未来增强

- [ ] 支持读取更多项目元数据 (如用户配置、环境变量)
- [ ] 项目上下文增量更新 (当状态改变时自动刷新)
- [ ] 多项目上下文对比分析
- [ ] 项目历史追踪 (记录之前失败的模块和修复方案)
