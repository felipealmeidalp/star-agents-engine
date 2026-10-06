"""Behavioral tests for the Instagram comments webhook: GET/POST /api/instagram.

The DB cases need a reachable Postgres with migration 011 applied and the seeded
company_id=5 (with an OpenAI key); without it they SKIP with a clear message. Only the
Instagram API client and the OpenAI call are mocked.

    DATABASE_URL=postgresql+asyncpg://... API_KEY=x python tests/test_instagram_route.py
"""

import asyncio
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

sys.path.insert(0, ".")

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres",
)
os.environ.setdefault("API_KEY", "test-key")

import httpx  # noqa: E402
from sqlalchemy import delete, select, text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.database import AsyncSessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.schemas import OpenAIChoice, OpenAIMessage, OpenAIResponse  # noqa: E402
from app.models.tables import ChatHistory, Company, Customer  # noqa: E402
from app.services.instagram_welcome import InstagramComment  # noqa: E402

SEED_COMPANY_ID = 5

VERIFY_TOKEN = "test-verify-token"
APP_SECRET = "test-app-secret"
KNOWN_ACCOUNT = "test-ig-account-0001"

ACCESS_TOKEN = "test-ig-access-token"
PUBLIC_REPLY = "Te chamei no direct!"
DM_TEXT = "Oi @maria.sinuca, vi seu comentário no post da mesa!"
POST = "18000000000000009"
OTHER_POST = "18000000000000010"
# a commenter whose id is long enough to be looked up as a Customer: "+" + its last 13 digits
KNOWN_AUTHOR = {"id": "8880009876543210", "username": "maria.sinuca"}
KNOWN_PHONE = "+0009876543210"
TEST_SESSION = "ig-welcome-test-"
FILLED_PROMPT = (
    "Escreva uma DM curta de boas-vindas.\n"
    "<dados>\nnome: Maria Sinuca\ncomentário: quanto custa?\nlegenda: Mesa de sinuca oficial\n"
    "</dados>"
)

Handoffs = list[tuple[int, InstagramComment]]
ClientCalls = list[tuple[str, ...]]


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


def _signed(payload: Any, signed_with: str | None) -> tuple[bytes, dict[str, str]]:
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if signed_with is not None:
        digest = hmac.new(signed_with.encode(), raw, hashlib.sha256).hexdigest()
        headers["X-Hub-Signature-256"] = "sha256=" + digest
    return raw, headers


async def _post_event(
    payload: Any,
    *,
    signed_with: str | None = APP_SECRET,
    app_secret: str = APP_SECRET,
    failing_comment: str | None = None,
) -> tuple[httpx.Response, Handoffs, list[str]]:
    """Post an event signed the way Meta does; return (response, hand-offs, alerts)."""
    raw, headers = _signed(payload, signed_with)

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


