"""Weekly asyncio loop that fires the additive sync at Saturday 00:00.

Thin wiring over the seam (run_sync): no sync logic here. Started in app/main.py's
lifespan next to the RabbitMQ consumers, cancelled cleanly on shutdown.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings
from app.services.product_sync.sync import SyncInProgressError, SyncMode, run_sync
from app.utils.alerter import send_critical_alert

logger = logging.getLogger(__name__)

_SATURDAY = 5  # datetime.weekday(): Mon=0


def seconds_until_next_saturday(now: datetime) -> float:
    """Delta from now to the next Saturday 00:00 in the same tz. Always > 0 (rolls
    forward when now already is the target, so the loop never busy-loops)."""
    days_ahead = (_SATURDAY - now.weekday()) % 7
    target = (now + timedelta(days=days_ahead)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if target <= now:
        target += timedelta(days=7)
    return (target - now).total_seconds()


async def _run_additive_sync() -> None:
    """One production additive run. Skips if a sync already holds the lock."""
    # lazy: mantém `import app.services.product_sync` livre de httpx/playwright (seam)
    from app.db.database import AsyncSessionLocal
    from app.repositories.product import ProductRepository
    from app.services.product_sync.render import product_renderer
    from app.services.product_sync.rest import fetch_catalog

    async with AsyncSessionLocal() as db, product_renderer() as render:
        try:
            written = await run_sync(
                source=fetch_catalog,
                render=render,
                repo=ProductRepository(db),
                mode=SyncMode.ADDITIVE,
            )
            logger.info("[ProductSync] weekly additive sync wrote %d rows", written)
        except SyncInProgressError:
            logger.info("[ProductSync] a sync is already running — skipping this round")


async def _weekly_loop() -> None:
    tz = ZoneInfo(settings.product_sync_timezone)
    while True:
        # recomputa a cada volta: sleep de dias é frágil (drift, "próximo sábado" errado)
        delay = seconds_until_next_saturday(datetime.now(tz))
        logger.info("[ProductSync] next weekly sync in %.0fs", delay)
        await asyncio.sleep(delay)
        try:
            await _run_additive_sync()
        except Exception as err:
            logger.exception("[ProductSync] weekly sync run failed")
            send_critical_alert(
                "PRODUCT_SYNC_WEEKLY_FAILED",
                "services/product_sync/scheduler.py:_weekly_loop",
                err,
            )


def start_weekly_sync() -> asyncio.Task[None]:
    """Launch the weekly loop as a background task; caller keeps the handle to cancel."""
    return asyncio.create_task(_weekly_loop())


if __name__ == "__main__":
    # self-check do cálculo de "próximo sábado 00:00" (tz-aware, sem framework)
    tz = ZoneInfo("America/Sao_Paulo")
    # quinta 2026-09-24 10:00 → sábado 2026-09-26 00:00 = 1 dia e 14h
    thu = datetime(2026, 9, 24, 10, 0, tzinfo=tz)
    assert seconds_until_next_saturday(thu) == (14 + 24) * 3600
    # já é sábado 00:00 → rola pro seguinte (nunca 0)
    sat = datetime(2026, 9, 26, 0, 0, tzinfo=tz)
    assert seconds_until_next_saturday(sat) == 7 * 24 * 3600
    print("ok")
