"""OpenAI integration service."""

import io
import logging
import time
from typing import Any

import httpx
from openai import (
    AsyncOpenAI,
    APIError,
    BadRequestError,
    RateLimitError,
    AuthenticationError,
    APITimeoutError,
)

from app.config import settings
from app.exceptions import (
    OpenAIError,
    OpenAIBadRequestError,
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



def _to_responses_input(messages: list[OpenAIMessage]) -> list[dict[str, Any]]:
    """
    Convert Chat-Completions-shaped messages to Responses API input items.

    The Responses API has no role="tool" and no assistant.tool_calls: a tool call
    is a flat {type: "function_call"} item and its result a {type:
    "function_call_output"} item. Sending the Chat Completions shape makes the API
    reject the request (400), which used to trigger the tool-stripping fallback and
    silently drop every tool result from the context.
    """
    items: list[dict[str, Any]] = []

    for msg in messages:
        # Assistant message carrying tool calls -> one function_call item each
        if msg.role == "assistant" and msg.tool_calls:
            for tc in msg.tool_calls:
                fn = tc.get("function", tc)
                items.append(
                    {
                        "type": "function_call",
                        "call_id": tc.get("id") or tc.get("call_id", ""),
                        "name": fn.get("name", ""),
                        "arguments": fn.get("arguments", "{}"),
                    }
                )
            # Any text that came alongside the tool calls stays a normal message
            if msg.content:
                items.append({"role": "assistant", "content": msg.content})
            continue

        # Tool result -> function_call_output item
        if msg.role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": msg.tool_call_id or "",
                    "output": msg.content or "",
                }
            )
            continue

        dumped = msg.model_dump(exclude_none=True)
        dumped.pop("tool_calls", None)
        dumped.pop("tool_call_id", None)
        # Responses API rejects a message item without content
        dumped.setdefault("content", "")
        items.append(dumped)

    return items


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
        Call OpenAI Responses API.

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
            # temperature is intentionally never sent: rejected by gpt-5.x models
            request_kwargs: dict[str, Any] = {
                "model": payload.model,
                "input": _to_responses_input(payload.messages),
            }

            if payload.tools:
                # Responses API expects flat function tools (no "function" wrapper)
                request_kwargs["tools"] = [
                    {"type": "function", **t["function"]} if "function" in t else t
                    for t in payload.tools
                ]

            if payload.response_format:
                # {type, json_schema:{name,strict,schema}} -> {format:{type,name,strict,schema}}
                schema = payload.response_format.get("json_schema", {})
                request_kwargs["text"] = {
                    "format": {"type": payload.response_format["type"], **schema}
                }

            reasoning_effort = getattr(payload, "reasoning_effort", None)
            if reasoning_effort:
                request_kwargs["reasoning"] = {"effort": reasoning_effort}

            logger.debug(
                "[OpenAI] Enviando request: model=%s, messages=%d, tools=%d, reasoning=%s",
                payload.model,
                len(payload.messages),
                len(payload.tools) if payload.tools else 0,
                reasoning_effort or "-",
            )

            start_time = time.perf_counter()
            response = await self.client.responses.create(**request_kwargs)
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            # Log token usage
            usage = response.usage
            if usage:
                reasoning_tokens = getattr(
                    getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0
                )
                logger.info(
                    "[OpenAI] Resposta em %.0fms: tokens(in=%d, out=%d, total=%d, reasoning=%d)",
                    elapsed_ms,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.total_tokens,
                    reasoning_tokens or 0,
                )
            else:
                logger.info("[OpenAI] Resposta em %.0fms (sem info de tokens)", elapsed_ms)

            return self._parse_response(response)

        except AuthenticationError as e:
            logger.error("[OpenAI] Erro de autenticação: API key inválida")
            raise OpenAIAuthenticationError(f"Invalid OpenAI API key: {e}") from e
        except BadRequestError as e:
            logger.error("[OpenAI] Bad request (400): %s", e)
            raise OpenAIBadRequestError(f"OpenAI bad request: {e}") from e
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
        Parse raw Responses API output into the typed Chat-Completions-shaped schema.

        Keeps OpenAIResponse as a stable facade: output[] items are folded into a
        single choice with a synthesized finish_reason.

        Args:
            response: Raw response from openai SDK

        Returns:
            Typed OpenAIResponse object
        """
        text_parts: list[str] = []
        tool_calls_list: list[dict[str, Any]] = []

        for item in getattr(response, "output", None) or []:
            item_type = getattr(item, "type", None)
            if item_type == "function_call":
                tool_calls_list.append(
                    {
                        "id": getattr(item, "call_id", None) or getattr(item, "id", ""),
                        "type": "function",
                        "function": {
                            "name": item.name,
                            "arguments": item.arguments,
                        },
                    }
                )
                continue

            for part in getattr(item, "content", None) or []:
                if getattr(part, "type", None) == "output_text":
                    text_parts.append(part.text)

        message = OpenAIMessage(
            role="assistant",
            content="".join(text_parts) or None,
            tool_calls=tool_calls_list or None,
        )

        choices = [
            OpenAIChoice(
                index=0,
                message=message,
                finish_reason="tool_calls" if tool_calls_list else "stop",
            )
        ]

        # Build usage dict with token details (Chat Completions key names)
        usage_dict = None
        if response.usage:
            usage_dict = {
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.total_tokens,
            }
            input_details = getattr(response.usage, "input_tokens_details", None)
            if input_details:
                usage_dict["prompt_tokens_details"] = {
                    "cached_tokens": getattr(input_details, "cached_tokens", 0) or 0,
                }
            output_details = getattr(response.usage, "output_tokens_details", None)
            if output_details:
                usage_dict["completion_tokens_details"] = {
                    "reasoning_tokens": getattr(output_details, "reasoning_tokens", 0) or 0,
                }

        return OpenAIResponse(
            id=response.id,
            model=response.model,
            choices=choices,
            usage=usage_dict,
            created=int(getattr(response, "created_at", 0) or 0),
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

    async def transcribe_audio(self, audio_url: str) -> str:
        """
        Download audio from URL and transcribe via Whisper API.

        Args:
            audio_url: Public URL of the audio file

        Returns:
            Transcribed text

        Raises:
            OpenAIError: If download or transcription fails
        """
        try:
            # Download audio (follow_redirects for CDN redirects like Facebook)
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http:
                resp = await http.get(audio_url)
                resp.raise_for_status()

            audio_bytes = resp.content
            content_type = resp.headers.get("content-type", "")

            # Determine file extension from content type
            ext = "ogg"
            if "mpeg" in content_type or "mp3" in content_type:
                ext = "mp3"
            elif "mp4" in content_type or "m4a" in content_type:
                ext = "m4a"
            elif "wav" in content_type:
                ext = "wav"
            elif "webm" in content_type:
                ext = "webm"

            logger.info(
                "[OpenAI] Transcribing audio: url=%s, size=%d bytes, type=%s",
                audio_url[:80],
                len(audio_bytes),
                content_type,
            )

            start_time = time.perf_counter()
            audio_file = io.BytesIO(audio_bytes)
            audio_file.name = f"audio.{ext}"

            transcription = await self.client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language="pt",
            )
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            text = transcription.text.strip()
            logger.info(
                "[OpenAI] Transcription done in %.0fms: %d chars",
                elapsed_ms,
                len(text),
            )
            return text

        except httpx.HTTPError as e:
            logger.error("[OpenAI] Failed to download audio from %s: %s", audio_url[:80], e)
            raise OpenAIError(f"Failed to download audio: {e}") from e
        except AuthenticationError as e:
            logger.error("[OpenAI] Whisper auth error: invalid API key")
            raise OpenAIAuthenticationError(f"Invalid OpenAI API key: {e}") from e
        except RateLimitError as e:
            logger.error("[OpenAI] Whisper rate limit exceeded")
            raise OpenAIRateLimitError(f"OpenAI rate limit exceeded: {e}") from e
        except APITimeoutError as e:
            logger.error("[OpenAI] Whisper timeout")
            raise OpenAITimeoutError(f"OpenAI request timeout: {e}") from e
        except APIError as e:
            logger.error("[OpenAI] Whisper API error: %s", e)
            raise OpenAIError(f"OpenAI Whisper API error: {e}") from e
