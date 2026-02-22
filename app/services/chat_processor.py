"""
Chat processor - shared function for processing chat messages.

This module provides the core chat processing logic that can be called
by any receptor (HTTP API, Chatwoot webhook, Telegram, etc.).
"""

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.agent import AgentRepository
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.company import CompanyRepository
from app.repositories.prompt import PromptRepository
from app.services.chat_handler import ChatHandler, MessageSenderCallback
from app.services.context_builder import ContextBuilder
from app.services.conversation_turn import ConversationTurn
from app.services.openai import OpenAIService
from app.services.tool_handler import ToolHandler

logger = logging.getLogger(__name__)


async def process_chat(
    session_id: str,
    message: str,
    company_id: int,
    db: AsyncSession,
    on_send_messages: MessageSenderCallback | None = None,
) -> dict[str, Any]:
    """
    Process a chat message through the full orchestration pipeline (legacy).

    Saves user message immediately and writes to DB during processing.
    Used by receptors that don't need cancellation support (HTTP API, etc.).

    Args:
        session_id: Unique session identifier for the conversation.
        message: The user's message to process.
        company_id: Company ID for multi-tenancy isolation.
        db: Async database session.
        on_send_messages: Optional callback to send messages to the lead.

    Returns:
        Dict with the assistant's response.
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
        on_send_messages=on_send_messages,
    )

    return await handler.process(
        session_id=session_id,
        company_id=company_id,
    )


async def process_chat_in_memory(
    session_id: str,
    message: str,
    company_id: int,
    db: AsyncSession,
    conversation_turn: ConversationTurn,
    on_send_messages: MessageSenderCallback | None = None,
) -> dict[str, Any]:
    """
    Process a chat message with in-memory accumulation.

    Does NOT save to database during processing. All writes are accumulated
    in the provided ConversationTurn and saved atomically at the end via
    asyncio.shield.

    Used by RequestManager for cancellation-safe processing.

    Args:
        session_id: Unique session identifier for the conversation.
        message: The user's message (possibly concatenated from buffer).
        company_id: Company ID for multi-tenancy isolation.
        db: Async database session.
        conversation_turn: ConversationTurn to accumulate messages into.
        on_send_messages: Optional callback to send messages to the lead.

    Returns:
        Response dict from the assistant.
    """
    # 1. Setup repositories
    chat_repo = ChatHistoryRepository(db)
    agent_repo = AgentRepository(db)
    company_repo = CompanyRepository(db)
    prompt_repo = PromptRepository(db)

    # 3. Get company API key
    api_key = await company_repo.get_openai_api_key(company_id)

    # 4. Setup services
    context_builder = ContextBuilder(agent_repo, chat_repo, prompt_repo)
    openai_service = OpenAIService(api_key)
    tool_handler = ToolHandler()

    # 5. Create handler with ConversationTurn
    handler = ChatHandler(
        context_builder=context_builder,
        openai_service=openai_service,
        tool_handler=tool_handler,
        chat_repo=chat_repo,
        db=db,
        openai_api_key=api_key,
        on_send_messages=on_send_messages,
        conversation_turn=conversation_turn,
    )

    # 6. Process (all writes go to ConversationTurn in memory)
    response = await handler.process(
        session_id=session_id,
        company_id=company_id,
    )

    # 7. Get agent/sub_agent IDs for the atomic save
    context = context_builder.last_context
    agent_id = context.customer.agent_id if context else None
    sub_agent_id = context.customer.sub_agent_id if context else None

    # 8. Atomic save - protected from cancellation
    await asyncio.shield(
        conversation_turn.save_all(
            chat_repo=chat_repo,
            session_id=session_id,
            company_id=company_id,
            agent_id=agent_id,
            sub_agent_id=sub_agent_id,
        )
    )

    logger.info(
        "[ChatProcessor] In-memory processing complete, all messages saved "
        "atomically for session=%s",
        session_id,
    )

    return response
