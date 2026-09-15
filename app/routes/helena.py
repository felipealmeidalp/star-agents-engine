"""Helena webhook endpoint."""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.db.database import AsyncSessionLocal
from app.helena.schemas import HelenaWebhookPayload
from app.helena.service import HelenaService
from app.repositories.company import CompanyRepository
from app.routes.chatwoot import _is_entry_allowed
from app.utils.alerter import send_critical_alert

logger = logging.getLogger(__name__)

router = APIRouter()


async def process_webhook_background(
    payload: HelenaWebhookPayload,
    token: str,
) -> None:
    """
    Process a Helena webhook in the background.

    Opens its own DB session (the request session is closed after the immediate
    response). Resolves the company by URL token; if none matches, aborts
    silently (log only). Any error logs and fires a critical alert — no retry.

    Args:
        payload: Validated webhook payload
        token: Helena webhook token for company lookup
    """
    session_id = payload.content.sessionId
    try:
        async with AsyncSessionLocal() as db:
            company = await CompanyRepository(db).get_by_helena_token(token)
            if company is None:
                logger.warning(
                    "[HelenaWebhook] Company not found for token, aborting silently"
                )
                return

            # Gate by allowed_inbox (channel). The n8n pre-processor injects the
            # numeric channel into content.channel; we reuse the Chatwoot allowlist
            # (same column, same rules): no config → all allowed; channel not in
            # the list → blocked; channel listed with empty contacts → all allowed.
            # Helena has no int contact_id, so contact-level filtering degrades to
            # channel-level; contact 0 only matters if a channel lists contacts.
            channel = payload.content.channel
            if channel is not None and not _is_entry_allowed(
                company.allowed_contacts, channel, 0
            ):
                logger.info(
                    "[HelenaWebhook] BLOCKED: channel %s not allowed for company %d",
                    channel,
                    company.id,
                )
                return

            result = await HelenaService(db).process_webhook(payload, company)
            logger.info("[HelenaWebhook] Background processing complete: %s", result)
    except Exception as e:
        logger.exception("[HelenaWebhook] Unexpected error in background: %s", e)
        send_critical_alert(
            "HELENA_WEBHOOK_UNHANDLED_ERROR",
            "helena.py:process_webhook_background",
            e,
            extra=f"session={session_id}",
        )


@router.post("/helena/{token}")
async def helena_webhook(
    token: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Handle Helena webhook events.

    Returns immediately with {"status": "received"} and processes the message in
    a background task. No DB queries in the sync handler.

    Args:
        token: Webhook token for company identification
        request: Raw request to extract and validate the payload
        background_tasks: FastAPI background tasks

    Returns:
        Dict with received status (immediate)
    """
    try:
        body = await request.json()
    except Exception as e:
        logger.error("[HelenaWebhook] Failed to parse JSON body: %s", e)
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    try:
        payload = HelenaWebhookPayload.model_validate(body)
    except Exception as e:
        # Tolerant boundary: a payload we cannot parse is acknowledged, not retried.
        logger.warning("[HelenaWebhook] Ignoring unparseable payload: %s", e)
        return {"status": "ignored", "reason": "invalid_payload"}

    logger.info("[HelenaWebhook] Received: eventType=%s", payload.eventType)

    if payload.eventType != "MESSAGE_RECEIVED":
        logger.info("[HelenaWebhook] Ignoring event: %s", payload.eventType)
        return {"status": "ignored", "reason": f"event_{payload.eventType}"}

    # ponytail: o MVP processa todo MESSAGE_RECEIVED sem filtrar por content.direction
    # (o payload de teste veio com direction="FROM_HUB" numa msg do próprio usuário).
    # Se aparecerem eventos espúrios (a IA respondendo a si mesma), reintroduzir um
    # filtro de direção assim que capturarmos o payload de uma msg genuína de lead.
    background_tasks.add_task(
        process_webhook_background,
        payload=payload,
        token=token,
    )

    return {"status": "received"}
