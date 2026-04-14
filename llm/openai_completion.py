"""OpenAI completion interface wrapper for LLM operations."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class OpenAICompletion:
    """Wrapper for OpenAI completion API."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        temperature: float = 0.3,
        base_url: str | None = None,
    ):
        """Initialize OpenAI completion client.

        Args:
            model: Model name (e.g., "gpt-4o-mini", "gpt-4o", "deepseek/deepseek-chat")
            temperature: Temperature for generation (0.0-2.0)
            base_url: Custom API endpoint (e.g., "https://openrouter.ai/api/v1").
                      Can also be set via OPENAI_BASE_URL environment variable.
                      If model contains "/" (e.g., "qwen/qwen3.6-plus"), defaults to OpenRouter.
        """
        self.model = model
        self.temperature = temperature

        # Auto-detect OpenRouter models and set base_url
        if base_url:
            self.base_url = base_url
        elif "/" in model and not model.startswith("gpt"):
            # Models like "qwen/qwen3.6-plus" or "deepseek/deepseek-chat" use OpenRouter
            self.base_url = "https://openrouter.ai/api/v1"
        else:
            self.base_url = os.getenv("OPENAI_BASE_URL")

        # Prefer OPENROUTER_API_KEY for OpenRouter, fallback to OPENAI_API_KEY
        self.api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")

        if not self.api_key:
            logger.warning("OPENROUTER_API_KEY or OPENAI_API_KEY not set. LLM features will be disabled.")

    def completion(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 1000,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Call OpenAI completion API.

        Args:
            messages: List of message dicts with 'role' and 'content'
            max_tokens: Maximum tokens to generate
            **kwargs: Additional parameters to pass to OpenAI API

        Returns:
            Response dict with 'choices' containing generated text

        Raises:
            ValueError: If API key not set
            Exception: If API call fails
        """
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not set. Please set the environment variable.")

        try:
            from openai import OpenAI

            # Create client with optional base_url
            client_params = {"api_key": self.api_key}
            if self.base_url:
                client_params["base_url"] = self.base_url

            client = OpenAI(**client_params)

            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=max_tokens,
                **kwargs,
            )

            # Convert to dict format similar to litellm for compatibility
            return {
                "choices": [
                    {
                        "message": {
                            "content": choice.message.content,
                            "role": choice.message.role,
                        },
                        "finish_reason": choice.finish_reason,
                        "index": choice.index,
                    }
                    for choice in response.choices
                ],
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
                "model": response.model,
            }

        except ImportError:
            logger.error("openai package not installed. Install with: pip install openai")
            raise
        except Exception as e:
            logger.error("OpenAI API call failed: %s", e)
            raise


def create_completion_client(
    model: str = "gpt-4o-mini",
    temperature: float = 0.3,
    base_url: str | None = None,
) -> OpenAICompletion:
    """Factory function to create OpenAI completion client.

    Args:
        model: Model name
        temperature: Temperature for generation
        base_url: Custom API endpoint (optional)

    Returns:
        OpenAICompletion instance
    """
    return OpenAICompletion(model=model, temperature=temperature, base_url=base_url)
