"""Real-time Communication Hub for AI Workflow Visualization.

Enables real-time interaction between users and AI agents (GuardianSoul).
Similar to Cursor's AI chat interface.
"""

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class MessageType(Enum):
    """Types of messages in AI workflow."""

    # AI messages
    AI_THINKING = "ai_thinking"  # AI is analyzing
    AI_TOOL_CALL = "ai_tool_call"  # AI is calling a tool
    AI_TOOL_RESULT = "ai_tool_result"  # Tool execution result
    AI_DECISION = "ai_decision"  # AI made a decision
    AI_ERROR = "ai_error"  # AI encountered an error
    AI_SUCCESS = "ai_success"  # AI successfully fixed

    # User messages
    USER_MESSAGE = "user_message"  # User sent a message
    USER_CONFIRMATION = "user_confirmation"  # User confirmed an action

    # System messages
    SYSTEM_MODULE_START = "module_start"  # Module started
    SYSTEM_MODULE_ERROR = "module_error"  # Module failed
    SYSTEM_MODULE_SUCCESS = "module_success"  # Module succeeded
    SYSTEM_GUARDIAN_ACTIVATED = "guardian_activated"  # Guardian activated

    # Module injection
    MODULE_INJECTION_REQUEST = "module_injection_request"  # Request to inject new module
    MODULE_INJECTION_RESULT = "module_injection_result"  # Injection result (approved/rejected)


@dataclass
class WorkflowMessage:
    """A message in the AI workflow."""

    type: MessageType
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


