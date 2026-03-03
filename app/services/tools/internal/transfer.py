"""Transfer to Human tool."""

import logging
from typing import Any

from sqlalchemy import select

from app.chatwoot.client import ChatwootClient
from app.models.schemas import ToolExecutionContext, ToolResult
from app.models.tables import Agent
from app.repositories.company import CompanyRepository
from app.repositories.customer import CustomerRepository
from app.services.tool_handler import BaseTool

logger = logging.getLogger(__name__)


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
        Transfer conversation to a human agent.

        Steps:
        1. Set customer.status = False (blocks AI from responding)
        2. Assign conversation to responsible team in Chatwoot (if configured)

        Args:
            arguments: Tool arguments (e.g., reason for transfer)
            context: Execution context

        Returns:
            ToolResult indicating success
        """
        if not context.db:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                tool_type="interna",
                success=False,
                content="Erro interno: sessão de banco de dados não disponível.",
            )

        customer_repo = CustomerRepository(context.db)
        company_repo = CompanyRepository(context.db)

        # 1. Set customer.status = False (blocks AI)
        await customer_repo.update_status(
            context.session_id,
            context.company_id,
            new_status=False,
        )
        logger.info(
            "[TransferToHuman] Customer status set to False for session=%s, company=%d",
            context.session_id,
            context.company_id,
        )

        # 2. Get agent's responsible_team
        result = await context.db.execute(
            select(Agent.responsible_team).where(Agent.id == context.agent_id)
        )
        responsible_team: int | None = result.scalar_one_or_none()

        if not responsible_team:
            logger.warning(
                "[TransferToHuman] No responsible_team configured for agent=%d. "
                "AI blocked but conversation not assigned to any team.",
                context.agent_id,
            )
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                tool_type="interna",
                success=True,
                content="Conversa transferida para atendimento humano com sucesso.",
            )

        # 3. Get company Chatwoot data
        company = await company_repo.get_by_id(context.company_id)
        if not company or not company.cw_base_url or not company.cw_apikey or not company.cw_account_id:
            logger.warning(
                "[TransferToHuman] Company %d missing Chatwoot config. "
                "AI blocked but team assignment skipped.",
                context.company_id,
            )
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                tool_type="interna",
                success=True,
                content="Conversa transferida para atendimento humano com sucesso.",
            )

        # 4. Get customer's cw_conversation_id
        customer = await customer_repo.get_by_session(
            context.session_id, context.company_id
        )
        if not customer or not customer.cw_conversation_id:
            logger.warning(
                "[TransferToHuman] Customer has no cw_conversation_id for session=%s. "
                "AI blocked but team assignment skipped.",
                context.session_id,
            )
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                tool_type="interna",
                success=True,
                content="Conversa transferida para atendimento humano com sucesso.",
            )

        # 5. Assign conversation to team in Chatwoot
        try:
            chatwoot_client = ChatwootClient()
            await chatwoot_client.assign_conversation_to_team(
                base_url=company.cw_base_url,
                account_id=company.cw_account_id,
                conversation_id=customer.cw_conversation_id,
                team_id=responsible_team,
                api_key=company.cw_apikey,
            )
            logger.info(
                "[TransferToHuman] Conversation %d assigned to team %d",
                customer.cw_conversation_id,
                responsible_team,
            )

            # 6. Add "atendimento-humano" label
            await chatwoot_client.add_label_to_conversation(
                base_url=company.cw_base_url,
                account_id=company.cw_account_id,
                conversation_id=customer.cw_conversation_id,
                label="atendimento-humano",
                api_key=company.cw_apikey,
            )
            logger.info(
                "[TransferToHuman] Label 'atendimento-humano' added to conversation %d",
                customer.cw_conversation_id,
            )
        except Exception as e:
            logger.error(
                "[TransferToHuman] Failed to assign conversation %d to team %d: %s",
                customer.cw_conversation_id,
                responsible_team,
                str(e),
            )

        return ToolResult(
            tool_call_id="",
            tool_name=self.name,
            tool_type="interna",
            success=True,
            content="Conversa transferida para atendimento humano com sucesso.",
        )
