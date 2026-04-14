#!/usr/bin/env python
"""GuardianSoul 持续交互式 Guardian Agent 测试.

使用 MockProvider 模拟 LLM 调用，验证：
1. 基本激活和错误分析
2. 多轮工具调用（读文件→生成修复→提交决策）
3. 达到最大步数时的优雅终止
4. 继续对话功能
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from core.llm import MockProvider, LLMResponse, ToolCall
from core.guardian_soul import GuardianSoul


def test_basic_activation():
    """Test 1: 基本激活和错误分析."""
    print("=" * 70)
    print("TEST 1: GuardianSoul 基本激活")
    print("=" * 70)

    responses = [
        # 第一轮: LLM 分析错误并直接 finish
        LLMResponse(
            content="这是一个编码错误，文件使用了非 UTF-8 编码。",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="finish",
                    arguments=json.dumps({
                        "outcome": "success",
                        "summary": "检测到编码错误，需要添加编码检测功能",
                        "confidence": 0.9,
                    }),
                )
            ],
            finish_reason="tool_calls",
        ),
    ]

    provider = MockProvider(responses=responses)

    with tempfile.TemporaryDirectory() as tmpdir:
        soul = GuardianSoul(
            module_name="preprocessor",
            llm=provider,
            workspace_dir=Path(tmpdir),
            max_steps=10,
        )

        error = UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 2, "invalid start byte")
        context = {"input_file": "test.csv", "n_docs": 100}

        decision = soul.activate(error, context)

        print(f"\n  Module: {decision.module}")
        print(f"  Outcome: {decision.outcome}")
        print(f"  Confidence: {decision.analysis.confidence}")
        print(f"  Summary: {decision.analysis.root_cause}")

        assert decision.module == "preprocessor"
        assert decision.outcome == "success"
        assert decision.analysis.confidence == 0.9

        # 验证 LLM 被调用
        assert len(provider.call_log) >= 1
        print(f"  LLM calls: {len(provider.call_log)}")

        print("  [OK] 基本激活测试通过")

    print("=" * 70)
    print("[SUCCESS] TEST 1 PASSED!")
    print("=" * 70)


def test_multi_turn_tools():
    """Test 2: 多轮工具调用."""
    print("\n" + "=" * 70)
    print("TEST 2: 多轮工具调用")
    print("=" * 70)

    responses = [
        # 第一轮: LLM 请求读取文件
        LLMResponse(
            content="让我先查看错误日志和相关文件。",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="read_file",
                    arguments=json.dumps({"path": "agent_logs/preprocessor.log"}),
                )
            ],
            finish_reason="tool_calls",
        ),
        # 第二轮: LLM 搜索相关文件
        LLMResponse(
            content="找到了日志，现在搜索相关修复模板。",
            tool_calls=[
                ToolCall(
                    id="call_2",
                    name="search_files",
                    arguments=json.dumps({"pattern": "*encoding*"}),
                )
            ],
            finish_reason="tool_calls",
        ),
        # 第三轮: LLM 生成修复
        LLMResponse(
            content="根据分析，这是一个编码错误。生成修复代码。",
            tool_calls=[
                ToolCall(
                    id="call_3",
                    name="generate_fix",
                    arguments=json.dumps({
                        "code": "def fix_encoding(f):\n    return open(f, encoding='utf-8').read()\n",
                        "description": "使用 UTF-8 编码读取文件",
                        "error_type": "encoding",
                    }),
                )
            ],
            finish_reason="tool_calls",
        ),
        # 第四轮: LLM 测试修复
        LLMResponse(
            content="修复代码已生成，让我验证一下。",
            tool_calls=[
                ToolCall(
                    id="call_4",
                    name="run_command",
                    arguments=json.dumps({
                        "command": "python -c \"compile('def fix_encoding(f):\\n    return open(f, encoding=chr(39)+chr(117)+chr(116)+chr(102)+chr(45)+chr(56)+chr(39)).read()\\n', '<string>', 'exec')\"",
                        "timeout": 10,
                    }),
                )
            ],
            finish_reason="tool_calls",
        ),
        # 第五轮: 提交最终决策
        LLMResponse(
            content="验证通过，提交最终决策。",
            tool_calls=[
                ToolCall(
                    id="call_5",
                    name="finish",
                    arguments=json.dumps({
                        "outcome": "success",
                        "summary": "编码错误已分析并生成修复：使用 UTF-8 编码读取文件",
                        "confidence": 0.85,
                    }),
                )
            ],
            finish_reason="tool_calls",
        ),
    ]

    provider = MockProvider(responses=responses)

    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建一些测试文件供工具操作
        workspace = Path(tmpdir)
        log_dir = workspace / "agent_logs"
        log_dir.mkdir(parents=True)
        (log_dir / "preprocessor.log").write_text(
            "ERROR: UnicodeDecodeError in load_documents\ntest.csv: encoding error\n",
            encoding="utf-8",
        )

        soul = GuardianSoul(
            module_name="preprocessor",
            llm=provider,
            workspace_dir=workspace,
            max_steps=10,
        )

        error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")
        decision = soul.activate(error, {"input_file": "test.csv"})

        print(f"\n  Module: {decision.module}")
        print(f"  Outcome: {decision.outcome}")
        print(f"  Fix generated: {decision.fix_generated}")
        print(f"  Fix path: {decision.fix_path}")
        print(f"  Test passed: {decision.test_passed}")

        assert decision.outcome == "success"
        assert decision.fix_generated
        assert decision.test_passed

        # 验证 LLM 调用了 5 轮
        print(f"  LLM rounds: {len(provider.call_log)}")
        assert len(provider.call_log) >= 4

        # 验证修复文件被保存
        fixes_dir = workspace / "fixes"
        assert fixes_dir.exists()
        fix_files = list(fixes_dir.glob("guardian_fix_*.py"))
        assert len(fix_files) >= 1
        print(f"  Fix file: {fix_files[0].name}")

        # 验证决策日志
        log_file = workspace / "agent_logs" / "preprocessor_guardian.json"
        assert log_file.exists()
        with open(log_file, "r", encoding="utf-8") as f:
            logs = json.load(f)
        assert len(logs) >= 1
        print(f"  Decision logs: {len(logs)}")

        print("  [OK] 多轮工具调用测试通过")

    print("=" * 70)
    print("[SUCCESS] TEST 2 PASSED!")
    print("=" * 70)


def test_max_steps():
    """Test 3: 达到最大步数时优雅终止."""
    print("\n" + "=" * 70)
    print("TEST 3: 最大步数限制")
    print("=" * 70)

    # LLM 每轮只回复文本，不调用 finish
    responses = [
        LLMResponse(
            content="分析中...",
            tool_calls=[
                ToolCall(
                    id=f"call_{i}",
                    name="search_files",
                    arguments=json.dumps({"pattern": "*.py"}),
                )
            ],
            finish_reason="tool_calls",
        )
        for i in range(10)
    ]

    provider = MockProvider(responses=responses)

    with tempfile.TemporaryDirectory() as tmpdir:
        soul = GuardianSoul(
            module_name="preprocessor",
            llm=provider,
            workspace_dir=Path(tmpdir),
            max_steps=3,  # 只允许 3 步
        )

        error = ValueError("test error")
        decision = soul.activate(error, {})

        print(f"\n  Outcome: {decision.outcome}")
        print(f"  LLM calls made: {len(provider.call_log)}")

        # 应该在 3 步后终止
        assert len(provider.call_log) <= 3
        print("  [OK] 最大步数限制测试通过")

    print("=" * 70)
    print("[SUCCESS] TEST 3 PASSED!")
    print("=" * 70)


def test_continue_dialogue():
    """Test 4: 继续对话功能."""
    print("\n" + "=" * 70)
    print("TEST 4: 继续对话")
    print("=" * 70)

    # 初始激活的响应
    init_responses = [
        LLMResponse(
            content="分析完成，生成了修复代码。",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="generate_fix",
                    arguments=json.dumps({
                        "code": "def fix(): return 'fixed'",
                        "description": "测试修复",
                        "error_type": "test",
                    }),
                )
            ],
            finish_reason="tool_calls",
        ),
        LLMResponse(
            content="提交决策。",
            tool_calls=[
                ToolCall(
                    id="call_2",
                    name="finish",
                    arguments=json.dumps({
                        "outcome": "success",
                        "summary": "修复完成",
                        "confidence": 0.8,
                    }),
                )
            ],
            finish_reason="tool_calls",
        ),
    ]

    provider = MockProvider(responses=init_responses)

    with tempfile.TemporaryDirectory() as tmpdir:
        soul = GuardianSoul(
            module_name="test_module",
            llm=provider,
            workspace_dir=Path(tmpdir),
            max_steps=10,
        )

        error = RuntimeError("test error")
        decision = soul.activate(error, {})

        print(f"\n  初始激活结果: {decision.outcome}")
        assert decision.outcome == "success"

        # 用户追问
        dialogue_responses = [
            LLMResponse(
                content="根据追问，我建议进一步优化。",
                tool_calls=[
                    ToolCall(
                        id="call_3",
                        name="finish",
                        arguments=json.dumps({
                            "outcome": "success",
                            "summary": "进一步优化完成",
                            "confidence": 0.85,
                        }),
                    )
                ],
                finish_reason="tool_calls",
            ),
        ]

        provider2 = MockProvider(responses=dialogue_responses)
        soul.llm = provider2
        # 注入初始系统消息保持上下文
        soul.messages = soul.messages[:1]  # 只保留 system prompt

        decision2 = soul.continue_dialogue("能否进一步优化修复方案？")

        print(f"  对话继续结果: {decision2.outcome}")
        assert decision2.outcome == "success"

        print("  [OK] 继续对话测试通过")

    print("=" * 70)
    print("[SUCCESS] TEST 4 PASSED!")
    print("=" * 70)


def test_orchestrator_integration():
    """Test 5: Orchestrator 集成测试."""
    print("\n" + "=" * 70)
    print("TEST 5: Orchestrator GuardianSoul 集成")
    print("=" * 70)

    from core.orchestrator import PipelineOrchestrator
    from core.state_manager import StateManager
    from modules.registry import ModuleRegistry

    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建 Orchestrator（不配置 LLM，使用模板 Guardian）
        registry = ModuleRegistry()
        state_mgr = StateManager(Path(tmpdir) / "state")

        orchestrator = PipelineOrchestrator(
            registry=registry,
            state_manager=state_mgr,
            config={},  # 无 LLM 配置
        )

        print(f"\n  LLM initialized: {orchestrator._llm is not None}")
        assert orchestrator._llm is None  # 未配置 LLM

        print("  [OK] Orchestrator 无 LLM 模式正常")

        # 测试 LLM 配置
        orchestrator_llm = PipelineOrchestrator(
            registry=registry,
            state_manager=state_mgr,
            config={"llm": {"provider": "mock"}},
        )

        print(f"  LLM with mock: {orchestrator_llm._llm is not None}")
        assert orchestrator_llm._llm is not None

        print("  [OK] Orchestrator Mock LLM 模式正常")

    print("=" * 70)
    print("[SUCCESS] TEST 5 PASSED!")
    print("=" * 70)


if __name__ == "__main__":
    print("\n")
    print("+" + "=" * 68 + "+")
    print("|" + " " * 68 + "|")
    print("|" + " " * 12 + "GUARDIAN SOUL TEST SUITE" + " " * 32 + "|")
    print("|" + " " * 68 + "|")
    print("+" + "=" * 68 + "+")
    print("\n")

    try:
        test_basic_activation()
        test_multi_turn_tools()
        test_max_steps()
        test_continue_dialogue()
        test_orchestrator_integration()

        print("\n")
        print("+" + "=" * 68 + "+")
        print("|" + " " * 68 + "|")
        print("|" + " " * 22 + "ALL TESTS PASSED!" + " " * 26 + "|")
        print("|" + " " * 68 + "|")
        print("+" + "=" * 68 + "+")
        print("\n")

        print("GuardianSoul 持续交互系统验证完成!")
        print("\n架构特性:")
        print("  1. LLM 驱动的多轮分析（类似 kimi-cli KimiSoul）")
        print("  2. 工具调用循环（读文件、搜索、执行命令、生成修复）")
        print("  3. 自动激活：流水线出错时自动唤醒 GuardianSoul")
        print("  4. 模板回退：无 LLM 时使用 GuardianAgent 模板")
        print("  5. 继续对话：用户可追加指令持续交互")
        print("  6. 步数限制：防止无限循环")

        sys.exit(0)

    except Exception as e:
        print("\n")
        print("+" + "=" * 68 + "+")
        print("|" + " " * 68 + "|")
        print("|" + " " * 24 + "TESTS FAILED!" + " " * 28 + "|")
        print("|" + " " * 68 + "|")
        print("+" + "=" * 68 + "+")
        print("\n")

        import traceback
        traceback.print_exc()

        sys.exit(1)