class CommunicationHub:
    """Central hub for real-time AI-user communication.

    Features:
    - Broadcast AI thinking process to all connected clients
    - Receive user messages and forward to AI agents
    - Support user confirmation requests
    - Maintain conversation history
    """

    def __init__(self):
        # WebSocket connections per project
        self.connections: Dict[str, List[Any]] = defaultdict(list)

        # Message history per project
        self.history: Dict[str, List[WorkflowMessage]] = defaultdict(list)

        # User message queues (for AI to read)
        self.user_messages: Dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)

        # Steer queues (for pipeline control: PAUSE, SKIP, etc.)
        self.steer_queues: Dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)

        # Callbacks for handling user messages
        self.message_handlers: Dict[str, Callable] = {}

        logger.info("CommunicationHub initialized")

    async def connect(self, project_id: str, websocket: Any):
        """Register a WebSocket connection for a project."""
        self.connections[project_id].append(websocket)
        # Don't overwrite existing queues on reconnect
        if project_id not in self.user_messages:
            self.user_messages[project_id] = asyncio.Queue()
        if project_id not in self.steer_queues:
            self.steer_queues[project_id] = asyncio.Queue()

        logger.info(f"WebSocket connected for project {project_id}. Total: {len(self.connections[project_id])}")

        # Don't send history on connect — selectProject() already loads
        # history via HTTP /chat-history. Sending here causes duplicates.

    def disconnect(self, project_id: str, websocket: Any):
        """Unregister a WebSocket connection."""
        if websocket in self.connections[project_id]:
            self.connections[project_id].remove(websocket)

        logger.info(f"WebSocket disconnected for project {project_id}. Remaining: {len(self.connections[project_id])}")

    async def broadcast(self, project_id: str, message: WorkflowMessage):
        """Broadcast a message to all connected clients for a project."""
        # Store in history
        self.history[project_id].append(message)

        # Limit history size
        if len(self.history[project_id]) > 1000:
            self.history[project_id] = self.history[project_id][-500:]

        # Send to all connections
        disconnected = []
        for websocket in self.connections[project_id]:
            try:
                await websocket.send_json(message.to_dict())
            except Exception as e:
                logger.error(f"Failed to send message: {e}")
                disconnected.append(websocket)

        # Clean up disconnected
        for ws in disconnected:
            self.disconnect(project_id, ws)

    async def send_user_message(self, project_id: str, message: str):
        """Receive a user message and queue it for AI to read."""
        # Create message
        msg = WorkflowMessage(
            type=MessageType.USER_MESSAGE,
            content=message,
            metadata={"source": "user"}
        )

        # Queue for AI (auto-create queue if needed)
        await self.user_messages[project_id].put(message)

        # Broadcast to all clients (will add to history)
        await self.broadcast(project_id, msg)

    async def get_user_message(self, project_id: str, timeout: float = None) -> Optional[str]:
        """Get the next user message (for AI to read)."""
        if project_id not in self.user_messages:
            return None

        try:
            if timeout:
                message = await asyncio.wait_for(
                    self.user_messages[project_id].get(),
                    timeout=timeout
                )
            else:
                message = await self.user_messages[project_id].get()
            return message
        except asyncio.TimeoutError:
            return None

    def steer(self, project_id: str, command: str):
        """Send a steer command to the pipeline (PAUSE, SKIP:module_name, etc.)."""
        self.steer_queues[project_id].put_nowait(command)
        logger.info(f"Steer command queued for {project_id}: {command}")

    def get_steer(self, project_id: str, timeout: float = 0.1) -> Optional[str]:
        """Get the next steer command (non-blocking).

        Returns:
            Steer command string (e.g., "PAUSE", "SKIP:paper_fetcher") or None
        """
        if project_id not in self.steer_queues:
            return None

        try:
            # Non-blocking get
            return self.steer_queues[project_id].get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def ai_thinking(self, project_id: str, thought: str):
        """Broadcast AI thinking process."""
        await self.broadcast(project_id, WorkflowMessage(
            type=MessageType.AI_THINKING,
            content=thought,
            metadata={"source": "ai"}
        ))

    async def ai_tool_call(self, project_id: str, tool_name: str, arguments: dict):
        """Broadcast AI tool call."""
        await self.broadcast(project_id, WorkflowMessage(
            type=MessageType.AI_TOOL_CALL,
            content=f"Calling tool: {tool_name}",
            metadata={"tool": tool_name, "arguments": arguments}
        ))

    async def ai_tool_result(self, project_id: str, tool_name: str, result: str):
        """Broadcast tool execution result."""
        await self.broadcast(project_id, WorkflowMessage(
            type=MessageType.AI_TOOL_RESULT,
            content=result[:500],  # Truncate long results
            metadata={"tool": tool_name, "full_result": result}
        ))

    async def ai_decision(self, project_id: str, decision: str, confidence: float):
        """Broadcast AI decision."""
        await self.broadcast(project_id, WorkflowMessage(
            type=MessageType.AI_DECISION,
            content=decision,
            metadata={"confidence": confidence}
        ))

    async def ai_error(self, project_id: str, error_message: str):
        """Broadcast AI error."""
        await self.broadcast(project_id, WorkflowMessage(
            type=MessageType.AI_ERROR,
            content=error_message,
            metadata={"source": "ai"}
        ))

    async def request_user_confirmation(
        self,
        project_id: str,
        question: str,
        options: List[str] = None
    ) -> str:
        """Request user confirmation and wait for response."""
        msg = WorkflowMessage(
            type=MessageType.AI_DECISION,
            content=question,
            metadata={
                "requires_confirmation": True,
                "options": options or ["yes", "no"]
            }
        )
        await self.broadcast(project_id, msg)

        # Wait for user response
        response = await self.get_user_message(project_id, timeout=300)  # 5 min timeout
        return response or "no response"

    async def request_module_injection(
        self,
        project_id: str,
        module_name: str,
        description: str,
        input_schema: dict,
        output_schema: dict,
        insert_after: str = None,
        insert_before: str = None,
        compatibility_warnings: list = None,
    ) -> str:
        """Request user confirmation for module injection.

        Broadcasts injection metadata to frontend, then polls steer queue
        for INJECT_APPROVE:{name} or INJECT_REJECT:{name}.

        Returns:
            "approved", "rejected", or "timeout"
        """
        msg = WorkflowMessage(
            type=MessageType.MODULE_INJECTION_REQUEST,
            content=f"请求添加新模块: {module_name}",
            metadata={
                "module_name": module_name,
                "description": description,
                "input_schema": input_schema,
                "output_schema": output_schema,
                "insert_after": insert_after,
                "insert_before": insert_before,
                "compatibility_warnings": compatibility_warnings or [],
            },
        )
        await self.broadcast(project_id, msg)

        # Poll steer queue for approval/rejection (5-min timeout)
        deadline = asyncio.get_event_loop().time() + 300
        approve_token = f"INJECT_APPROVE:{module_name}"
        reject_token = f"INJECT_REJECT:{module_name}"

        while asyncio.get_event_loop().time() < deadline:
            steer = self.get_steer(project_id)
            if steer == approve_token:
                return "approved"
            elif steer == reject_token:
                return "rejected"
            await asyncio.sleep(1)

        return "timeout"

    def get_history(self, project_id: str, limit: int = 100) -> List[dict]:
        """Get message history for a project."""
        messages = self.history.get(project_id, [])
        return [msg.to_dict() for msg in messages[-limit:]]


# Global communication hub
_hub: Optional[CommunicationHub] = None


def get_communication_hub() -> CommunicationHub:
    """Get or create global communication hub."""
    global _hub
    if _hub is None:
        _hub = CommunicationHub()
    return _hub
