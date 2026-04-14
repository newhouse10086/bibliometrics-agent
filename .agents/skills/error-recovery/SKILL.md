---
name: error-recovery
description: 通用错误恢复技能，用于分析错误、生成修复方案、测试修复有效性。所有 Guardian Agent 都应掌握此技能。
---

# Error Recovery Skill

Guardian Agent 的核心技能：自动诊断错误并生成修复代码。

## 错误处理流程

### 1. 错误分析阶段

**目标**：准确识别错误类型和根本原因

**步骤**：
1. 解析异常堆栈，提取关键信息
2. 匹配已知错误模式（encoding, memory, api, network 等）
3. 分析上下文数据（输入文件、配置参数、系统状态）
4. 评估修复置信度（0-1 分数）

**输出**：`ErrorAnalysis` 对象
- `error_type`: 错误类型标签
- `root_cause`: 根本原因描述
- `suggested_fix`: 建议的修复方向
- `confidence`: 置信度分数
- `context`: 相关上下文数据

**工具使用**：
- `ReadFile` - 读取错误日志、配置文件
- `Grep` - 搜索相似错误案例
- `Shell` - 运行诊断命令

**示例**：
```python
# 编码错误分析
error = UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 2, "invalid start byte")
analysis = ErrorAnalysis(
    error_type="encoding",
    root_cause="File encoding is not UTF-8 or supported format",
    suggested_fix="Add automatic encoding detection with fallback handling",
    confidence=0.9,
    context={"original_error": "UnicodeDecodeError"}
)
```

### 2. 修复生成阶段

**目标**：生成项目特定的、可执行的修复代码

**原则**：
- 代码必须完整可运行，不能有占位符
- 包含详细注释和文档字符串
- 遵循项目代码规范
- 尽量使用项目已有的依赖

**模板库**：
修复代码应优先使用预定义模板，位于 `modules/guardians/templates/`：
- `encoding_fix.py` - 编码错误修复
- `memory_chunking.py` - 内存分块处理
- `api_retry.py` - API 重试逻辑
- `spacy_download.py` - spaCy 模型下载

**工具使用**：
- `ReadFile` - 读取修复模板
- `WriteFile` - 保存生成的修复代码
- `Glob` - 查找相关修复案例

**输出**：`FixCode` 对象
- `module_name`: 模块名称
- `code`: 完整的 Python 代码
- `description`: 修复描述
- `timestamp`: 生成时间
- `error_type`: 错误类型
- `metadata`: 元数据

**示例**：
```python
fix = FixCode(
    module_name="preprocessor",
    code='''
def load_documents_with_encoding_fix(file_path):
    """Auto-detect and handle file encoding errors."""
    import chardet
    from pathlib import Path

    path = Path(file_path)
    with open(path, 'rb') as f:
        raw_data = f.read()

    detected = chardet.detect(raw_data)
    encoding = detected.get('encoding', 'utf-8')

    try:
        text = raw_data.decode(encoding)
        return text
    except Exception:
        return raw_data.decode('utf-8', errors='ignore')
''',
    description="Auto-detect file encoding with fallback",
    timestamp="2026-04-08T20:00:00",
    error_type="encoding"
)
```

### 3. 修复测试阶段

**目标**：验证修复代码的有效性和安全性

**测试层次**：
1. **语法检查**：`compile(code, '<string>', 'exec')`
2. **导入测试**：尝试导入生成的函数/类
3. **单元测试**（可选）：运行预定义的测试用例
4. **集成测试**（可选）：在沙箱环境中运行

**工具使用**：
- `Shell` - 运行 Python 语法检查
- `WriteFile` + `Shell` - 创建并运行测试脚本

**输出**：布尔值（True/False）

**示例**：
```python
# 语法检查
try:
    compile(fix.code, '<string>', 'exec')
    syntax_ok = True
except SyntaxError as e:
    syntax_ok = False
    logger.error(f"Syntax error in generated fix: {e}")

# 导入测试
test_script = f'''
import sys
sys.path.insert(0, "{workspace_dir}/fixes")

# 执行修复代码
{fix.code}

# 尝试调用函数
result = load_documents_with_encoding_fix("test.csv")
print("Import test passed")
'''

result = Shell().run(f'python -c "{test_script}"')
import_ok = result.success
```

### 4. 决策记录阶段

**目标**：记录完整的错误处理过程，便于审计和学习

**记录内容**：
- 错误详情（类型、消息、堆栈）
- 分析结果
- 生成的修复
- 测试结果
- 最终决策（success/failed/escalated）

**工具使用**：
- `WriteFile` - 写入 JSON 日志

**输出**：`GuardianDecision` 对象

**日志格式**：
```json
{
  "module": "preprocessor",
  "error": {
    "type": "UnicodeDecodeError",
    "message": "'utf-8' codec can't decode byte 0xff",
    "traceback": "..."
  },
  "analysis": {
    "error_type": "encoding",
    "root_cause": "File encoding not UTF-8",
    "suggested_fix": "Add encoding detection",
    "confidence": 0.9
  },
  "fix_generated": true,
  "fix_path": "workspace/fixes/preprocessor_fix_20260408.py",
  "test_passed": true,
  "applied": false,
  "outcome": "success",
  "timestamp": "2026-04-08T20:00:00"
}
```

## 错误模式库

### 编码错误 (encoding)

**特征**：
- `UnicodeDecodeError`
- `UnicodeEncodeError`
- 错误消息包含 "codec", "encoding", "invalid byte"

**根本原因**：
- 文件编码不是 UTF-8
- 文件包含 BOM 标记
- 混合编码（如 GBK + UTF-8）

