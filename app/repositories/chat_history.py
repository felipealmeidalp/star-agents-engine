"""Repository for chat history operations."""

from datetime import datetime
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
            isHuman=True,
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
        is_human: bool = False,
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
            isHuman=is_human,
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
        Fetch last 20 messages with orphan tool handling.

        If there's a role='tool' in the last 20 messages, ensures that the
        assistant message with tool_calls is included (even if it falls outside
        the 20 message limit).

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
                  AND role != 'dev'
                ORDER BY created_at DESC, id DESC
                LIMIT 20
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
                ORDER BY ch.created_at DESC, ch.id DESC
                LIMIT 1
            )
            SELECT * FROM (
                SELECT * FROM extra_message
                UNION ALL
                SELECT * FROM base_messages
            ) final
            ORDER BY created_at ASC, id ASC
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
        rag_result: list[dict] | None = None,
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
            rag_result=rag_result,
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

    async def has_recent_follow_up(
        self,
        session_id: str,
        company_id: int,
        minutes: int = 2,
    ) -> bool:
        """
        Check if a follow-up message was saved recently for this session.

        Used to prevent duplicate saves when the follow-up consumer already
        saved the message and Chatwoot sends back an outgoing webhook.

        Args:
            session_id: The session identifier
            company_id: Company ID for multi-tenancy
            minutes: Time window in minutes to check

        Returns:
            True if a recent follow-up message exists
        """
        query = text("""
            SELECT EXISTS (
                SELECT 1
                FROM chat_history
                WHERE "sessionId" = :session_id
                  AND company_id = :company_id
                  AND is_follow_up = true
                  AND created_at >= NOW() - INTERVAL ':minutes minutes'
            ) AS has_recent
        """.replace(":minutes", str(int(minutes))))

        result = await self.db.execute(
            query,
            {"session_id": session_id, "company_id": company_id},
        )
        row = result.fetchone()
        return bool(row and row.has_recent)

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

    async def list_conversations(
        self,
        company_id: int,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """
        List sessions for a company, most recently active first.

        Args:
            company_id: Company ID for multi-tenancy
            limit: Max items to return
            cursor: ISO timestamp from a previous page's next_cursor

        Returns:
            Tuple of (items, next_cursor). next_cursor is None on the last page.
        """
        query = text("""
            SELECT DISTINCT ON (ch."sessionId")
                   ch."sessionId" AS session_id,
                   ch.content AS preview,
                   ch.role AS preview_role,
                   ch.created_at AS last_message_at,
                   c.name AS customer_name,
                   c.customer_context
            FROM chat_history ch
            JOIN customers c
              ON c."sessionId" = ch."sessionId"
             AND c.company_id = ch.company_id
             AND c.deleted_at IS NULL
            WHERE ch.company_id = :company_id
              AND ch.role IN ('user', 'assistant')
              AND ch.content IS NOT NULL
            ORDER BY ch."sessionId", ch.created_at DESC, ch.id DESC
        """)
        result = await self.db.execute(query, {"company_id": company_id})
        rows = [dict(r._mapping) for r in result.fetchall()]

        # ponytail: ordenação/paginação em memória — DISTINCT ON exige ordenar por
        # sessionId primeiro. Vira window function se passar de ~alguns milhares de sessões.
        rows.sort(key=lambda r: r["last_message_at"], reverse=True)
        if cursor:
            # Compara datetime, não string: o mesmo instante serializa como "Z"
            # ou "+00:00" dependendo da origem, e a ordem ASCII dos dois difere.
            after = datetime.fromisoformat(cursor)
            rows = [r for r in rows if r["last_message_at"] < after]

        page = rows[:limit]
        next_cursor = (
            page[-1]["last_message_at"].isoformat() if len(rows) > limit else None
        )
        return page, next_cursor

    async def list_messages(
        self,
        session_id: str,
        company_id: int,
    ) -> list[dict[str, Any]]:
        """
        Fetch the full visible history of a session, oldest first.

        Only user/assistant messages with content — tool calls and dev
        commands are internal and never rendered by a client.
        """
        result = await self.db.execute(
            select(ChatHistory)
            .where(
                ChatHistory.sessionId == session_id,
                ChatHistory.company_id == company_id,
                ChatHistory.role.in_(("user", "assistant")),
                ChatHistory.content.isnot(None),
            )
            .order_by(ChatHistory.created_at.asc(), ChatHistory.id.asc())
        )
        return [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "fragments": m.content.split("\n") if m.role == "assistant" else None,
                "is_human": m.isHuman,
                "created_at": m.created_at,
            }
            for m in result.scalars().all()
        ]
