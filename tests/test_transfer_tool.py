"""Behavioral tests for transfer_to_human at ONE seam: ToolHandler.execute_all.

Feed a transfer_to_human tool call through the real ToolHandler with a
ToolExecutionContext(channel=...); assert what leaves via HelenaClient.assign_session
(mocked) and that customers.status flips to False for real against the test DB.
The tool runs for real; only the external edge (HelenaClient.assign_session, the
outbound httpx PUT) is mocked. update_status runs against the DB.

Harness: option (b) — self-contained ``asyncio.run(main())`` with plain asserts,
zero new dependencies, matching ``tests/test_helena_route.py`` /
``tests/test_categories_tool.py``. No pytest/conftest.

DB: every case drives update_status against a real customer row, so it needs a
reachable Postgres with the seeded company_id=5 fixture (same one the other tests
use). This script stamps helena_assignee_id/apikey onto that company and upserts a
throwaway customer row for a test session. When no DB is reachable it SKIPs cleanly
rather than falsely passing.

    DATABASE_URL=postgresql+asyncpg://... API_KEY=x python tests/test_transfer_tool.py
"""

import asyncio
import os
import sys
from typing import Any
from unittest.mock import patch

sys.path.insert(0, ".")

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres",
)
os.environ.setdefault("API_KEY", "test-key")

from sqlalchemy import text  # noqa: E402

from app.db.database import AsyncSessionLocal  # noqa: E402
from app.models.schemas import ToolCall, ToolCallFunction, ToolExecutionContext  # noqa: E402
from app.repositories.customer import CustomerRepository  # noqa: E402
from app.services.tool_handler import ToolHandler  # noqa: E402

SEED_COMPANY_ID = 5
SEED_AGENT_ID = 39
SEED_SUB_AGENT_ID = 137
SESSION_ID = "transfer-test-session-xyz"
ASSIGNEE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"  # helena_assignee_id (userId)
HELENA_APIKEY = "test-helena-apikey"


def _transfer_call() -> ToolCall:
    return ToolCall(
        id="call-1",
        type="function",
        function=ToolCallFunction(name="transfer_to_human", arguments="{}"),
    )


def _ctx(db: Any, channel: str) -> ToolExecutionContext:
    return ToolExecutionContext(
        session_id=SESSION_ID,
        company_id=SEED_COMPANY_ID,
        agent_id=SEED_AGENT_ID,
        sub_agent_id=SEED_SUB_AGENT_ID,
        db=db,
        channel=channel,
    )


def patch_assign() -> tuple[list[tuple[str, str, str]], Any]:
    """Patch HelenaClient.assign_session, recording each call as (session, user, apikey)."""
    calls: list[tuple[str, str, str]] = []

    async def _stub(self: Any, session_id: str, user_id: str, apikey: str) -> dict[str, Any]:
        calls.append((session_id, user_id, apikey))
        return {"ok": True}

    return calls, patch("app.helena.client.HelenaClient.assign_session", _stub)


async def db_reachable() -> bool:
    try:
        async with AsyncSessionLocal() as db:
            await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=5)
        return True
    except Exception as e:
        print(f"  (DB unreachable: {type(e).__name__}: {str(e)[:120]})")
        return False


async def seed(*, assignee_id: str | None, apikey: str | None) -> None:
    """Stamp Helena assignee/apikey onto the company and a fresh customer row (status=True)."""
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE companies SET helena_assignee_id = :aid, helena_apikey = :key "
                "WHERE id = :cid"
            ),
            {"aid": assignee_id, "key": apikey, "cid": SEED_COMPANY_ID},
        )
        await db.execute(
            text(
                'DELETE FROM customers WHERE "sessionId" = :sid AND company_id = :cid'
            ),
            {"sid": SESSION_ID, "cid": SEED_COMPANY_ID},
        )
        await db.execute(
            text(
                'INSERT INTO customers (company_id, "sessionId", status) '
                "VALUES (:cid, :sid, TRUE)"
            ),
            {"cid": SEED_COMPANY_ID, "sid": SESSION_ID},
        )
        await db.commit()


async def read_status() -> bool | None:
    async with AsyncSessionLocal() as db:
        return await CustomerRepository(db).get_status(SESSION_ID, SEED_COMPANY_ID)


