"""Chat Handler - Orchestrates conversation with tool calling loop."""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from app.exceptions import MaxIterationsExceededError, OpenAIBadRequestError

logger = logging.getLogger(__name__)
from app.models.schemas import (
    AgentContext,
    OpenAIMessage,
    OpenAIPayload,
    TokenUsage,
    ToolExecutionContext,
    extract_token_usage,
)
from app.repositories.chat_history import ChatHistoryRepository
from app.services.context_builder import ContextBuilder
from app.services.conversation_turn import ConversationTurn
from app.services.openai import OpenAIService
from app.services.tool_handler import ToolHandler
from app.utils.alerter import send_critical_alert
from app.utils.content_formatter import format_content_for_storage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

MessageSenderCallback = Callable[[list[str]], Awaitable[None]]


class ChatHandler:
    """Orchestrates LLM conversation with tool calling loop."""

    MAX_ITERATIONS = 5

    def __init__(
        self,
        context_builder: ContextBuilder,
        openai_service: OpenAIService,
        tool_handler: ToolHandler,
        chat_repo: ChatHistoryRepository,
        db: "AsyncSession",
        openai_api_key: str,
        on_send_messages: MessageSenderCallback | None = None,
        conversation_turn: ConversationTurn | None = None,
    ) -> None:
        """
        Initialize the chat handler.

        Args:
            context_builder: Service to build OpenAI payload
            openai_service: Service to call OpenAI API
            tool_handler: Service to execute tools
            chat_repo: Repository for chat history
            db: Database session for internal tools
            openai_api_key: OpenAI API key for internal tools
            on_send_messages: Optional callback to send messages to the lead
            conversation_turn: Optional ConversationTurn for in-memory accumulation
        """
        self.context_builder = context_builder
        self.openai_service = openai_service
        self.tool_handler = tool_handler
        self.chat_repo = chat_repo
        self.db = db
        self.openai_api_key = openai_api_key
        self.on_send_messages = on_send_messages
        self.conversation_turn = conversation_turn

    async def process(
        self,
        session_id: str,
        company_id: int,
    ) -> dict[str, Any]:
        """
        Process conversation with tool calling loop.

        Executes the following loop:
        1. Build payload (with cache after first iteration)
        2. Call OpenAI
        3. If finish_reason == "stop": save and return response
        4. If finish_reason == "tool_calls":
           a. Save assistant message with tool_calls
           b. Execute tools
           c. Save tool results
           d. Loop back to step 1
        5. On 6th iteration, remove tools to force response

        Args:
            session_id: Session identifier
            company_id: Company ID for multi-tenancy

        Returns:
            Final response from assistant

        Raises:
            MaxIterationsExceededError: If loop exceeds MAX_ITERATIONS
        """
        iteration = 0
        cached_context: AgentContext | None = None

        logger.info(
            "[ChatHandler] ========== NOVA REQUISIÇÃO ========== "
            "session=%s, company=%s",
            session_id,
            company_id,
        )

        while iteration <= self.MAX_ITERATIONS:
            logger.info(
                "[ChatHandler] ----- Iteração %d/%d -----",
                iteration + 1,
                self.MAX_ITERATIONS + 1,
            )

            # 1. Build payload (with cache after first iteration)
            # Pass pending messages from ConversationTurn so context includes
            # in-memory user message + prior tool history + current tool loop state
            pending = None
            if self.conversation_turn:
                pending = self.conversation_turn.get_pending_as_openai_messages()

            payload = await self.context_builder.build(
                session_id=session_id,
                company_id=company_id,
                cached_context=cached_context,
                pending_messages=pending,
            )

            # Cache context for subsequent iterations
            if cached_context is None:
                cached_context = self.context_builder.last_context

            # 2. On last allowed iteration, remove tools to force response
            if iteration == self.MAX_ITERATIONS:
                payload.tools = None

            # 3. Call OpenAI
            logger.info("[ChatHandler] Chamando OpenAI...")
            try:
                response = await self.openai_service.chat_completion(payload)
            except OpenAIBadRequestError as e:
                logger.error(
                    "[ChatHandler] OpenAI 400 BadRequest - tentando fallback sem tools: %s", e
                )
                send_critical_alert(
                    "OPENAI_400_HISTORY_FALLBACK",
                    "chat_handler.py:process",
                    e,
                    company_id=company_id,
                    extra=f"session={session_id}, iteration={iteration}",
                )
                # Fallback: strip all tool-related messages and tools, retry once
                clean_messages = [
                    msg for msg in payload.messages
                    if msg.role not in ("tool",)
                    and not (msg.role == "assistant" and msg.tool_calls)
                ]
                fallback_payload = OpenAIPayload(
                    model=payload.model,
                    temperature=payload.temperature,
                    messages=clean_messages,
                    tools=None,
                    response_format=payload.response_format,
                )
                logger.info(
                    "[ChatHandler] Fallback payload: %d messages (was %d), sem tools",
                    len(clean_messages),
                    len(payload.messages),
                )
                response = await self.openai_service.chat_completion(fallback_payload)

            finish_reason = response.choices[0].finish_reason if response.choices else "unknown"
            logger.info("[ChatHandler] OpenAI respondeu: finish_reason=%s", finish_reason)

            # 4. Check if final response (no tool calls)
            if not self.openai_service.has_tool_calls(response):
                logger.info("[ChatHandler] Resposta final recebida (sem tool_calls)")
                return await self._handle_final_response(
                    response=response,
                    session_id=session_id,
                    company_id=company_id,
                )

            # 5. Handle tool calls
            tool_names = [
                tc.function.name
                for tc in self.openai_service.get_tool_calls(response) or []
            ]
            raw_content = response.choices[0].message.content
            logger.info(
                "[ChatHandler] Tool calls detectados: %s, content=%r → executando...",
                tool_names,
                raw_content,
            )

            should_invalidate = await self._handle_tool_calls(
                response=response,
                session_id=session_id,
                company_id=company_id,
                cached_context=cached_context,
                payload=payload,
            )

            # 6. Invalidate cache if any tool requested it
            if should_invalidate:
                logger.info("[ChatHandler] Cache invalidado por tool, recarregando contexto...")
                cached_context = None

            logger.info("[ChatHandler] Tools executadas, voltando ao loop...")
            iteration += 1

        # Should not reach here, but safety measure
        raise MaxIterationsExceededError(
            f"Tool calling loop exceeded {self.MAX_ITERATIONS} iterations"
        )

    async def _handle_final_response(
        self,
        response: Any,
        session_id: str,
        company_id: int,
    ) -> dict[str, Any]:
        """
        Handle final response (finish_reason == 'stop').

        If a ConversationTurn is available, accumulates in memory.
        Otherwise, saves directly to the database (legacy behavior).

        Args:
            response: OpenAI response
            session_id: Session identifier
            company_id: Company ID

        Returns:
            Parsed response content
        """
        content = response.choices[0].message.content
        token_usage = extract_token_usage(response)

        # Format for storage
        formatted_content = format_content_for_storage(content)

        if self.conversation_turn:
            # Accumulate in memory - will be saved atomically later
            self.conversation_turn.add_assistant_message(
                formatted_content, token_usage=token_usage,
            )
            logger.info(
                "[ChatHandler] ========== REQUISIÇÃO FINALIZADA ========== "
                "resposta acumulada em memória"
            )
        else:
            # Legacy: save directly to database
            try:
                await self.chat_repo.insert_assistant_message(
                    session_id=session_id,
                    content=formatted_content,
                    company_id=company_id,
                    input_tokens=token_usage.input_tokens,
                    input_cached_tokens=token_usage.input_cached_tokens,
                    output_tokens=token_usage.output_tokens,
                    model=token_usage.model,
                )
            except Exception as e:
                logger.exception("[ChatHandler] Failed to save assistant message: %s", e)
                send_critical_alert(
                    "DB_WRITE_ASSISTANT_MSG_FAILED",
                    "chat_handler.py:_handle_final_response",
                    e,
                    company_id=company_id,
                    extra=f"session={session_id}",
                )
            logger.info(
                "[ChatHandler] ========== REQUISIÇÃO FINALIZADA ========== "
                "resposta salva no banco"
            )

        # Parse and return response
        return self._parse_response(content)

    def _should_send_content_before_tools(
        self,
        tool_calls: list,
        cached_context: AgentContext,
    ) -> bool:
        """Check if any called tool has send_content_before_execution=True."""
        tool_flags: dict[str, bool] = {}
        for tool in cached_context.tools:
            if tool.complete_json and tool.complete_json.get("name"):
                tool_flags[tool.complete_json["name"]] = tool.send_content_before_execution

        return any(tool_flags.get(tc["function"]["name"], False) for tc in tool_calls)

    async def _generate_pre_tool_content(
        self,
        payload: OpenAIPayload,
        tool_names: list[str],
    ) -> tuple[str | None, TokenUsage]:
        """Chamada extra à OpenAI SEM tools para forçar geração de texto.

        Usada quando send_content_before_execution=true e o modelo
        retornou content=null com tool_calls (comportamento do GPT-4.1).

        Returns:
            Tuple of (generated content, token usage from extra call)
        """
        messages = list(payload.messages)
        messages.append(OpenAIMessage(
            role="system",
            content=(
                f"INSTRUÇÃO: Você vai executar a(s) ferramenta(s): {', '.join(tool_names)}. "
                "Gere agora APENAS a mensagem de texto que o lead deve receber ANTES da "
                "execução da ferramenta, conforme descrito no passo a passo. "
                "Não mencione a ferramenta ao lead. Não avance para passos posteriores."
            ),
        ))

        text_payload = OpenAIPayload(
            model=payload.model,
            temperature=payload.temperature,
            messages=messages,
            tools=None,
            response_format=payload.response_format,
        )

        logger.info("[ChatHandler] Chamada extra sem tools para forçar content pre-tool...")
        response = await self.openai_service.chat_completion(text_payload)

        content = response.choices[0].message.content if response.choices else None
        extra_usage = extract_token_usage(response)
        logger.info(
            "[ChatHandler] Content forçado recebido: %s",
            content[:200] if content else "None",
        )
        return content, extra_usage

    async def _handle_tool_calls(
        self,
        response: Any,
        session_id: str,
        company_id: int,
        cached_context: AgentContext,
        payload: OpenAIPayload,
    ) -> bool:
        """
        Handle tool calls from response.

        Args:
            response: OpenAI response with tool_calls
            session_id: Session identifier
            company_id: Company ID
            cached_context: Cached agent context
            payload: Original OpenAI payload (used for extra call when needed)

        Returns:
            True if any tool requested cache invalidation, False otherwise
        """
        # Extract tool calls
        tool_calls = self.openai_service.get_tool_calls(response)
        tool_calls_raw = response.choices[0].message.tool_calls

        if not tool_calls:
            return False

        # Get agent/sub_agent IDs from cached context
        agent_id = cached_context.customer.agent_id
        sub_agent_id = cached_context.customer.sub_agent_id

        # Extract token usage from the main response
        token_usage = extract_token_usage(response)

        content_to_save = None
        should_send = self._should_send_content_before_tools(tool_calls_raw, cached_context)

        if should_send and self.on_send_messages:
            # GPT-4.1 sempre retorna content=null com tool_calls.
            # Chamada extra sem tools para forçar o texto.
            tool_names = [tc["function"]["name"] for tc in tool_calls_raw]
            try:
                forced_content, extra_usage = await self._generate_pre_tool_content(
                    payload, tool_names,
                )
                # Merge tokens from both calls
                token_usage = token_usage.merge(extra_usage)
            except Exception as e:
                logger.exception("[ChatHandler] Extra OpenAI call failed: %s", e)
                send_critical_alert(
                    "OPENAI_EXTRA_CALL_FAILED",
                    "chat_handler.py:_handle_tool_calls",
                    e,
                    company_id=company_id,
                    extra=f"session={session_id}",
                )
                forced_content = None

            if forced_content:
                content_to_save = format_content_for_storage(forced_content)
                parsed = self._parse_response(forced_content)
                messages = parsed.get("resposta", [forced_content])
                await self.on_send_messages(messages)
                logger.info(
                    "[ChatHandler] Content enviado ao lead antes das tools (%d msgs)",
                    len(messages),
                )

        if self.conversation_turn:
            # Accumulate in memory
            self.conversation_turn.add_assistant_with_tool_calls(
                tool_calls=tool_calls_raw,
                content=content_to_save,
                token_usage=token_usage,
            )
        else:
            # Legacy: save directly to database
            try:
                await self.chat_repo.insert_assistant_with_tool_calls(
                    session_id=session_id,
                    company_id=company_id,
                    agent_id=agent_id,
                    sub_agent_id=sub_agent_id,
                    tool_calls=tool_calls_raw,
                    content=content_to_save,
                    input_tokens=token_usage.input_tokens,
                    input_cached_tokens=token_usage.input_cached_tokens,
                    output_tokens=token_usage.output_tokens,
                    model=token_usage.model,
                )
            except Exception as e:
                logger.exception("[ChatHandler] Failed to save tool_calls: %s", e)
                send_critical_alert(
                    "DB_WRITE_TOOL_CALLS_FAILED",
                    "chat_handler.py:_handle_tool_calls",
                    e,
                    company_id=company_id,
                    extra=f"session={session_id}",
                )

        # 2. Fetch chat history for tools that need conversation context
        chat_history = await self.chat_repo.get_history_with_orphan_handling(
            session_id=session_id,
            company_id=company_id,
        )

        # 3. Execute tools
        execution_context = ToolExecutionContext(
            session_id=session_id,
            company_id=company_id,
            agent_id=agent_id,
            sub_agent_id=sub_agent_id,
            customer_id=cached_context.customer.id,
            db=self.db,
            openai_api_key=self.openai_api_key,
            chat_history=chat_history,
        )

        results = await self.tool_handler.execute_all(tool_calls, execution_context)

        # 4. Save tool results
        if self.conversation_turn:
            # Accumulate in memory
            for result in results:
                self.conversation_turn.add_tool_result(
                    tool_call_id=result.tool_call_id,
                    content=result.content,
                )
        else:
            # Legacy: save directly to database
            for result in results:
                try:
                    await self.chat_repo.insert_tool_result(
                        session_id=session_id,
                        company_id=company_id,
                        agent_id=agent_id,
                        sub_agent_id=sub_agent_id,
                        tool_call_id=result.tool_call_id,
                        content=result.content,
                    )
                except Exception as e:
                    logger.exception(
                        "[ChatHandler] Failed to save tool result %s: %s",
                        result.tool_call_id,
                        e,
                    )
                    send_critical_alert(
                        "DB_WRITE_TOOL_RESULT_FAILED",
                        "chat_handler.py:_handle_tool_calls",
                        e,
                        company_id=company_id,
                        extra=f"session={session_id}, tool_call_id={result.tool_call_id}",
                    )

        # 5. Check if any tool requested cache invalidation
        return any(result.invalidate_cache for result in results)

    def _parse_response(self, content: str | None) -> dict[str, Any]:
        """
        Parse response content.

        Args:
            content: Raw response content

        Returns:
            Parsed response (JSON or wrapped string)
        """
        if not content:
            return {}

        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return {"response": content}
