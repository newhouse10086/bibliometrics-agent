#!/usr/bin/env python
"""验证所有工具完整性."""

from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parent))

from core.tools import create_default_registry

def test_all_tools():
    print("=" * 70)
    print("工具系统完整性验证")
    print("=" * 70)

    registry = create_default_registry(Path(__file__).parent)

    print("\n已注册工具:")
    for tool_name in registry.list_tools():
        print(f"  - {tool_name}")

    print("\n" + "=" * 70)

    # 测试每个工具
    tests = [
        ("ReadFile", {"file_path": "README.md"}),
        ("WriteFile", {"file_path": "test_output.txt", "content": "test content"}),
        ("Glob", {"pattern": "*.py"}),
        ("Grep", {"pattern": "import", "file_pattern": "*.py"}),
        ("Shell", {"command": "echo test"}),
        ("WebSearch", {"query": "Python requests library", "max_results": 3}),
        ("WebFetch", {"url": "https://httpbin.org/html"}),
        ("HttpRequest", {"url": "https://httpbin.org/get", "method": "GET"}),
    ]

    for tool_name, kwargs in tests:
        print(f"\n测试 {tool_name}...")
        tool = registry.get(tool_name)

        if not tool:
            print(f"  [FAIL] 工具未注册")
            continue

        try:
            result = tool.run(**kwargs)

            if result.success:
                output_info = ""
                if isinstance(result.output, str):
                    output_info = f", 输出: {len(result.output)} 字符"
                elif isinstance(result.output, list):
                    output_info = f", 结果: {len(result.output)} 条"
                elif result.output:
                    output_info = f", 输出: {type(result.output).__name__}"

                print(f"  [OK] 成功{output_info}")
            else:
                print(f"  [FAIL] 执行失败")
                if result.error:
                    print(f"  错误: {result.error[:100]}")

        except Exception as e:
            print(f"  [FAIL] 异常: {str(e)[:100]}")

    print("\n" + "=" * 70)
    print("[OK] 工具系统验证完成")
    print("=" * 70)

if __name__ == "__main__":
    test_all_tools()
