"""Transfer to Human tool."""

import logging
import random
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
        return "transfer_to_human"

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

        # 5. Swap labels + assignment
        try:
            chatwoot_client = ChatwootClient()

            # Swap labels: add "atendimento-humano", remove "atendimento-ia"
            await chatwoot_client.swap_label(
                base_url=company.cw_base_url,
                account_id=company.cw_account_id,
                conversation_id=customer.cw_conversation_id,
                add="atendimento-humano",
                remove="atendimento-ia",
                api_key=company.cw_apikey,
            )
            logger.info(
                "[TransferToHuman] Label 'atendimento-humano' added to conversation %d",
                customer.cw_conversation_id,
            )

            # Assignment: pick a human from the team (or keep AI)
            assignee_id: int | None = None
            if responsible_team:
                assignee_id = await self._pick_human_assignee(
                    chatwoot_client=chatwoot_client,
                    base_url=company.cw_base_url,
                    account_id=company.cw_account_id,
                    team_id=responsible_team,
                    api_key=company.cw_apikey,
                    ai_agent_id=company.ai_agent_id,
                )

            if responsible_team or assignee_id:
                await chatwoot_client.assign_conversation(
                    base_url=company.cw_base_url,
                    account_id=company.cw_account_id,
                    conversation_id=customer.cw_conversation_id,
                    api_key=company.cw_apikey,
                    assignee_id=assignee_id,
                    team_id=responsible_team,
                )
                logger.info(
                    "[TransferToHuman] Conversation %d assigned: assignee=%s, team=%s",
                    customer.cw_conversation_id,
                    assignee_id,
                    responsible_team,
                )
        except Exception as e:
            logger.error(
                "[TransferToHuman] Failed Chatwoot operations for conversation %d: %s",
                customer.cw_conversation_id,
                str(e),
            )

        return ToolResult(
            tool_call_id="",
            tool_name=self.name,
            tool_type="interna",
            success=True,
            content="Conversa transferida para atendimento humano com sucesso.",
        )

    async def _pick_human_assignee(
        self,
        chatwoot_client: ChatwootClient,
        base_url: str,
        account_id: int,
        team_id: int,
        api_key: str,
        ai_agent_id: int | None,
    ) -> int | None:
        """Pick a random human team member, excluding the AI bot."""
        try:
            members = await chatwoot_client.get_team_members(
                base_url=base_url,
                account_id=account_id,
                team_id=team_id,
                api_key=api_key,
            )

            humans = [m for m in members if m.get("id") != ai_agent_id]

            if not humans:
                logger.info(
                    "[TransferToHuman] No human members in team %d, keeping AI",
                    team_id,
                )
                return None

            chosen = random.choice(humans)
            logger.info(
                "[TransferToHuman] Picked human assignee %d (%s) from team %d",
                chosen["id"],
                chosen.get("name", "?"),
                team_id,
            )
            return chosen["id"]
        except Exception as e:
            logger.warning(
                "[TransferToHuman] Failed to get team members for team %d: %s",
                team_id,
                str(e),
            )
            return None
