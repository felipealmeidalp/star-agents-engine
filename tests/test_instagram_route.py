"""Behavioral tests for the Instagram comments webhook: GET/POST /api/instagram.

The DB cases need a reachable Postgres with migration 010 applied and the seeded
company_id=5; without it they SKIP with a clear message.

    DATABASE_URL=postgresql+asyncpg://... API_KEY=x python tests/test_instagram_route.py
"""

import asyncio
import hashlib
import hmac
import json
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

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.database import AsyncSessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.services.instagram_welcome import InstagramComment  # noqa: E402

SEED_COMPANY_ID = 5

VERIFY_TOKEN = "test-verify-token"
APP_SECRET = "test-app-secret"
KNOWN_ACCOUNT = "test-ig-account-0001"

Handoffs = list[tuple[int, InstagramComment]]


def make_event(*values: dict[str, Any], account: str = KNOWN_ACCOUNT) -> dict[str, Any]:
    return {
        "object": "instagram",
        "entry": [
            {
                "id": account,
                "time": 1759312800,
                "changes": [{"field": "comments", "value": v} for v in values],
            }
        ],
    }


def make_comment(comment_id: str = "17900000000000001", **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": comment_id,
        "text": "quanto custa?",
        "from": {"id": "5550001", "username": "maria.sinuca"},
        "media": {"id": "18000000000000009", "media_product_type": "FEED"},
    }
    value.update(overrides)
    return value


async def _post_event(
    payload: Any,
    *,
    signed_with: str | None = APP_SECRET,
    app_secret: str = APP_SECRET,
    failing_comment: str | None = None,
) -> tuple[httpx.Response, Handoffs, list[str]]:
    """Post an event signed the way Meta does; return (response, hand-offs, alerts)."""
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if signed_with is not None:
        digest = hmac.new(signed_with.encode(), raw, hashlib.sha256).hexdigest()
        headers["X-Hub-Signature-256"] = "sha256=" + digest

    handoffs: Handoffs = []
    alerts: list[str] = []

    async def _record_handoff(db: Any, company: Any, comment: InstagramComment) -> None:
        if comment.comment_id == failing_comment:
            raise RuntimeError("hand-off boom")
        handoffs.append((company.id, comment))

    transport = httpx.ASGITransport(app=app)
    with patch.object(settings, "instagram_app_secret", app_secret), patch(
        "app.routes.instagram.handle_comment", _record_handoff
    ), patch(
        "app.routes.instagram.send_critical_alert",
        lambda error_type, *a, **k: alerts.append(error_type),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # content=raw: json= would re-serialize and break the signature
            resp = await client.post("/api/instagram", content=raw, headers=headers)
    return resp, handoffs, alerts


async def _get(params: dict[str, str]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/api/instagram", params=params)


async def case_handshake_right_token() -> None:
    with patch.object(settings, "instagram_verify_token", VERIFY_TOKEN):
        resp = await _get({"hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1158201444"})
    assert resp.status_code == 200, resp.status_code
    assert resp.text == "1158201444", resp.text
    print("  handshake with the right verify token → challenge echoed: OK")


async def case_handshake_wrong_token() -> None:
    with patch.object(settings, "instagram_verify_token", VERIFY_TOKEN):
        resp = await _get({"hub.verify_token": "another-token", "hub.challenge": "1158201444"})
    assert resp.status_code == 403, resp.status_code
    print("  handshake with a wrong verify token → 403: OK")


async def case_handshake_token_unset() -> None:
    with patch.object(settings, "instagram_verify_token", ""):
        resp = await _get({"hub.challenge": "1158201444"})
    assert resp.status_code == 403, resp.status_code
    print("  handshake with INSTAGRAM_VERIFY_TOKEN unset → 403: OK")


async def case_event_without_signature() -> None:
    resp, handoffs, _ = await _post_event(make_event(make_comment()), signed_with=None)
    assert resp.status_code == 403, resp.status_code
    assert handoffs == [], handoffs
    print("  event without signature → 403, no hand-off: OK")


async def case_event_signed_with_another_secret() -> None:
    resp, handoffs, _ = await _post_event(make_event(make_comment()), signed_with="another-secret")
    assert resp.status_code == 403, resp.status_code
    assert handoffs == [], handoffs
    print("  event signed with another secret → 403, no hand-off: OK")


async def case_event_with_secret_unset() -> None:
    resp, handoffs, _ = await _post_event(
        make_event(make_comment()), signed_with="", app_secret=""
    )
    assert resp.status_code == 403, resp.status_code
    assert handoffs == [], handoffs
    print("  INSTAGRAM_APP_SECRET unset, event signed with empty secret → 403, no hand-off: OK")


async def case_non_comment_event() -> None:
    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": KNOWN_ACCOUNT,
                "time": 1759312800,
                "changes": [{"field": "mentions", "value": {"media_id": "1", "comment_id": "2"}}],
            },
            {"id": KNOWN_ACCOUNT, "time": 1759312800, "messaging": [{"sender": {"id": "9"}}]},
        ],
    }
    resp, handoffs, alerts = await _post_event(payload)
    assert resp.status_code == 200, resp.status_code
    assert handoffs == [], handoffs
    assert alerts == [], alerts
    print("  signed non-comment event → 200, no hand-off, no alert: OK")


