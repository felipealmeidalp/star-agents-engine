"""Manual product-sync trigger (7ball/Reebok). Authenticated, additive, background."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import settings
from app.dependencies import verify_api_key
from app.services.product_sync import SyncInProgressError, release, run_sync, try_acquire

logger = logging.getLogger(__name__)

router = APIRouter()


async def _run_additive_sync() -> None:
    """Own the sync: run_sync re-acquires and releases the lock in its own finally.

    ponytail: theoretical re-acquire gap between the handler's gate and this task —
    a second concurrent manual fire loses run_sync's try_acquire and raises (logged,
    no double-sync). Single worker + occasional manual trigger; a Redis lock only if
    that ever stops holding.
    """
    # imports adiados: render/rest arrastam playwright/httpx, fora do import da rota
    from app.db.database import AsyncSessionLocal
    from app.repositories.product import ProductRepository
    from app.services.product_sync.render import product_renderer
    from app.services.product_sync.rest import fetch_catalog

    async with product_renderer() as render:
        async with AsyncSessionLocal() as db:
            try:
                written = await run_sync(
                    source=fetch_catalog, render=render, repo=ProductRepository(db)
                )
                logger.info("[ProductSync] manual sync wrote %d rows", written)
            except SyncInProgressError:
                logger.warning("[ProductSync] manual sync lost the re-acquire race; skipped")
            except Exception:
                logger.exception("[ProductSync] manual sync failed")


@router.post("/products/sync", status_code=status.HTTP_202_ACCEPTED)
async def trigger_sync(_api_key: str = Depends(verify_api_key)) -> dict[str, str]:
    """Fire the additive sync in the background; 409 on the spot if one is running."""
    if not settings.product_sync_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="sync de produtos desativado"
        )
    if not try_acquire():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="sync já em andamento"
        )
    release()  # gate only — _run_additive_sync's run_sync re-acquires and owns release
    asyncio.create_task(_run_additive_sync())
    return {"status": "accepted", "detail": "sync aditivo disparado"}
