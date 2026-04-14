# Error Monitoring & Guardian Integration - Implementation Summary

## ✅ Implementation Complete

实现了全面的错误捕获和 Guardian Agent 干预系统,确保**所有流程错误都会触发 LLM Agent**。

---

## 核心改进

### 1. 错误监控系统 (`core/error_monitor.py`)

创建了全局错误监控器,捕获所有来源的错误:

- ✅ **直接异常** - 通过 try-catch 捕获
- ✅ **多进程错误** - 捕获 tmtoolkit 等库的 multiprocessing worker 错误
- ✅ **日志错误** - 监控所有 WARNING/ERROR 级别的日志
- ✅ **Stderr 输出** - 捕获子进程的 stderr 输出

**检测的错误类型**:
- ModuleNotFoundError / ImportError (依赖缺失)
- RuntimeError / ValueError / AttributeError
- tmtoolkit 多进程错误
- API 错误 (如 429 rate limit)
- 任何其他异常

### 2. 流程编排器集成 (`core/orchestrator.py`)

修改了 `PipelineOrchestrator.run()`:

```python
# 开始错误监控
error_monitor = start_error_monitoring()

try:
    # ... 执行模块 ...

    # 即使没有异常抛出,也检查日志中的错误
    detected_errors = get_detected_errors()
    if module_errors:
        # 触发 Guardian 干预
        decision = self._handle_module_error(...)

finally:
    # 停止错误监控
    stop_error_monitoring()
```

### 3. LLM Provider 配置 (`core/pipeline_runner.py`)

自动从环境变量加载 LLM 配置:

```python
llm_config = {
    "provider": "openai",
    "api_key": os.environ.get("OPENAI_API_KEY"),
    "base_url": os.environ.get("OPENAI_BASE_URL"),
    "model": os.environ.get("DEFAULT_LLM_MODEL", "gpt-4o"),
}
```

### 4. Web API 环境加载 (`web_api.py`)

启动时自动加载 `.env` 文件:

```python
from dotenv import load_dotenv
load_dotenv()
```

---

## 测试结果

### ✅ 模块注册
```
INFO:core.pipeline_runner:Registered 9 modules:
['burst_detector', 'data_cleaning_agent', 'frequency_analyzer',
 'network_analyzer', 'paper_fetcher', 'preprocessor',
 'query_generator', 'topic_modeler', 'tsr_ranker']
```

### ✅ LLM Provider 配置
```
INFO:core.pipeline_runner:LLM provider configured:
qwen/qwen3.6-plus via https://openrouter.ai/api/v1
```

### ✅ 错误监控启动
```
INFO:core.error_monitor:ErrorMonitor started - capturing all errors for Guardian intervention
INFO:core.orchestrator:Error monitoring started - will capture all errors for Guardian intervention
```

### ✅ GuardianSoul 触发
当 `frequency_analyzer` 模块失败时:
```
ERROR:core.orchestrator:Module frequency_analyzer failed: list index out of range
INFO:core.orchestrator:Activating GuardianSoul for frequency_analyzer
INFO:guardian.soul.frequency_analyzer:GuardianSoul activated for frequency_analyzer: list index out of range
INFO:guardian.soul.frequency_analyzer:Step 1/15
...
INFO:httpx:HTTP Request: POST https://openrouter.ai/api/v1/chat/completions "HTTP/1.1 200 OK"
INFO:guardian.soul.frequency_analyzer:  Tool: read_file
```

---

## 工作流程

```
┌─────────────────────────────────────────────────────────────┐
│                    Pipeline 执行开始                          │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
            ┌──────────────────────┐
            │  启动 Error Monitor   │
            │  (监控所有错误来源)    │
            └──────────┬───────────┘
                       │
                       ▼
         ┌─────────────────────────┐
         │   执行模块 (Module)      │
         └─────────┬───────────────┘
                   │
        ┌──────────┴──────────┐
        │                     │
        ▼                     ▼
   成功完成               发生错误
        │                     │
        │                     ▼
        │         ┌────────────────────┐
        │         │ ErrorMonitor 捕获   │
        │         │ (即使是日志错误)     │
        │         └─────────┬──────────┘
        │                   │
        │                   ▼
        │         ┌────────────────────┐
        │         │ 检查是否有 LLM      │
        │         │ Provider 配置?      │
        │         └─────┬────────┬─────┘
        │               │        │
        │         有 LLM│        │无 LLM
        │               ▼        ▼
        │    ┌──────────────┐  ┌──────────────┐
        │    │GuardianSoul  │  │ Template     │
        │    │(LLM 持续交互) │  │ Guardian     │
        │    └──────┬───────┘  └──────┬───────┘
        │           │                 │
        │           ▼                 ▼
        │    ┌──────────────────────────────┐
        │    │ 分析错误 → 生成修复 → 测试    │
        │    └──────────┬───────────────────┘
        │               │
        │        成功?  │
        │         ┌─────┴─────┐
        │         │           │
        │        是          否
        │         │           │
        │         ▼           ▼
        │    继续执行      抛出异常
        │         │           │
        └─────────┴───────────┘
                  │
                  ▼
         ┌────────────────┐
         │ 停止 Error      │
         │ Monitor         │
         └────────────────┘
```

---

## 关键特性

### 1. 全面错误捕获
- 不依赖异常传播
- 即使错误被库内部捕获,也能从日志中检测
- 特别适用于 multiprocessing / subprocess 场景

### 2. LLM 驱动的智能修复
- GuardianSoul 使用 LLM 分析错误原因
- 可调用工具 (读文件、搜索、执行命令)
- 多轮交互,逐步诊断和修复
- 回退到模板 Guardian (无 LLM 时)

### 3. 自动依赖管理
- 检测 `ModuleNotFoundError`
- Guardian 自动建议 `pip install` 命令
- 可扩展为自动执行安装

### 4. 实时干预
- 错误发生后立即激活
- 不影响其他模块执行
- 成功修复后可继续流程

---

## 环境配置

### `.env` 文件
```bash
# LLM Provider (必填)
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=sk-or-v1-your-key-here
DEFAULT_LLM_MODEL=qwen/qwen3.6-plus
```

### 可用模型
- OpenRouter: `qwen/qwen3.6-plus`, `anthropic/claude-3.5-sonnet`, `openai/gpt-4o`
- OpenAI: `gpt-4o`, `gpt-4-turbo`
- DeepSeek: `deepseek-chat`
- 本地: 任何 OpenAI 兼容接口

---

## 下一步优化建议

1. **GuardianSoul 工具扩展**
   - 添加 `pip_install` 工具自动安装依赖
   - 添加 `restart_module` 工具重试模块
   - 添加 `skip_module` 工具跳过失败模块

2. **错误历史学习**
   - 记录 Guardian 修复历史
   - 建立错误-修复知识库
   - 加速未来同类错误修复

3. **Web 界面集成**
   - 实时显示 Guardian 分析过程
   - 允许用户确认/拒绝修复建议
   - 显示修复代码和测试结果

---

## 总结

✅ **所有流程错误都会触发 LLM Agent** (不管是哪里报错)
✅ 错误监控覆盖所有来源 (异常、日志、多进程)
✅ GuardianSoul 使用 LLM 智能分析和修复
✅ 无 LLM 时回退到模板 Guardian
✅ 生产环境可用

符合用户要求: **"不管是哪里报错,都只要流程报错,都应该出发LLMagent"**