**修复策略**：
1. 使用 `chardet` 自动检测编码
2. 尝试常见编码列表（utf-8, latin1, cp1252, gbk）
3. 使用容错解码（`errors='ignore'` 或 `errors='replace'`）

**工具依赖**：
- `chardet` 包
- `ReadFile` 读取原始字节

### 内存错误 (memory)

**特征**：
- `MemoryError`
- 系统监控显示内存占用过高
- 程序崩溃无异常

**根本原因**：
- 文档集过大，无法一次性加载
- 词汇表过大
- DTM 矩阵稀疏但占用内存大

**修复策略**：
1. 分块处理（chunk processing）
2. 减少词汇表大小（max_features, min_freq）
3. 使用稀疏矩阵格式（scipy.sparse）
4. 流式处理（streaming）

**工具依赖**：
- `gc.collect()` 强制垃圾回收
- `sys.getsizeof()` 监控内存

### API 错误 (api_error)

**特征**：
- `HTTPError`, `ConnectionError`
- 错误消息包含 "rate limit", "timeout", "unauthorized"
- HTTP 状态码 429, 500, 503

**根本原因**：
- API 调用频率超限
- 网络不稳定
- API Key 无效或过期

**修复策略**：
1. 指数退避重试（exponential backoff）
2. 切换备用 API 端点
3. 缓存已获取的数据
4. 提示用户更新 API Key

**工具依赖**：
- `time.sleep()` 延迟
- `requests.Session()` 会话复用
- `SearchWeb` 查询 API 文档

### 模型缺失 (model_missing)

**特征**：
- `OSError: Can't find model`
- `ModuleNotFoundError`
- 错误消息包含 "spacy", "model not found"

**根本原因**：
- spaCy 模型未安装
- 模型路径配置错误
- 虚拟环境不一致

**修复策略**：
1. 自动下载模型：`python -m spacy download en_core_web_sm`
2. 使用 `spacy.cli.download()`
3. 提示用户手动安装

**工具依赖**：
- `subprocess` 执行安装命令
- `Shell` 运行 spacy CLI

## 最佳实践

### 1. 修复代码质量

✅ **应该**：
- 完整的错误处理
- 详细的文档字符串
- 清晰的变量命名
- 日志记录

❌ **避免**：
- 硬编码路径
- 缺少异常处理
- 占位符代码
- 过于复杂的逻辑

### 2. 置信度评估

**高置信度（> 0.8）**：
- 错误模式清晰
- 有现成修复模板
- 项目上下文完整

**中置信度（0.5-0.8）**：
- 错误原因基本确定
- 需要少量参数调整
- 需要用户确认

**低置信度（< 0.5）**：
- 错误原因不明
- 需要更多信息
- 建议升级到 HITL

### 3. 工具使用规范

- **最小权限原则**：只使用必要的工具
- **安全执行**：Shell 命令需要验证参数
- **错误传播**：工具失败时及时返回错误
- **日志记录**：记录工具调用和结果

### 4. 工作区管理

**修复代码保存位置**：
```
workspace/
├── fixes/
│   ├── preprocessor_fix_20260408_200000.py
│   ├── preprocessor_fix_20260408_201500.py
│   └── topic_modeler_fix_20260408_203000.py
├── agent_logs/
│   ├── preprocessor_guardian.json
│   └── topic_modeler_guardian.json
└── test_artifacts/
    └── encoding_fix_test.log
```

**命名规范**：
- 修复文件：`{module}_fix_{timestamp}.py`
- 日志文件：`{module}_guardian.json`

## 与 LLM 集成（未来）

当前使用模板生成修复，未来可集成 LLM：

```python
def generate_fix_with_llm(self, analysis: ErrorAnalysis) -> FixCode:
    """使用 LLM 生成修复代码."""

    prompt = f"""
    错误类型: {analysis.error_type}
    根本原因: {analysis.root_cause}
    建议修复: {analysis.suggested_fix}
    上下文: {analysis.context}

    请生成 Python 修复代码，要求：
    1. 完整可运行
    2. 包含文档字符串
    3. 遵循 PEP 8 规范
    """

    response = llm_client.generate(prompt)
    return FixCode(
        module_name=self.module_name,
        code=response.code,
        description=response.description,
        ...
    )
```

**LLM 辅助场景**：
- 复杂错误模式识别
- 生成定制化修复代码
- 自然语言解释错误原因
- 推荐最佳修复方案

## 示例：完整错误处理流程

```python
# 1. 错误发生
try:
    docs = preprocessor.load_documents("data.csv")
except UnicodeDecodeError as e:
    error = e

# 2. Guardian 接管
guardian = PreprocessorGuardianAgent("preprocessor")

# 3. 分析错误
analysis = guardian.analyze_error(error, {"input_file": "data.csv"})
# => ErrorAnalysis(error_type="encoding", confidence=0.9, ...)

# 4. 生成修复
fix = guardian.generate_fix(analysis)
# => FixCode(code="def load_with_encoding_fix(...)", ...)

# 5. 测试修复
test_passed = guardian.test_fix(fix, context)
# => True

# 6. 保存修复
decision = guardian.handle_error(error, context, workspace_dir)
# => GuardianDecision(outcome="success", fix_path="workspace/fixes/...")

# 7. 用户确认后应用修复
if user_approves:
    orchestrator.apply_fix(decision.fix_path)
```

## 相关技能

- `encoding-fix` - 编码错误专项修复
- `memory-optimization` - 内存优化策略
- `api-retry` - API 错误重试逻辑
- `code-generation` - 代码生成最佳实践
