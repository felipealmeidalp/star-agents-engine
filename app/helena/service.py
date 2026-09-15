"""Lean service for Helena webhook processing (happy path only)."""

import hashlib
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.chatwoot.buffer import MessageBuffer
from app.chatwoot.service import get_request_manager
from app.helena.client import HelenaClient
from app.helena.schemas import HelenaWebhookPayload
from app.models.tables import Company
from app.repositories.customer import CustomerRepository

logger = logging.getLogger(__name__)


def _contact_key(session_id: str) -> int:
    """Derive a stable BigInteger buffer/lock key from the Helena sessionId.

    `on_new_message` and the buffer key on an int; Helena identifies a
    conversation by a UUID string, so we hash it into the first 8 bytes of a
    sha1 digest (fits in BigInteger). Same sessionId → same key, so a burst of
    messages from the same session is grouped by the shared buffer/lock.
    """
    return int.from_bytes(hashlib.sha1(session_id.encode()).digest()[:8], "big")


class HelenaService:
    """Orchestrates Helena webhook processing by delegating to the shared core."""

    def __init__(self, db: AsyncSession) -> None:
        """Initialize service with database session."""
        self.db = db
        self.customer_repo = CustomerRepository(db)
        self.buffer = MessageBuffer()
        self.client = HelenaClient()
        # Shared singleton so locks/buffer state stay consistent across channels.
        self.request_manager = get_request_manager()

    async def process_webhook(
        self,
        payload: HelenaWebhookPayload,
        company: Company,
    ) -> dict[str, Any]:
        """
        Process a Helena MESSAGE_RECEIVED event (happy path only).

        Args:
            payload: Validated webhook payload
            company: Company resolved from the URL token

        Returns:
            Dict with processing result
        """
        session_id = payload.content.sessionId
        text = payload.content.text

        # A message with no text (e.g. attachment only) is ignored in the MVP.
        if not text:
            logger.info(
                "[HelenaService] Empty message ignored for session %s (company %d)",
                session_id,
                company.id,
            )
            return {"status": "ignored", "reason": "no_text", "session_id": session_id}

        # Read ORM attributes into locals BEFORE any await to avoid a SQLAlchemy
        # lazy-load outside the greenlet (mirrors ChatwootService.process_webhook).
        helena_apikey = company.helena_apikey
        phone = payload.content.details.from_ if payload.content.details else None

        contact_id = _contact_key(session_id)

        logger.info(
            "[HelenaService] Processing session=%s, contact_key=%d, company=%d",
            session_id,
            contact_id,
            company.id,
        )

        await self.customer_repo.upsert_api_customer(
            session_id=session_id,
            company_id=company.id,
            agent_id=None,
            sub_agent_id=None,
            fallback_agent_id=company.standard_agent_id,
            fallback_sub_agent_id=company.standard_sub_agent_id,
            custom_information_patch={"phone": phone},
        )

        async def on_send_messages(messages: list[str]) -> None:
            """Send messages to the lead through Helena before tool execution."""
            await self.client.send_messages(session_id, messages, helena_apikey)

        response = await self.request_manager.on_new_message(
            contact_id=contact_id,
            message=text,
            session_id=session_id,
            company_id=company.id,
            db=self.db,
            on_send_messages=on_send_messages,
            on_send_private_notes=None,
            dev_mode=False,
        )

        # None → message discarded (a newer one is buffered); nothing to send.
        if response is None:
            logger.info(
                "[HelenaService] Message discarded for session %s "
                "(newer message in buffer)",
                session_id,
            )
            return {
                "status": "buffered",
                "reason": "newer_message_pending",
                "session_id": session_id,
            }

        messages = response.get("resposta", [])
        if messages:
            await self.client.send_messages(session_id, messages, helena_apikey)

        return {
            "status": "processed",
            "session_id": session_id,
            "messages_sent": len(messages),
        }