async def _post_welcome(
    *payloads: Any,
    failing: str | None = None,
    media: dict[str, Any] | None = None,
    llm_content: str | None = None,
) -> tuple[httpx.Response, ClientCalls, list[str], list[tuple[str, Any]]]:
    """`failing` names what raises: a client method, "<method>:<comment id>" or "openai"."""
    calls: ClientCalls = []
    alerts: list[str] = []
    llm_calls: list[tuple[str, Any]] = []

    def _record(name: str, *args: str) -> None:
        calls.append((name, *args))
        if failing in (name, f"{name}:{args[0]}"):
            raise httpx.ConnectError(f"{name} boom")

    async def _get_media(self: Any, media_id: str, access_token: str) -> dict[str, Any]:
        _record("get_media", media_id)
        if media is not None:
            return media
        return {"id": media_id, "caption": "Mesa de sinuca oficial", "permalink": "https://ig/p/x"}

    async def _get_user_name(self: Any, user_id: str, access_token: str) -> str:
        return "Maria Sinuca"

    async def _send_private_reply(
        self: Any, comment_id: str, text: str, access_token: str
    ) -> dict[str, Any]:
        _record("send_private_reply", comment_id, text)
        return {"recipient_id": "7770001", "message_id": "mid.1"}

    async def _release_thread_control(
        self: Any, recipient_id: str, access_token: str
    ) -> dict[str, Any]:
        _record("release_thread_control", recipient_id)
        return {"success": True}

    async def _reply_to_comment(
        self: Any, comment_id: str, text: str, access_token: str
    ) -> dict[str, Any]:
        _record("reply_to_comment", comment_id, text)
        return {"id": "17900000000000999"}

    async def _chat_completion(self: Any, llm_payload: Any) -> OpenAIResponse:
        llm_calls.append((self.client.api_key, llm_payload))
        if failing == "openai":
            raise RuntimeError("openai boom")
        content = llm_content
        if content is None:
            content = json.dumps({"dm": DM_TEXT, "product": "Mesa de sinuca oficial"})
        return OpenAIResponse(
            id="resp-test",
            model="gpt-test",
            choices=[
                OpenAIChoice(
                    index=0,
                    message=OpenAIMessage(role="assistant", content=content),
                    finish_reason="stop",
                )
            ],
        )

    def _alert(error_type: str, *args: Any, **kwargs: Any) -> None:
        alerts.append(error_type)

    client = "app.services.instagram_client.InstagramClient"
    transport = httpx.ASGITransport(app=app)
    with patch.object(settings, "instagram_app_secret", APP_SECRET), patch.object(
        settings, "instagram_dm_dev_mode", False
    ), patch(f"{client}.get_media", _get_media), patch(
        f"{client}.get_user_name", _get_user_name
    ), patch(f"{client}.send_private_reply", _send_private_reply), patch(
        f"{client}.release_thread_control", _release_thread_control
    ), patch(
        f"{client}.reply_to_comment", _reply_to_comment
    ), patch(
        "app.services.openai.OpenAIService.chat_completion", _chat_completion
    ), patch("app.services.instagram_welcome.send_critical_alert", _alert), patch(
        "app.routes.instagram.send_critical_alert", _alert
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            responses = await asyncio.gather(
                *(
                    http.post("/api/instagram", content=raw, headers=headers)
                    for raw, headers in (_signed(p, APP_SECRET) for p in payloads)
                )
            )
    return responses[-1], calls, alerts, llm_calls


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
                db.execute(text("SELECT id FROM instagram_welcomes LIMIT 1")), timeout=5
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


WELCOME_PROMPT = (
    "Escreva uma DM curta de boas-vindas.\n"
    "<dados>\nnome: [NOME]\ncomentário: [COMENTARIO]\nlegenda: [LEGENDA]\n</dados>"
)
PROMPT_KEY = {"cid": SEED_COMPANY_ID, "name": "instagram_welcome_dm", "reason": "instagram_welcome"}
PROMPT_WHERE = "company_id = :cid AND name = :name AND reason = :reason"


async def _execute(sql: str, **params: Any) -> Any:
    async with AsyncSessionLocal() as db:
        result = await db.execute(text(sql), params)
        await db.commit()
        return result


IG_COLUMNS = "instagram_account_id, instagram_access_token, instagram_public_reply"


async def take_snapshot() -> tuple[Any, list[dict[str, Any]]]:
    """What the script overwrites on the seeded Company, to put back at the end."""
    company = await _execute(
        f"SELECT {IG_COLUMNS} FROM companies WHERE id = :cid", cid=SEED_COMPANY_ID
    )
    prompts = await _execute(f"SELECT * FROM prompts WHERE {PROMPT_WHERE}", **PROMPT_KEY)
    return company.one(), [dict(row) for row in prompts.mappings()]


async def restore_snapshot(snapshot: tuple[Any, list[dict[str, Any]]]) -> None:
    company, prompts = snapshot
    await stamp_account(company.instagram_account_id)
    await stamp_config(company.instagram_access_token, company.instagram_public_reply)
    await set_prompt(False)
    for row in prompts:
        columns = ", ".join(row)
        values = ", ".join(f":{c}" for c in row)
        await _execute(f"INSERT INTO prompts ({columns}) VALUES ({values})", **row)
    await clear_welcomes()
    await clear_customers()


async def stamp_config(token: str | None, public_reply: str | None) -> None:
    await _execute(
        "UPDATE companies SET instagram_access_token = :tok, instagram_public_reply = :reply "
        "WHERE id = :cid",
        tok=token,
        reply=public_reply,
        cid=SEED_COMPANY_ID,
    )


async def set_prompt(present: bool) -> None:
    await _execute(f"DELETE FROM prompts WHERE {PROMPT_WHERE}", **PROMPT_KEY)
    if present:
        await _execute(
            "INSERT INTO prompts (company_id, name, reason, prompt, model, reasoning_effort, "
            "created_at) VALUES (:cid, :name, :reason, :prompt, 'gpt-test-welcome', 'low', now())",
            prompt=WELCOME_PROMPT,
            **PROMPT_KEY,
        )


async def clear_welcomes() -> None:
    await _execute(
        "DELETE FROM instagram_welcomes WHERE company_id = :cid AND media_id IN (:a, :b)",
        cid=SEED_COMPANY_ID,
        a=POST,
        b=OTHER_POST,
    )


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


def full_flow(comment_id: str = "17900000000000001", post: str = POST) -> ClientCalls:
    return [
        ("get_media", post),
        ("send_private_reply", comment_id, DM_TEXT),
        ("release_thread_control", "7770001"),
        ("reply_to_comment", comment_id, PUBLIC_REPLY),
    ]


FULL_FLOW = full_flow()


async def case_eligible_comment_gets_dm_then_public_reply() -> None:
    await clear_welcomes()
    resp, calls, alerts, llm_calls = await _post_welcome(make_event(make_comment()))
    assert resp.status_code == 200, resp.status_code
    assert calls == FULL_FLOW, calls
    assert alerts == [], alerts

    assert len(llm_calls) == 1, llm_calls
    api_key, llm_payload = llm_calls[0]
    seeded_key = await _execute(
        'SELECT "openAi_apiKey" FROM companies WHERE id = :cid', cid=SEED_COMPANY_ID
    )
    assert api_key == seeded_key.scalar_one(), "the LLM call must use the Company's key"
    assert llm_payload.model == "gpt-test-welcome", llm_payload.model
    assert llm_payload.reasoning_effort == "low", llm_payload.reasoning_effort
    assert [m.role for m in llm_payload.messages] == ["user"], llm_payload.messages
    assert not llm_payload.tools, llm_payload.tools
    print("  Comentário elegível → caption read, one DM, then the Resposta pública: OK")


async def case_own_account_comment() -> None:
    await clear_welcomes()
    own = make_comment(
        "17900000000000001", **{"from": {"id": KNOWN_ACCOUNT, "username": "loja.oficial"}}
    )
    resp, calls, alerts, llm_calls = await _post_welcome(make_event(own))
    assert resp.status_code == 200, resp.status_code
    assert calls == [], calls
    assert llm_calls == [], llm_calls
    assert alerts == [], alerts
    print("  comment by the Company's own account → no client call, no LLM call: OK")


async def case_reply_gets_no_welcome() -> None:
    await clear_welcomes()
    reply = make_comment("17900000000000002", parent_id="17900000000000001")
    resp, calls, alerts, llm_calls = await _post_welcome(make_event(reply))
    assert resp.status_code == 200, resp.status_code
    assert calls == [], calls
    assert llm_calls == [], llm_calls
    assert alerts == [], alerts
    print("  reply to another comment → no client call, no LLM call: OK")


async def case_second_comment_same_post() -> None:
    await clear_welcomes()
    _, first, _, _ = await _post_welcome(make_event(make_comment("17900000000000001")))
    assert first == FULL_FLOW, first
    resp, calls, alerts, llm_calls = await _post_welcome(
        make_event(make_comment("17900000000000002"))
    )
    assert resp.status_code == 200, resp.status_code
    assert calls == [], calls
    assert llm_calls == [], llm_calls
    assert alerts == [], alerts
    print("  same person, same post, second comment → no client call: OK")


async def case_event_delivered_twice() -> None:
    await clear_welcomes()
    event = make_event(make_comment())
    _, first, _, _ = await _post_welcome(event)
    assert first == FULL_FLOW, first
    resp, calls, alerts, _ = await _post_welcome(event)
    assert resp.status_code == 200, resp.status_code
    assert calls == [], calls
    assert alerts == [], alerts
    print("  same event delivered twice → one DM and one Resposta pública only: OK")


async def case_same_person_other_post() -> None:
    await clear_welcomes()
    _, first, _, _ = await _post_welcome(make_event(make_comment("17900000000000001")))
    assert first == FULL_FLOW, first
    other = make_comment("17900000000000005", media={"id": OTHER_POST})
    _, calls, alerts, _ = await _post_welcome(make_event(other))
    assert calls == full_flow("17900000000000005", OTHER_POST), calls
    assert alerts == [], alerts
    print("  same person on another post → a new DM and a new Resposta pública: OK")


async def case_dm_failure_then_retry_on_new_comment() -> None:
    await clear_welcomes()
    resp, calls, alerts, _ = await _post_welcome(
        make_event(make_comment("17900000000000001")), failing="send_private_reply"
    )
    assert resp.status_code == 200, resp.status_code
    assert calls == [
        ("get_media", POST),
        ("send_private_reply", "17900000000000001", DM_TEXT),
    ], calls
    assert alerts == ["INSTAGRAM_WELCOME_FAILED"], alerts

    _, calls, alerts, _ = await _post_welcome(make_event(make_comment("17900000000000002")))
    assert calls == full_flow("17900000000000002"), calls
    assert alerts == [], alerts
    print("  DM send fails → nothing published, alert; new comment → the flow runs again: OK")


async def case_public_reply_failure_keeps_welcome_pending() -> None:
    await clear_welcomes()
    resp, calls, alerts, _ = await _post_welcome(
        make_event(make_comment("17900000000000001")), failing="reply_to_comment"
    )
    assert resp.status_code == 200, resp.status_code
    assert calls == FULL_FLOW, calls
    assert alerts == ["INSTAGRAM_PUBLIC_REPLY_FAILED"], alerts

    _, calls, alerts, _ = await _post_welcome(make_event(make_comment("17900000000000002")))
    assert calls == [], calls
    assert alerts == [], alerts
    print("  Resposta pública fails → alert, DM sent, the person's turn stays taken: OK")


async def case_thread_release_failure_keeps_the_flow() -> None:
    await clear_welcomes()
    resp, calls, alerts, _ = await _post_welcome(
        make_event(make_comment("17900000000000001")), failing="release_thread_control"
    )
    assert resp.status_code == 200, resp.status_code
    assert calls == FULL_FLOW, calls
    assert alerts == ["INSTAGRAM_THREAD_RELEASE_FAILED"], alerts
    print("  thread release fails → alert, DM sent, Resposta pública still published: OK")


async def case_unknown_account_gets_no_welcome() -> None:
    await clear_welcomes()
    resp, calls, alerts, llm_calls = await _post_welcome(
        make_event(make_comment(), account="test-ig-account-unknown")
    )
    assert resp.status_code == 200, resp.status_code
    assert calls == [], calls
    assert llm_calls == [], llm_calls
    assert alerts == [], alerts
    print("  account no Company has → 200, no client call, no alert: OK")


SECOND_PERSON = {"id": "5550002", "username": "joao.taco"}


async def case_batch_with_two_eligible_comments() -> None:
    await clear_welcomes()
    resp, calls, alerts, _ = await _post_welcome(
        make_event(
            make_comment("17900000000000001"),
            make_comment("17900000000000002", **{"from": SECOND_PERSON}),
        )
    )
    assert resp.status_code == 200, resp.status_code
    assert calls == [*FULL_FLOW, *full_flow("17900000000000002")], calls
    assert alerts == [], alerts
    print("  one POST with two eligible comments → two DMs and two Respostas públicas: OK")


async def case_batch_first_fails_second_goes() -> None:
    await clear_welcomes()
    resp, calls, alerts, _ = await _post_welcome(
        make_event(
            make_comment("17900000000000001"),
            make_comment("17900000000000002", **{"from": SECOND_PERSON}),
        ),
        failing="send_private_reply:17900000000000001",
    )
    assert resp.status_code == 200, resp.status_code
    assert calls == [
        ("get_media", POST),
        ("send_private_reply", "17900000000000001", DM_TEXT),
        *full_flow("17900000000000002"),
    ], calls
    assert alerts == ["INSTAGRAM_WELCOME_FAILED"], alerts
    print("  batch whose first DM fails → alert, the second comment still gets its DM: OK")


async def case_caption_fetch_failure() -> None:
    await clear_welcomes()
    resp, calls, alerts, llm_calls = await _post_welcome(
        make_event(make_comment()), failing="get_media"
    )
    assert resp.status_code == 200, resp.status_code
    assert calls == [("get_media", POST)], calls
    assert llm_calls == [], llm_calls
    assert alerts == ["INSTAGRAM_WELCOME_FAILED"], alerts
    print("  caption fetch fails → no DM, no Resposta pública, alert: OK")


async def case_llm_failure() -> None:
    await clear_welcomes()
    resp, calls, alerts, _ = await _post_welcome(make_event(make_comment()), failing="openai")
    assert resp.status_code == 200, resp.status_code
    assert calls == [("get_media", POST)], calls
    assert alerts == ["INSTAGRAM_WELCOME_FAILED"], alerts
    print("  OpenAI raises → no DM, no Resposta pública, alert: OK")


async def case_llm_returns_unusable_dm() -> None:
    for content in ("", "not json", '{"dm": "  ", "product": null}'):
        await clear_welcomes()
        _, calls, alerts, _ = await _post_welcome(
            make_event(make_comment()), llm_content=content
        )
        assert calls == [("get_media", POST)], (content, calls)
        assert alerts == ["INSTAGRAM_WELCOME_FAILED"], (content, alerts)
    print("  LLM returns empty content, invalid JSON or an empty dm → no DM, alert: OK")


async def case_post_without_caption() -> None:
    await clear_welcomes()
    resp, calls, alerts, _ = await _post_welcome(make_event(make_comment()), media={"id": POST})
    assert resp.status_code == 200, resp.status_code
    assert calls == FULL_FLOW, calls
    assert alerts == [], alerts
    print("  post without caption → DM sent and Resposta pública published: OK")


async def clear_customers() -> None:
    async with AsyncSessionLocal() as db:
        for table in (ChatHistory, Customer):
            await db.execute(
                delete(table).where(
                    table.company_id == SEED_COMPANY_ID, table.sessionId.like(TEST_SESSION + "%")
                )
            )
        await db.commit()


async def seed_customer(
    session_id: str,
    history: list[dict[str, Any]],
    *,
    created_at: datetime | None = None,
    with_agent: bool = True,
) -> None:
    """Add a Customer of the known commenter with a conversation already in its ChatHistory."""
    # naive UTC, as the customers and chat_history columns store it
    created_at = created_at or datetime.utcnow() - timedelta(hours=1)
    async with AsyncSessionLocal() as db:
        company = await db.get(Company, SEED_COMPANY_ID)
        assert company is not None
        ids = {
            "company_id": SEED_COMPANY_ID,
            "agent_id": company.standard_agent_id,
            "sub_agent_id": company.standard_sub_agent_id,
            "sessionId": session_id,
        }
        db.add(
            Customer(
                **(ids if with_agent else {"company_id": SEED_COMPANY_ID, "sessionId": session_id}),
                custom_information={"phone": KNOWN_PHONE},
                customer_context={"etapa": "orçamento"},
                created_at=created_at,
            )
        )
        for offset, row in enumerate(history):
            db.add(ChatHistory(**ids, **row, created_at=created_at + timedelta(seconds=offset)))
        await db.commit()


async def session_rows(session_id: str) -> list[tuple[str, str | None]]:
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(ChatHistory.role, ChatHistory.content)
            .where(ChatHistory.sessionId == session_id)
            .order_by(ChatHistory.created_at, ChatHistory.id)
        )
        return [(role, content) for role, content in rows]


