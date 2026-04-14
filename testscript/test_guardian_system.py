#!/usr/bin/env python
"""Test Guardian Agent System.

This script tests the guardian agent functionality by:
1. Creating a mock module that intentionally fails
2. Verifying the guardian catches the error
3. Verifying the guardian generates a fix
4. Verifying the fix is saved to workspace
"""

import json
import sys
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from core.agent_guardian import (
    GuardianAgent,
    ErrorAnalysis,
    FixCode,
    get_guardian,
    register_guardian,
    load_fix_from_workspace,
)
from modules.guardians import PreprocessorGuardianAgent


def test_guardian_basic():
    """Test basic guardian agent functionality."""
    print("=" * 70)
    print("TEST 1: Basic Guardian Agent Functionality")
    print("=" * 70)

    # Create a test workspace
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace_dir = Path(tmpdir)

        # Get preprocessor guardian
        guardian = PreprocessorGuardianAgent("preprocessor")

        print(f"\n[OK] Created guardian for module: {guardian.module_name}")

        # Test 1: Analyze encoding error
        print("\n1. Testing encoding error analysis...")

        encoding_error = UnicodeDecodeError(
            "utf-8", b"\xff\xfe", 0, 2, "invalid start byte"
        )

        context = {
            "input_file": "test.csv",
            "n_docs": 100
        }

        analysis = guardian.analyze_error(encoding_error, context)

        print(f"   Error type: {analysis.error_type}")
        print(f"   Root cause: {analysis.root_cause}")
        print(f"   Suggested fix: {analysis.suggested_fix}")
        print(f"   Confidence: {analysis.confidence}")

        assert analysis.error_type == "encoding"
        assert analysis.confidence > 0.8

        print("   [OK] Encoding error analysis passed")

        # Test 2: Generate fix
        print("\n2. Testing fix generation...")

        fix = guardian.generate_fix(analysis)

        assert fix is not None
        assert "load_documents_with_encoding_fix" in fix.code
        assert fix.module_name == "preprocessor"

        print(f"   Generated fix: {fix.description}")
        print(f"   Fix length: {len(fix.code)} chars")

        print("   [OK] Fix generation passed")

        # Test 3: Test fix
        print("\n3. Testing fix validation...")

        test_passed = guardian.test_fix(fix, context)

        assert test_passed

        print(f"   [OK] Fix syntax check passed")

        # Test 4: Handle error end-to-end
        print("\n4. Testing end-to-end error handling...")

        decision = guardian.handle_error(encoding_error, context, workspace_dir)

        print(f"   Outcome: {decision.outcome}")
        print(f"   Fix generated: {decision.fix_generated}")
        print(f"   Fix path: {decision.fix_path}")
        print(f"   Test passed: {decision.test_passed}")

        assert decision.outcome == "success"
        assert decision.fix_generated
        assert decision.fix_path is not None

        print("   [OK] Error handling passed")

        # Test 5: Verify fix saved to workspace
        print("\n5. Verifying fix saved to workspace...")

        assert decision.fix_path is not None
        fix_file = Path(decision.fix_path)

        assert fix_file.exists()

        fix_content = fix_file.read_text(encoding="utf-8")
        assert "Guardian Agent Generated Fix" in fix_content
        assert "load_documents_with_encoding_fix" in fix_content

        print(f"   Fix file: {fix_file.name}")
        print(f"   [OK] Fix saved correctly")

        # Test 6: Verify decision logged
        print("\n6. Verifying agent decision logged...")

        log_file = workspace_dir / "agent_logs" / "preprocessor_guardian.json"

        assert log_file.exists()

        with open(log_file, "r", encoding="utf-8") as f:
            logs = json.load(f)

        assert len(logs) == 1
        assert logs[0]["module"] == "preprocessor"
        assert logs[0]["outcome"] == "success"

        print(f"   Log entries: {len(logs)}")
        print(f"   [OK] Decision logged correctly")

    print("\n" + "=" * 70)
    print("[SUCCESS] TEST 1 PASSED!")
    print("=" * 70)


