"""测试会话记忆系统 - 历史轮转 + 上下文续接"""

import tempfile
import sys
from pathlib import Path
from core.context import ConversationContext
from core.llm import Message

# Fix Windows encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')


def test_history_rotation():
    """测试历史轮转功能"""
    with tempfile.TemporaryDirectory() as tmpdir:
        context_file = Path(tmpdir) / "test_context.jsonl"

        # 创建 ConversationContext，设置最多保留 3 个历史文件
        ctx = ConversationContext(
            context_file=context_file,
            max_history_files=3,
            preserve_recent_messages=5,
        )

        # 添加系统消息
        ctx.append_message(Message(role="system", content="You are a helpful assistant."))

        # 添加一些消息
        for i in range(20):
            ctx.append_message(Message(role="user", content=f"User message {i}"))
            ctx.append_message(Message(role="assistant", content=f"Assistant response {i}"))

        print(f"✓ 添加了 {len(ctx)} 条消息")

        # 模拟压缩（不使用 LLM，直接调用轮转）
        ctx._rotate_history_files()

        # 检查 .old1 文件是否存在
        old1 = context_file.with_suffix(".jsonl.old1")
        assert old1.exists(), ".old1 文件应该存在"
        print(f"✓ 历史文件已创建: {old1.name}")

        # 再次添加消息并压缩
        for i in range(20, 40):
            ctx.append_message(Message(role="user", content=f"User message {i}"))
            ctx.append_message(Message(role="assistant", content=f"Assistant response {i}"))

        ctx._rotate_history_files()

        old2 = context_file.with_suffix(".jsonl.old2")
        assert old2.exists(), ".old2 文件应该存在"
        print(f"✓ 历史文件已创建: {old2.name}")

        # 继续压缩，直到触发删除
        for _ in range(3):
            ctx._rotate_history_files()

        # 检查旧文件是否被删除
        old_files = list(Path(tmpdir).glob("test_context.jsonl.old*"))
        print(f"✓ 当前历史文件数量: {len(old_files)} (预期 ≤ 3)")
        assert len(old_files) <= 3, f"历史文件应该不超过 3 个，实际有 {len(old_files)} 个"

        # 列出所有历史文件
        for f in sorted(old_files):
            print(f"  - {f.name}")

        print("\n✅ 历史轮转测试通过！")


def test_session_resumption():
    """测试会话续接功能"""
    with tempfile.TemporaryDirectory() as tmpdir:
        context_file = Path(tmpdir) / "test_context.jsonl"

        # 第一次会话
        ctx1 = ConversationContext(context_file=context_file)
        ctx1.append_message(Message(role="system", content="You are a helpful assistant."))
        ctx1.append_message(Message(role="user", content="Hello, my name is Alice."))
        ctx1.append_message(Message(role="assistant", content="Hello Alice! How can I help you today?"))

        print(f"✓ 第一次会话：{len(ctx1)} 条消息")

        # 模拟会话结束，重新加载
        ctx2 = ConversationContext(context_file=context_file)
        print(f"✓ 重新加载会话：{len(ctx2)} 条消息")

        # 验证历史是否正确加载
        assert len(ctx2) == 3, "历史应该被正确加载"
        assert ctx2.history[1].content == "Hello, my name is Alice.", "用户消息应该被保留"

        print("✓ 历史消息正确加载")

        # 继续对话
        ctx2.append_message(Message(role="user", content="What's my name?"))
        ctx2.append_message(Message(role="assistant", content="Your name is Alice."))

        print(f"✓ 继续对话：{len(ctx2)} 条消息")

        # 再次重新加载
        ctx3 = ConversationContext(context_file=context_file)
        assert len(ctx3) == 5, "完整历史应该被保留"
        print("✓ 完整历史正确加载")

        print("\n✅ 会话续接测试通过！")


def test_preserve_recent_config():
    """测试 preserve_recent_messages 配置"""
    with tempfile.TemporaryDirectory() as tmpdir:
        context_file = Path(tmpdir) / "test_context.jsonl"

        # 创建上下文，设置保留最近 50 条消息
        ctx = ConversationContext(
            context_file=context_file,
            preserve_recent_messages=50,
        )

        # 添加系统消息
        ctx.append_message(Message(role="system", content="You are a helpful assistant."))

        # 添加 100 条消息
        for i in range(100):
            ctx.append_message(Message(role="user", content=f"Message {i}"))
            ctx.append_message(Message(role="assistant", content=f"Response {i}"))

        print(f"✓ 添加了 {len(ctx)} 条消息")

        # 检查配置是否正确
        assert ctx.preserve_recent_messages == 50, "preserve_recent_messages 应该为 50"
        print(f"✓ preserve_recent_messages = {ctx.preserve_recent_messages}")

        print("\n✅ 配置测试通过！")


if __name__ == "__main__":
    print("=" * 60)
    print("测试会话记忆系统")
    print("=" * 60)

    test_history_rotation()
    print()

    test_session_resumption()
    print()

    test_preserve_recent_config()

    print("\n" + "=" * 60)
    print("所有测试通过！✅")
    print("=" * 60)