async def customer_context(session_id: str) -> Any:
    result = await _execute(
        'SELECT customer_context FROM customers WHERE "sessionId" = :sid', sid=session_id
    )
    return result.scalar_one()


async def welcome_states() -> list[tuple[Any, ...]]:
    result = await _execute(
        "SELECT status, session_id, matched_at FROM instagram_welcomes "
        "WHERE company_id = :cid AND media_id = :post",
        cid=SEED_COMPANY_ID,
        post=POST,
    )
    return [tuple(row) for row in result]


def _sent_messages(llm_calls: list[tuple[str, Any]]) -> list[tuple[str, str | None]]:
    assert len(llm_calls) == 1, llm_calls
    return [(m.role, m.content) for m in llm_calls[0][1].messages]


async def case_commenter_with_customer() -> None:
    await clear_welcomes()
    await clear_customers()
    session_id = TEST_SESSION + "open"
    tool_call = {"id": "call_1", "type": "function", "function": {"name": "rag", "arguments": "{}"}}
    await seed_customer(
        session_id,
        [
            {"role": "user", "content": "oi, tem mesa de sinuca?"},
            {"role": "assistant", "content": None, "tool_calls": [tool_call]},
            {"role": "tool", "content": "catálogo: mesa oficial", "tool_call_id": "call_1"},
            {"role": "assistant", "content": "Temos sim! Qual tamanho?"},
            {"role": "user", "content": "a oficial"},
        ],
    )
    resp, calls, alerts, llm_calls = await _post_welcome(
        make_event(make_comment(**{"from": KNOWN_AUTHOR}))
    )
    assert resp.status_code == 200, resp.status_code
    assert calls == FULL_FLOW, calls
    assert alerts == [], alerts
    conversation = [
        ("user", "oi, tem mesa de sinuca?"),
        ("assistant", "Temos sim! Qual tamanho?"),
        ("user", "a oficial"),
    ]
    assert _sent_messages(llm_calls) == [*conversation, ("user", FILLED_PROMPT)], llm_calls
    assert not llm_calls[0][1].tools, llm_calls[0][1].tools

    rows = await session_rows(session_id)
    assert rows[-1] == ("assistant", DM_TEXT), rows
    assert rows.count(("assistant", DM_TEXT)) == 1, rows
    assert await customer_context(session_id) == {
        "etapa": "orçamento",
        "origem_comentario_instagram": {
            "legenda_do_post": "Mesa de sinuca oficial",
            "comentario": "quanto custa?",
            "produto": "Mesa de sinuca oficial",
        },
    }
    assert await welcome_states() == [("pendente", None, None)], await welcome_states()
    print("  commenter with a Customer → conversation in the request, DM in the session: OK")


