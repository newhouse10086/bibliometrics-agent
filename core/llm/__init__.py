"""LLM Provider 抽象层.

参考 kimi-cli 的 kosong 包，为 Guardian Agent 提供 LLM 推理能力。
支持 OpenAI API 兼容接口（包括 OpenAI、DeepSeek、Moonshot 等）。
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """聊天消息."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: Optional[str] = None
    name: Optional[str] = None  # 工具调用时的名称
    tool_call_id: Optional[str] = None  # 工具调用返回时的 ID
    tool_calls: Optional[list[ToolCall]] = None  # assistant 消息携带的工具调用


@dataclass
class ToolCall:
    """LLM 请求的工具调用."""

    id: str
    name: str
    arguments: str  # JSON 字符串


@dataclass
class ToolDef:
    """工具定义（传给 LLM 的 schema）."""

    name: str
    description: str
    parameters: dict  # JSON Schema


@dataclass
class LLMResponse:
    """LLM 响应."""

    content: Optional[str] = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""  # "stop" | "tool_calls" | "length"
    usage: dict = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class BaseLLMProvider(ABC):
    """LLM Provider 基类."""

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        tools: Optional[list[ToolDef]] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """发送聊天请求.

        Args:
            messages: 消息列表
            tools: 可用工具列表
            temperature: 温度参数
            max_tokens: 最大 token 数

        Returns:
            LLMResponse
        """
        pass

    @abstractmethod
    def chat_stream(
        self,
        messages: list[Message],
        tools: Optional[list[ToolDef]] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Generator[str, None, None]:
        """流式聊天请求.

        Yields:
            内容文本片段
        """
        pass


class OpenAIProvider(BaseLLMProvider):
    """OpenAI API 兼容的 Provider.

    支持 OpenAI、DeepSeek、Moonshot 等兼容接口。
    通过环境变量配置:
    - OPENAI_API_KEY: API Key
    - OPENAI_BASE_URL: API 基础 URL（可选，用于非 OpenAI 的兼容服务）
    - OPENAI_MODEL: 模型名称（默认 gpt-4o）
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL", None)
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o")
        self.logger = logging.getLogger("llm.openai")

    def _get_client(self):
        """获取 OpenAI 客户端."""
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "openai package not installed. Run: pip install openai"
            )

        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url

        return OpenAI(**kwargs)

    def _build_messages(self, messages: list[Message]) -> list[dict]:
        """构建 API 消息格式."""
        result = []
        for msg in messages:
            d: dict[str, Any] = {
                "role": msg.role,
                "content": msg.content if msg.content is not None else "",
            }
            if msg.name:
                d["name"] = msg.name
            if msg.tool_call_id:
                d["tool_call_id"] = msg.tool_call_id
            # Assistant messages with tool_calls
            if msg.tool_calls:
                d["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": tc.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            result.append(d)
        return result

    def _build_tools(self, tools: Optional[list[ToolDef]]) -> Optional[list[dict]]:
        """构建 API 工具格式."""
        if not tools:
            return None

        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    def chat(
        self,
        messages: list[Message],
        tools: Optional[list[ToolDef]] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """发送聊天请求."""
        client = self._get_client()

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._build_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        api_tools = self._build_tools(tools)
        if api_tools:
            kwargs["tools"] = api_tools

        self.logger.debug(f"LLM request: {len(messages)} messages, {len(tools or [])} tools")

        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as e:
            self.logger.error(f"LLM request failed: {e}")
            raise

        choice = response.choices[0]

        # 解析工具调用
        tool_calls = []
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=tc.function.arguments,
                ))

        content = choice.message.content
        usage = {
            "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
            "completion_tokens": response.usage.completion_tokens if response.usage else 0,
            "total_tokens": response.usage.total_tokens if response.usage else 0,
        }

        self.logger.debug(
            f"LLM response: finish_reason={choice.finish_reason}, "
            f"tool_calls={len(tool_calls)}, tokens={usage.get('total_tokens', 0)}"
        )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "",
            usage=usage,
        )

    def chat_stream(
        self,
        messages: list[Message],
        tools: Optional[list[ToolDef]] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Generator[str, None, None]:
        """流式聊天请求."""
        client = self._get_client()

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._build_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        api_tools = self._build_tools(tools)
        if api_tools:
            kwargs["tools"] = api_tools

        stream = client.chat.completions.create(**kwargs)

        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content


class MockProvider(BaseLLMProvider):
    """Mock Provider，用于测试.

    模拟 LLM 响应，不需要真实 API 调用。
    """

    def __init__(self, responses: Optional[list[LLMResponse]] = None):
        self.responses = responses or []
        self._call_index = 0
        self.call_log: list[dict] = []

    def chat(
        self,
        messages: list[Message],
        tools: Optional[list[ToolDef]] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """返回预设的响应."""
        self.call_log.append({
            "messages": [(m.role, m.content[:100] if m.content else "") for m in messages],
            "tools": [t.name for t in (tools or [])],
            "temperature": temperature,
        })

        if self._call_index < len(self.responses):
            resp = self.responses[self._call_index]
            self._call_index += 1
            return resp

        # 默认：停止响应
        return LLMResponse(
            content="I have completed the analysis. No further action needed.",
            finish_reason="stop",
        )

    def chat_stream(
        self,
        messages: list[Message],
        tools: Optional[list[ToolDef]] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Generator[str, None, None]:
        """流式返回预设内容."""
        resp = self.chat(messages, tools, temperature, max_tokens)
        if resp.content:
            yield resp.content


def create_provider(config: Optional[dict] = None, llm_config_from_state: Optional[dict] = None) -> BaseLLMProvider:
    """根据配置创建 LLM Provider.

    Args:
        config: 配置字典，包含:
            - provider: "openai" | "mock"
            - api_key: API Key
            - base_url: API 基础 URL
            - model: 模型名称
        llm_config_from_state: 从 state.json 读取的配置（优先级更高）

    Returns:
        LLM Provider 实例
    """
    # 优先级：llm_config_from_state > config 参数 > 环境变量
    if llm_config_from_state:
        provider_type = llm_config_from_state.get("provider", "openai")

        if provider_type == "mock":
            return MockProvider()

        if provider_type == "openai":
            return OpenAIProvider(
                api_key=llm_config_from_state.get("api_key"),
                base_url=llm_config_from_state.get("base_url"),
                model=llm_config_from_state.get("model"),
            )

    # Fallback to config parameter or environment variables
    config = config or {}

    provider_type = config.get("provider", "openai")

    if provider_type == "mock":
        return MockProvider()

    if provider_type == "openai":
        return OpenAIProvider(
            api_key=config.get("api_key"),
            base_url=config.get("base_url"),
            model=config.get("model"),
        )

    raise ValueError(f"Unknown LLM provider: {provider_type}")
