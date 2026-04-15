"""Meta (WhatsApp Business) webhook proxy endpoint.

Returns 200 to Meta immediately and processes the forward to Chatwoot
in a background task, preventing timeout-related retries and duplicates.
"""

import logging

import httpx
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import Response

from app.config import settings
from app.db.database import AsyncSessionLocal
from app.repositories.company import CompanyRepository
from app.repositories.imbox import ImboxRepository
from app.services.meta_webhook import MetaDeduplicator, MetaWebhookCache, verify_signature
from app.utils.alerter import send_critical_alert

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level singletons (reuse Redis connections across requests)
_deduplicator = MetaDeduplicator()
_cache = MetaWebhookCache()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_message_ids(body: dict) -> list[str]:
    """
    Extract all unique event IDs from a Meta webhook payload.

    Iterates entry[].changes[].value.messages[].id and
    entry[].changes[].value.statuses[].id.
    """
    ids: list[str] = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                if mid := msg.get("id"):
                    ids.append(mid)
            for st in value.get("statuses", []):
                if sid := st.get("id"):
                    ids.append(sid)
    return ids


async def _forward_to_chatwoot_background(raw_body: bytes, whatsapp: str) -> None:
    """
    Background task: resolve Chatwoot URL and forward the raw payload.

    Uses MetaWebhookCache to avoid repeated DB lookups for the same number.
    Error handling follows the same pattern as chatwoot.py background tasks.
    """
    try:
        # 1. Try cache first
        cached = await _cache.get(whatsapp)

        if cached:
            company_id, cw_base_url = cached
        else:
            # 2. DB lookup
            async with AsyncSessionLocal() as db:
                imbox_repo = ImboxRepository(db)
                imbox = await imbox_repo.get_by_whatsapp(whatsapp)

                if not imbox or not imbox.company_id:
                    logger.warning(
                        f"[MetaWebhook] No imbox found for whatsapp={whatsapp}"
                    )
                    return

                company_repo = CompanyRepository(db)
                company = await company_repo.get_by_id(imbox.company_id)

                if not company or not company.cw_base_url:
                    logger.warning(
                        f"[MetaWebhook] No company/cw_base_url for "
                        f"company_id={imbox.company_id}"
                    )
                    return

                company_id = company.id
                cw_base_url = company.cw_base_url

            # 3. Populate cache for next time
            await _cache.set(whatsapp, company_id, cw_base_url)

        # 4. Forward to Chatwoot
        chatwoot_url = f"{cw_base_url}/webhooks/whatsapp/{whatsapp}"
        logger.info(f"[MetaWebhook] Forwarding to {chatwoot_url}")

        async with httpx.AsyncClient(
            timeout=float(settings.meta_forward_timeout),
        ) as client:
            response = await client.post(
                chatwoot_url,
                content=raw_body,
                headers={"Content-Type": "application/json"},
            )

        logger.info(
            f"[MetaWebhook] Chatwoot responded with status={response.status_code}"
        )

    except httpx.TimeoutException as e:
        logger.error(
            f"[MetaWebhook] Timeout forwarding to Chatwoot for whatsapp={whatsapp}"
        )
        send_critical_alert(
            "META_FORWARD_TIMEOUT",
            "meta.py:_forward_to_chatwoot_background",
            str(e) if str(e) else f"Timeout forwarding for whatsapp={whatsapp}",
            extra=f"whatsapp={whatsapp}",
        )
    except httpx.HTTPError as e:
        logger.error(
            f"[MetaWebhook] HTTP error forwarding to Chatwoot: {e}"
        )
        send_critical_alert(
            "META_FORWARD_HTTP_ERROR",
            "meta.py:_forward_to_chatwoot_background",
            e,
            extra=f"whatsapp={whatsapp}",
        )
    except Exception as e:
        logger.exception(f"[MetaWebhook] Unexpected error in background: {e}")
        send_critical_alert(
            "META_FORWARD_UNHANDLED_ERROR",
            "meta.py:_forward_to_chatwoot_background",
            e,
            extra=f"whatsapp={whatsapp}",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/meta")
async def meta_webhook_verify(request: Request) -> Response:
    """
    Meta webhook verification (hub.challenge handshake).

    Validates hub.verify_token before returning the challenge.
    Returns 403 if verify_token is configured and doesn't match.
    """
    mode = request.query_params.get("hub.mode", "")
    token = request.query_params.get("hub.verify_token", "")
    challenge = request.query_params.get("hub.challenge", "")

    logger.info(f"[MetaWebhook] Verification request, mode={mode}, challenge={challenge}")

    # Validate verify_token if configured
    if settings.meta_verify_token and token != settings.meta_verify_token:
        logger.warning("[MetaWebhook] Invalid verify_token")
        return Response(content="Forbidden", status_code=403)

    return Response(content=challenge, media_type="text/plain")


@router.post("/meta")
async def meta_webhook_proxy(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response:
    """
    Receive Meta webhook events and return 200 immediately.

    Validates signature, deduplicates by message ID, then processes
    the forward to Chatwoot in a background task.

    Always returns 200 to Meta (except 403 on signature failure)
    to prevent retries and exponential backoff.
    """
    raw_body = await request.body()

    # 1. Verify signature if app_secret is configured
    if settings.meta_app_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not verify_signature(raw_body, signature, settings.meta_app_secret):
            logger.warning("[MetaWebhook] Invalid signature")
            return Response(content="Forbidden", status_code=403)

    # 2. Parse JSON
    try:
        body = await request.json()
    except Exception as e:
        logger.warning(f"[MetaWebhook] Invalid JSON payload: {e}")
        send_critical_alert(
            "META_INVALID_JSON_PAYLOAD",
            "meta.py:meta_webhook",
            e,
        )
        return Response(status_code=200)

    # 3. Extract whatsapp number
    try:
        whatsapp = body["entry"][0]["changes"][0]["value"]["metadata"]["display_phone_number"]
    except (KeyError, IndexError, TypeError) as e:
        logger.warning(f"[MetaWebhook] Could not extract phone number: {e}")
        send_critical_alert(
            "META_PHONE_EXTRACTION_FAILED",
            "meta.py:meta_webhook",
            e,
        )
        # Return 200 anyway — don't make Meta retry for malformed payloads
        return Response(status_code=200)

    logger.info(f"[MetaWebhook] Received event for whatsapp={whatsapp}")

    # 4. Deduplicate by message/status IDs
    event_ids = _extract_message_ids(body)
    for event_id in event_ids:
        if await _deduplicator.is_duplicate(event_id):
            logger.info(f"[MetaWebhook] Skipping duplicate event_id={event_id}")
            return Response(status_code=200)

    # 5. Schedule background forward and return 200 immediately
    background_tasks.add_task(_forward_to_chatwoot_background, raw_body, whatsapp)

    return Response(status_code=200)
