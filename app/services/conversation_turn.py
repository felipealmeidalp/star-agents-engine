"""ConversationTurn - In-memory accumulator for a single conversation turn.

Collects all messages (user, assistant, tool_calls, tool results) during
a processing cycle and saves them atomically at the end. This prevents
orphaned records in the database when a request is cancelled mid-processing.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import OpenAIMessage, TokenUsage
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
        *,
        user_message_already_saved: bool = False,
        pending_checker: Callable[[], Awaitable[list[str] | None]] | None = None,
        dev_mode: bool = False,
    ) -> None:
        """
        Initialize a conversation turn.

        Args:
            user_message: The concatenated user message(s)
            prior_tool_history: Tool history preserved from a cancelled task
                (list of dicts with role, content, tool_calls, tool_call_id)
            user_message_already_saved: If True, skip saving/including user message
                (it was already persisted by a previous save operation)
            pending_checker: Async callback that checks for messages that arrived
                during tool execution. Returns list of strings or None.
            dev_mode: If True, save assistant/tool messages with role="dev"
                instead of their original role. Preserves history and tokens
                but keeps them invisible to the AI context.
        """
        self.user_message = user_message
        self.prior_tool_history = prior_tool_history or []
        self.pending_messages: list[dict[str, Any]] = []
        self._final_response: str | None = None
        self.objection_generating: bool = False
        self.user_message_already_saved = user_message_already_saved
        self.pending_checker = pending_checker
        self._save_context: dict[str, Any] | None = None
        self.dev_mode = dev_mode

    def set_save_context(self, **kwargs: Any) -> None:
        """Store save parameters for deferred saving.

        Called by chat_processor when skip_save=True so that the caller
        (RequestManager) can trigger the save later with correct ordering.
        """
        self._save_context = kwargs

    def add_assistant_message(
        self, content: str, token_usage: TokenUsage | None = None,
    ) -> None:
        """Record the final assistant response (no tool_calls)."""
        self._final_response = content
        msg: dict[str, Any] = {
            "role": "assistant",
            "content": content,
        }
        if token_usage is not None:
            msg["_token_usage"] = token_usage
        self.pending_messages.append(msg)

    def add_assistant_with_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        content: str | None = None,
        token_usage: TokenUsage | None = None,
    ) -> None:
        """Record an assistant message that includes tool_calls."""
        msg: dict[str, Any] = {
            "role": "assistant",
            "tool_calls": tool_calls,
        }
        if content is not None:
            msg["content"] = content
        if token_usage is not None:
            msg["_token_usage"] = token_usage
        self.pending_messages.append(msg)

    def add_tool_result(
        self,
        tool_call_id: str,
        content: str,
        rag_result: list[dict] | None = None,
    ) -> None:
        """Record a tool execution result."""
        msg: dict[str, Any] = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        }
        if rag_result is not None:
            msg["_rag_result"] = rag_result
        self.pending_messages.append(msg)

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

        When user_message_already_saved=True, the user message is skipped
        (it's already in the database and will be loaded from chat history).

        Returns:
            List of OpenAIMessage objects
        """
        result: list[OpenAIMessage] = []

        # 1. User message (skip if already saved to DB — it'll come from history)
        if not self.user_message_already_saved:
            result.append(OpenAIMessage(role="user", content=self.user_message))

        # 2. Prior tool history (from cancelled task)
        for msg in self.prior_tool_history:
            result.append(OpenAIMessage(**msg))

        # 3. Current pending messages (accumulated during this turn)
        for msg in self.pending_messages:
            filtered = {k: v for k, v in msg.items() if not k.startswith("_")}
            result.append(OpenAIMessage(**filtered))

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
            "[ConversationTurn] Saving all messages: user=%s, "
            "prior_tool_history=%d, pending=%d",
            "skip" if self.user_message_already_saved else "1",
            len(self.prior_tool_history),
            len(self.pending_messages),
        )

        # 1. Save user message (skip if already persisted)
        if not self.user_message_already_saved:
            db.add(ChatHistory(
                sessionId=session_id,
                role="user",
                content=self.user_message,
                agent_id=agent_id,
                sub_agent_id=sub_agent_id,
                company_id=company_id,
            ))

        def _resolve_role(original_role: str, msg: dict[str, Any]) -> str:
            """In dev mode, only remap final assistant response to 'dev'.

            Tool-calling assistant messages and tool results keep their
            original role so the OpenAI context stays valid.
            """
            if not self.dev_mode:
                return original_role
            if original_role == "assistant" and msg.get("tool_calls"):
                return "assistant"
            if original_role == "tool":
                return "tool"
            if original_role == "assistant":
                return "dev"
            return original_role

        # 2. Save prior tool history
        for msg in self.prior_tool_history:
            tu: TokenUsage | None = msg.get("_token_usage")
            record = ChatHistory(
                sessionId=session_id,
                role=_resolve_role(msg["role"], msg),
                content=msg.get("content"),
                agent_id=agent_id,
                sub_agent_id=sub_agent_id,
                company_id=company_id,
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
                input_tokens=tu.input_tokens if tu else None,
                input_cached_tokens=tu.input_cached_tokens if tu else None,
                output_tokens=tu.output_tokens if tu else None,
                model=tu.model if tu else None,
            )
            db.add(record)

        # 3. Save pending messages
        for msg in self.pending_messages:
            tu = msg.get("_token_usage")
            record = ChatHistory(
                sessionId=session_id,
                role=_resolve_role(msg["role"], msg),
                content=msg.get("content"),
                agent_id=agent_id,
                sub_agent_id=sub_agent_id,
                company_id=company_id,
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
                input_tokens=tu.input_tokens if tu else None,
                input_cached_tokens=tu.input_cached_tokens if tu else None,
                output_tokens=tu.output_tokens if tu else None,
                model=tu.model if tu else None,
                rag_result=msg.get("_rag_result"),
            )
            db.add(record)

        # Atomic commit
        await db.commit()

        logger.info(
            "[ConversationTurn] All messages saved successfully for session=%s",
            session_id,
        )

    async def save_with_interjected_users(
        self,
        interjected_user_messages: list[str],
        chat_repo: ChatHistoryRepository,
        session_id: str,
        company_id: int,
        agent_id: int,
        sub_agent_id: int,
    ) -> None:
        """Persist messages with user messages inserted BEFORE the final assistant response.

        Saves in order:
        1. User message (original)
        2. Prior tool history
        3. Pending messages EXCEPT the last one (tool_calls + results)
        4. Interjected user messages (from objection-pending buffer)
        5. Last pending message (the assistant's final response / pitch)

        This ensures the LLM sees pending user messages BEFORE the pitch
        in subsequent turns.

        Args:
            interjected_user_messages: Messages that arrived during generation
            chat_repo: Chat history repository
            session_id: Session identifier
            company_id: Company ID
            agent_id: Agent ID
            sub_agent_id: Sub-agent ID
        """
        from app.models.tables import ChatHistory

        db = chat_repo.db

        logger.info(
            "[ConversationTurn] Saving with interjected users: user=1, "
            "prior_tool_history=%d, pending=%d, interjected=%d",
            len(self.prior_tool_history),
            len(self.pending_messages),
            len(interjected_user_messages),
        )

        def _resolve_role(original_role: str, msg: dict[str, Any]) -> str:
            """In dev mode, only remap final assistant response to 'dev'."""
            if not self.dev_mode:
                return original_role
            if original_role == "assistant" and msg.get("tool_calls"):
                return "assistant"
            if original_role == "tool":
                return "tool"
            if original_role == "assistant":
                return "dev"
            return original_role

        def _add_record(msg: dict[str, Any]) -> None:
            tu: TokenUsage | None = msg.get("_token_usage")
            db.add(ChatHistory(
                sessionId=session_id,
                role=_resolve_role(msg["role"], msg),
                content=msg.get("content"),
                agent_id=agent_id,
                sub_agent_id=sub_agent_id,
                company_id=company_id,
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
                input_tokens=tu.input_tokens if tu else None,
                input_cached_tokens=tu.input_cached_tokens if tu else None,
                output_tokens=tu.output_tokens if tu else None,
                model=tu.model if tu else None,
                rag_result=msg.get("_rag_result"),
            ))

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
            _add_record(msg)

        # 3. Save pending messages EXCEPT the last one
        for msg in self.pending_messages[:-1]:
            _add_record(msg)

        # 4. Save interjected user messages
        concatenated = "\n".join(interjected_user_messages)
        db.add(ChatHistory(
            sessionId=session_id,
            role="user",
            content=concatenated,
            agent_id=agent_id,
            sub_agent_id=sub_agent_id,
            company_id=company_id,
        ))

        # 5. Save the last pending message (assistant final response)
        if self.pending_messages:
            _add_record(self.pending_messages[-1])

        # Atomic commit
        await db.commit()

        logger.info(
            "[ConversationTurn] Messages saved with interjected users "
            "for session=%s",
            session_id,
        )

    async def deferred_save(self) -> None:
        """Execute save_all() using previously stored save context.

        Raises ValueError if set_save_context() was not called.
        """
        if self._save_context is None:
            raise ValueError("set_save_context() must be called before deferred_save()")
        await self.save_all(**self._save_context)

    async def deferred_save_with_interjected_users(
        self,
        interjected_user_messages: list[str],
    ) -> None:
        """Execute save_with_interjected_users() using previously stored save context.

        Raises ValueError if set_save_context() was not called.
        """
        if self._save_context is None:
            raise ValueError(
                "set_save_context() must be called before "
                "deferred_save_with_interjected_users()"
            )
        await self.save_with_interjected_users(
            interjected_user_messages=interjected_user_messages,
            **self._save_context,
        )
