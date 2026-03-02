"""Context Builder service for mounting agent context."""

import json
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

from app.models.schemas import (
    AgentContext,
    AgentSchema,
    CustomerSchema,
    DecisionRuleSchema,
    OpenAIMessage,
    OpenAIPayload,
    StepSchema,
    SubAgentConnectionSchema,
    SubAgentSchema,
    ToolParameterSchema,
    ToolSchema,
)
from app.repositories.agent import AgentRepository
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.prompt import PromptRepository, PromptWithConfig
from app.utils.alerter import send_critical_alert


class ContextBuilder:
    """Builds complete agent context for OpenAI API calls."""

    FINISH_OBJECTION_BREAKER_TOOL_DEF = {
        "type": "function",
        "function": {
            "name": "finish_objection_breaker",
            "description": "Encerra o modo de quebra de objecao e retorna ao fluxo normal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Resumo de como a objecao foi tratada e o estado atual da conversa.",
                    }
                },
                "required": ["summary"],
                "additionalProperties": False,
            },
        },
    }

    FINISH_OBJECTION_INSTRUCTION = (
        "\n\n## Ferramenta de Saida\n"
        "Quando voce resolver a objecao do lead com sucesso, chame a ferramenta "
        "`finish_objection_breaker` passando um resumo do que aconteceu. "
        "Esta e a UNICA ferramenta disponivel para voce neste modo."
    )

    def __init__(
        self,
        agent_repo: AgentRepository,
        chat_repo: ChatHistoryRepository,
        prompt_repo: PromptRepository,
    ) -> None:
        self.agent_repo = agent_repo
        self.chat_repo = chat_repo
        self.prompt_repo = prompt_repo
        self.last_context: AgentContext | None = None
        self._variable_prompt_cache: PromptWithConfig | None = None

    async def build(
        self,
        session_id: str,
        company_id: int,
        cached_context: AgentContext | None = None,
        pending_messages: list[OpenAIMessage] | None = None,
    ) -> OpenAIPayload:
        """
        Build complete OpenAI payload.

        1. Fetch agent context (agent, sub_agent, steps, rules, tools)
        2. Check for variable prompt override
        3. Fetch chat history with orphan handling
        4. Generate system prompt (or use variable prompt)
        5. Format tools for OpenAI
        6. Return ready-to-use payload

        Args:
            session_id: The session identifier
            company_id: Company ID for multi-tenancy
            cached_context: Optional cached AgentContext to skip database fetch
            pending_messages: Optional in-memory messages to append after DB history

        Returns:
            OpenAIPayload ready to send to OpenAI API
        """
        logger.info(f"[ContextBuilder] Iniciando build: session_id={session_id}, company_id={company_id}")

        try:
            return await self._build_internal(
                session_id=session_id,
                company_id=company_id,
                cached_context=cached_context,
                pending_messages=pending_messages,
            )
        except Exception as e:
            logger.exception("[ContextBuilder] Failed to build context: %s", e)
            send_critical_alert(
                "CONTEXT_BUILD_FAILED",
                "context_builder.py:build",
                e,
                company_id=company_id,
                extra=f"session={session_id}",
            )
            raise

    async def _build_internal(
        self,
        session_id: str,
        company_id: int,
        cached_context: AgentContext | None = None,
        pending_messages: list[OpenAIMessage] | None = None,
    ) -> OpenAIPayload:
        """Internal build logic, separated for clean error handling."""
        # 1. Use cached context if available, otherwise fetch from DB
        if cached_context is not None:
            logger.debug("[ContextBuilder] Usando context do cache")
            context = cached_context
            # Use cached variable prompt if available
            variable_prompt = self._variable_prompt_cache
        else:
            logger.debug("[ContextBuilder] Buscando context do banco")
            raw_context = await self.agent_repo.get_full_context(session_id, company_id)
            context = self._parse_context(raw_context)
            logger.info(
                f"[ContextBuilder] Context carregado: agent={context.agent.name}, "
                f"sub_agent={context.sub_agent.name}, tools_count={len(context.tools)}"
            )

            # 2. Check for variable prompt override (only when not using cache)
            variable_prompt = await self._fetch_variable_prompt_if_needed(context)
            # Cache for subsequent iterations
            self._variable_prompt_cache = variable_prompt

        # Store context for external access (caching in tool loop)
        self.last_context = context

        # 3. Fetch chat history (always fresh to include new tool results)
        raw_history = await self.chat_repo.get_history_with_orphan_handling(
            session_id, company_id
        )

        # 4. Build system prompt (use variable prompt if available)
        if variable_prompt:
            output_fmt = context.agent.output_instructions or ""
            system_prompt = (
                variable_prompt.prompt
                + (f"\n\n## Formatacao de Saida\n{output_fmt}" if output_fmt else "")
                + self.FINISH_OBJECTION_INSTRUCTION
            )
        else:
            system_prompt = self._build_system_prompt(context)

        # 5. Format messages
        messages = self._format_messages(raw_history, system_prompt)

        # 5b. Append pending in-memory messages (from ConversationTurn)
        if pending_messages:
            messages.extend(pending_messages)

        # 6. Format tools (override with finish_objection_breaker when in objection mode)
        if variable_prompt:
            tools = [self.FINISH_OBJECTION_BREAKER_TOOL_DEF]
            logger.info("[ContextBuilder] Modo objecao ativo: tools substituidas por [finish_objection_breaker]")
        else:
            tools = self._format_tools_for_openai(context.tools)
            if tools:
                tool_names = [t["function"]["name"] for t in tools]
                logger.info(f"[ContextBuilder] Tools formatadas: {tool_names}")

        # 7. Get response format if needed
        response_format = self._get_response_format(context.agent.output_type)

        # 8. Build payload (use variable prompt model/temp if available)
        if variable_prompt:
            model = variable_prompt.model
            temperature = variable_prompt.temperature
        else:
            model = context.sub_agent.model or "gpt-4"
            temperature = context.sub_agent.temperature or 0.7

        payload = OpenAIPayload(
            model=model,
            temperature=temperature,
            messages=messages,
            tools=tools if tools else None,
            response_format=response_format,
        )

        # Log payload summary
        logger.info(
            f"[ContextBuilder] Payload pronto: model={model}, temp={temperature}, "
            f"messages={len(messages)}, tools={len(tools) if tools else 0}"
        )
        # Full payload for debugging
        logger.debug(
            "[ContextBuilder] Full payload:\n%s",
            json.dumps(payload.model_dump(exclude_none=True), indent=2, ensure_ascii=False),
        )

        return payload

    async def _fetch_variable_prompt_if_needed(
        self,
        context: AgentContext,
    ) -> PromptWithConfig | None:
        """
        Check if customer has variable prompt enabled and fetch it.

        Args:
            context: The parsed agent context

        Returns:
            PromptWithConfig if variable prompt is active, None otherwise
        """
        customer = context.customer

        # Check if variable prompt is enabled
        if not customer.variable_prompt_status:
            return None

        if not customer.variable_prompt_id:
            return None

        # Fetch the variable prompt from database
        return await self.prompt_repo.get_prompt_by_id(customer.variable_prompt_id)

    def _parse_context(self, raw: dict[str, Any]) -> AgentContext:
        """Parse raw SQL result into typed AgentContext."""
        # Parse customer
        customer = CustomerSchema(
            id=raw["customer"]["id"],
            sessionId=raw["customer"]["sessionId"],
            agent_id=raw["customer"].get("agent_id"),
            sub_agent_id=raw["customer"].get("sub_agent_id"),
            variable_prompt_status=raw["customer"].get("variable_prompt_status"),
            variable_prompt_id=raw["customer"].get("variable_prompt_id"),
            customer_context=raw["customer"].get("customer_context"),
        )

        # Parse agent (DB uses 'title' instead of 'name')
        agent_data = raw["agent"]
        agent = AgentSchema(
            id=agent_data["id"],
            name=agent_data.get("title") or agent_data.get("name", ""),
            identity=agent_data.get("identity"),
            voice_tone=agent_data.get("voice_tone"),
            master_goal=agent_data.get("master_goal"),
            golden_rules=agent_data.get("golden_rules"),
            negative_rules=agent_data.get("negative_rules"),
            output_instructions=agent_data.get("output_instructions"),
            output_type=agent_data.get("output_type"),
        )

        # Parse sub_agent (DB uses 'title' instead of 'name')
        sub_agent_data = raw["sub_agent"]
        sub_agent = SubAgentSchema(
            id=sub_agent_data["id"],
            name=sub_agent_data.get("title") or sub_agent_data.get("name", ""),
            mission=sub_agent_data.get("mission"),
            tools=sub_agent_data.get("tools"),
            model=sub_agent_data.get("model"),
            temperature=sub_agent_data.get("temperature"),
        )

        # Parse steps
        steps = [
            StepSchema(
                id=s["id"],
                step=s.get("step"),
                relative_id=s.get("relative_id"),
            )
            for s in raw.get("steps", [])
        ]

        # Parse decision rules with connections
        decision_rules = []
        for item in raw.get("decision_rules", []):
            dr = item.get("decision_rule", {})
            connections = [
                SubAgentConnectionSchema(
                    id=conn["id"],
                    target_sub_agent_id=conn.get("target_sub_agent_id"),
                )
                for conn in item.get("sub_agent_connections", [])
            ]
            decision_rules.append(
                DecisionRuleSchema(
                    id=dr["id"],
                    rule=dr.get("rule"),
                    relative_id=dr.get("relative_id"),
                    connections=connections,
                )
            )

        # Parse tools with parameters
        tools = []
        for item in raw.get("tools", []):
            tool_data = item.get("tool", {})
            params = [
                ToolParameterSchema(
                    name=p.get("name"),
                    type=p.get("type"),
                    description=p.get("description"),
                    required=p.get("required"),
                )
                for p in item.get("parameters", [])
            ]
            tools.append(
                ToolSchema(
                    id=tool_data["id"],
                    title=tool_data.get("title"),
                    instructions=tool_data.get("instructions"),
                    complete_json=tool_data.get("complete_json"),
                    parameters=params,
                    send_content_before_execution=tool_data.get(
                        "send_content_before_execution", False
                    ),
                )
            )

        return AgentContext(
            customer=customer,
            agent=agent,
            sub_agent=sub_agent,
            steps=steps,
            decision_rules=decision_rules,
            tools=tools,
        )

    def _build_system_prompt(self, context: AgentContext) -> str:
        """Generate system prompt with static sections first (cacheable) and dynamic last."""
        # Build tools text
        tools_text = ""
        for i, tool in enumerate(context.tools, 1):
            if tool.instructions:
                tools_text += f"**Ferramenta {i}:**\n{tool.instructions}\n\n"

        # Build steps text
        steps_text = ""
        sorted_steps = sorted(context.steps, key=lambda s: s.relative_id or 0)
        for i, step in enumerate(sorted_steps, 1):
            if step.step:
                steps_text += f"**Passo {i}:**\n{step.step}\n\n"

        # Build decision rules text
        decision_rules_text = ""
        sorted_rules = sorted(
            context.decision_rules, key=lambda r: r.relative_id or 0
        )
        for rule in sorted_rules:
            if rule.rule:
                for conn in rule.connections:
                    if conn.target_sub_agent_id:
                        decision_rules_text += (
                            f"{rule.rule}: Chame a ferramenta "
                            f"`next_step(id: id do proximo agente)` passando o id "
                            f"{conn.target_sub_agent_id}\n\n"
                        )

        # Static sections first (cacheable by OpenAI)
        parts = [
            f"## Identidade\n{context.agent.identity or ''}",
            f"## Tom de Voz\n{context.agent.voice_tone or ''}",
            f"## Objetivo Geral\n{context.agent.master_goal or ''}",
            f"## Regras de Ouro\n{context.agent.golden_rules or ''}",
            f"## Regras Negativas\n{context.agent.negative_rules or ''}",
            f"## Missao Principal\n{context.sub_agent.mission or ''}",
            f"## Ferramentas Disponiveis\n{tools_text.strip()}",
            f"## Fluxo de Passos\n{steps_text.strip()}",
        ]

        if decision_rules_text.strip():
            parts.append(f"## Regra de decisao\n{decision_rules_text.strip()}")

        parts.append(
            f"## Formatacao de Saida\n{context.agent.output_instructions or ''}"
        )

        # Dynamic sections last (date/time changes every request)
        now = datetime.now(ZoneInfo("America/Sao_Paulo"))
        data_hora_atual = now.strftime("%d/%m/%Y %H:%M")
        parts.append(f"## Informacoes adicionais\nData e hora atual: {data_hora_atual}")

        # Lead context (only if available)
        customer_ctx = context.customer.customer_context
        if customer_ctx:
            parts.append(
                f"## Contexto sobre o Lead\n"
                f"{json.dumps(customer_ctx, ensure_ascii=False, indent=2)}"
            )

        return "\n\n".join(parts).strip()

    def _format_tools_for_openai(
        self, tools: list[ToolSchema]
    ) -> list[dict[str, Any]]:
        """Convert tools to OpenAI function calling format."""
        openai_tools = []

        for tool in tools:
            if not tool.complete_json:
                continue

            cj = tool.complete_json
            if not cj.get("name") or not cj.get("parameters"):
                continue

            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": cj["name"],
                        "description": cj.get("description", ""),
                        "parameters": cj["parameters"],
                    },
                }
            )

        return openai_tools

    def _format_messages(
        self,
        history: list[dict[str, Any]],
        system_prompt: str,
    ) -> list[OpenAIMessage]:
        """Convert chat history to OpenAI message format."""
        messages: list[OpenAIMessage] = []

        # System message always first
        messages.append(OpenAIMessage(role="system", content=system_prompt))

        # Sanitize: remove orphaned tool_calls and tool responses
        sanitized = self._sanitize_tool_pairs(history)

        # Add history messages
        for row in sanitized:
            if not row.get("role"):
                continue

            msg = OpenAIMessage(role=row["role"])

            # Add content if exists
            content = row.get("content")
            if content is not None:
                msg.content = str(content) if content else ""

            # Add tool_call_id if exists
            if row.get("tool_call_id"):
                msg.tool_call_id = row["tool_call_id"]

            # Add tool_calls if exists
            tool_calls = row.get("tool_calls")
            if tool_calls:
                if isinstance(tool_calls, list):
                    msg.tool_calls = tool_calls
                else:
                    msg.tool_calls = [tool_calls]

            messages.append(msg)

        # Pre-flight validation: if sequence is still invalid, strip all tool messages
        if not self._validate_messages_for_openai(messages):
            logger.warning(
                "[ContextBuilder] Validação pre-flight falhou - removendo todas mensagens de tool"
            )
            send_critical_alert(
                "CONTEXT_BUILDER_TOOL_SEQUENCE_INVALID",
                "context_builder.py:_format_messages",
                Exception("Tool message sequence invalid after sanitize + reorder"),
                extra=f"messages_count={len(messages)}",
            )
            messages = [
                msg for msg in messages
                if msg.role not in ("tool",)
                and not (msg.role == "assistant" and msg.tool_calls)
            ]

        return messages

    def _validate_messages_for_openai(
        self,
        messages: list[OpenAIMessage],
    ) -> bool:
        """
        Validate that tool message sequence is correct for OpenAI API.

        Rules:
        - Every assistant message with tool_calls must be immediately followed by
          tool messages for ALL of those tool_call_ids.
        - Every tool message must have a preceding assistant with matching tool_call_id.

        Returns:
            True if valid, False if sequence is broken.
        """
        i = 0
        while i < len(messages):
            msg = messages[i]

            if msg.role == "assistant" and msg.tool_calls:
                # Extract expected tool_call_ids
                expected_ids = []
                for tc in msg.tool_calls:
                    if isinstance(tc, dict):
                        cid = tc.get("id") or tc.get("tool_call_id")
                    else:
                        cid = getattr(tc, "id", None) or getattr(tc, "tool_call_id", None)
                    if cid:
                        expected_ids.append(cid)

                # Check that the next N messages are tool responses for these IDs
                found_ids = set()
                for j in range(i + 1, i + 1 + len(expected_ids)):
                    if j >= len(messages):
                        logger.warning(
                            "[ContextBuilder] Validação: faltam tool responses após assistant "
                            "(esperado %d, encontrou %d)",
                            len(expected_ids),
                            len(found_ids),
                        )
                        return False
                    next_msg = messages[j]
                    if next_msg.role != "tool" or not next_msg.tool_call_id:
                        logger.warning(
                            "[ContextBuilder] Validação: esperava tool response na posição %d, "
                            "encontrou role=%s",
                            j,
                            next_msg.role,
                        )
                        return False
                    found_ids.add(next_msg.tool_call_id)

                if set(expected_ids) != found_ids:
                    logger.warning(
                        "[ContextBuilder] Validação: tool_call_ids não correspondem. "
                        "Esperado=%s, Encontrado=%s",
                        expected_ids,
                        found_ids,
                    )
                    return False

                # Skip past the tool responses
                i += 1 + len(expected_ids)
            else:
                i += 1

        return True

    def _sanitize_tool_pairs(
        self,
        history: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Remove orphaned tool_calls and tool responses from history.

        Ensures every assistant message with tool_calls has ALL corresponding
        tool responses, and every tool response has its parent assistant message.

        Prevents OpenAI 400 errors:
        "An assistant message with 'tool_calls' must be followed by tool messages
        responding to each 'tool_call_id'."
        """
        # 1. Collect all tool_call_ids that have responses in history
        tool_response_ids: set[str] = set()
        for row in history:
            if row.get("role") == "tool" and row.get("tool_call_id"):
                tool_response_ids.add(row["tool_call_id"])

        # 2. Find assistant messages where ALL tool_calls have responses
        valid_tool_call_ids: set[str] = set()
        for row in history:
            if row.get("role") != "assistant":
                continue
            tool_calls = row.get("tool_calls")
            if not tool_calls:
                continue
            call_ids = self._extract_tool_call_ids(tool_calls)
            if call_ids and all(cid in tool_response_ids for cid in call_ids):
                valid_tool_call_ids.update(call_ids)

        # 3. Filter out orphans
        sanitized: list[dict[str, Any]] = []
        for row in history:
            role = row.get("role")

            # Skip assistant messages with incomplete tool_calls
            if role == "assistant" and row.get("tool_calls"):
                call_ids = self._extract_tool_call_ids(row["tool_calls"])
                if call_ids and not all(cid in tool_response_ids for cid in call_ids):
                    logger.warning(
                        "[ContextBuilder] Removendo assistant com tool_calls órfãos: %s",
                        call_ids,
                    )
                    continue

            # Skip tool responses without matching assistant
            if role == "tool" and row.get("tool_call_id"):
                if row["tool_call_id"] not in valid_tool_call_ids:
                    logger.warning(
                        "[ContextBuilder] Removendo tool response órfã: %s",
                        row["tool_call_id"],
                    )
                    continue

            sanitized.append(row)

        # 4. Reorder: ensure tool responses immediately follow their assistant message
        reordered = self._reorder_tool_messages(sanitized)

        return reordered

    def _reorder_tool_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Reorder messages so tool responses immediately follow their assistant+tool_calls.

        OpenAI requires tool messages right after the assistant message that requested them.
        If the DB returned them out of order (same timestamp), this fixes the sequence.
        """
        # Index tool messages by tool_call_id
        tool_msgs_by_id: dict[str, dict[str, Any]] = {}
        for row in messages:
            if row.get("role") == "tool" and row.get("tool_call_id"):
                tool_msgs_by_id[row["tool_call_id"]] = row

        if not tool_msgs_by_id:
            return messages  # No tool messages, nothing to reorder

        # Build reordered list: skip tool messages (insert them after their assistant)
        reordered: list[dict[str, Any]] = []
        placed_tool_ids: set[str] = set()

        for row in messages:
            if row.get("role") == "tool" and row.get("tool_call_id"):
                # Skip - will be placed after corresponding assistant
                continue

            reordered.append(row)

            # If this is an assistant with tool_calls, insert tool responses right after
            if row.get("role") == "assistant" and row.get("tool_calls"):
                call_ids = self._extract_tool_call_ids(row["tool_calls"])
                for cid in call_ids:
                    if cid in tool_msgs_by_id:
                        reordered.append(tool_msgs_by_id[cid])
                        placed_tool_ids.add(cid)

        # Safety: append any tool messages that weren't placed (shouldn't happen after sanitize)
        for cid, msg in tool_msgs_by_id.items():
            if cid not in placed_tool_ids:
                logger.warning(
                    "[ContextBuilder] Tool message não associada, adicionando ao final: %s",
                    cid,
                )
                reordered.append(msg)

        if placed_tool_ids:
            logger.info(
                "[ContextBuilder] Reordenando tool messages: %d tools reposicionadas",
                len(placed_tool_ids),
            )

        return reordered

    @staticmethod
    def _extract_tool_call_ids(tool_calls: Any) -> list[str]:
        """Extract tool_call IDs from tool_calls field (list or single dict)."""
        if isinstance(tool_calls, list):
            return [
                tc.get("id") or tc.get("tool_call_id", "")
                for tc in tool_calls
                if isinstance(tc, dict)
            ]
        if isinstance(tool_calls, dict):
            cid = tool_calls.get("id") or tool_calls.get("tool_call_id")
            return [cid] if cid else []
        return []

    def _get_response_format(self, output_type: str | None) -> dict[str, Any] | None:
        """Return response_format if needed based on output_type."""
        if output_type == "quebra_mensagens":
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "purity_answer",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "resposta": {
                                "type": "array",
                                "items": {"type": "string"},
                            }
                        },
                        "required": ["resposta"],
                        "additionalProperties": False,
                    },
                },
            }

        return None
