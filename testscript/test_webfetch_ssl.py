#!/usr/bin/env python
"""验证 WebFetch SSL 修复."""

from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from core.tools.web_tools import WebFetch

def test_webfetch_ssl():
    print("=" * 70)
    print("WebFetch SSL 修复验证")
    print("=" * 70)

    fetcher = WebFetch(Path(__file__).parent)

    test_urls = [
        "https://httpbin.org/html",
        "https://www.example.com",
        "https://api.github.com",
    ]

    for url in test_urls:
        print(f"\n测试: {url}")
        result = fetcher.run(url=url)

        if result.success:
            print(f"  [OK] 成功，大小: {len(result.output)} 字符")
        else:
            print(f"  [FAIL] 失败")
            print(f"  错误: {result.error[:200] if result.error else 'N/A'}")

    print("\n" + "=" * 70)
    print("[OK] WebFetch SSL 验证完成")
    print("=" * 70)

if __name__ == "__main__":
    test_webfetch_ssl()
