"""Weekly renewal of each Company's Instagram access token, which expires after 60 days."""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings
from app.db.database import AsyncSessionLocal
from app.repositories.company import CompanyRepository
from app.services.instagram_client import InstagramClient
from app.services.product_sync.scheduler import seconds_until_next_saturday
from app.utils.alerter import send_critical_alert

logger = logging.getLogger(__name__)


async def renew_instagram_tokens(repo: CompanyRepository) -> None:
    """Renew and save the token of every Company with Instagram configured."""
    client = InstagramClient()
    # plain values up front: the rollback of a failed save expires every loaded Company
    tokens = [
        (company.id, company.instagram_access_token)
        for company in await repo.list_with_instagram()
        if company.instagram_access_token
    ]
    for company_id, token in tokens:
        try:
            new_token = await client.refresh_access_token(token)
            await repo.update_instagram_access_token(company_id, new_token)
        except Exception as err:
            logger.exception("[InstagramTokenRenewal] company %s: renewal failed", company_id)
            send_critical_alert(
                "INSTAGRAM_TOKEN_RENEWAL_FAILED",
                "instagram_token_renewal.py:renew_instagram_tokens",
                err,
                company_id=company_id,
            )


async def _weekly_loop() -> None:
    tz = ZoneInfo(settings.product_sync_timezone)
    while True:
        # same slot as the Sync semanal: a 60-day token gets ~8 weekly tries, the hour is moot
        delay = seconds_until_next_saturday(datetime.now(tz))
        logger.info("[InstagramTokenRenewal] next weekly renewal in %.0fs", delay)
        await asyncio.sleep(delay)
        try:
            async with AsyncSessionLocal() as db:
                await renew_instagram_tokens(CompanyRepository(db))
        except Exception as err:
            logger.exception("[InstagramTokenRenewal] weekly renewal run failed")
            send_critical_alert(
                "INSTAGRAM_TOKEN_RENEWAL_FAILED",
                "instagram_token_renewal.py:_weekly_loop",
                err,
            )


def start_weekly_token_renewal() -> asyncio.Task[None]:
    """Launch the weekly loop as a background task; caller keeps the handle to cancel."""
    # ponytail: in-memory loop, one replica; with more, each renews every token weekly
    return asyncio.create_task(_weekly_loop())
