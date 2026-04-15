"""RequestManager - Coordinates request lifecycle per contact_id.

Handles message buffering, request cancellation, and task management
to ensure only one active processing task exists per contact at a time.
When a new message arrives during active processing, the current task
is cancelled and a new one is created with all accumulated messages.
"""

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chatwoot.buffer import MessageBuffer
from app.config import settings
from app.services.chat_processor import process_chat_in_memory
from app.services.conversation_turn import ConversationTurn
from app.utils.alerter import send_critical_alert

logger = logging.getLogger(__name__)

# Type alias for the send-messages callback
MessageSenderCallback = Any


class _ActiveRequest:
    """Tracks an active processing task for a contact."""

    __slots__ = ("task", "conversation_turn")

    def __init__(
        self,
        task: asyncio.Task[Any],
        conversation_turn: ConversationTurn,
    ) -> None:
        self.task = task
        self.conversation_turn = conversation_turn


class RequestManager:
    """Manages the lifecycle of chat processing requests per contact.

    Responsibilities:
    - One asyncio.Lock per contact_id to prevent race conditions
    - Cancels active tasks when new messages arrive
    - Preserves completed tool history from cancelled tasks
    - Moves messages between buffer and processing keys atomically
    - Creates new processing tasks with full message context
    """

    def __init__(self, buffer: MessageBuffer | None = None) -> None:
        self._buffer = buffer or MessageBuffer()
        self._locks: dict[int, asyncio.Lock] = {}
        self._active_requests: dict[int, _ActiveRequest] = {}
        # Preserved tool history from cancelled tasks, keyed by contact_id.
        # Persists across message discards until consumed by a processing task.
        self._pending_tool_history: dict[int, list[dict[str, Any]]] = {}

    def _get_lock(self, contact_id: int) -> asyncio.Lock:
        """Get or create a lock for the given contact_id."""
        if contact_id not in self._locks:
            self._locks[contact_id] = asyncio.Lock()
        return self._locks[contact_id]

    async def on_new_message(
        self,
        contact_id: int,
        message: str,
        session_id: str,
        company_id: int,
        db: AsyncSession,
        on_send_messages: MessageSenderCallback | None = None,
        on_send_private_notes: MessageSenderCallback | None = None,
        dev_mode: bool = False,
    ) -> dict[str, Any] | None:
        """Handle a new incoming message for a contact.

        This is the main entry point called by ChatwootService.

        Flow:
        1. Acquire lock for contact_id
        2. If active task exists: cancel it, recover messages, preserve tool history
        3. Add new message to buffer
        4. Release lock
        5. Wait for buffer delay
        6. Check if this message is the most recent
        7. If yes: move buffer to processing, create task
        8. Return result or None if discarded

        Args:
            contact_id: Chatwoot contact/sender ID
            message: The message content
            session_id: Session identifier for the conversation
            company_id: Company ID for multi-tenancy
            db: Database session
            on_send_messages: Callback to send messages to lead
            dev_mode: If True, save AI messages with role='dev' instead of 'assistant'

        Returns:
            Processing result dict, or None if message was discarded
        """
        # DEV_MODE bypass
        if settings.dev_mode:
            logger.info(
                "[RequestManager] DEV_MODE active, processing immediately "
                "for contact %d",
                contact_id,
            )
            from app.services.chat_processor import process_chat
            return await process_chat(
                session_id=session_id,
                message=message,
                company_id=company_id,
                db=db,
                on_send_messages=on_send_messages,
                on_send_private_notes=on_send_private_notes,
            )

        lock = self._get_lock(contact_id)

        # Phase 1: Cancel active request + add to buffer (under lock)
        async with lock:
            cancel_result = await self._cancel_active_if_exists(contact_id, company_id)

            if cancel_result == "OBJECTION_IN_PROGRESS":
                await self._buffer.add_objection_pending(message, contact_id)
                logger.info(
                    "[RequestManager] Message buffered as objection-pending "
                    "for contact %d",
                    contact_id,
                )
                return None

            if cancel_result:  # tool_history (list)
                existing = self._pending_tool_history.get(contact_id, [])
                existing.extend(cancel_result)
                self._pending_tool_history[contact_id] = existing

            msg_uuid = await self._buffer.add_to_buffer(message, contact_id)

        # Phase 2: Wait for buffer delay (OUTSIDE lock to not block others)
        is_last = await self._buffer.wait_and_check_is_last(msg_uuid, contact_id)

        if not is_last:
            logger.info(
                "[RequestManager] Discarding message for contact %d "
                "(newer message in buffer, uuid=%s)",
                contact_id,
                msg_uuid,
            )
            return None

        # Phase 3: Move to processing + create task (under lock)
        async with lock:
            # Double-check: another coroutine may have already processed
            messages = await self._buffer.move_to_processing(contact_id)

            if not messages:
                logger.warning(
                    "[RequestManager] No messages to process for contact %d",
                    contact_id,
                )
                return None

            # Concatenate all buffered messages
            concatenated = "\n".join(messages)

            # Consume pending tool history (if any)
            prior_tool_history = self._pending_tool_history.pop(contact_id, None)

            logger.info(
                "[RequestManager] Processing %d messages for contact %d "
                "(prior_tool_history=%d): %s",
                len(messages),
                contact_id,
                len(prior_tool_history) if prior_tool_history else 0,
                concatenated[:200],
            )

            # Create callback for ChatHandler to check pending messages mid-loop
            async def _check_pending(
                _cid: int = contact_id,
            ) -> list[str] | None:
                return await self._buffer.get_and_clear_objection_pending(_cid)

            # Create ConversationTurn for the new task
            conversation_turn = ConversationTurn(
                user_message=concatenated,
                prior_tool_history=prior_tool_history,
                pending_checker=_check_pending,
                dev_mode=dev_mode,
            )

            # Create the processing task
            task = asyncio.create_task(
                self._process_task(
                    contact_id=contact_id,
                    session_id=session_id,
                    message=concatenated,
                    company_id=company_id,
                    db=db,
                    on_send_messages=on_send_messages,
                    on_send_private_notes=on_send_private_notes,
                    conversation_turn=conversation_turn,
                ),
                name=f"chat-{contact_id}",
            )

            # Register as active
            self._active_requests[contact_id] = _ActiveRequest(
                task=task,
                conversation_turn=conversation_turn,
            )

        # Wait for the task to complete
        try:
            result = await task
            return result
        except asyncio.CancelledError:
            # Task was cancelled by a newer message
            logger.info(
                "[RequestManager] Task cancelled for contact %d (new message arrived)",
                contact_id,
            )
            return None
        except Exception as e:
            logger.exception(
                "[RequestManager] Task failed for contact %d",
                contact_id,
            )
            send_critical_alert(
                "REQUEST_MANAGER_TASK_FAILED",
                "request_manager.py:on_new_message",
                e,
                contact_id=contact_id,
                company_id=company_id,
            )
            raise

    async def _cancel_active_if_exists(
        self,
        contact_id: int,
        company_id: int | None = None,
    ) -> list[dict[str, Any]] | None | str:
        """Cancel the active request for a contact if one exists.

        Must be called under the contact's lock.

        Args:
            contact_id: Contact ID

        Returns:
            - Completed tool history (list) from the cancelled task
            - "OBJECTION_IN_PROGRESS" if objection generation is active
            - None if no active request
        """
        active = self._active_requests.get(contact_id)
        if active is None:
            return None

        # If generating objection, do NOT cancel
        if active.conversation_turn.objection_generating:
            logger.info(
                "[RequestManager] Contact %d in objection generation - NOT cancelling",
                contact_id,
            )
            return "OBJECTION_IN_PROGRESS"

        # Normal cancellation flow
        self._active_requests.pop(contact_id)

        logger.info(
            "[RequestManager] Cancelling active task for contact %d",
            contact_id,
        )

        # Cancel the task
        active.task.cancel()
        try:
            await active.task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(
                "[RequestManager] Exception during cancel for contact %d: %s",
                contact_id,
                e,
            )
            send_critical_alert(
                "REQUEST_MANAGER_CANCEL_ERROR",
                "request_manager.py:_cancel_active_if_exists",
                e,
                contact_id=contact_id,
                company_id=company_id,
            )

        # Extract completed tool history before discarding the turn
        tool_history = active.conversation_turn.get_completed_tool_history()
        if tool_history:
            logger.info(
                "[RequestManager] Preserved %d tool history messages from "
                "cancelled task for contact %d",
                len(tool_history),
                contact_id,
            )

        # Recover messages from processing back to buffer
        recovered = await self._buffer.recover_processing_to_buffer(contact_id)
        logger.info(
            "[RequestManager] Recovered %d messages from processing to buffer "
            "for contact %d",
            recovered,
            contact_id,
        )

        return tool_history if tool_history else None

    async def _process_task(
        self,
        contact_id: int,
        session_id: str,
        message: str,
        company_id: int,
        db: AsyncSession,
        on_send_messages: MessageSenderCallback | None,
        on_send_private_notes: MessageSenderCallback | None,
        conversation_turn: ConversationTurn,
    ) -> dict[str, Any]:
        """Execute the chat processing task.

        This runs as an asyncio.Task and can be cancelled.

        Args:
            contact_id: Contact ID
            session_id: Session identifier
            message: Concatenated message content
            company_id: Company ID
            db: Database session
            on_send_messages: Message sender callback
            conversation_turn: Shared ConversationTurn for in-memory accumulation

        Returns:
            Processing result dict
        """
        try:
            # 1. Process (defer save so we can interject pending msgs)
            response = await process_chat_in_memory(
                session_id=session_id,
                message=message,
                company_id=company_id,
                db=db,
                conversation_turn=conversation_turn,
                on_send_messages=on_send_messages,
                on_send_private_notes=on_send_private_notes,
                skip_save=True,
            )

            # 2. Check for messages that arrived during objection generation
            pending_messages = await self._buffer.get_and_clear_objection_pending(
                contact_id,
            )

            if pending_messages:
                # Edge case: messages arrived during the LAST OpenAI call
                # (after the ChatHandler loop finished). Save with correct
                # ordering — no second LLM call needed.
                logger.info(
                    "[RequestManager] %d objection-pending for contact %d"
                    " — saving with interjected ordering (no extra LLM call)",
                    len(pending_messages), contact_id,
                )
                try:
                    await asyncio.shield(
                        conversation_turn.deferred_save_with_interjected_users(
                            pending_messages,
                        )
                    )
                except Exception as e:
                    logger.exception(
                        "[RequestManager] Failed to save with interjected users "
                        "for contact %d: %s",
                        contact_id, e,
                    )
                    send_critical_alert(
                        "CONVERSATION_SAVE_FAILED",
                        "request_manager.py:_process_task",
                        e,
                        contact_id=contact_id,
                        company_id=company_id,
                    )
            else:
                # No pending — save normally (deferred)
                try:
                    await asyncio.shield(conversation_turn.deferred_save())
                except Exception as e:
                    logger.exception(
                        "[RequestManager] Failed to save conversation "
                        "for contact %d: %s",
                        contact_id, e,
                    )
                    send_critical_alert(
                        "CONVERSATION_SAVE_FAILED",
                        "request_manager.py:_process_task",
                        e,
                        contact_id=contact_id,
                        company_id=company_id,
                    )

            # Clean up processing key
            await self._buffer.clear_processing(contact_id)

            return response

        finally:
            # Remove from active requests on completion
            self._active_requests.pop(contact_id, None)
            # Clean up any stale pending tool history
            self._pending_tool_history.pop(contact_id, None)