async def run_transfer(channel: str) -> tuple[Any, list[tuple[str, str, str]]]:
    calls, p = patch_assign()
    async with AsyncSessionLocal() as db:
        with p:
            results = await ToolHandler().execute_all([_transfer_call()], _ctx(db, channel))
    return results[0], calls


# --------------------------------------------------------------------------- #
# Cases
# --------------------------------------------------------------------------- #

async def case_helena_assigns() -> None:
    await seed(assignee_id=ASSIGNEE_ID, apikey=HELENA_APIKEY)
    result, calls = await run_transfer("helena")
    assert result.success, result.content
    assert await read_status() is False, "AI must be blocked (status=False)"
    assert len(calls) == 1, f"expected exactly one assign_session call, got {calls}"
    sess, user, key = calls[0]
    assert sess == SESSION_ID, sess
    assert user == ASSIGNEE_ID, user  # company.helena_assignee_id, as-is
    assert key == HELENA_APIKEY, key
    print("  (helena) transfer → status False + assign_session(assignee): OK")


async def case_chatwoot_no_assign() -> None:
    # channel=chatwoot must NOT touch assign_session. Company 5 has no Chatwoot
    # config in this fixture, so the tool blocks the AI and skip-succeeds — the
    # point here is that the Helena edge is never called on the chatwoot branch.
    await seed(assignee_id=ASSIGNEE_ID, apikey=HELENA_APIKEY)
    result, calls = await run_transfer("chatwoot")
    assert result.success, result.content
    assert await read_status() is False, "AI must be blocked on chatwoot too"
    assert calls == [], f"assign_session must NOT be called on chatwoot, got {calls}"
    print("  (chatwoot) transfer → status False, assign_session NOT called: OK")


async def case_helena_missing_config() -> None:
    # No helena_assignee_id → status still False, no assign call, still success.
    await seed(assignee_id=None, apikey=HELENA_APIKEY)
    result, calls = await run_transfer("helena")
    assert result.success, result.content
    assert await read_status() is False, "AI must be blocked even without assignee"
    assert calls == [], f"assign_session must be skipped when config missing, got {calls}"
    print("  (helena, no assignee) → status False, assign skipped, success: OK")


async def case_helena_assign_raises() -> None:
    # assign_session raises → status stays False, critical alert fired, still success.
    await seed(assignee_id=ASSIGNEE_ID, apikey=HELENA_APIKEY)
    alerts: list[str] = []

    async def _boom(self: Any, session_id: str, user_id: str, apikey: str) -> dict[str, Any]:
        raise RuntimeError("helena 500")

    def _record_alert(error_type: str, *a: Any, **k: Any) -> None:
        alerts.append(error_type)

    async with AsyncSessionLocal() as db:
        with patch("app.helena.client.HelenaClient.assign_session", _boom), patch(
            "app.services.tools.internal.transfer.send_critical_alert", _record_alert
        ):
            results = await ToolHandler().execute_all([_transfer_call()], _ctx(db, "helena"))

    assert results[0].success, results[0].content
    assert await read_status() is False, "AI must stay blocked on assign failure"
    assert "HELENA_ASSIGN_FAILED" in alerts, f"expected critical alert, got {alerts}"
    print("  (helena, assign raises) → status False, alert, success: OK")


async def cleanup() -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text('DELETE FROM customers WHERE "sessionId" = :sid AND company_id = :cid'),
            {"sid": SESSION_ID, "cid": SEED_COMPANY_ID},
        )
        await db.execute(
            text(
                "UPDATE companies SET helena_assignee_id = NULL, helena_apikey = NULL "
                "WHERE id = :cid"
            ),
            {"cid": SEED_COMPANY_ID},
        )
        await db.commit()


async def main() -> None:
    print("transfer_to_human behavioral tests (seam: ToolHandler.execute_all)")

    if not await db_reachable():
        print(
            "\nSKIPPED (no reachable DB): every case runs update_status against a real "
            "customer row and reads status back.\nPoint DATABASE_URL at a dev Postgres "
            "with seeded company_id=5 to run them. Not a logic failure — DB only."
        )
        return

    try:
        await case_helena_assigns()
        await case_chatwoot_no_assign()
        await case_helena_missing_config()
        await case_helena_assign_raises()
    finally:
        await cleanup()
    print("\nOK - all transfer_to_human cases passed")


if __name__ == "__main__":
    asyncio.run(main())
