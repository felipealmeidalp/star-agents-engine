"""Next Step tool for sub-agent transitions."""

from typing import Any

from app.models.schemas import ToolExecutionContext, ToolResult
from app.repositories.agent import AgentRepository
from app.repositories.customer import CustomerRepository
from app.repositories.prompt import PromptRepository
from app.services.tool_handler import BaseTool


class NextStepTool(BaseTool):
    """Tool for transitioning to a different sub-agent."""

    @property
    def name(self) -> str:
        return "next_step"

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """
        Execute next_step tool for sub-agent transitions.

        Behavior depends on the id argument:
        - id = 0: Deactivate variable_prompt (does NOT change sub_agent_id)
        - id > 0: Update sub_agent_id to the new sub-agent

        Args:
            arguments: Tool arguments containing 'id' (target sub_agent_id or 0)
            context: Execution context

        Returns:
            ToolResult indicating success
        """
        target_sub_agent_id = arguments.get("id")

        customer_repo = CustomerRepository(context.db)

        if target_sub_agent_id == 0:
            # CASE 1: Deactivate variable_prompt (return to original context)
            # Does NOT change sub_agent_id
            variable_prompt_id = await customer_repo.clear_variable_prompt(
                context.session_id,
                context.company_id,
            )

            if variable_prompt_id:
                prompt_repo = PromptRepository(context.db)
                await prompt_repo.deactivate_prompt(variable_prompt_id)
        else:
            # CASE 2: Normal sub-agent transition
            # Updates sub_agent_id, does NOT touch variable_prompt
            await customer_repo.update_sub_agent(
                context.session_id,
                context.company_id,
                target_sub_agent_id,
            )

        # Fetch target sub-agent info for context
        content = "Lead transferido para o próximo agente com sucesso!"
        if target_sub_agent_id and target_sub_agent_id > 0:
            agent_repo = AgentRepository(context.db)
            sub_agent_info = await agent_repo.get_sub_agent_info(target_sub_agent_id)
            if sub_agent_info:
                content = (
                    f"Lead transferido para o sub-agente "
                    f"'{sub_agent_info['name']}' (id: {sub_agent_info['id']}) "
                    f"com sucesso!"
                )

        return ToolResult(
            tool_call_id="",  # Set by handler
            tool_name=self.name,
            tool_type="interna",
            success=True,
            content=content,
            invalidate_cache=True,  # Force context rebuild (sub-agent or variable_prompt changed)
        )
