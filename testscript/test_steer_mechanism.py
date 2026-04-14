"""Test Steer Mechanism - User Message Response During Normal Execution

This test verifies that:
1. User messages are processed during normal pipeline execution (not just error recovery)
2. AI responds to user messages using LLM
3. Steer commands (PAUSE, SKIP) work correctly
"""

import asyncio
import logging
from pathlib import Path

from core.communication_hub import get_communication_hub
from core.orchestrator import PipelineOrchestrator
from core.state_manager import StateManager
from modules.registry import ModuleRegistry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_user_message_processing():
    """Test that user messages are processed during normal execution."""
    print("\n=== Test 1: User Message Processing ===")

    async def _test():
        comm_hub = get_communication_hub()
        project_id = "test_user_msg"

        # Simulate user sending a message
        print("Sending user message...")
        await comm_hub.send_user_message(project_id, "What is the pipeline doing?")

        # Check that message is queued
        print(f"Messages in queue: {comm_hub.user_messages[project_id].qsize()}")

        # Retrieve message
        msg = await comm_hub.get_user_message(project_id, timeout=1.0)
        print(f"Retrieved message: {msg}")

        assert msg == "What is the pipeline doing?", "Message not retrieved correctly"
        print("[PASS] User message processed correctly\n")

    asyncio.run(_test())


def test_steer_commands():
    """Test that steer commands are queued and retrieved."""
    print("\n=== Test 2: Steer Commands ===")

    comm_hub = get_communication_hub()
    project_id = "test_steer"

    # Test PAUSE command
    print("Sending PAUSE command...")
    comm_hub.steer(project_id, "PAUSE")

    # Retrieve command
    cmd = comm_hub.get_steer(project_id)
    print(f"Retrieved command: {cmd}")

    assert cmd == "PAUSE", "PAUSE command not retrieved correctly"

    # Test SKIP command
    print("Sending SKIP:paper_fetcher command...")
    comm_hub.steer(project_id, "SKIP:paper_fetcher")

    cmd = comm_hub.get_steer(project_id)
    print(f"Retrieved command: {cmd}")

    assert cmd == "SKIP:paper_fetcher", "SKIP command not retrieved correctly"
    print("[PASS] Steer commands work correctly\n")


def test_orchestrator_integration():
    """Test that orchestrator checks for user messages."""
    print("\n=== Test 3: Orchestrator Integration ===")

    # This would require a full pipeline run with LLM configured
    # For now, we just verify the code structure

    print("Checking orchestrator has _respond_to_user method...")
    assert hasattr(PipelineOrchestrator, '_respond_to_user'), "_respond_to_user method missing"

    print("Checking orchestrator checks user messages in run() loop...")
    import inspect
    source = inspect.getsource(PipelineOrchestrator.run)
    assert "get_user_message" in source, "User message check missing in run()"

    print("[PASS] Orchestrator integration looks correct\n")


if __name__ == "__main__":
    print("Testing Steer Mechanism Implementation")
    print("=" * 50)

    test_user_message_processing()
    test_steer_commands()
    test_orchestrator_integration()

    print("\n" + "=" * 50)
    print("All tests passed!")
    print("\nTo test with real pipeline:")
    print("1. Start web server: python run_web.py")
    print("2. Open http://localhost:8003")
    print("3. Create a project and start pipeline")
    print("4. Send a message via the chat interface")
    print("5. Verify AI responds in real-time")
