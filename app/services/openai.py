"""OpenAI integration service."""

import logging
import time
from typing import Any

from openai import (
    AsyncOpenAI,
    APIError,
    RateLimitError,
    AuthenticationError,
    APITimeoutError,
)

from app.config import settings
from app.exceptions import (
    OpenAIError,
    OpenAIRateLimitError,
    OpenAIAuthenticationError,
    OpenAITimeoutError,
)
from app.models.schemas import (
    OpenAIPayload,
    OpenAIResponse,
    OpenAIChoice,
    OpenAIMessage,
    ToolCall,
    ToolCallFunction,
)

logger = logging.getLogger(__name__)


class OpenAIService:
    """Service for interacting with OpenAI API."""

    def __init__(self, api_key: str) -> None:
        """
        Initialize OpenAI service with API key.

        Args:
            api_key: OpenAI API key from company
        """
        self.client = AsyncOpenAI(
            api_key=api_key,
            timeout=settings.openai_timeout,
        )

    async def chat_completion(self, payload: OpenAIPayload) -> OpenAIResponse:
        """
        Call OpenAI Chat Completions API.

        Args:
            payload: Complete OpenAI payload from ContextBuilder

        Returns:
            Parsed OpenAI response with message and finish_reason

        Raises:
            OpenAIAuthenticationError: Invalid API key
            OpenAIRateLimitError: Rate limit exceeded
            OpenAITimeoutError: Request timeout
            OpenAIError: Other API errors
        """
        try:
            request_kwargs: dict[str, Any] = {
                "model": payload.model,
                "temperature": payload.temperature,
                "messages": [
                    msg.model_dump(exclude_none=True) for msg in payload.messages
                ],
            }

            if payload.tools:
                request_kwargs["tools"] = payload.tools

            if payload.response_format:
                request_kwargs["response_format"] = payload.response_format

            logger.debug(
                "[OpenAI] Enviando request: model=%s, messages=%d, tools=%d",
                payload.model,
                len(payload.messages),
                len(payload.tools) if payload.tools else 0,
            )

            start_time = time.perf_counter()
            response = await self.client.chat.completions.create(**request_kwargs)
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            # Log token usage
            usage = response.usage
            if usage:
                logger.info(
                    "[OpenAI] Resposta em %.0fms: tokens(in=%d, out=%d, total=%d)",
                    elapsed_ms,
                    usage.prompt_tokens,
                    usage.completion_tokens,
                    usage.total_tokens,
                )
            else:
                logger.info("[OpenAI] Resposta em %.0fms (sem info de tokens)", elapsed_ms)

            return self._parse_response(response)

        except AuthenticationError as e:
            logger.error("[OpenAI] Erro de autenticação: API key inválida")
            raise OpenAIAuthenticationError(f"Invalid OpenAI API key: {e}") from e
        except RateLimitError as e:
            logger.error("[OpenAI] Rate limit excedido")
            raise OpenAIRateLimitError(f"OpenAI rate limit exceeded: {e}") from e
        except APITimeoutError as e:
            logger.error("[OpenAI] Timeout na requisição")
            raise OpenAITimeoutError(f"OpenAI request timeout: {e}") from e
        except APIError as e:
            logger.error("[OpenAI] Erro na API: %s", e)
            raise OpenAIError(f"OpenAI API error: {e}") from e

    def _parse_response(self, response: Any) -> OpenAIResponse:
        """
        Parse raw OpenAI SDK response into typed schema.

        Args:
            response: Raw response from openai SDK

        Returns:
            Typed OpenAIResponse object
        """
        choices = []
        for choice in response.choices:
            tool_calls_list = None
            if choice.message.tool_calls:
                tool_calls_list = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in choice.message.tool_calls
                ]

            message = OpenAIMessage(
                role=choice.message.role,
                content=choice.message.content,
                tool_calls=tool_calls_list,
            )

            choices.append(
                OpenAIChoice(
                    index=choice.index,
                    message=message,
                    finish_reason=choice.finish_reason,
                )
            )

        # Build usage dict with token details
        usage_dict = None
        if response.usage:
            usage_dict = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
            # Add prompt_tokens_details if available
            if response.usage.prompt_tokens_details:
                usage_dict["prompt_tokens_details"] = {
                    "cached_tokens": getattr(
                        response.usage.prompt_tokens_details, "cached_tokens", 0
                    ),
                    "audio_tokens": getattr(
                        response.usage.prompt_tokens_details, "audio_tokens", 0
                    ),
                }
            # Add completion_tokens_details if available
            if response.usage.completion_tokens_details:
                usage_dict["completion_tokens_details"] = {
                    "reasoning_tokens": getattr(
                        response.usage.completion_tokens_details, "reasoning_tokens", 0
                    ),
                    "audio_tokens": getattr(
                        response.usage.completion_tokens_details, "audio_tokens", 0
                    ),
                    "accepted_prediction_tokens": getattr(
                        response.usage.completion_tokens_details,
                        "accepted_prediction_tokens",
                        0,
                    ),
                    "rejected_prediction_tokens": getattr(
                        response.usage.completion_tokens_details,
                        "rejected_prediction_tokens",
                        0,
                    ),
                }

        return OpenAIResponse(
            id=response.id,
            model=response.model,
            choices=choices,
            usage=usage_dict,
            created=response.created,
            service_tier=getattr(response, "service_tier", None),
            system_fingerprint=getattr(response, "system_fingerprint", None),
        )

    def has_tool_calls(self, response: OpenAIResponse) -> bool:
        """
        Check if response contains tool calls.

        Args:
            response: Parsed OpenAI response

        Returns:
            True if finish_reason is "tool_calls", False otherwise
        """
        if not response.choices:
            return False
        return response.choices[0].finish_reason == "tool_calls"

    def get_tool_calls(self, response: OpenAIResponse) -> list[ToolCall] | None:
        """
        Extract tool calls from response.

        Args:
            response: Parsed OpenAI response

        Returns:
            List of ToolCall objects or None if no tool calls
        """
        if not response.choices:
            return None

        message = response.choices[0].message
        if not message.tool_calls:
            return None

        return [
            ToolCall(
                id=tc["id"],
                type=tc.get("type", "function"),
                function=ToolCallFunction(
                    name=tc["function"]["name"],
                    arguments=tc["function"]["arguments"],
                ),
            )
            for tc in message.tool_calls
        ]
