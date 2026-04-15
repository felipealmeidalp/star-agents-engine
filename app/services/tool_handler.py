"""Tool Handler - Unified tool execution with routing and timeout."""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from app.config import settings
from app.models.schemas import ToolCall, ToolExecutionContext, ToolResult
from app.utils.alerter import send_critical_alert

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class BaseTool(ABC):
    """Abstract base class for all tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name matching OpenAI function name."""
        pass

    @abstractmethod
    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """
        Execute the tool with given arguments.

        Args:
            arguments: Parsed arguments from tool call
            context: Execution context with session info

        Returns:
            ToolResult with execution outcome
        """
        pass


class ToolHandler:
    """Handles tool execution with routing (internal/external) and timeout."""

    INTERNAL_TOOLS = {"rag", "next_step", "transfer_to_human", "finish_objection_breaker"}

    def __init__(self, timeout: int | None = None) -> None:
        """
        Initialize tool handler.

        Args:
            timeout: Timeout in seconds for tool execution (default from settings)
        """
        self.timeout = timeout if timeout is not None else settings.tool_execution_timeout
        self._tools: dict[str, BaseTool] = {}
        self._register_internal_tools()

    def _register_internal_tools(self) -> None:
        """Register all internal tools."""
        from app.services.tools.internal import (
            FinishObjectionBreakerTool,
            NextStepTool,
            RagTool,
            TransferToHumanTool,
        )

        self._tools = {
            "rag": RagTool(),
            "next_step": NextStepTool(),
            "transfer_to_human": TransferToHumanTool(),
            "finish_objection_breaker": FinishObjectionBreakerTool(),
        }

    async def execute_all(
        self,
        tool_calls: list[ToolCall],
        context: ToolExecutionContext,
    ) -> list[ToolResult]:
        """
        Execute all tool calls sequentially with timeout.

        Args:
            tool_calls: List of tool calls to execute
            context: Execution context

        Returns:
            List of ToolResult for each tool call
        """
        results: list[ToolResult] = []

        for tool_call in tool_calls:
            result = await self._execute_with_timeout(tool_call, context)
            results.append(result)

        return results

    async def _execute_with_timeout(
        self,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """
        Execute a single tool with timeout.

        Args:
            tool_call: The tool call to execute
            context: Execution context

        Returns:
            ToolResult from execution
        """
        try:
            result = await asyncio.wait_for(
                self._route_and_execute(tool_call, context),
                timeout=self.timeout,
            )
            return result

        except TimeoutError:
            send_critical_alert(
                "TOOL_EXECUTION_TIMEOUT",
                "tool_handler.py:_execute_with_timeout",
                f"Tool '{tool_call.function.name}' timeout after {self.timeout}s",
                company_id=context.company_id,
                extra=f"session={context.session_id}, tool={tool_call.function.name}",
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.function.name,
                tool_type="desconhecido",
                success=False,
                content=f"Timeout: execucao da tool excedeu {self.timeout}s",
            )

        except Exception as e:
            send_critical_alert(
                "TOOL_EXECUTION_ERROR",
                "tool_handler.py:_execute_with_timeout",
                e,
                company_id=context.company_id,
                extra=f"session={context.session_id}, tool={tool_call.function.name}",
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.function.name,
                tool_type="desconhecido",
                success=False,
                content=f"Erro na execucao: {str(e)}",
            )

    async def _route_and_execute(
        self,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """
        Route to internal or external execution.

        Args:
            tool_call: The tool call to execute
            context: Execution context

        Returns:
            ToolResult from execution
        """
        tool_name = tool_call.function.name

        if tool_name in self.INTERNAL_TOOLS:
            return await self._execute_internal(tool_call, context)

        return await self._execute_external(tool_call, context)

    async def _execute_internal(
        self,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """
        Execute an internal tool.

        Args:
            tool_call: The tool call
            context: Execution context

        Returns:
            ToolResult from internal tool execution
        """
        tool = self._tools.get(tool_call.function.name)

        if tool is None:
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.function.name,
                tool_type="interna",
                success=False,
                content=f"Tool interna '{tool_call.function.name}' nao encontrada",
            )

        # Parse arguments from JSON string
        arguments = self._parse_arguments(tool_call.function.arguments)

        # Execute tool
        result = await tool.execute(arguments, context)

        # Set the tool_call_id
        result.tool_call_id = tool_call.id

        return result

    async def _execute_external(
        self,
        tool_call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """
        Execute an external HTTP tool.

        Args:
            tool_call: The tool call
            context: Execution context

        Returns:
            ToolResult from external tool execution
        """
        from app.repositories.tool import ToolRepository
        from app.services.tools.external import HttpToolExecutor

        tool_name = tool_call.function.name
        logger.info(f"[ToolHandler] Executando tool externa: {tool_name}")
        logger.debug(f"[ToolHandler] tool_call.id: {tool_call.id}")
        logger.debug(f"[ToolHandler] tool_call.function.arguments: {tool_call.function.arguments}")

        # 1. Validate context has db
        if not context.db:
            logger.error("[ToolHandler] Database session não disponível no context")
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_name,
                tool_type="externa",
                success=False,
                content="Erro: database session não disponível",
            )

        # 2. Fetch tool config from database
        logger.info(f"[ToolHandler] Buscando config da tool no banco: {tool_name}, company_id={context.company_id}")
        tool_repo = ToolRepository(context.db)
        tool_config = await tool_repo.get_external_tool_config(
            tool_name=tool_name,
            company_id=context.company_id,
        )

        if not tool_config:
            logger.error(f"[ToolHandler] Tool não encontrada no banco: {tool_name}")
            return ToolResult(
                tool_call_id=tool_call.id,
                tool_name=tool_name,
                tool_type="externa",
                success=False,
                content=f"Tool externa '{tool_name}' não encontrada",
            )

        logger.info(f"[ToolHandler] Tool encontrada: id={tool_config.get('id')}, method={tool_config.get('method')}, endpoint={tool_config.get('endpoint')}")
        logger.debug(f"[ToolHandler] Tool config completo: {json.dumps(tool_config, default=str)}")

        # 3. Parse AI arguments
        arguments = self._parse_arguments(tool_call.function.arguments)
        logger.info(f"[ToolHandler] Arguments parseados: {arguments}")

        # 4. Execute via HttpToolExecutor
        logger.info(f"[ToolHandler] Iniciando HttpToolExecutor com timeout={self.timeout}s")
        executor = HttpToolExecutor(timeout=self.timeout)
        result = await executor.execute(
            tool_call_id=tool_call.id,
            tool_name=tool_name,
            tool_config=tool_config,
            ai_arguments=arguments,
            customer_id=context.customer_id,
        )
        logger.info(f"[ToolHandler] Resultado: success={result.success}, content_length={len(result.content)}")
        return result

    def _parse_arguments(self, arguments: str) -> dict[str, Any]:
        """
        Parse tool arguments from JSON string.

        Args:
            arguments: JSON string of arguments

        Returns:
            Parsed dictionary of arguments
        """
        if not arguments:
            return {}

        try:
            return json.loads(arguments)
        except json.JSONDecodeError:
            return {}