async def case_customer_without_agent_gets_the_dm() -> None:
    await clear_welcomes()
    await clear_customers()
    session_id = TEST_SESSION + "no-agent"
    await seed_customer(session_id, [], with_agent=False)
    resp, calls, alerts, _ = await _post_welcome(
        make_event(make_comment(**{"from": KNOWN_AUTHOR}))
    )
    assert resp.status_code == 200, resp.status_code
    assert calls == FULL_FLOW, calls
    assert await session_rows(session_id) == [("assistant", DM_TEXT)]
    print("  Customer without an agent → DM written with the Company's standard agent: OK")


async def case_commenter_without_customer() -> None:
    await clear_welcomes()
    await clear_customers()
    resp, calls, alerts, llm_calls = await _post_welcome(
        make_event(make_comment(**{"from": KNOWN_AUTHOR}))
    )
    assert resp.status_code == 200, resp.status_code
    assert calls == FULL_FLOW, calls
    assert alerts == [], alerts
    assert _sent_messages(llm_calls) == [("user", FILLED_PROMPT)], llm_calls
    written = await _execute(
        "SELECT count(*) FROM chat_history WHERE company_id = :cid AND content = :dm",
        cid=SEED_COMPANY_ID,
        dm=DM_TEXT,
    )
    assert written.scalar_one() == 0, "no ChatHistory row may be written without a Customer"
    print("  commenter without a Customer → single-message request, nothing written: OK")


