"""Instagram comments webhook endpoint."""

import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import Response

from app.config import settings
from app.db.database import AsyncSessionLocal
from app.repositories.company import CompanyRepository
from app.services.instagram_welcome import handle_comment, parse_comments
from app.services.meta_webhook import verify_signature
from app.utils.alerter import send_critical_alert

logger = logging.getLogger(__name__)

router = APIRouter()


async def process_webhook_background(body: dict[str, Any]) -> None:
    """Resolve the Company of each comment and hand it off, with its own DB session."""
    try:
        pairs = parse_comments(body)
        if not pairs:
            logger.info("[InstagramWebhook] Event has no comments, ignoring")
            return

        async with AsyncSessionLocal() as db:
            for account_id, comment in pairs:
                try:
                    if comment.parent_id:
                        logger.info(
                            "[InstagramWebhook] Skipping reply to another comment: comment=%s",
                            comment.comment_id,
                        )
                        continue

                    company = await CompanyRepository(db).get_by_instagram_account_id(account_id)
                    if company is None:
                        logger.warning(
                            "[InstagramWebhook] No Company for instagram account %s, discarding",
                            account_id,
                        )
                        continue

                    await handle_comment(db, company, comment)
                except Exception as e:
                    logger.exception("[InstagramWebhook] Unexpected error on comment: %s", e)
                    send_critical_alert(
                        "INSTAGRAM_WEBHOOK_UNHANDLED_ERROR",
                        "instagram.py:process_webhook_background",
                        e,
                        extra=f"account={account_id} comment={comment.comment_id}",
                    )
                    # a failed transaction would poison the next comment's queries
                    await db.rollback()
    except Exception as e:
        logger.exception("[InstagramWebhook] Unexpected error in background: %s", e)
        send_critical_alert(
            "INSTAGRAM_WEBHOOK_UNHANDLED_ERROR",
            "instagram.py:process_webhook_background",
            e,
        )


@router.get("/instagram")
async def instagram_webhook_verify(request: Request) -> Response:
    """Meta webhook verification (hub.challenge handshake)."""
    token = request.query_params.get("hub.verify_token", "")
    challenge = request.query_params.get("hub.challenge", "")

    # a missing param and an unset setting are both "", so the equality alone would pass
    if not settings.instagram_verify_token or token != settings.instagram_verify_token:
        logger.warning("[InstagramWebhook] Invalid verify_token")
        return Response(content="Forbidden", status_code=403)

    return Response(content=challenge, media_type="text/plain")


@router.post("/instagram", response_model=None)
async def instagram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> Response | dict[str, str]:
    """Receive Instagram comment events; the signature is mandatory."""
    raw_body = await request.body()

    # verify_signature with an empty key still yields an HMAC anyone can compute
    if not settings.instagram_app_secret:
        logger.error("[InstagramWebhook] INSTAGRAM_APP_SECRET not configured, rejecting event")
        return Response(content="Forbidden", status_code=403)

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_signature(raw_body, signature, settings.instagram_app_secret):
        logger.warning("[InstagramWebhook] Invalid signature")
        return Response(content="Forbidden", status_code=403)

    try:
        body = json.loads(raw_body)
    except ValueError:
        body = None
    if not isinstance(body, dict):
        # signed but unparseable: acknowledge, a retry from Meta would not fix it
        logger.warning("[InstagramWebhook] Ignoring signed body that is not a JSON object")
        return {"status": "ignored"}

    background_tasks.add_task(process_webhook_background, body)
    return {"status": "received"}
