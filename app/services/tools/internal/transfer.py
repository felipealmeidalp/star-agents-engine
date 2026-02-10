"""Transfer to Human tool."""

from typing import Any

from app.models.schemas import ToolExecutionContext, ToolResult
from app.services.tool_handler import BaseTool


class TransferToHumanTool(BaseTool):
    """Tool for transferring conversation to a human agent."""

    @property
    def name(self) -> str:
        return "transferir_para_humano"

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """
        Execute transfer to human tool (placeholder implementation).

        Args:
            arguments: Tool arguments (e.g., reason for transfer)
            context: Execution context

        Returns:
            ToolResult indicating success
        """
        # TODO: Implement actual human handoff logic
        return ToolResult(
            tool_call_id="",  # Set by executor
            tool_name=self.name,
            tool_type="interna",
            success=True,
            content="tool interna acionada com sucesso, transferir_para_humano",
        )
