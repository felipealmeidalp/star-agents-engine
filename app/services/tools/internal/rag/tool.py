"""RAG (Retrieval Augmented Generation) tool."""

import json
from typing import Any

from openai import AsyncOpenAI

from app.config import settings
from app.models.schemas import ToolExecutionContext, ToolResult
from app.repositories.company import CompanyRepository
from app.repositories.customer import CustomerRepository
from app.repositories.objection import ObjectionRepository
from app.repositories.prompt import PromptRepository
from app.services.tool_handler import BaseTool

from .embedding import EmbeddingService
from .qdrant import QdrantService


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
        5. If "faq": generate embeddings, search Qdrant, summarize
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
            company_repo = CompanyRepository(context.db)
            openai_client = AsyncOpenAI(api_key=context.openai_api_key)

            # 3. Fetch classification prompt with config (model, temperature)
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

            # 4. Replace [question] placeholder
            filled_prompt = classification_config.prompt.replace("[question]", question)

            # 5. Classify question (obj vs faq) using config from database
            classification = await self._classify_question(
                client=openai_client,
                prompt=filled_prompt,
                model=classification_config.model,
                temperature=classification_config.temperature,
            )

            # 6. If "obj", handle objection flow
            if classification == "obj":
                return await self._handle_objection(
                    context=context,
                    question=question,
                    prompt_repo=prompt_repo,
                    openai_client=openai_client,
                )

            # 7. FAQ flow: Get company's RAG collection
            rag_collection = await company_repo.get_rag_collection(context.company_id)
            if not rag_collection:
                return ToolResult(
                    tool_call_id="",
                    tool_name=self.name,
                    tool_type="interna",
                    success=False,
                    content="Erro: RAG collection nao configurada para esta empresa",
                )

            # 8. Generate embeddings
            embedding_service = EmbeddingService(context.openai_api_key)
            embedding = await embedding_service.generate(question)

            # 9. Search Qdrant
            qdrant_service = QdrantService()
            search_results = await qdrant_service.search(
                collection=rag_collection,
                vector=embedding,
                limit=10,
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
            )

        except Exception as e:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                tool_type="interna",
                success=False,
                content=f"Erro na execucao do RAG: {str(e)}",
            )

    async def _handle_objection(
        self,
        context: ToolExecutionContext,
        question: str,
        prompt_repo: PromptRepository,
        openai_client: AsyncOpenAI,
    ) -> ToolResult:
        """
        Handle objection flow when classification returns "obj".

        Flow:
        1. Fetch script from objections table
        2. Fetch prompt "objection_agent_generation" with config
        3. Format chat history
        4. Replace placeholders: [SCRIPT], [MENSAGENS_ANTERIORES], [OBJECAO]
        5. Call OpenAI with JSON Schema response
        6. Insert generated prompt in prompts table
        7. Update customer with variable_prompt_id
        8. Return success

        Args:
            context: Execution context with session info
            question: The objection question from user
            prompt_repo: Prompt repository instance
            openai_client: OpenAI async client

        Returns:
            ToolResult with success or error
        """
        # 1. Fetch script from objections table
        objection_repo = ObjectionRepository(context.db)
        script = await objection_repo.get_script(
            company_id=context.company_id,
            agent_id=context.agent_id,
        )

        if not script:
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

        # 4. Replace placeholders in prompt
        filled_prompt = (
            objection_prompt_config.prompt
            .replace("[SCRIPT]", script)
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
                                "description": "Objecao utilizada sem espacos, usando underline",
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

        # 7. Insert generated prompt in prompts table (using model/temp from .env)
        prompt_id = await prompt_repo.insert_variable_prompt(
            company_id=context.company_id,
            name=generated["name"],
            prompt=generated["prompt"],
            reason=generated["reason"],
            model=settings.objection_agent_generated_model,
            temperature=settings.objection_agent_generated_temperature,
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

    async def _classify_question(
        self,
        client: AsyncOpenAI,
        prompt: str,
        model: str,
        temperature: float,
    ) -> str:
        """
        Classify question as 'obj' or 'faq' using structured output.

        Args:
            client: OpenAI async client
            prompt: Formatted classification prompt
            model: Model name from config
            temperature: Temperature from config

        Returns:
            Classification result ("obj" or "faq")
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
                            "result": {
                                "type": "string",
                                "enum": ["obj", "faq"],
                            }
                        },
                        "required": ["result"],
                        "additionalProperties": False,
                    },
                },
            },
        )

        content = response.choices[0].message.content
        if not content:
            return "faq"  # Default to faq if no response

        result = json.loads(content)
        return result.get("result", "faq")

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
        Extract and combine text from Qdrant search results.

        Args:
            results: List of search results from Qdrant

        Returns:
            Combined text separated by dividers
        """
        texts = []
        for result in results:
            payload = result.get("payload", {})
            text = payload.get("text") or payload.get("content", "")
            if text:
                texts.append(text)

        return "\n\n---\n\n".join(texts)
