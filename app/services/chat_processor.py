"""
Chat processor - shared function for processing chat messages.

This module provides the core chat processing logic that can be called
by any receptor (HTTP API, Chatwoot webhook, Telegram, etc.).
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.agent import AgentRepository
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.company import CompanyRepository
from app.repositories.prompt import PromptRepository
from app.services.chat_handler import ChatHandler
from app.services.context_builder import ContextBuilder
from app.services.openai import OpenAIService
from app.services.tool_handler import ToolHandler


async def process_chat(
    session_id: str,
    message: str,
    company_id: int,
    db: AsyncSession,
) -> dict[str, Any]:
    """
    Process a chat message through the full orchestration pipeline.

    This is the shared entry point that handles:
    1. Inserting the user message into chat history
    2. Getting company configuration (API key)
    3. Setting up all services and dependencies
    4. Calling ChatHandler to orchestrate the conversation
    5. Returning the final response

    Can be called by any receptor (HTTP, Chatwoot, Telegram, etc.).

    Args:
        session_id: Unique session identifier for the conversation.
        message: The user's message to process.
        company_id: Company ID for multi-tenancy isolation.
        db: Async database session.

    Returns:
        Dict with the assistant's response.

    Raises:
        MaxIterationsExceededError: If tool calling loop exceeds max iterations.
        ValueError: If company not found or API key missing.
        OpenAIAuthenticationError: If OpenAI API key is invalid.
        OpenAIRateLimitError: If OpenAI rate limit is exceeded.
        OpenAITimeoutError: If OpenAI request times out.
        OpenAIError: For other OpenAI-related errors.
    """
    # 1. Setup repositories
    chat_repo = ChatHistoryRepository(db)
    agent_repo = AgentRepository(db)
    company_repo = CompanyRepository(db)
    prompt_repo = PromptRepository(db)

    # 2. Insert user message
    await chat_repo.insert_user_message(
        session_id=session_id,
        message=message,
        company_id=company_id,
    )

    # 3. Get company API key
    api_key = await company_repo.get_openai_api_key(company_id)

    # 4. Setup services
    context_builder = ContextBuilder(agent_repo, chat_repo, prompt_repo)
    openai_service = OpenAIService(api_key)
    tool_handler = ToolHandler()

    # 5. Create handler and process
    handler = ChatHandler(
        context_builder=context_builder,
        openai_service=openai_service,
        tool_handler=tool_handler,
        chat_repo=chat_repo,
        db=db,
        openai_api_key=api_key,
    )

    return await handler.process(
        session_id=session_id,
        company_id=company_id,
    )