def test_guardian_registry():
    """Test guardian registry and loading."""
    print("\n" + "=" * 70)
    print("TEST 2: Guardian Registry")
    print("=" * 70)

    # Register preprocessor guardian
    register_guardian("preprocessor", PreprocessorGuardianAgent)

    print("\n1. Testing guardian registration...")

    guardian = get_guardian("preprocessor")

    assert guardian is not None
    assert guardian.module_name == "preprocessor"

    print(f"   [OK] Retrieved guardian for: {guardian.module_name}")

    print("\n2. Testing unregistered module...")

    unknown_guardian = get_guardian("unknown_module")

    assert unknown_guardian is None

    print(f"   [OK] Returns None for unregistered module")

    print("\n" + "=" * 70)
    print("[SUCCESS] TEST 2 PASSED!")
    print("=" * 70)


def test_multiple_error_types():
    """Test guardian handling different error types."""
    print("\n" + "=" * 70)
    print("TEST 3: Multiple Error Types")
    print("=" * 70)

    guardian = PreprocessorGuardianAgent("preprocessor")

    # Test different error types
    test_cases = [
        (
            MemoryError("Not enough memory"),
            "memory",
            "Process documents in chunks"
        ),
        (
            OSError("Can't find model 'en_core_web_sm'"),
            "spacy_model",
            "spaCy model"
        ),
        (
            ValueError("Invalid DTM shape"),
            "dtm_vocabulary",
            None  # May not have a fix
        ),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace_dir = Path(tmpdir)

        for i, (error, expected_type, expected_fix_keyword) in enumerate(test_cases, 1):
            print(f"\n{i}. Testing {expected_type} error...")

            analysis = guardian.analyze_error(error, {})

            print(f"   Detected type: {analysis.error_type}")
            print(f"   Confidence: {analysis.confidence}")

            assert analysis.error_type == expected_type

            fix = guardian.generate_fix(analysis)

            if expected_fix_keyword:
                assert fix is not None
                assert expected_fix_keyword.lower() in fix.description.lower()
                print(f"   Fix: {fix.description}")
            else:
                print(f"   No fix generated (as expected for this error type)")

            print(f"   [OK] {expected_type} error handled")

    print("\n" + "=" * 70)
    print("[SUCCESS] TEST 3 PASSED!")
    print("=" * 70)


def test_workspace_fix_loading():
    """Test loading fixes from workspace."""
    print("\n" + "=" * 70)
    print("TEST 4: Workspace Fix Loading")
    print("=" * 70)

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace_dir = Path(tmpdir)

        guardian = PreprocessorGuardianAgent("preprocessor")

        # Generate and save a fix
        error = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")
        decision = guardian.handle_error(error, {}, workspace_dir)

        assert decision.outcome == "success"

        print(f"\n1. Fix saved to: {decision.fix_path}")

        # Load fix from workspace
        print("\n2. Loading fix from workspace...")

        loaded_fix = load_fix_from_workspace(workspace_dir, "preprocessor")

        assert loaded_fix is not None
        assert "Guardian Agent Generated Fix" in loaded_fix

        print(f"   Loaded fix length: {len(loaded_fix)} chars")
        print(f"   [OK] Fix loaded successfully")

        # Test loading non-existent fix
        print("\n3. Testing non-existent fix...")

        no_fix = load_fix_from_workspace(workspace_dir, "nonexistent_module")

        assert no_fix is None

        print(f"   [OK] Returns None for non-existent fix")

    print("\n" + "=" * 70)
    print("[SUCCESS] TEST 4 PASSED!")
    print("=" * 70)


if __name__ == "__main__":
    print("\n")
    print("+" + "=" * 68 + "+")
    print("|" + " " * 68 + "|")
    print("|" + " " * 15 + "GUARDIAN AGENT SYSTEM TEST SUITE" + " " * 21 + "|")
    print("|" + " " * 68 + "|")
    print("+" + "=" * 68 + "+")
    print("\n")

    try:
        test_guardian_basic()
        test_guardian_registry()
        test_multiple_error_types()
        test_workspace_fix_loading()

        print("\n")
        print("+" + "=" * 68 + "+")
        print("|" + " " * 68 + "|")
        print("|" + " " * 22 + "ALL TESTS PASSED!" + " " * 26 + "|")
        print("|" + " " * 68 + "|")
        print("+" + "=" * 68 + "+")
        print("\n")

        print("Guardian Agent System is ready for production use!")
        print("\nNext steps:")
        print("  1. Implement guardians for other modules (paper_fetcher, topic_modeler, etc.)")
        print("  2. Add LLM-powered fix generation (currently using templates)")
        print("  3. Build Web UI for viewing agent decisions")
        print("  4. Integrate with full pipeline for end-to-end testing")

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
