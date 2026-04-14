---
name: code-generation
description: 代码生成技能，指导 Guardian Agent 生成高质量的修复代码。包括编码规范、模板使用、测试策略。
---

# Code Generation Skill

指导 Guardian Agent 生成安全、可测试、可维护的修复代码。

## 生成原则

### 1. 完整性

生成的代码必须是**完整可运行**的，不能有 TODO、占位符或假设。

```python
# BAD - 有占位符
def fix_encoding(file_path):
    # TODO: implement encoding detection
    pass

# GOOD - 完整实现
def fix_encoding(file_path, fallback='utf-8'):
    """自动检测文件编码并读取内容."""
    import chardet
    from pathlib import Path

    path = Path(file_path)
    raw = path.read_bytes()

    detected = chardet.detect(raw)
    encoding = detected.get('encoding') or fallback

    try:
        return raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return raw.decode(fallback, errors='ignore')
```

### 2. 安全性

**禁止**：
- `eval()`, `exec()` 执行不受信任的输入
- `subprocess` 执行用户提供的命令（除非必要）
- 硬编码 API Key 或密码
- 修改项目核心代码

**必须**：
- 输入验证
- 异常处理
- 路径安全检查
- 日志记录

### 3. 项目一致性

遵循项目现有风格：
- 使用项目已有的依赖（`numpy`, `scipy`, `spacy` 等）
- 遵循现有的命名约定
- 使用 `loguru` 或 `logging` 进行日志记录
- 返回与原函数兼容的数据结构

## 代码模板系统

### 模板加载

```python
# modules/guardians/templates/__init__.py
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent

def load_template(template_name: str) -> str:
    """加载修复模板."""
    template_path = TEMPLATES_DIR / f"{template_name}.py"
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_name}")
    return template_path.read_text(encoding="utf-8")

def render_template(template_name: str, **kwargs) -> str:
    """渲染模板，替换占位符."""
    template = load_template(template_name)
    for key, value in kwargs.items():
        template = template.replace(f"{{{{{key}}}}}", str(value))
    return template
```

### 模板格式

模板使用 `{{placeholder}}` 格式：

```python
# modules/guardians/templates/encoding_fix.py
"""Auto-generated fix for encoding errors.

Error Type: encoding
Module: {{module_name}}
Generated: {{timestamp}}
"""

def load_documents_with_encoding_fix(file_path, fallback='utf-8'):
    """自动检测并处理文件编码错误.

    Args:
        file_path: 输入文件路径
        fallback: 回退编码

    Returns:
        解码后的文本内容
    """
    import chardet
    from pathlib import Path
    import logging

    logger = logging.getLogger("{{module_name}}.encoding_fix")

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    raw_data = path.read_bytes()

    # Step 1: chardet 自动检测
    try:
        detected = chardet.detect(raw_data)
        encoding = detected.get('encoding', fallback)

        if encoding and detected.get('confidence', 0) > 0.7:
            logger.info(f"Detected encoding: {encoding} (confidence: {detected['confidence']:.2f})")
            text = raw_data.decode(encoding)
            return text
    except Exception as e:
        logger.warning(f"chardet detection failed: {e}")

    # Step 2: 尝试常见编码
    common_encodings = ['utf-8', 'utf-8-sig', 'latin1', 'cp1252', 'gbk', 'gb2312']
    for enc in common_encodings:
        try:
            text = raw_data.decode(enc)
            logger.info(f"Successfully decoded with: {enc}")
            return text
        except (UnicodeDecodeError, LookupError):
            continue

    # Step 3: 容错回退
    logger.warning(f"Using fallback encoding: {fallback} with error ignoring")
    return raw_data.decode(fallback, errors='ignore')
```

## 修复代码结构

每个修复文件应包含：

```python
"""Guardian Agent Generated Fix
Module: {module_name}
Error Type: {error_type}
Description: {description}
Timestamp: {timestamp}
Confidence: {confidence}
"""

# 1. 导入区 - 只导入必要的包
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 2. 主修复函数
def fix_function_name(*args, **kwargs):
    """修复函数的文档字符串.

    Args:
        ...

    Returns:
        ...

    Raises:
        ...
    """
    # 实现
    pass

# 3. 辅助函数（如需要）
def _helper_function():
    pass

# 4. 应用逻辑（如何集成到原模块）
def apply_fix(context: dict) -> dict:
    """将修复应用到模块上下文.

    Args:
        context: 模块执行上下文

    Returns:
        更新后的上下文
    """
    pass

# 5. 测试入口（可选）
if __name__ == "__main__":
    # 自测代码
    print("Fix self-test...")
```

## 测试策略

### 语法测试

```python
def test_syntax(fix_code: str) -> bool:
    """测试修复代码语法."""
    try:
        compile(fix_code, '<string>', 'exec')
        return True
    except SyntaxError:
        return False
```

### 功能测试

```python
def test_encoding_fix():
    """测试编码修复功能."""
    import tempfile

    # 创建测试文件（GBK 编码）
    with tempfile.NamedTemporaryFile(mode='wb', suffix='.csv', delete=False) as f:
        f.write("标题,内容\n".encode('gbk'))
        f.write("测试,中文内容\n".encode('gbk'))
        test_file = f.name

    # 运行修复
    result = load_documents_with_encoding_fix(test_file)
    assert "中文" in result
    assert "标题" in result

    # 清理
    Path(test_file).unlink()
```

### 集成测试

```python
def test_integration_with_preprocessor():
    """测试修复与预处理模块的集成."""
    # 模拟上下文
    context = {
        "input_file": "test_data.csv",
        "encoding": "auto"
    }

    # 应用修复
    result = apply_fix(context)
    assert result is not None
```

## 代码审查检查清单

生成修复后，自我检查：

- [ ] 语法正确（`compile()` 通过）
- [ ] 没有硬编码路径
- [ ] 包含文档字符串
- [ ] 有异常处理
- [ ] 日志记录关键操作
- [ ] 不修改项目核心代码
- [ ] 返回值与原函数兼容
- [ ] 依赖已在项目中声明
- [ ] 命名符合项目规范
- [ ] 无安全风险（eval, exec 等）
