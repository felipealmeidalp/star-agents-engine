"""Categories tool - loads the company's catalog text into the conversation context."""

import logging
from typing import Any

from sqlalchemy import select

from app.models.schemas import ToolExecutionContext, ToolResult
from app.models.tables import Category
from app.services.tool_handler import BaseTool

logger = logging.getLogger(__name__)


class CategoriesTool(BaseTool):
    """Returns the full category catalog stored for the company."""

    @property
    def name(self) -> str:
        return "categories"

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """
        Fetch the company's catalog text.

        Args:
            arguments: Unused (tool takes no parameters)
            context: Execution context with db session and company_id

        Returns:
            ToolResult with the catalog text as content
        """
        if not context.db:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                tool_type="interna",
                success=False,
                content="Erro interno: sessão de banco de dados não disponível.",
            )

        result = await context.db.execute(
            select(Category.content).where(Category.company_id == context.company_id)
        )
        content = result.scalar_one_or_none()

        if not content:
            logger.warning(
                "[Categories] No catalog found for company %d", context.company_id
            )
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                tool_type="interna",
                success=False,
                content="Nenhum catálogo de categorias cadastrado.",
            )

        return ToolResult(
            tool_call_id="",
            tool_name=self.name,
            tool_type="interna",
            success=True,
            content=content,
        )