async def case_most_recent_customer_wins() -> None:
    await clear_welcomes()
    await clear_customers()
    old_session, new_session = TEST_SESSION + "old", TEST_SESSION + "new"
    await seed_customer(
        old_session,
        [{"role": "user", "content": "mensagem da sessão antiga"}],
        created_at=datetime(2025, 1, 1),
    )
    await seed_customer(new_session, [{"role": "user", "content": "mensagem da sessão nova"}])
    _, calls, alerts, llm_calls = await _post_welcome(
        make_event(make_comment(**{"from": KNOWN_AUTHOR}))
    )
    assert calls == FULL_FLOW, calls
    assert alerts == [], alerts
    assert _sent_messages(llm_calls) == [
        ("user", "mensagem da sessão nova"),
        ("user", FILLED_PROMPT),
    ], llm_calls
    assert await session_rows(new_session) == [
        ("user", "mensagem da sessão nova"),
        ("assistant", DM_TEXT),
    ]
    assert await session_rows(old_session) == [("user", "mensagem da sessão antiga")]
    assert "origem_comentario_instagram" not in await customer_context(old_session)
    print("  two Customers of the same person → only the most recent is read and written: OK")


async def case_customer_lookup_failure_keeps_the_welcome() -> None:
    await clear_welcomes()
    await clear_customers()
    await seed_customer(TEST_SESSION + "open", [{"role": "user", "content": "oi"}])

    async def _boom(self: Any, *args: Any) -> None:
        raise RuntimeError("customer lookup boom")

    with patch("app.repositories.customer.CustomerRepository.find_latest_by_phone", _boom):
        resp, calls, alerts, llm_calls = await _post_welcome(
            make_event(make_comment(**{"from": KNOWN_AUTHOR}))
        )
    assert resp.status_code == 200, resp.status_code
    assert calls == FULL_FLOW, calls
    assert alerts == [], alerts
    assert _sent_messages(llm_calls) == [("user", FILLED_PROMPT)], llm_calls
    print("  Customer lookup fails → single-message DM, Resposta pública published: OK")


