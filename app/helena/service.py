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
from app.repositories.chat_history import ChatHistoryRepository
from app.repositories.company import CompanyRepository
from app.repositories.customer import CustomerRepository
from app.services.openai import OpenAIService
from app.utils.alerter import send_critical_alert
from app.utils.attachments import (
    AUDIO_EMPTY_MSG,
    AUDIO_FAILURE_MSG,
    EmptyTranscriptError,
    resolve_attachment_message,
)

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
        self.chat_history_repo = ChatHistoryRepository(db)
        self.company_repo = CompanyRepository(db)
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
        attachment_url = payload.content.attachment_url
        attachment_type = payload.content.attachment_type

        # No text AND no attachment → nothing to process (MVP behaviour preserved).
        if not text and not attachment_url:
            logger.info(
                "[HelenaService] Empty message ignored for session %s (company %d)",
                session_id,
                company.id,
            )
            return {"status": "ignored", "reason": "no_text", "session_id": session_id}

        # Read ORM attributes into locals BEFORE any await to avoid a SQLAlchemy
        # lazy-load outside the greenlet (mirrors ChatwootService.process_webhook).
        helena_apikey = company.helena_apikey
        company_id = company.id
        # Optional lead phone, kept only for the customer record (custom_information).
        # It is NOT the send target: replies route by sessionId via
        # POST /v1/session/{id}/message, the only contract that works on
        # non-WhatsApp channels (Instagram/Facebook), where details.from is absent.
        phone = payload.content.details.from_ if payload.content.details else None

        # No Bearer configured → every send would 401; abort before touching the AI.
        if not helena_apikey:
            logger.error(
                "[HelenaService] Company %d has no helena_apikey — cannot send reply",
                company.id,
            )
            return {"status": "ignored", "reason": "no_apikey", "session_id": session_id}

        apikey: str = helena_apikey  # narrowed non-None; Bearer for session send

        # Resolve an attachment (audio → Whisper transcript; image/video/file →
        # descriptive phrase) into the text the core receives. Text has precedence.
        # Transcription is injected so the shared helper stays free of OpenAI/key
        # wiring; the failure path (below) is channel-specific (HelenaClient + alert).
        async def transcribe(url: str) -> str:
            api_key = await self.company_repo.get_openai_api_key(company_id)
            return await OpenAIService(api_key=api_key).transcribe_audio(url)

        try:
            message, should_continue = await resolve_attachment_message(
                text, attachment_type, attachment_url, transcribe
            )
        except Exception as e:
            # Audio transcription failed (exception or empty transcript): tell the
            # lead and fire the same critical alert the Chatwoot channel uses. Empty
            # transcript vs hard failure get the two distinct Chatwoot strings.
            logger.error(
                "[HelenaService] Audio transcription failed for session %s "
                "(company %d): %s",
                session_id,
                company_id,
                e,
            )
            error_msg = (
                AUDIO_EMPTY_MSG if isinstance(e, EmptyTranscriptError) else AUDIO_FAILURE_MSG
            )
            await self.client.send_text(session_id, error_msg, apikey)
            send_critical_alert(
                "AUDIO_TRANSCRIPTION_FAILED",
                "helena/service.py:process_webhook",
                e,
                contact_id=_contact_key(session_id),
                company_id=company_id,
            )
            return {
                "status": "ignored",
                "reason": "transcription_failed",
                "session_id": session_id,
            }

        if not should_continue:
            # Reachable only if a payload carries an attachment_url that resolves to
            # a skip; the up-front guard already handled the no-text-no-attachment
            # case. Preserve the same shape as today.
            return {"status": "ignored", "reason": "no_text", "session_id": session_id}

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

        # Gate de IA: se a conversa foi escalada para um humano (status=False), a
        # IA fica calada. A mensagem do lead é gravada no histórico (o atendente vê
        # o contexto) mas nenhuma chamada à IA acontece — mesma semântica do
        # Chatwoot (passo "1.5"). status None/True → IA ativa, segue o fluxo.
        # Helena não tem follow-up, então _update_follow_up_and_schedule não é replicado.
        status = await self.customer_repo.get_status(session_id, company.id)
        if status is False:
            logger.info(
                "[HelenaService] AI deactivated for session %s (status=False), "
                "saving user message",
                session_id,
            )
            await self.chat_history_repo.insert_user_message(
                session_id=session_id,
                message=message,
                company_id=company.id,
            )
            return {"status": "ai_deactivated", "session_id": session_id}

        async def on_send_messages(messages: list[str]) -> None:
            """Send messages to the lead through Helena before tool execution."""
            await self.client.send_messages(session_id, messages, apikey)

        response = await self.request_manager.on_new_message(
            contact_id=contact_id,
            message=message,
            session_id=session_id,
            company_id=company.id,
            db=self.db,
            on_send_messages=on_send_messages,
            on_send_private_notes=None,
            dev_mode=False,
            channel="helena",
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
            await self.client.send_messages(session_id, messages, apikey)

        return {
            "status": "processed",
            "session_id": session_id,
            "messages_sent": len(messages),
        }
