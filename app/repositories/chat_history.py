"""Repository for chat history operations."""

from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import ChatHistory, Customer


class ChatHistoryRepository:
    """Data access layer for chat_history table."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def insert_user_message(
        self,
        session_id: str,
        message: str,
        company_id: int,
    ) -> ChatHistory:
        """
        Insert a user message into chat_history.

        Fetches agent_id and sub_agent_id from the customer's session.

        Args:
            session_id: The session identifier
            message: The user's message content
            company_id: Company ID for multi-tenancy

        Returns:
            The created ChatHistory record

        Raises:
            ValueError: If session not found for the given company
        """
        # Get customer session to retrieve agent_id and sub_agent_id
        result = await self.db.execute(
            select(Customer).where(
                Customer.sessionId == session_id,
                Customer.company_id == company_id,
                Customer.deleted_at.is_(None),
            )
        )
        customer = result.scalar_one_or_none()

        if not customer:
            raise ValueError(
                f"Session '{session_id}' not found for company {company_id}"
            )

        # Create chat history record
        chat_record = ChatHistory(
            sessionId=session_id,
            role="user",
            content=message,
            agent_id=customer.agent_id,
            sub_agent_id=customer.sub_agent_id,
            company_id=company_id,
        )

        self.db.add(chat_record)
        await self.db.commit()
        await self.db.refresh(chat_record)

        return chat_record

    async def insert_assistant_message(
        self,
        session_id: str,
        content: str,
        company_id: int,
        *,
        input_tokens: int | None = None,
        input_cached_tokens: int | None = None,
        output_tokens: int | None = None,
        model: str | None = None,
    ) -> ChatHistory:
        """
        Insert an assistant message into chat_history.

        Args:
            session_id: The session identifier
            content: Formatted message content
            company_id: Company ID for multi-tenancy

        Returns:
            The created ChatHistory record

        Raises:
            ValueError: If session not found for the given company
        """
        # Get customer session to retrieve agent_id and sub_agent_id
        result = await self.db.execute(
            select(Customer).where(
                Customer.sessionId == session_id,
                Customer.company_id == company_id,
                Customer.deleted_at.is_(None),
            )
        )
        customer = result.scalar_one_or_none()

        if not customer:
            raise ValueError(
                f"Session '{session_id}' not found for company {company_id}"
            )

        chat_record = ChatHistory(
            sessionId=session_id,
            role="assistant",
            content=content,
            agent_id=customer.agent_id,
            sub_agent_id=customer.sub_agent_id,
            company_id=company_id,
            input_tokens=input_tokens,
            input_cached_tokens=input_cached_tokens,
            output_tokens=output_tokens,
            model=model,
        )

        self.db.add(chat_record)
        await self.db.commit()
        await self.db.refresh(chat_record)

        return chat_record

    async def get_history_with_orphan_handling(
        self,
        session_id: str,
        company_id: int,
    ) -> list[dict[str, Any]]:
        """
        Fetch last 10 messages with orphan tool handling.

        If there's a role='tool' in the last 10 messages, ensures that the
        assistant message with tool_calls is included (even if it falls outside
        the 10 message limit).

        Args:
            session_id: The session identifier
            company_id: Company ID for multi-tenancy

        Returns:
            List of chat history records as dicts, ordered by created_at ASC
        """
        query = text("""
            WITH base_messages AS (
                SELECT *
                FROM chat_history
                WHERE "sessionId" = :session_id
                  AND company_id = :company_id
                ORDER BY created_at DESC
                LIMIT 10
            ),
            has_orphan_tool AS (
                SELECT EXISTS (
                    SELECT 1 FROM base_messages WHERE role = 'tool'
                ) as has_tool
            ),
            extra_message AS (
                SELECT ch.*
                FROM chat_history ch
                CROSS JOIN has_orphan_tool hot
                WHERE hot.has_tool
                  AND ch."sessionId" = :session_id
                  AND ch.company_id = :company_id
                  AND ch.created_at < (SELECT MIN(created_at) FROM base_messages)
                  AND ch.role = 'assistant'
                  AND ch.tool_calls IS NOT NULL
                ORDER BY ch.created_at DESC
                LIMIT 1
            )
            SELECT * FROM (
                SELECT * FROM extra_message
                UNION ALL
                SELECT * FROM base_messages
            ) final
            ORDER BY created_at ASC
        """)

        result = await self.db.execute(
            query, {"session_id": session_id, "company_id": company_id}
        )
        rows = result.fetchall()

        return [dict(row._mapping) for row in rows]

    async def insert_assistant_with_tool_calls(
        self,
        session_id: str,
        company_id: int,
        agent_id: int,
        sub_agent_id: int,
        tool_calls: list[dict],
        content: str | None = None,
        *,
        input_tokens: int | None = None,
        input_cached_tokens: int | None = None,
        output_tokens: int | None = None,
        model: str | None = None,
    ) -> ChatHistory:
        """
        Insert assistant message with tool_calls.

        Args:
            session_id: The session identifier
            company_id: Company ID for multi-tenancy
            agent_id: Agent ID
            sub_agent_id: Sub-agent ID
            tool_calls: List of tool calls in OpenAI format
            content: Optional text content sent before tool execution

        Returns:
            The created ChatHistory record
        """
        chat_record = ChatHistory(
            sessionId=session_id,
            role="assistant",
            content=content,
            agent_id=agent_id,
            sub_agent_id=sub_agent_id,
            company_id=company_id,
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            input_cached_tokens=input_cached_tokens,
            output_tokens=output_tokens,
            model=model,
        )

        self.db.add(chat_record)
        await self.db.commit()
        await self.db.refresh(chat_record)

        return chat_record

    async def insert_tool_result(
        self,
        session_id: str,
        company_id: int,
        agent_id: int,
        sub_agent_id: int,
        tool_call_id: str,
        content: str,
    ) -> ChatHistory:
        """
        Insert tool result message (role='tool').

        Args:
            session_id: The session identifier
            company_id: Company ID for multi-tenancy
            agent_id: Agent ID
            sub_agent_id: Sub-agent ID
            tool_call_id: The tool call ID this result corresponds to
            content: The tool execution result content

        Returns:
            The created ChatHistory record
        """
        chat_record = ChatHistory(
            sessionId=session_id,
            role="tool",
            content=content,
            agent_id=agent_id,
            sub_agent_id=sub_agent_id,
            company_id=company_id,
            tool_call_id=tool_call_id,
        )

        self.db.add(chat_record)
        await self.db.commit()
        await self.db.refresh(chat_record)

        return chat_record

    async def insert_follow_up_message(
        self,
        session_id: str,
        company_id: int,
        agent_id: int,
        sub_agent_id: int,
        content: str,
    ) -> ChatHistory:
        """
        Insert follow-up message with is_follow_up=True.

        Args:
            session_id: The session identifier
            company_id: Company ID for multi-tenancy
            agent_id: Agent ID
            sub_agent_id: Sub-agent ID
            content: Formatted message content

        Returns:
            The created ChatHistory record
        """
        chat_record = ChatHistory(
            sessionId=session_id,
            role="assistant",
            content=content,
            agent_id=agent_id,
            sub_agent_id=sub_agent_id,
            company_id=company_id,
            is_follow_up=True,
        )

        self.db.add(chat_record)
        await self.db.commit()
        await self.db.refresh(chat_record)

        return chat_record

    async def delete_by_session(
        self,
        session_id: str,
        company_id: int,
    ) -> int:
        """
        Delete all chat history for a session.

        Args:
            session_id: The session identifier
            company_id: Company ID for multi-tenancy

        Returns:
            Number of deleted rows
        """
        result = await self.db.execute(
            delete(ChatHistory).where(
                ChatHistory.sessionId == session_id,
                ChatHistory.company_id == company_id,
            )
        )
        await self.db.commit()
        return result.rowcount
