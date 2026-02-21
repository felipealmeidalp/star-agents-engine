"""Meta (WhatsApp Business) webhook proxy endpoint."""

import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.db.database import AsyncSessionLocal
from app.repositories.company import CompanyRepository
from app.repositories.imbox import ImboxRepository

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/meta")
async def meta_webhook_verify(request: Request) -> Response:
    """
    Meta webhook verification (hub.challenge handshake).

    Meta sends a GET with hub.challenge query param during webhook registration.
    Must return the challenge value as plain text.
    """
    challenge = request.query_params.get("hub.challenge", "")
    logger.info(f"[MetaWebhook] Verification request, challenge={challenge}")
    return Response(content=challenge, media_type="text/plain")


@router.post("/meta")
async def meta_webhook_proxy(request: Request) -> dict:
    """
    Proxy Meta webhook events to Chatwoot.

    Extracts the WhatsApp number from the Meta payload at
    body.entry[0].changes[0].value.metadata.display_phone_number,
    looks up the corresponding Chatwoot instance via imboxes + companies,
    and forwards the raw payload.

    Returns:
        Dict with forwarding status and Chatwoot response code
    """
    raw_body = await request.body()

    # Extract whatsapp number from Meta payload
    try:
        body = await request.json()
        whatsapp = body["entry"][0]["changes"][0]["value"]["metadata"]["display_phone_number"]
    except (KeyError, IndexError, TypeError) as e:
        logger.warning(f"[MetaWebhook] Could not extract phone number from payload: {e}")
        return {"status": "error", "detail": "display_phone_number not found in payload"}

    logger.info(f"[MetaWebhook] Received event for whatsapp={whatsapp}")

    async with AsyncSessionLocal() as db:
        # 1. Find imbox by whatsapp number
        imbox_repo = ImboxRepository(db)
        imbox = await imbox_repo.get_by_whatsapp(whatsapp)

        if not imbox or not imbox.company_id:
            logger.warning(f"[MetaWebhook] No imbox found for whatsapp={whatsapp}")
            return {"status": "error", "detail": "imbox not found"}

        # 2. Find company to get Chatwoot base URL
        company_repo = CompanyRepository(db)
        company = await company_repo.get_by_id(imbox.company_id)

        if not company or not company.cw_base_url:
            logger.warning(
                f"[MetaWebhook] No company/cw_base_url for company_id={imbox.company_id}"
            )
            return {"status": "error", "detail": "company or cw_base_url not found"}

    # 3. Forward to Chatwoot
    chatwoot_url = f"{company.cw_base_url}/webhooks/whatsapp/{whatsapp}"
    logger.info(f"[MetaWebhook] Forwarding to {chatwoot_url}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            chatwoot_url,
            content=raw_body,
            headers={"Content-Type": "application/json"},
        )

    logger.info(f"[MetaWebhook] Chatwoot responded with status={response.status_code}")
    return {"status": "forwarded", "chatwoot_status_code": response.status_code}