async def case_reply_to_another_comment(when: str) -> None:
    reply = make_comment("17900000000000002", parent_id="17900000000000001")
    resp, handoffs, alerts = await _post_event(make_event(reply))
    assert resp.status_code == 200, resp.status_code
    assert handoffs == [], handoffs
    assert alerts == [], alerts
    print(f"  reply to another comment ({when}) → 200, no hand-off: OK")


async def case_signed_unparseable_body() -> None:
    for raw in (b"not json at all", b"[1, 2, 3]"):
        resp, handoffs, alerts = await _post_event(raw)
        assert resp.status_code == 200, resp.status_code
        assert resp.json() == {"status": "ignored"}, resp.json()
        assert handoffs == [], handoffs
        assert alerts == [], alerts
    print("  signed body that is not a JSON object → 200 ignored, no hand-off: OK")


async def db_ready() -> bool:
    try:
        async with AsyncSessionLocal() as db:
            await asyncio.wait_for(
                db.execute(text("SELECT instagram_account_id FROM companies LIMIT 1")), timeout=5
            )
        return True
    except Exception as e:
        print(f"  (DB not ready: {type(e).__name__}: {str(e)[:120]})")
        return False


async def stamp_account(account_id: str | None) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("UPDATE companies SET instagram_account_id = :acc WHERE id = :cid"),
            {"acc": account_id, "cid": SEED_COMPANY_ID},
        )
        await db.commit()


async def case_first_level_comment_reaches_handoff() -> None:
    resp, handoffs, alerts = await _post_event(make_event(make_comment()))
    assert resp.status_code == 200, resp.status_code
    assert handoffs == [
        (
            SEED_COMPANY_ID,
            InstagramComment(
                comment_id="17900000000000001",
                media_id="18000000000000009",
                author_id="5550001",
                author_username="maria.sinuca",
                text="quanto custa?",
                parent_id=None,
            ),
        )
    ], handoffs
    assert alerts == [], alerts
    print("  first-level comment of a known Company → one hand-off with the parsed comment: OK")


async def case_comment_id_alias() -> None:
    value = make_comment()
    value["comment_id"] = value.pop("id")
    resp, handoffs, _ = await _post_event(make_event(value))
    assert resp.status_code == 200, resp.status_code
    assert [c.comment_id for _, c in handoffs] == ["17900000000000001"], handoffs
    print("  comment id named `comment_id` instead of `id` → same hand-off: OK")


async def case_unknown_account() -> None:
    resp, handoffs, alerts = await _post_event(
        make_event(make_comment(), account="test-ig-account-unknown")
    )
    assert resp.status_code == 200, resp.status_code
    assert handoffs == [], handoffs
    assert alerts == [], alerts
    print("  comment of an account no Company has → 200, no hand-off, no alert: OK")


async def case_batch_with_malformed_item() -> None:
    malformed = make_comment("17900000000000002")
    del malformed["media"]
    resp, handoffs, _ = await _post_event(
        make_event(
            make_comment("17900000000000001"),
            malformed,
            make_comment("17900000000000003"),
        )
    )
    assert resp.status_code == 200, resp.status_code
    assert [(cid, c.comment_id) for cid, c in handoffs] == [
        (SEED_COMPANY_ID, "17900000000000001"),
        (SEED_COMPANY_ID, "17900000000000003"),
    ], handoffs
    print("  batch with a malformed comment in the middle → the other two, in order: OK")


async def case_failing_comment_does_not_drop_the_others() -> None:
    resp, handoffs, alerts = await _post_event(
        make_event(make_comment("17900000000000001"), make_comment("17900000000000002")),
        failing_comment="17900000000000001",
    )
    assert resp.status_code == 200, resp.status_code
    assert [c.comment_id for _, c in handoffs] == ["17900000000000002"], handoffs
    assert alerts == ["INSTAGRAM_WEBHOOK_UNHANDLED_ERROR"], alerts
    print("  hand-off failing on one comment → alert, next comment still handed off: OK")


async def main() -> None:
    print("Instagram route behavioral tests (seam: GET/POST /api/instagram)")
    await case_handshake_right_token()
    await case_handshake_wrong_token()
    await case_handshake_token_unset()
    await case_event_without_signature()
    await case_event_signed_with_another_secret()
    await case_event_with_secret_unset()
    await case_signed_unparseable_body()
    await case_non_comment_event()
    await case_reply_to_another_comment("no Company resolved")

    if not await db_ready():
        print(
            "\nSKIPPED (DB unreachable or migration 010 not applied): the cases that resolve "
            "the Company by instagram_account_id (first-level comment, comment_id alias, "
            "unknown account, batch). Point DATABASE_URL at a Postgres with migration 010 "
            "applied and the seeded company_id=5 to run them."
        )
        print("\nOK - no-DB cases passed; DB cases pending")
        return

    await stamp_account(KNOWN_ACCOUNT)
    try:
        await case_first_level_comment_reaches_handoff()
        await case_comment_id_alias()
        # only with the account stamped does this prove parent_id is what blocks the hand-off
        await case_reply_to_another_comment("account of a known Company")
        await case_unknown_account()
        await case_batch_with_malformed_item()
        await case_failing_comment_does_not_drop_the_others()
    finally:
        # the column is unique: never leave the fake id on the seeded Company
        await stamp_account(None)
    print("\nOK - all Instagram route cases passed")


if __name__ == "__main__":
    asyncio.run(main())
