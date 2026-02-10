"""Chatwoot webhook endpoint."""

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from app.chatwoot.schemas import ChatwootWebhookPayload
from app.chatwoot.service import ChatwootService
from app.db.database import AsyncSessionLocal
from app.exceptions import (
    MaxIterationsExceededError,
    OpenAIAuthenticationError,
    OpenAIError,
    OpenAIRateLimitError,
    OpenAITimeoutError,
)
from app.repositories.company import CompanyRepository

logger = logging.getLogger(__name__)

router = APIRouter()


def _is_entry_allowed(
    allowed_entries: dict[str, Any] | None,
    inbox_id: int,
    contact_id: int,
) -> bool:
    """
    Verifica se inbox+contact e permitido.

    Regras:
    - None ou {"allowed_inboxes": []} → tudo permitido
    - Se inbox nao esta na lista → bloqueado
    - Se inbox esta na lista com allowed_contacts: [] → todos contatos permitidos
    - Se inbox esta na lista com allowed_contacts: [1,2] → apenas esses contatos
    """
    if not allowed_entries:
        return True

    allowed_inboxes = allowed_entries.get("allowed_inboxes", [])
    if not allowed_inboxes:
        return True

    for inbox_config in allowed_inboxes:
        if inbox_config.get("id") == inbox_id:
            allowed_contacts = inbox_config.get("allowed_contacts", [])
            if not allowed_contacts:
                return True  # inbox permite todos contatos
            return contact_id in allowed_contacts

    return False  # inbox nao encontrada = bloqueado


@router.post("/chatwoot/{token}/debug")
async def chatwoot_webhook_debug(
    token: str,
    request: Request,
) -> dict:
    """
    Debug endpoint to see raw payload before Pydantic validation.

    Use this to debug 422 errors - shows exactly what Chatwoot is sending.
    """
    body = await request.json()
    logger.info(f"[ChatwootWebhook DEBUG] Token: {token}")
    logger.info(f"[ChatwootWebhook DEBUG] Raw payload: {body}")
    return {"status": "debug", "payload": body}


async def process_webhook_background(
    payload: ChatwootWebhookPayload,
    token: str,
) -> None:
    """
    Process Chatwoot webhook in background.

    Creates its own database session since the request session
    is closed after immediate response.

    All DB operations happen here (company lookup, allowed_entries validation, etc.)

    Args:
        payload: Validated webhook payload
        token: Chatwoot webhook token for company lookup
    """
    logger.info(
        f"[ChatwootWebhook] Background processing started for contact {payload.sender.id}"
    )

    async with AsyncSessionLocal() as db:
        try:
            # 1. Fetch company by token
            company_repo = CompanyRepository(db)
            company = await company_repo.get_by_cw_token(token)

            if not company:
                logger.error(
                    f"[ChatwootWebhook] Company not found for token {token}"
                )
                return

            logger.info(f"[ChatwootWebhook] Found company: {company.id} - {company.name}")

            # 2. Validate allowed_entries (inbox + contact)
            inbox_id = payload.inbox.id if payload.inbox else payload.conversation.inbox_id
            contact_id = payload.sender.id

            logger.info(
                f"[ChatwootWebhook] Validating entry: inbox={inbox_id}, contact={contact_id}, "
                f"allowed_contacts={company.allowed_contacts}"
            )

            if not _is_entry_allowed(company.allowed_contacts, inbox_id, contact_id):
                logger.info(
                    f"[ChatwootWebhook] BLOCKED: inbox={inbox_id}, contact={contact_id} "
                    f"not in allowed_entries for company {company.id}"
                )
                return

            # 3. Process webhook
            service = ChatwootService(db)
            result = await service.process_webhook(payload, company)

            logger.info(f"[ChatwootWebhook] Background processing complete: {result}")

        except MaxIterationsExceededError as e:
            logger.error(f"[ChatwootWebhook] MaxIterations in background: {e}")
        except OpenAIAuthenticationError as e:
            logger.error(f"[ChatwootWebhook] OpenAI Auth in background: {e}")
        except OpenAIRateLimitError as e:
            logger.error(f"[ChatwootWebhook] OpenAI RateLimit in background: {e}")
        except OpenAITimeoutError as e:
            logger.error(f"[ChatwootWebhook] OpenAI Timeout in background: {e}")
        except OpenAIError as e:
            logger.error(f"[ChatwootWebhook] OpenAI Error in background: {e}")
        except ValueError as e:
            logger.error(f"[ChatwootWebhook] ValueError in background: {e}")
        except Exception as e:
            logger.exception(f"[ChatwootWebhook] Unexpected error in background: {e}")


@router.post("/chatwoot/{token}")
async def chatwoot_webhook(
    token: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Handle Chatwoot webhook events.

    Returns immediately with {"status": "received"} and processes
    the message in a background task. NO DATABASE QUERIES HERE.

    Filters out (without DB):
    - Messages from sender.id == 1 (EvolutionAPI connection updates)
    - Outgoing messages (agent responses)
    - Non message_created events
    - Non incoming messages

    All DB operations (company lookup, allowed_entries check) happen in background task.

    Args:
        token: Webhook token for company identification
        request: Raw request to extract and validate payload
        background_tasks: FastAPI background tasks

    Returns:
        Dict with received status (immediate)
    """
    # Parse and validate payload manually for better error logging
    from pydantic import ValidationError

    try:
        body = await request.json()
    except Exception as e:
        logger.error(f"[ChatwootWebhook] Failed to parse JSON body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Log raw payload for debugging
    logger.debug(f"[ChatwootWebhook] Raw payload: {body}")

    try:
        payload = ChatwootWebhookPayload.model_validate(body)
    except ValidationError as e:
        logger.error(f"[ChatwootWebhook] Validation error: {e}")
        logger.error(f"[ChatwootWebhook] Received payload: {body}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=e.errors(),
        )

    logger.info(
        f"[ChatwootWebhook] Received: event={payload.event}, "
        f"message_type={payload.message_type}, sender_id={payload.sender.id}"
    )

    # Filter 1: Ignore EvolutionAPI connection updates (sender.id == 1)
    if payload.sender.id == 1:
        logger.info("[ChatwootWebhook] Ignoring EvolutionAPI update (sender.id=1)")
        return {"status": "ignored", "reason": "evolution_api_update"}

    # Filter 2: Ignore outgoing messages (agent responses)
    if payload.message_type == "outgoing":
        logger.info("[ChatwootWebhook] Ignoring outgoing message")
        return {"status": "ignored", "reason": "outgoing_message"}

    # Filter 3: Only process message_created events
    if payload.event != "message_created":
        logger.info(f"[ChatwootWebhook] Ignoring event: {payload.event}")
        return {"status": "ignored", "reason": f"event_{payload.event}"}

    # Filter 4: Only process incoming messages
    if payload.message_type != "incoming":
        logger.info(f"[ChatwootWebhook] Ignoring message_type: {payload.message_type}")
        return {"status": "ignored", "reason": f"message_type_{payload.message_type}"}

    # Add processing to background tasks (non-blocking)
    # All DB operations happen in background task
    background_tasks.add_task(
        process_webhook_background,
        payload=payload,
        token=token,
    )

    # Return immediately - zero DB queries
    return {"status": "received"}
