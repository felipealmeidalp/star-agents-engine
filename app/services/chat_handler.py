"""Chat Handler - Orchestrates conversation with tool calling loop."""

import json
import logging
from typing import TYPE_CHECKING, Any

from app.exceptions import MaxIterationsExceededError

logger = logging.getLogger(__name__)
from app.models.schemas import AgentContext, ToolExecutionContext
from app.repositories.chat_history import ChatHistoryRepository
from app.services.context_builder import ContextBuilder
from app.services.openai import OpenAIService
from app.services.tool_handler import ToolHandler
from app.utils.content_formatter import format_content_for_storage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


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
        """
        self.context_builder = context_builder
        self.openai_service = openai_service
        self.tool_handler = tool_handler
        self.chat_repo = chat_repo
        self.db = db
        self.openai_api_key = openai_api_key

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
            payload = await self.context_builder.build(
                session_id=session_id,
                company_id=company_id,
                cached_context=cached_context,
            )

            # Cache context for subsequent iterations
            if cached_context is None:
                cached_context = self.context_builder.last_context

            # 2. On last allowed iteration, remove tools to force response
            if iteration == self.MAX_ITERATIONS:
                payload.tools = None

            # 3. Call OpenAI
            logger.info("[ChatHandler] Chamando OpenAI...")
            response = await self.openai_service.chat_completion(payload)

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
            logger.info(
                "[ChatHandler] Tool calls detectados: %s → executando...",
                tool_names,
            )

            should_invalidate = await self._handle_tool_calls(
                response=response,
                session_id=session_id,
                company_id=company_id,
                cached_context=cached_context,
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

        Args:
            response: OpenAI response
            session_id: Session identifier
            company_id: Company ID

        Returns:
            Parsed response content
        """
        content = response.choices[0].message.content

        # Format for storage
        formatted_content = format_content_for_storage(content)

        # Save to database
        await self.chat_repo.insert_assistant_message(
            session_id=session_id,
            content=formatted_content,
            company_id=company_id,
        )

        logger.info(
            "[ChatHandler] ========== REQUISIÇÃO FINALIZADA ========== "
            "resposta salva no banco"
        )

        # Parse and return response
        return self._parse_response(content)

    async def _handle_tool_calls(
        self,
        response: Any,
        session_id: str,
        company_id: int,
        cached_context: AgentContext,
    ) -> bool:
        """
        Handle tool calls from response.

        Args:
            response: OpenAI response with tool_calls
            session_id: Session identifier
            company_id: Company ID
            cached_context: Cached agent context

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

        # 1. Save assistant message with tool_calls
        await self.chat_repo.insert_assistant_with_tool_calls(
            session_id=session_id,
            company_id=company_id,
            agent_id=agent_id,
            sub_agent_id=sub_agent_id,
            tool_calls=tool_calls_raw,
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
            db=self.db,
            openai_api_key=self.openai_api_key,
            chat_history=chat_history,
        )

        results = await self.tool_handler.execute_all(tool_calls, execution_context)

        # 4. Save tool results
        for result in results:
            await self.chat_repo.insert_tool_result(
                session_id=session_id,
                company_id=company_id,
                agent_id=agent_id,
                sub_agent_id=sub_agent_id,
                tool_call_id=result.tool_call_id,
                content=result.content,
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
