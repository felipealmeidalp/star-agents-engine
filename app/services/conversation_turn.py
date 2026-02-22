"""ConversationTurn - In-memory accumulator for a single conversation turn.

Collects all messages (user, assistant, tool_calls, tool results) during
a processing cycle and saves them atomically at the end. This prevents
orphaned records in the database when a request is cancelled mid-processing.
"""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import OpenAIMessage
from app.repositories.chat_history import ChatHistoryRepository

logger = logging.getLogger(__name__)


class ConversationTurn:
    """Accumulates all messages for a conversation turn in memory.

    Instead of writing each message to the database immediately,
    this class collects everything and provides a single save_all()
    method that persists everything in one transaction.
    """

    def __init__(
        self,
        user_message: str,
        prior_tool_history: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        Initialize a conversation turn.

        Args:
            user_message: The concatenated user message(s)
            prior_tool_history: Tool history preserved from a cancelled task
                (list of dicts with role, content, tool_calls, tool_call_id)
        """
        self.user_message = user_message
        self.prior_tool_history = prior_tool_history or []
        self.pending_messages: list[dict[str, Any]] = []
        self._final_response: str | None = None

    def add_assistant_message(self, content: str) -> None:
        """Record the final assistant response (no tool_calls)."""
        self._final_response = content
        self.pending_messages.append({
            "role": "assistant",
            "content": content,
        })

    def add_assistant_with_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        content: str | None = None,
    ) -> None:
        """Record an assistant message that includes tool_calls."""
        msg: dict[str, Any] = {
            "role": "assistant",
            "tool_calls": tool_calls,
        }
        if content is not None:
            msg["content"] = content
        self.pending_messages.append(msg)

    def add_tool_result(
        self,
        tool_call_id: str,
        content: str,
    ) -> None:
        """Record a tool execution result."""
        self.pending_messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        })

    def get_completed_tool_history(self) -> list[dict[str, Any]]:
        """Extract completed tool call pairs (assistant+tool_calls with their results).

        Returns only pairs where ALL tool_calls in an assistant message have
        corresponding tool results. Used to preserve history when cancelling
        a task that has already executed tools with side effects.

        Returns:
            List of dicts representing assistant+tool_calls and tool result messages.
        """
        result: list[dict[str, Any]] = []
        i = 0
        messages = self.pending_messages

        while i < len(messages):
            msg = messages[i]

            # Look for assistant messages with tool_calls
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                tool_calls = msg["tool_calls"]
                expected_ids = set()
                for tc in tool_calls:
                    tc_id = tc.get("id") or tc.get("tool_call_id", "")
                    if tc_id:
                        expected_ids.add(tc_id)

                # Collect subsequent tool results
                tool_results = []
                found_ids = set()
                j = i + 1
                while j < len(messages) and messages[j].get("role") == "tool":
                    tool_msg = messages[j]
                    tc_id = tool_msg.get("tool_call_id", "")
                    if tc_id in expected_ids:
                        found_ids.add(tc_id)
                        tool_results.append(tool_msg)
                    j += 1

                # Only include if ALL tool_calls have results
                if expected_ids and expected_ids == found_ids:
                    result.append(msg)
                    result.extend(tool_results)
                    i = j
                    continue
                else:
                    # Incomplete pair - stop here (everything after is incomplete)
                    break

            i += 1

        return result

    def get_pending_as_openai_messages(self) -> list[OpenAIMessage]:
        """Convert pending messages to OpenAI message format.

        Returns user message + prior_tool_history + pending_messages
        as a list of OpenAIMessage objects for the context builder.

        Returns:
            List of OpenAIMessage objects
        """
        result: list[OpenAIMessage] = []

        # 1. User message
        result.append(OpenAIMessage(role="user", content=self.user_message))

        # 2. Prior tool history (from cancelled task)
        for msg in self.prior_tool_history:
            result.append(OpenAIMessage(**msg))

        # 3. Current pending messages (accumulated during this turn)
        for msg in self.pending_messages:
            result.append(OpenAIMessage(**msg))

        return result

    async def save_all(
        self,
        chat_repo: ChatHistoryRepository,
        session_id: str,
        company_id: int,
        agent_id: int,
        sub_agent_id: int,
    ) -> None:
        """Persist all accumulated messages to the database atomically.

        Saves in order:
        1. User message
        2. Prior tool history (from cancelled task)
        3. All pending messages (assistant, tool_calls, tool results)

        Uses a single commit at the end for atomicity.

        Args:
            chat_repo: Chat history repository
            session_id: Session identifier
            company_id: Company ID
            agent_id: Agent ID
            sub_agent_id: Sub-agent ID
        """
        from app.models.tables import ChatHistory

        db = chat_repo.db

        logger.info(
            "[ConversationTurn] Saving all messages: user=1, "
            "prior_tool_history=%d, pending=%d",
            len(self.prior_tool_history),
            len(self.pending_messages),
        )

        # 1. Save user message
        db.add(ChatHistory(
            sessionId=session_id,
            role="user",
            content=self.user_message,
            agent_id=agent_id,
            sub_agent_id=sub_agent_id,
            company_id=company_id,
        ))

        # 2. Save prior tool history
        for msg in self.prior_tool_history:
            record = ChatHistory(
                sessionId=session_id,
                role=msg["role"],
                content=msg.get("content"),
                agent_id=agent_id,
                sub_agent_id=sub_agent_id,
                company_id=company_id,
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
            )
            db.add(record)

        # 3. Save pending messages
        for msg in self.pending_messages:
            record = ChatHistory(
                sessionId=session_id,
                role=msg["role"],
                content=msg.get("content"),
                agent_id=agent_id,
                sub_agent_id=sub_agent_id,
                company_id=company_id,
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
            )
            db.add(record)

        # Atomic commit
        await db.commit()

        logger.info(
            "[ConversationTurn] All messages saved successfully for session=%s",
            session_id,
        )
