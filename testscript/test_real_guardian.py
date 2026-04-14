#!/usr/bin/env python
"""测试真实的 GuardianSoul 运行（需要 API Key）.

这个脚本会：
1. 加载 .env 文件中的 LLM 配置
2. 创建真实的 LLM Provider
3. 模拟一个错误场景
4. 让 GuardianSoul 使用真实 LLM 分析并修复
"""

from pathlib import Path
import sys
import json
import os

# 加载环境变量
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("[WARN] python-dotenv not installed, using system environment variables")
    print("  Install with: pip install python-dotenv\n")

sys.path.insert(0, str(Path(__file__).parent))

from core.llm import create_provider
from core.guardian_soul import GuardianSoul


def check_api_config():
    """检查 API 配置."""
    print("=" * 70)
    print("LLM API 配置检查")
    print("=" * 70)

    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    model = os.environ.get("OPENAI_MODEL", os.environ.get("DEFAULT_LLM_MODEL", ""))

    print(f"\nAPI Key: {api_key[:20]}...{api_key[-10:] if len(api_key) > 30 else '(too short)'}")
    print(f"Base URL: {base_url or '(not set, will use OpenAI default)'}")
    print(f"Model: {model or '(not set, will use default)'}")

    if not api_key or api_key == "sk-or-v1-your-api-key-here":
        print("\n[ERROR] API Key 未配置或使用的是示例 Key")
        print("\n请按照以下步骤配置：")
        print("  1. 复制 .env.example 为 .env")
        print("  2. 编辑 .env 文件，填入您的真实 API Key")
        print("  3. 配置 OPENAI_BASE_URL（如果使用非 OpenAI 服务）")
        print("  4. 配置 OPENAI_MODEL 或 DEFAULT_LLM_MODEL")
        return False

    print("\n[OK] API 配置检查通过\n")
    return True


def test_simple_error_analysis():
    """测试简单的错误分析场景."""
    print("=" * 70)
    print("GuardianSoul 真实运行测试 - 场景 1：文件不存在错误")
    print("=" * 70)

    # 创建 LLM Provider
    config = {
        "provider": "openai",
        "api_key": os.environ.get("OPENAI_API_KEY"),
        "base_url": os.environ.get("OPENAI_BASE_URL"),
        "model": os.environ.get("OPENAI_MODEL") or os.environ.get("DEFAULT_LLM_MODEL"),
    }

    print(f"\n初始化 LLM Provider...")
    print(f"  Model: {config['model']}")

    try:
        llm = create_provider(config)
    except Exception as e:
        print(f"[ERROR] 创建 LLM Provider 失败: {e}")
        return False

    # 创建 GuardianSoul
    workspace = Path(__file__).parent
    soul = GuardianSoul(
        module_name="paper_fetcher",
        llm=llm,
        workspace_dir=workspace
    )

    # 模拟错误场景
    error = FileNotFoundError("papers.csv not found in workspace")
    context = {
        "input_data": {
            "query": "machine learning",
            "max_papers": 100
        },
        "config": {
            "require_abstract": True,
            "batch_size": 20
        },
        "previous_outputs": {
            "query_generator": {
                "status": "success",
                "output_file": "runs/test/query.json"
            }
        }
    }

    print(f"\n模拟错误：")
    print(f"  模块: paper_fetcher")
    print(f"  错误: {error}")
    print(f"\n运行 GuardianSoul（这可能需要 10-30 秒）...")
    print("-" * 70)

    try:
        decision = soul.activate(error=error, context=context)

        print("\n" + "=" * 70)
        print("运行结果:")
        print("-" * 70)
        print(f"结果: {decision.outcome}")
        print(f"模块: {decision.module}")
        print(f"修复已生成: {decision.fix_generated}")

        if decision.fix_path:
            print(f"\n修复文件: {decision.fix_path}")

        if decision.analysis:
            print(f"\n错误分析:")
            print(f"  根因: {decision.analysis.root_cause[:200]}...")

        print("\n" + "=" * 70)

        if decision.outcome == "success":
            print("[OK] GuardianSoul 成功处理错误")
            return True
        else:
            print("[INFO] GuardianSoul 无法自动修复（可能需要人工介入）")
            return False

    except Exception as e:
        print(f"\n[ERROR] GuardianSoul 运行失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_api_connection():
    """测试 API 连接."""
    print("\n" * 2)
    print("=" * 70)
    print("LLM API 连接测试")
    print("=" * 70)

    config = {
        "provider": "openai",
        "api_key": os.environ.get("OPENAI_API_KEY"),
        "base_url": os.environ.get("OPENAI_BASE_URL"),
        "model": os.environ.get("OPENAI_MODEL") or os.environ.get("DEFAULT_LLM_MODEL"),
    }

    try:
        llm = create_provider(config)

        print(f"\n发送测试请求到 {config['model']}...")

        from core.llm import Message
        messages = [
            Message(role="system", content="You are a helpful assistant."),
            Message(role="user", content="Say 'Hello, Guardian!' in one line."),
        ]

        response = llm.chat(messages=messages, temperature=0.3, max_tokens=50)

        print(f"\n响应: {response.content}")
        print(f"Token 使用: {response.usage}")

        if response.content and "guardian" in response.content.lower():
            print("\n[OK] API 连接成功，LLM 正常响应")
            return True
        else:
            print("\n[WARN] API 连接成功，但响应内容异常")
            return False

    except Exception as e:
        print(f"\n[ERROR] API 连接失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("\n" * 2)
    print("=" * 70)
    print("GuardianSoul 真实运行测试")
    print("=" * 70)
    print("\n")

    # Step 1: 检查 API 配置
    if not check_api_config():
        print("\n[ABORT] 请先配置 API Key")
        return

    # Step 2: 测试 API 连接
    api_ok = test_api_connection()
    if not api_ok:
        print("\n[ABORT] API 连接失败，请检查配置")
        return

    # Step 3: 测试 GuardianSoul
    print("\n" * 2)
    success = test_simple_error_analysis()

    print("\n" * 2)
    print("=" * 70)
    if success:
        print("[SUCCESS] GuardianSoul 真实运行测试通过")
    else:
        print("[INFO] GuardianSoul 测试完成，但可能需要调整")
    print("=" * 70)
    print("\n")


if __name__ == "__main__":
    main()
