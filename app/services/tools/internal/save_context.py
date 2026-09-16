"""Save Context tool — persists qualification state into customer_context."""

import logging
from typing import Any

from app.models.schemas import ToolExecutionContext, ToolResult
from app.repositories.customer import CustomerRepository
from app.services.tool_handler import BaseTool

logger = logging.getLogger(__name__)


class SaveContextTool(BaseTool):
    """Persists confirmed qualification fields into the lead's customer_context.

    Called after each datum is confirmed (produto, cidade, nome, telefone), so
    the state survives the chat-history window: on the next iteration the context
    is rebuilt and the saved fields show up in the "## Contexto sobre o Lead"
    section of the system prompt. invalidate_cache=True forces that rebuild.
    """

    @property
    def name(self) -> str:
        return "salvar_contexto"

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        # Only persist keys the model actually sent. The tool is instructed to send
        # ONLY new fields, but models sometimes re-send every field with empty/0
        # placeholders — those must never overwrite a value saved earlier, so we
        # drop None, "" and 0 (0 is never a real cidade_id/IBGE code).
        patch = {k: v for k, v in arguments.items() if v not in (None, "", 0)}

        if not patch:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                tool_type="interna",
                success=False,
                content="Nenhum campo para salvar.",
            )

        customer_repo = CustomerRepository(context.db)
        merged = await customer_repo.merge_customer_context(
            context.session_id,
            context.company_id,
            patch,
        )

        if merged is None:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                tool_type="interna",
                success=False,
                content="Sessão não encontrada para salvar o contexto.",
            )

        logger.info(
            "[SaveContext] Contexto atualizado para session=%s: campos=%s",
            context.session_id,
            list(patch.keys()),
        )

        return ToolResult(
            tool_call_id="",  # set by handler
            tool_name=self.name,
            tool_type="interna",
            success=True,
            content=f"Contexto salvo: {', '.join(patch.keys())}.",
            invalidate_cache=True,  # rebuild context so saved fields enter the prompt
        )
