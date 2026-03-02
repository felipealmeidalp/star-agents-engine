"""Finish Objection Breaker tool - exits objection mode and returns summary."""

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from app.models.schemas import ToolExecutionContext, ToolResult
from app.repositories.agent import AgentRepository
from app.repositories.customer import CustomerRepository
from app.repositories.prompt import PromptRepository
from app.services.tool_handler import BaseTool

logger = logging.getLogger(__name__)


class FinishObjectionBreakerTool(BaseTool):
    """Tool for exiting objection breaker mode and returning to normal flow."""

    @property
    def name(self) -> str:
        return "finish_objection_breaker"

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """
        Exit objection breaker mode and intelligently select the best sub-agent.

        1. Extract summary from arguments
        2. Clear variable_prompt_status on customer
        3. Deactivate the prompt if needed
        4. Use LLM to classify which sub-agent should continue
        5. Update customer with the chosen sub-agent
        6. Return summary as content (appears in history for next agent)

        Args:
            arguments: Tool arguments containing 'summary'
            context: Execution context

        Returns:
            ToolResult with summary as content and invalidate_cache=True
        """
        summary = arguments.get("summary", "Objecao tratada.")

        customer_repo = CustomerRepository(context.db)
        variable_prompt_id = await customer_repo.clear_variable_prompt(
            context.session_id,
            context.company_id,
        )

        if variable_prompt_id:
            prompt_repo = PromptRepository(context.db)
            await prompt_repo.deactivate_prompt(variable_prompt_id)

        # Classify best sub-agent to continue after objection
        await self._select_best_sub_agent(context, summary)

        return ToolResult(
            tool_call_id="",  # Set by handler
            tool_name=self.name,
            tool_type="interna",
            success=True,
            content=summary,
            invalidate_cache=True,
        )

    async def _select_best_sub_agent(
        self,
        context: ToolExecutionContext,
        summary: str,
    ) -> None:
        """
        Use LLM to classify which sub-agent should continue after objection.

        Fetches all sibling sub-agents + current, builds a classification prompt
        with chat history and objection summary, and updates the customer's
        sub_agent_id accordingly.

        Falls back to keeping current sub-agent if anything fails.
        """
        if not context.db or not context.openai_api_key:
            logger.warning("Missing db or openai_api_key, skipping sub-agent selection")
            return

        try:
            agent_repo = AgentRepository(context.db)

            # Fetch siblings (excludes current) + current sub-agent
            siblings = await agent_repo.get_sibling_sub_agents(
                agent_id=context.agent_id,
                current_sub_agent_id=context.sub_agent_id,
                company_id=context.company_id,
            )
            current_sub_agent = await agent_repo.get_sub_agent_info(context.sub_agent_id)

            if not current_sub_agent:
                logger.warning("Current sub-agent %d not found", context.sub_agent_id)
                return

            # Build full list: current + siblings
            all_sub_agents = [current_sub_agent] + siblings

            # If only one sub-agent exists, no need to classify
            if len(all_sub_agents) <= 1:
                return

            # Format sub-agents list for prompt
            sub_agents_text = "\n".join(
                f'- ID {sa["id"]}: "{sa["name"]}" - {sa.get("mission") or sa["name"]}'
                for sa in all_sub_agents
            )

            # Format last messages from chat history
            formatted_history = self._format_chat_history(context.chat_history)

            # Build classification prompt
            system_prompt = (
                "Voce e um roteador de conversa. Analise o historico e o resumo abaixo e escolha "
                "qual sub-agente deve continuar atendendo o lead.\n\n"
                f"Sub-agentes disponiveis:\n{sub_agents_text}\n\n"
                "Responda APENAS com JSON: {\"sub_agent_id\": <id do melhor sub-agente>}"
            )

            user_message = (
                f"Resumo da objecao tratada: {summary}\n\n"
                f"Historico recente da conversa:\n{formatted_history}"
            )

            # Call OpenAI for classification
            openai_client = AsyncOpenAI(api_key=context.openai_api_key)
            response = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            if not content:
                logger.warning("Empty LLM response for sub-agent classification")
                return

            decision = json.loads(content)
            chosen_id = decision.get("sub_agent_id")

            if not chosen_id:
                logger.warning("LLM response missing sub_agent_id: %s", content)
                return

            # Validate chosen_id against available sub-agents
            valid_ids = {sa["id"] for sa in all_sub_agents}
            if chosen_id not in valid_ids:
                logger.warning(
                    "LLM chose invalid sub_agent_id %d (valid: %s)", chosen_id, valid_ids
                )
                return

            # Update customer's sub-agent
            logger.info(
                "Sub-agent selection after objection: %d -> %d (from %s)",
                context.sub_agent_id,
                chosen_id,
                [sa["id"] for sa in all_sub_agents],
            )

            if chosen_id != context.sub_agent_id:
                customer_repo = CustomerRepository(context.db)
                await customer_repo.update_sub_agent(
                    session_id=context.session_id,
                    company_id=context.company_id,
                    new_sub_agent_id=chosen_id,
                )

        except Exception:
            logger.warning(
                "Failed to classify sub-agent after objection, keeping current",
                exc_info=True,
            )
            try:
                await context.db.rollback()
            except Exception:
                pass

    def _format_chat_history(
        self,
        chat_history: list[dict[str, Any]] | None,
    ) -> str:
        """Format chat history for the classification prompt."""
        if not chat_history:
            return "(sem historico)"

        formatted_messages = []
        for msg in chat_history:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "tool" or not content:
                continue

            role_label = "Usuario" if role == "user" else "Assistente"
            formatted_messages.append(f"{role_label}: {content}")

        return "\n".join(formatted_messages) if formatted_messages else "(sem historico)"
