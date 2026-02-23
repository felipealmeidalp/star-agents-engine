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
from app.models.tables import ChatHistory
from app.repositories.company import CompanyRepository
from app.repositories.customer import CustomerRepository
from app.utils.alerter import send_critical_alert

logger = logging.getLogger(__name__)

router = APIRouter()

# Configurações de trigger por (cw_account_id, inbox_id).
# Cada entrada mapeia trigger messages para seed messages (role, content) que serão
# inseridas no chat_history para dar contexto à IA quando o lead responder.
INBOX_TRIGGER_CONFIGS: dict[tuple[int, int], dict[str, list[tuple[str, str]]]] = {
    # cw_account_id=1 (Company 2), Inbox 1
    (1, 1): {
        "Me conta mais sobre você? Quero saber em que momento você está e onde quer chegar 🚀": [
            (
                "assistant",
                "Oieee! Acabei de ver minhas notificações, vim pra desejar as boas vindas ao meu perfil. Prazer ter você por aqui 🤩\n\nEspero agregar em sua trajetória 🚀🚀🚀\nVocê tem interesse ou já faz parte do mundo das palestras?",
            ),
            ("user", "Siim!"),
            (
                "assistant",
                "Me conta mais sobre você? Quero saber em que momento você está e onde quer chegar 🚀",
            ),
        ],
    },
}


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


