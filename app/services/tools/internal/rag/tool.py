"""RAG (Retrieval Augmented Generation) tool."""

import json
import logging
from typing import Any, TypedDict

from openai import AsyncOpenAI

from app.models.schemas import ToolExecutionContext, ToolResult
from app.repositories.agent import AgentRepository
from app.repositories.customer import CustomerRepository
from app.repositories.objection import ObjectionRepository
from app.repositories.prompt import PromptRepository
from app.services.tool_handler import BaseTool

from .embedding import EmbeddingService
from .vector_search import VectorSearchService

logger = logging.getLogger(__name__)


class ClassificationResult(TypedDict):
    """Result of question classification (obj vs faq)."""

    result: str  # "obj" or "faq"
    objection_ids: list[int]  # IDs of matched objections (empty if faq)
    reasoning: str  # LLM chain-of-thought reasoning


class RagTool(BaseTool):
    """Tool for RAG-based knowledge retrieval."""

    @property
    def name(self) -> str:
        return "rag"

    async def execute(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """
        Execute RAG tool with question classification and FAQ/objection handling.

        Flow:
        1. Parse question from arguments
        2. Fetch filter_obj_faq prompt with config (model, temperature)
        3. Call OpenAI to classify as "obj" or "faq"
        4. If "obj": handle objection flow
        5. If "faq": generate embeddings, search via pgvector, summarize
        6. Return result

        Args:
            arguments: Tool arguments containing "question"
            context: Execution context with session info

        Returns:
            ToolResult with RAG answer or error
        """
        # Validate dependencies
        if not context.db or not context.openai_api_key:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                tool_type="interna",
                success=False,
                content="Erro: dependencias nao disponiveis (db ou api_key)",
            )

        try:
            # 1. Parse question
            question = arguments.get("question", "")
            if not question:
                return ToolResult(
                    tool_call_id="",
                    tool_name=self.name,
                    tool_type="interna",
                    success=False,
                    content="Erro: parametro 'question' e obrigatorio",
                )

            # 2. Initialize repositories and services
            prompt_repo = PromptRepository(context.db)
            openai_client = AsyncOpenAI(api_key=context.openai_api_key)

            # --- Roteamento por sub-agente ---
            routing_result = await self._route_to_sub_agent(
                context=context,
                question=question,
                openai_client=openai_client,
                prompt_repo=prompt_repo,
            )
            if routing_result is not None:
                return routing_result
            # --- Fim do roteamento ---

            # 3. Fetch objection titles for this agent
            objection_repo = ObjectionRepository(context.db)
            objection_titles = await objection_repo.get_titles_by_agent(
                company_id=context.company_id,
                agent_id=context.agent_id,
            )

            # 4. If no objections registered, skip classification → go straight to FAQ
            if not objection_titles:
                logger.info(
                    "No objections for agent %s, skipping classification",
                    context.agent_id,
                )
            else:
                # 5. Fetch classification prompt with config
                classification_config = await prompt_repo.get_prompt_with_config(
                    company_id=context.company_id,
                    name="filter_obj_faq",
                    reason="obj_faq",
                )

                if not classification_config:
                    return ToolResult(
                        tool_call_id="",
                        tool_name=self.name,
                        tool_type="interna",
                        success=False,
                        content="Erro: prompt 'filter_obj_faq' nao configurado",
                    )

                # 6. Format objection titles for prompt
                titles_text = "\n".join(
                    f"- ID {obj['id']}: {obj['title']}" for obj in objection_titles
                )

                # 7. Replace placeholders
                filled_prompt = (
                    classification_config.prompt
                    .replace("[OBJECTION_TITLES]", titles_text)
                    .replace("[question]", question)
                )

                # 8. Classify question with chain-of-thought
                classification = await self._classify_question(
                    client=openai_client,
                    prompt=filled_prompt,
                    model=classification_config.model,
                    temperature=classification_config.temperature,
                )

                logger.info(
                    "Classification result: %s (objection_ids=%s, reasoning=%s)",
                    classification["result"],
                    classification["objection_ids"],
                    classification["reasoning"],
                )

                # 9. If "obj" with valid objection_ids, handle objection flow
                if classification["result"] == "obj" and classification["objection_ids"]:
                    # Set flag FIRST to prevent cancellation during generation
                    if context.conversation_turn:
                        context.conversation_turn.objection_generating = True

                    try:
                        # Fetch objection data needed for both connection msg and prompt
                        objection_repo_obj = ObjectionRepository(context.db)
                        objections = await objection_repo_obj.get_objections_by_ids(
                            classification["objection_ids"]
                        )

                        if not objections:
                            return ToolResult(
                                tool_call_id="",
                                tool_name=self.name,
                                tool_type="interna",
                                success=False,
                                content="Erro: script de objecao nao encontrado",
                            )

                        combined_titles = "\n\n".join(
                            obj["title"] for obj in objections
                        )

                        # Generate and send dynamic connection message
                        if context.on_send_messages:
                            try:
                                connection_msg = (
                                    await self._generate_connection_message(
                                        openai_client=openai_client,
                                        question=question,
                                        objection_titles=combined_titles,
                                        chat_history=context.chat_history,
                                    )
                                )
                                if connection_msg:
                                    await context.on_send_messages([connection_msg])
                                    logger.info(
                                        "[RagTool] Connection message sent: %s",
                                        connection_msg,
                                    )
                            except Exception:
                                logger.warning(
                                    "[RagTool] Failed to send connection message",
                                    exc_info=True,
                                )

                        # Generate objection prompt (the heavy work)
                        return await self._handle_objection(
                            context=context,
                            question=question,
                            prompt_repo=prompt_repo,
                            openai_client=openai_client,
                            objection_ids=classification["objection_ids"],
                            objections=objections,
                        )
                    finally:
                        if context.conversation_turn:
                            context.conversation_turn.objection_generating = False

            # 7. FAQ flow: Generate embeddings
            embedding_service = EmbeddingService(context.openai_api_key)
            embedding = await embedding_service.generate(question)

            # 8. Search via pgvector (match_chunks)
            vector_service = VectorSearchService(context.db)
            search_results = await vector_service.search(
                embedding=embedding,
                company_id=context.company_id,
                agent_id=context.agent_id,
                sub_agent_id=context.sub_agent_id,
                match_count=5,
            )

            # 10. Extract and combine text from results
            combined_text = self._combine_search_results(search_results)

            if not combined_text:
                return ToolResult(
                    tool_call_id="",
                    tool_name=self.name,
                    tool_type="interna",
                    success=True,
                    content="Nenhuma informacao encontrada no FAQ para esta pergunta.",
                )

            # 11. Fetch summarization prompt with config
            summary_config = await prompt_repo.get_prompt_with_config(
                company_id=context.company_id,
                name="rag_summary",
                reason="rag_summary",
            )

            if not summary_config:
                return ToolResult(
                    tool_call_id="",
                    tool_name=self.name,
                    tool_type="interna",
                    success=False,
                    content="Erro: prompt 'rag_summary' nao configurado",
                )

            # 12. Format prompt with variables
            filled_summary_prompt = summary_config.prompt.replace(
                "[userQuestion]", question
            ).replace("[combinedText]", combined_text)

            # 13. Call OpenAI for summarization using config from database
            summary = await self._summarize(
                client=openai_client,
                prompt=filled_summary_prompt,
                model=summary_config.model,
                temperature=summary_config.temperature,
            )

            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                tool_type="interna",
                success=True,
                content=summary,
                rag_result=search_results,
            )

        except Exception as e:
            # Rollback para evitar InFailedSQLTransactionError na próxima iteração
            try:
                await context.db.rollback()
            except Exception:
                pass
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                tool_type="interna",
                success=False,
                content=f"Erro na execucao do RAG: {str(e)}",
            )

    async def _route_to_sub_agent(
        self,
        context: ToolExecutionContext,
        question: str,
        openai_client: AsyncOpenAI,
        prompt_repo: PromptRepository,
    ) -> ToolResult | None:
        """
        Verifica se a pergunta deve ser roteada para outro sub-agente.

        Retorna ToolResult se roteado, None se deve continuar no fluxo RAG.
        """
        try:
            # 1. Buscar sub-agentes irmãos
            agent_repo = AgentRepository(context.db)
            siblings = await agent_repo.get_sibling_sub_agents(
                agent_id=context.agent_id,
                current_sub_agent_id=context.sub_agent_id,
                company_id=context.company_id,
            )

            if not siblings:
                return None

            # 2. Buscar info do sub-agente atual
            current_sub_agent = await agent_repo.get_sub_agent_info(context.sub_agent_id)
            if not current_sub_agent:
                return None

            # 3. Buscar prompt de roteamento
            routing_config = await prompt_repo.get_prompt_with_config(
                company_id=context.company_id,
                name="rag_sub_agent_routing",
                reason="rag_routing",
            )

            if not routing_config:
                return None

            # 4. Montar texto com sub-agentes
            sub_agents_text = "\n".join(
                f'Sub-agente ID {s["id"]} - "{s["name"]}": {s.get("mission") or s["name"]}'
                for s in siblings
            )

            current_sa_text = (
                f'Sub-agente atual ID {current_sub_agent["id"]} - '
                f'"{current_sub_agent["name"]}": '
                f'{current_sub_agent.get("mission") or current_sub_agent["name"]}'
            )

            # 5. Substituir placeholders
            formatted_history = self._format_chat_history(context.chat_history)
            filled_prompt = (
                routing_config.prompt
                .replace("[CURRENT_SUB_AGENT]", current_sa_text)
                .replace("[SUB_AGENTS]", sub_agents_text)
                .replace("[CHAT_HISTORY]", formatted_history)
                .replace("[QUESTION]", question)
            )

            # 6. Chamar OpenAI com JSON Schema
            response = await openai_client.chat.completions.create(
                model=routing_config.model,
                temperature=routing_config.temperature,
                messages=[{"role": "user", "content": filled_prompt}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "routing_decision",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "action": {
                                    "type": "string",
                                    "enum": ["route", "continue"],
                                },
                                "sub_agent_id": {
                                    "type": "integer",
                                },
                                "instruction": {
                                    "type": "string",
                                },
                            },
                            "required": ["action", "sub_agent_id", "instruction"],
                            "additionalProperties": False,
                        },
                    },
                },
            )

            content = response.choices[0].message.content
            if not content:
                return None

            decision = json.loads(content)

            if decision.get("action") != "route":
                return None

            # 7. Validar sub_agent_id contra lista de irmãos
            target_id = decision.get("sub_agent_id", 0)
            valid_ids = {s["id"] for s in siblings}
            if target_id not in valid_ids:
                return None

            # 8. Atualizar sub_agent do customer
            customer_repo = CustomerRepository(context.db)
            await customer_repo.update_sub_agent(
                session_id=context.session_id,
                company_id=context.company_id,
                new_sub_agent_id=target_id,
            )

            # 9. Encontrar nome do sub-agente alvo
            target_name = next(
                (s["name"] for s in siblings if s["id"] == target_id), str(target_id)
            )
            instruction = decision.get("instruction", "")

            transfer_parts = [
                f"A duvida do usuario foi redirecionada para voce ({target_name}) "
                f"porque voce e o sub-agente mais adequado para responder.",
                f"Pergunta original do usuario: \"{question}\"",
                "Responda a pergunta diretamente, como se o usuario tivesse feito "
                "a pergunta para voce desde o inicio. NAO mencione transferencia "
                "ou redirecionamento ao usuario.",
            ]
            if instruction:
                transfer_parts.append(f"Instrucao adicional: {instruction}")

            transfer_msg = "\n".join(transfer_parts)

            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                tool_type="interna",
                success=True,
                content=transfer_msg,
                invalidate_cache=True,
            )
        except Exception:
            try:
                await context.db.rollback()
            except Exception:
                pass
            raise

    async def _handle_objection(
        self,
        context: ToolExecutionContext,
        question: str,
        prompt_repo: PromptRepository,
        openai_client: AsyncOpenAI,
        objection_ids: list[int],
        objections: list[dict[str, str]] | None = None,
    ) -> ToolResult:
        """
        Handle objection flow when classification returns "obj".

        Flow:
        1. Fetch scripts from objections table by multiple IDs
        2. Fetch prompt "objection_agent_generation" with config
        3. Format chat history
        4. Concatenate titles/scripts and replace placeholders
        5. Call OpenAI with JSON Schema response
        6. Insert generated prompt in prompts table
        7. Update customer with variable_prompt_id
        8. Return success

        Args:
            context: Execution context with session info
            question: The objection question from user
            prompt_repo: Prompt repository instance
            openai_client: OpenAI async client
            objection_ids: List of objection IDs identified by classifier

        Returns:
            ToolResult with success or error
        """
        try:
            # 1. Fetch objections (title + script) by IDs, preserving order
            if objections is None:
                objection_repo = ObjectionRepository(context.db)
                objections = await objection_repo.get_objections_by_ids(objection_ids)

            if not objections:
                return ToolResult(
                    tool_call_id="",
                    tool_name=self.name,
                    tool_type="interna",
                    success=False,
                    content="Erro: script de objecao nao encontrado para este agente",
                )

            # 2. Fetch prompt "objection_agent_generation" with config
            objection_prompt_config = await prompt_repo.get_prompt_with_config(
                company_id=context.company_id,
                name="objection_agent_generation",
                reason="objection_agent_generation",
            )

            if not objection_prompt_config:
                return ToolResult(
                    tool_call_id="",
                    tool_name=self.name,
                    tool_type="interna",
                    success=False,
                    content="Erro: prompt 'objection_agent_generation' nao configurado",
                )

            # 3. Format chat history
            formatted_history = self._format_chat_history(context.chat_history)

            # 4. Concatenate titles and scripts from all matched objections
            combined_titles = "\n\n".join(obj["title"] for obj in objections)
            combined_scripts = "\n\n".join(obj["script"] for obj in objections)

            # 5. Replace placeholders in prompt
            filled_prompt = (
                objection_prompt_config.prompt
                .replace("[TITLE]", combined_titles)
                .replace("[SCRIPT]", combined_scripts)
                .replace("[MENSAGENS_ANTERIORES]", formatted_history)
                .replace("[OBJECAO]", question)
            )

            # 5. Call OpenAI with JSON Schema response
            response = await openai_client.chat.completions.create(
                model=objection_prompt_config.model,
                temperature=objection_prompt_config.temperature,
                messages=[
                    {"role": "user", "content": filled_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "prompt_generator",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "Nome do prompt sem espacos, usando underline",
                                },
                                "prompt": {
                                    "type": "string",
                                    "description": "Texto do prompt gerado",
                                },
                                "reason": {
                                    "type": "string",
                                    "description": "Objecao utilizada sem espacos, "
                                    "usando underline",
                                },
                            },
                            "required": ["name", "prompt", "reason"],
                            "additionalProperties": False,
                        },
                    },
                },
            )

            content = response.choices[0].message.content
            if not content:
                return ToolResult(
                    tool_call_id="",
                    tool_name=self.name,
                    tool_type="interna",
                    success=False,
                    content="Erro: OpenAI retornou resposta vazia",
                )

            # 6. Parse response JSON
            generated = json.loads(content)

            # 7. Insert generated prompt in prompts table
            prompt_id = await prompt_repo.insert_variable_prompt(
                company_id=context.company_id,
                name=generated["name"],
                prompt=generated["prompt"],
                reason=generated["reason"],
                model=objection_prompt_config.model,
                temperature=objection_prompt_config.temperature,
            )

            # 8. Update customer with variable_prompt_id
            customer_repo = CustomerRepository(context.db)
            await customer_repo.update_variable_prompt(
                session_id=context.session_id,
                company_id=context.company_id,
                prompt_id=prompt_id,
            )

            # 9. Return success with cache invalidation (context changed)
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                tool_type="interna",
                success=True,
                content="prompt de quebra de objecao gerado com sucesso",
                invalidate_cache=True,  # Force context rebuild on next iteration
            )
        except Exception:
            try:
                await context.db.rollback()
            except Exception:
                pass
            raise

    def _format_chat_history(
        self,
        chat_history: list[dict[str, Any]] | None,
    ) -> str:
        """
        Format chat history for prompt placeholder.

        Args:
            chat_history: List of chat history records

        Returns:
            Formatted string with conversation history
        """
        if not chat_history:
            return ""

        formatted_messages = []
        for msg in chat_history:
            role = msg.get("role", "")
            content = msg.get("content", "")

            # Skip tool messages and empty content
            if role == "tool" or not content:
                continue

            # Format: "Role: Content"
            role_label = "Usuario" if role == "user" else "Assistente"
            formatted_messages.append(f"{role_label}: {content}")

        return "\n".join(formatted_messages)

    async def _generate_connection_message(
        self,
        openai_client: AsyncOpenAI,
        question: str,
        objection_titles: str,
        chat_history: list[dict[str, Any]] | None,
    ) -> str | None:
        """
        Generate a short connection message to keep the lead engaged while the
        objection prompt is being generated.

        Args:
            openai_client: OpenAI async client
            question: The lead's objection message
            objection_titles: Combined objection titles identified
            chat_history: Recent chat history

        Returns:
            Generated message string or None if generation fails
        """
        try:
            # Format last 4 messages for context
            recent_history = ""
            if chat_history:
                recent = [
                    m for m in chat_history
                    if m.get("role") != "tool" and m.get("content")
                ][-4:]
                recent_history = "\n".join(
                    f"{'Usuario' if m['role'] == 'user' else 'Assistente'}: {m['content']}"
                    for m in recent
                )

            prompt = (
                "Voce e um assistente de vendas. O lead acabou de fazer uma objecao sobre: "
                f"{objection_titles}.\n"
                f'A mensagem do lead foi: "{question}"\n\n'
                f"Historico recente:\n{recent_history}\n\n"
                "Gere UMA mensagem curta (1-2 frases) que:\n"
                "- Demonstre que voce entendeu a preocupacao\n"
                "- Crie curiosidade sobre a resposta que vira\n"
                "- Use o nome do lead se voce souber qual e\n"
                "- NAO responda a objecao, apenas deixe o lead curioso\n"
                "- Seja natural e conversacional, como um vendedor experiente\n"
                "- NAO use emojis\n\n"
                "Responda APENAS com a mensagem, sem explicacoes."
            )

            response = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.7,
                messages=[{"role": "user", "content": prompt}],
            )

            content = response.choices[0].message.content
            return content.strip() if content else None
        except Exception:
            logger.warning(
                "[RagTool] Failed to generate connection message", exc_info=True,
            )
            return None

    async def _classify_question(
        self,
        client: AsyncOpenAI,
        prompt: str,
        model: str,
        temperature: float,
    ) -> ClassificationResult:
        """
        Classify question as 'obj' or 'faq' using structured output with chain-of-thought.

        The LLM receives the list of objection titles and reasons about which ones
        match the user's question, returning the specific objection_ids.

        Args:
            client: OpenAI async client
            prompt: Formatted classification prompt with objection titles
            model: Model name from config
            temperature: Temperature from config

        Returns:
            ClassificationResult with result, objection_ids, and reasoning
        """
        response = await client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "user", "content": prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "classification_result",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "reasoning": {
                                "type": "string",
                            },
                            "result": {
                                "type": "string",
                                "enum": ["obj", "faq"],
                            },
                            "objection_ids": {
                                "type": "array",
                                "items": {"type": "integer"},
                            },
                        },
                        "required": ["reasoning", "result", "objection_ids"],
                        "additionalProperties": False,
                    },
                },
            },
        )

        content = response.choices[0].message.content
        if not content:
            return ClassificationResult(
                result="faq", objection_ids=[], reasoning="empty response"
            )

        parsed = json.loads(content)
        return ClassificationResult(
            result=parsed.get("result", "faq"),
            objection_ids=parsed.get("objection_ids", []),
            reasoning=parsed.get("reasoning", ""),
        )

    async def _summarize(
        self,
        client: AsyncOpenAI,
        prompt: str,
        model: str,
        temperature: float,
    ) -> str:
        """
        Summarize FAQ content using OpenAI.

        Args:
            client: OpenAI async client
            prompt: Formatted summary prompt with RAG results
            model: Model name from config
            temperature: Temperature from config

        Returns:
            Summarized answer
        """
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    def _combine_search_results(self, results: list[dict[str, Any]]) -> str:
        """
        Extract and combine text from pgvector search results.

        Args:
            results: List of search results from match_chunks()

        Returns:
            Combined text separated by dividers
        """
        texts = []
        for result in results:
            question = result.get("question", "")
            answer = result.get("answer", "")
            if question or answer:
                texts.append(f"Pergunta: {question}\nResposta: {answer}")

        return "\n\n---\n\n".join(texts)