async def case_session_write_failure_keeps_the_welcome() -> None:
    await clear_welcomes()
    await clear_customers()
    await seed_customer(TEST_SESSION + "open", [{"role": "user", "content": "oi"}])

    async def _boom(self: Any, *args: Any) -> None:
        raise RuntimeError("session write boom")

    with patch(
        "app.repositories.instagram_welcome.InstagramWelcomeRepository.write_dm_to_session", _boom
    ):
        resp, calls, alerts, _ = await _post_welcome(
            make_event(make_comment(**{"from": KNOWN_AUTHOR}))
        )
    assert resp.status_code == 200, resp.status_code
    assert calls == FULL_FLOW, calls
    assert alerts == [], alerts
    assert await welcome_states() == [("pendente", None, None)], await welcome_states()
    print("  write to the session fails → Resposta pública published, record pendente: OK")


async def case_feature_off() -> None:
    async def _assert_nothing_leaves(why: str) -> None:
        resp, calls, alerts, llm_calls = await _post_welcome(make_event(make_comment()))
        assert resp.status_code == 200, (why, resp.status_code)
        assert calls == [], (why, calls)
        assert llm_calls == [], (why, llm_calls)
        assert alerts == [], (why, alerts)

    await clear_welcomes()
    try:
        await set_prompt(False)
        await _assert_nothing_leaves("no prompt row")
        await set_prompt(True)
        await stamp_config(ACCESS_TOKEN, "")
        await _assert_nothing_leaves("empty Resposta pública")
        await stamp_config(None, PUBLIC_REPLY)
        await _assert_nothing_leaves("no access token")
    finally:
        await set_prompt(True)
        await stamp_config(ACCESS_TOKEN, PUBLIC_REPLY)

    # no turn was taken while the feature was off
    _, calls, _, _ = await _post_welcome(make_event(make_comment()))
    assert calls == FULL_FLOW, calls
    print("  Company without prompt row, Resposta pública or token → nothing leaves, no alert: OK")