async def _add_contact_background(
    token: str,
    inbox_id: int,
    contact_id: int,
    conversation_id: int,
    seed_messages: list[tuple[str, str]],
) -> None:
    """
    Background task to auto-add a contact, create customer and seed chat history.

    Args:
        token: Chatwoot webhook token for company lookup
        inbox_id: Inbox ID
        contact_id: Contact ID to add
        conversation_id: Chatwoot conversation ID (for customer creation)
        seed_messages: List of (role, content) tuples to insert as initial chat history
    """
    async with AsyncSessionLocal() as db:
        try:
            company_repo = CompanyRepository(db)
            company = await company_repo.get_by_cw_token(token)

            if not company:
                logger.error(
                    f"[ChatwootWebhook] _add_contact_background: "
                    f"Company not found for token {token}"
                )
                return

            # 1. Add contact to allowed_contacts list
            added = await company_repo.add_contact_to_allowed_list(
                company_id=company.id,
                inbox_id=inbox_id,
                contact_id=contact_id,
            )
            logger.info(
                f"[ChatwootWebhook] Auto-add contact result: "
                f"company={company.id}, inbox={inbox_id}, "
                f"contact={contact_id}, added={added}"
            )

            # 2. Create customer if doesn't exist
            customer_repo = CustomerRepository(db)
            existing = await customer_repo.get_by_cw_contact_id(contact_id)

            if existing:
                logger.info(
                    f"[ChatwootWebhook] Customer already exists for contact {contact_id}, "
                    f"skipping creation and seed"
                )
                return

            if not company.standard_agent_id or not company.standard_sub_agent_id:
                logger.error(
                    f"[ChatwootWebhook] Company {company.id} missing "
                    f"standard_agent_id or standard_sub_agent_id"
                )
                return

            customer = await customer_repo.create_from_chatwoot(
                cw_contact_id=contact_id,
                cw_conversation_id=conversation_id,
                company_id=company.id,
                agent_id=company.standard_agent_id,
                sub_agent_id=company.standard_sub_agent_id,
            )
            logger.info(
                f"[ChatwootWebhook] Created customer {customer.id} "
                f"for contact {contact_id}, company {company.id}"
            )

            # 3. Seed chat history with initial messages
            session_id = str(contact_id)
            for role, content in seed_messages:
                record = ChatHistory(
                    sessionId=session_id,
                    role=role,
                    content=content,
                    agent_id=company.standard_agent_id,
                    sub_agent_id=company.standard_sub_agent_id,
                    company_id=company.id,
                )
                db.add(record)
            await db.commit()

            logger.info(
                f"[ChatwootWebhook] Seeded {len(seed_messages)} messages "
                f"for session {session_id}, company {company.id}"
            )

        except Exception as e:
            logger.exception(
                f"[ChatwootWebhook] Error in _add_contact_background: {e}"
            )


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
    sender_id = payload.sender.id if payload.sender else None
    logger.info(
        f"[ChatwootWebhook] Background processing started for contact {sender_id}"
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
            contact_id = sender_id

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
            send_critical_alert(
                "MAX_ITERATIONS_EXCEEDED",
                "chatwoot.py:process_webhook_background",
                e,
                contact_id=sender_id,
            )
        except OpenAIAuthenticationError as e:
            logger.error(f"[ChatwootWebhook] OpenAI Auth in background: {e}")
            send_critical_alert(
                "OPENAI_AUTH_ERROR",
                "chatwoot.py:process_webhook_background",
                e,
                contact_id=sender_id,
            )
        except OpenAIRateLimitError as e:
            logger.error(f"[ChatwootWebhook] OpenAI RateLimit in background: {e}")
            send_critical_alert(
                "OPENAI_RATE_LIMIT",
                "chatwoot.py:process_webhook_background",
                e,
                contact_id=sender_id,
            )
        except OpenAITimeoutError as e:
            logger.error(f"[ChatwootWebhook] OpenAI Timeout in background: {e}")
            send_critical_alert(
                "OPENAI_TIMEOUT",
                "chatwoot.py:process_webhook_background",
                e,
                contact_id=sender_id,
            )
        except OpenAIError as e:
            logger.error(f"[ChatwootWebhook] OpenAI Error in background: {e}")
            send_critical_alert(
                "OPENAI_GENERIC_ERROR",
                "chatwoot.py:process_webhook_background",
                e,
                contact_id=sender_id,
            )
        except ValueError as e:
            logger.error(f"[ChatwootWebhook] ValueError in background: {e}")
            send_critical_alert(
                "WEBHOOK_VALUE_ERROR",
                "chatwoot.py:process_webhook_background",
                e,
                contact_id=sender_id,
            )
        except Exception as e:
            logger.exception(f"[ChatwootWebhook] Unexpected error in background: {e}")
            send_critical_alert(
                "WEBHOOK_UNHANDLED_ERROR",
                "chatwoot.py:process_webhook_background",
                e,
                contact_id=sender_id,
            )


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

    sender_id = payload.sender.id if payload.sender else None
    logger.info(
        f"[ChatwootWebhook] Received: event={payload.event}, "
        f"message_type={payload.message_type}, sender_id={sender_id}"
    )

    # Handle outgoing messages: check for trigger messages to auto-add contacts
    if payload.message_type == "outgoing":
        content = (payload.content or "").strip()
        inbox_id = payload.inbox.id if payload.inbox else payload.conversation.inbox_id
        trigger_config = INBOX_TRIGGER_CONFIGS.get((payload.account.id, inbox_id))
        seed_messages = trigger_config.get(content) if trigger_config else None
        if seed_messages is not None:
            contact_inbox = payload.conversation.contact_inbox
            if contact_inbox:
                if inbox_id is not None:
                    background_tasks.add_task(
                        _add_contact_background,
                        token=token,
                        inbox_id=inbox_id,
                        contact_id=contact_inbox.contact_id,
                        conversation_id=payload.conversation.id,
                        seed_messages=seed_messages,
                    )
                    logger.info(
                        f"[ChatwootWebhook] Trigger message detected, "
                        f"auto-adding contact {contact_inbox.contact_id} "
                        f"to inbox {inbox_id}"
                    )
                    return {"status": "contact_added"}
                else:
                    logger.warning(
                        "[ChatwootWebhook] Trigger message but no inbox_id found"
                    )
            else:
                logger.warning(
                    "[ChatwootWebhook] Trigger message but no contact_inbox in conversation"
                )
        logger.info("[ChatwootWebhook] Ignoring outgoing message")
        return {"status": "ignored", "reason": "outgoing_message"}

    # Filter 1: Ignore EvolutionAPI connection updates (sender.id == 1)
    if payload.sender and payload.sender.id == 1:
        logger.info("[ChatwootWebhook] Ignoring EvolutionAPI update (sender.id=1)")
        return {"status": "ignored", "reason": "evolution_api_update"}

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
