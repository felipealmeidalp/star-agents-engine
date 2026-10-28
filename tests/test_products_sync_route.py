"""Behavioral tests for the manual sync endpoint: POST /api/products/sync.

House style (ticket_03 → test pattern): self-contained ``asyncio.run(main())`` with
plain ``assert``s, no pytest/conftest, driving the route via httpx.ASGITransport
(lifespan not triggered → no RabbitMQ/DB). The sync module is mocked at the names the
route imported (app.routes.products.try_acquire / release / _run_additive_sync), so
no real sync runs: no browser, no DB, no httpx to 7ball.

Cases: (a) no X-API-Key → 401; (b) lock already held → 409 before scheduling;
(c) valid key + free lock → 202 and the additive sync was scheduled; (d) toggle off
→ 503 before touching the lock.

    API_KEY=test-key DATABASE_URL=postgresql+asyncpg://... \
        python tests/test_products_sync_route.py
"""

import asyncio
import os
import sys
from unittest.mock import patch

sys.path.insert(0, ".")

# app.config.Settings requires DATABASE_URL and API_KEY at import time; placeholders
# so the module imports without a .env. The URL never connects — the route is mocked.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres",
)
os.environ.setdefault("API_KEY", "test-key")

import httpx  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402


async def _post(headers: dict[str, str] | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # lifespan not triggered → no RabbitMQ/DB
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/products/sync", headers=headers or {})


async def case_a_missing_key_401() -> None:
    resp = await _post()
    assert resp.status_code == 401, resp.status_code
    print("  (a) no X-API-Key → 401: OK")


async def case_b_lock_held_409() -> None:
    scheduled: list[int] = []
    # Lock appears taken; assert we 409 BEFORE scheduling anything.
    with patch("app.routes.products.try_acquire", lambda: False), patch(
        "app.routes.products.asyncio.create_task", lambda coro: scheduled.append(1)
    ):
        resp = await _post({"X-API-Key": settings.api_key})
    assert resp.status_code == 409, resp.status_code
    assert resp.json()["detail"] == "sync já em andamento", resp.json()
    assert scheduled == [], "409 must refuse before scheduling any work"
    print("  (b) lock held → 409, nothing scheduled: OK")


async def case_c_free_lock_202_and_scheduled() -> None:
    scheduled: list[str] = []

    async def _fake_sync() -> None:
        scheduled.append("ran")

    released: list[int] = []
    with patch("app.routes.products.try_acquire", lambda: True), patch(
        "app.routes.products.release", lambda: released.append(1)
    ), patch("app.routes.products._run_additive_sync", _fake_sync):
        resp = await _post({"X-API-Key": settings.api_key})
        await asyncio.sleep(0)  # let the scheduled task run

    assert resp.status_code == 202, resp.status_code
    assert released == [1], "gate must release before scheduling (run_sync re-acquires)"
    assert scheduled == ["ran"], "the additive sync was not scheduled"
    print("  (c) valid key + free lock → 202, additive sync scheduled: OK")


async def case_d_disabled_503() -> None:
    acquired: list[int] = []
    with patch.object(settings, "product_sync_enabled", False), patch(
        "app.routes.products.try_acquire", lambda: acquired.append(1) or True
    ):
        resp = await _post({"X-API-Key": settings.api_key})
    assert resp.status_code == 503, resp.status_code
    assert acquired == [], "disabled sync must refuse before touching the lock"
    print("  (d) toggle off → 503, lock untouched: OK")


async def main() -> None:
    print("Products sync route tests (seam: POST /api/products/sync)")
    assert settings.api_key  # sanity: settings imported
    await case_a_missing_key_401()
    with patch.object(settings, "product_sync_enabled", True):
        await case_b_lock_held_409()
        await case_c_free_lock_202_and_scheduled()
    await case_d_disabled_503()
    print("\nOK - all products sync route cases passed")


if __name__ == "__main__":
    asyncio.run(main())