async def case_concurrent_deliveries() -> None:
    await clear_welcomes()
    _, calls, alerts, _ = await _post_welcome(
        make_event(make_comment("17900000000000001")),
        make_event(make_comment("17900000000000002")),
    )
    assert [c[0] for c in calls] == [
        "get_media",
        "send_private_reply",
        "release_thread_control",
        "reply_to_comment",
    ], calls
    assert alerts == [], alerts
    print("  two concurrent deliveries for the same person and post → one DM: OK")


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
            "\nSKIPPED (DB unreachable or migration 011 not applied): the cases that resolve "
            "the Company by instagram_account_id (first-level comment, comment_id alias, "
            "unknown account, batch) and the whole welcome flow. Point DATABASE_URL at a Postgres "
            "with migration 011 applied and the seeded company_id=5 to run them."
        )
        print("\nOK - no-DB cases passed; DB cases pending")
        return

    snapshot = await take_snapshot()
    await stamp_account(KNOWN_ACCOUNT)
    try:
        await case_first_level_comment_reaches_handoff()
        await case_comment_id_alias()
        # only with the account stamped does this prove parent_id is what blocks the hand-off
        await case_reply_to_another_comment("account of a known Company")
        await case_unknown_account()
        await case_batch_with_malformed_item()
        await case_failing_comment_does_not_drop_the_others()

        await stamp_config(ACCESS_TOKEN, PUBLIC_REPLY)
        await set_prompt(True)
        await case_eligible_comment_gets_dm_then_public_reply()
        await case_own_account_comment()
        await case_reply_gets_no_welcome()
        await case_second_comment_same_post()
        await case_event_delivered_twice()
        await case_same_person_other_post()
        await case_dm_failure_then_retry_on_new_comment()
        await case_public_reply_failure_keeps_welcome_pending()
        await case_thread_release_failure_keeps_the_flow()
        await case_unknown_account_gets_no_welcome()
        await case_batch_with_two_eligible_comments()
        await case_batch_first_fails_second_goes()
        await case_caption_fetch_failure()
        await case_llm_failure()
        await case_llm_returns_unusable_dm()
        await case_post_without_caption()
        await case_commenter_with_customer()
        await case_customer_without_agent_gets_the_dm()
        await case_commenter_without_customer()
        await case_most_recent_customer_wins()
        await case_customer_lookup_failure_keeps_the_welcome()
        await case_session_write_failure_keeps_the_welcome()
        await case_feature_off()
        await case_concurrent_deliveries()
    finally:
        # the seeded Company may be a real one: leave its Instagram config as it was
        await restore_snapshot(snapshot)
    print("\nOK - all Instagram route cases passed")


if __name__ == "__main__":
    asyncio.run(main())
