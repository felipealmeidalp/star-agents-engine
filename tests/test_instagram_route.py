"""Behavioral tests for the Instagram comments webhook: GET/POST /api/instagram.

The DB cases need a reachable Postgres with migration 012 applied and the seeded
company_id=5 (with an OpenAI key); without it they SKIP with a clear message. Only the
Instagram API client, the Decisions call and the OpenAI generation are mocked.

    DATABASE_URL=postgresql+asyncpg://... API_KEY=x python tests/test_instagram_route.py
"""

import asyncio
import hashlib
import hmac
import json
import os
import sys
from collections.abc import Iterable
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
from app.services.openai import OpenAIService  # noqa: E402

SEED_COMPANY_ID = 5

VERIFY_TOKEN = "test-verify-token"
APP_SECRET = "test-app-secret"
KNOWN_ACCOUNT = "test-ig-account-0001"

ACCESS_TOKEN = "test-ig-access-token"
PUBLIC_REPLY = "Te chamei no direct!"
DM_TEXT = "Oi @maria.sinuca, vi seu comentário no post da mesa!"
REPLY_TEXT = "Incrível, né? Te chamei no direct!"
ELOGIO_TEXT = "Esta mesa é simplesmente maravilhosa"
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
FILLED_ELOGIO_DM = (
    "Escreva uma DM leve agradecendo o elogio.\n"
    "<dados>\nnome: Maria Sinuca\ncomentário: Esta mesa é simplesmente maravilhosa\n"
    "legenda: Mesa de sinuca oficial\n</dados>"
)
FILLED_ELOGIO_REPLY = (
    "Responda ao elogio em público, numa frase.\n"
    "<dados>\nnome: Maria Sinuca\ncomentário: Esta mesa é simplesmente maravilhosa\n"
    "legenda: Mesa de sinuca oficial\n</dados>"
)

Handoffs = list[tuple[int, InstagramComment]]
ClientCalls = list[tuple[str, ...]]
Decisions = list[tuple[str, str, dict[str, str], str]]


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
    category: str | None = "comercial",
    decisions: Decisions | None = None,
    reply_content: str | None = None,
) -> tuple[httpx.Response, ClientCalls, list[str], list[tuple[str, Any]]]:
    """`failing` names what raises: a client method, "<method>:<comment id>", "openai" (the DM
    generation), "generate_public_reply" or "decide_choice"."""
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
        if llm_payload.response_format["json_schema"]["name"] == "instagram_public_reply":
            _record("generate_public_reply", llm_payload.model)
            content = json.dumps({"reply": REPLY_TEXT}) if reply_content is None else reply_content
        elif failing == "openai":
            raise RuntimeError("openai boom")
        else:
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

    async def _decide_choice(
        self: Any, instructions: str, choices: dict[str, str], input_text: str
    ) -> tuple[str | None, float]:
        calls.append(("decide_choice",))
        if failing == "decide_choice":
            raise RuntimeError("decisions boom")
        if decisions is not None:
            decisions.append((self.client.api_key, instructions, choices, input_text))
        return category, 0.9

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
    ), patch(
        "app.services.openai.OpenAIService.decide_choice", _decide_choice
    ), patch("app.services.instagram_welcome.send_critical_alert", _alert), patch(
        "app.routes.instagram.send_critical_alert", _alert
    ), patch("app.services.tools.internal.transfer.send_critical_alert", _alert):
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


# the Decisions API answer as tested on 08/10/2026 with the 7ball key
DECISIONS_RESPONSE = {
    "model": "gpt-6-luna",
    "answers": [
        {
            "type": "choice",
            "name": "choice",
            "choice": "comercial",
            "probabilities": [
                {"value": "so_emoji", "probability": 0.01},
                {"value": "comercial", "probability": 0.99},
            ],
            "confidence": 0.99,
        }
    ],
    "usage": {},
}


async def case_decide_choice_request() -> None:
    requests: list[tuple[str, Any]] = []

    async def _decide(response: dict[str, Any]) -> tuple[str, float | None]:
        service = OpenAIService("sk-test")

        async def _post(path: str, *, body: Any, **kwargs: Any) -> Any:
            requests.append((path, body))
            return response

        with patch.object(service.client, "post", _post):
            return await service.decide_choice(
                "Que tipo de comentário?",
                {"comercial": "Interesse de compra.", "outro": "Qualquer outra coisa."},
                "Comentário: quanto custa?",
            )

    assert await _decide(DECISIONS_RESPONSE) == ("comercial", 0.99)
    assert len(requests) == 1, requests
    path, body = requests[0]
    assert path == "/decisions", path
    assert body["model"] == "gpt-6-luna", body
    assert body["input"] == "Comentário: quanto custa?", body
    assert body["questions"] == [
        {
            "type": "choice",
            "name": "choice",
            "instructions": "Que tipo de comentário?",
            "choices": [
                {"value": "comercial", "description": "Interesse de compra."},
                {"value": "outro", "description": "Qualquer outra coisa."},
            ],
        }
    ], body

    no_choice = {"answers": [{"type": "choice", "name": "choice", "confidence": 0.5}]}
    for broken in ({"answers": []}, no_choice):
        try:
            await _decide(broken)
        except (KeyError, IndexError):
            continue
        raise AssertionError(f"an answer without a choice must raise: {broken}")
    print("  Decisions request → one choice question, answer and confidence read back: OK")


async def db_ready() -> bool:
    try:
        async with AsyncSessionLocal() as db:
            await asyncio.wait_for(
                db.execute(text("SELECT category FROM instagram_welcomes LIMIT 1")), timeout=5
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
ELOGIO_DM_PROMPT = (
    "Escreva uma DM leve agradecendo o elogio.\n"
    "<dados>\nnome: [NOME]\ncomentário: [COMENTARIO]\nlegenda: [LEGENDA]\n</dados>"
)
ELOGIO_REPLY_PROMPT = (
    "Responda ao elogio em público, numa frase.\n"
    "<dados>\nnome: [NOME]\ncomentário: [COMENTARIO]\nlegenda: [LEGENDA]\n</dados>"
)
RECLAMACAO_PROMPT = (
    "Acolha a reclamação e pergunte o que aconteceu.\n"
    "<dados>\nnome: [NOME]\ncomentário: [COMENTARIO]\nlegenda: [LEGENDA]\n</dados>"
)
PROMPTS = {
    "instagram_welcome_dm": ("gpt-test-welcome", "low", WELCOME_PROMPT),
    "instagram_welcome_dm_elogio": ("gpt-test-elogio-dm", "low", ELOGIO_DM_PROMPT),
    "instagram_public_reply_elogio": ("gpt-test-elogio-reply", "medium", ELOGIO_REPLY_PROMPT),
    "instagram_welcome_dm_reclamacao": ("gpt-test-reclamacao", "high", RECLAMACAO_PROMPT),
}
ELOGIO_PROMPTS = ("instagram_welcome_dm_elogio", "instagram_public_reply_elogio")
PROMPT_KEY = {"cid": SEED_COMPANY_ID, "reason": "instagram_welcome"}
PROMPT_WHERE = "company_id = :cid AND reason = :reason"


async def _execute(sql: str, **params: Any) -> Any:
    async with AsyncSessionLocal() as db:
        result = await db.execute(text(sql), params)
        await db.commit()
        return result


async def seeded_api_key() -> Any:
    result = await _execute(
        'SELECT "openAi_apiKey" FROM companies WHERE id = :cid', cid=SEED_COMPANY_ID
    )
    return result.scalar_one()


IG_COLUMNS = "instagram_account_id, instagram_access_token, instagram_public_reply"


async def take_snapshot() -> tuple[Any, list[dict[str, Any]]]:
    """What the script overwrites on the seeded Company, to put back at the end."""
    company = await _execute(
        f"SELECT {IG_COLUMNS}, helena_apikey, helena_assignee_id FROM companies WHERE id = :cid",
        cid=SEED_COMPANY_ID,
    )
    prompts = await _execute(f"SELECT * FROM prompts WHERE {PROMPT_WHERE}", **PROMPT_KEY)
    descriptions = await _execute(
        f"SELECT * FROM prompts WHERE {DESCRIPTION_WHERE}", **DESCRIPTION_KEY
    )
    rows = [*prompts.mappings(), *descriptions.mappings()]
    return company.one(), [dict(row) for row in rows]


async def restore_snapshot(snapshot: tuple[Any, list[dict[str, Any]]]) -> None:
    company, prompts = snapshot
    await stamp_account(company.instagram_account_id)
    await stamp_config(company.instagram_access_token, company.instagram_public_reply)
    await stamp_helena(company.helena_apikey, company.helena_assignee_id)
    await _execute(f"DELETE FROM prompts WHERE {PROMPT_WHERE}", **PROMPT_KEY)
    await set_descriptions(())
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


async def stamp_helena(apikey: str | None, assignee_id: str | None) -> None:
    await _execute(
        "UPDATE companies SET helena_apikey = :key, helena_assignee_id = :aid WHERE id = :cid",
        key=apikey,
        aid=assignee_id,
        cid=SEED_COMPANY_ID,
    )


async def set_prompt(present: bool, name: str = "instagram_welcome_dm") -> None:
    await _execute(
        f"DELETE FROM prompts WHERE {PROMPT_WHERE} AND name = :name", name=name, **PROMPT_KEY
    )
    if present:
        model, effort, prompt = PROMPTS[name]
        await _execute(
            "INSERT INTO prompts (company_id, name, reason, prompt, model, reasoning_effort, "
            "created_at) VALUES (:cid, :name, :reason, :prompt, :model, :effort, now())",
            name=name,
            prompt=prompt,
            model=model,
            effort=effort,
            **PROMPT_KEY,
        )


# the five starting texts of the spec, in the code's category order
DESCRIPTIONS = {
    "so_emoji": "Só emojis, sem texto.",
    "comercial": (
        "Interesse de compra: preço, valor, tamanho, medidas, prazo, pagamento, ou cita o "
        "nome de um produto ou modelo."
    ),
    "elogio": "Elogio ou reação positiva ao produto, ao post ou à marca.",
    "reclamacao": "Reclamação, insatisfação ou crítica à marca, ao atendimento ou ao produto.",
    "outro": "Qualquer outra coisa: marcar amigos, spam, assunto alheio.",
}
DESCRIPTION_KEY = {"cid": SEED_COMPANY_ID, "reason": "instagram_comment_category"}
DESCRIPTION_WHERE = "company_id = :cid AND reason = :reason"


async def set_descriptions(names: Iterable[str] = tuple(DESCRIPTIONS)) -> None:
    await _execute(f"DELETE FROM prompts WHERE {DESCRIPTION_WHERE}", **DESCRIPTION_KEY)
    for name in names:
        await _execute(
            "INSERT INTO prompts (company_id, name, reason, prompt, created_at) "
            "VALUES (:cid, :name, :reason, :prompt, now())",
            name=name,
            prompt=DESCRIPTIONS[name],
            **DESCRIPTION_KEY,
        )


async def clear_welcomes() -> None:
    await _execute(
        "DELETE FROM instagram_welcomes WHERE company_id = :cid AND media_id IN (:a, :b)",
        cid=SEED_COMPANY_ID,
        a=POST,
        b=OTHER_POST,
    )


async def welcome_rows() -> list[tuple[Any, ...]]:
    result = await _execute(
        "SELECT comment_id, category, status FROM instagram_welcomes "
        "WHERE company_id = :cid AND media_id = :post ORDER BY id",
        cid=SEED_COMPANY_ID,
        post=POST,
    )
    return [tuple(row) for row in result]


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
        ("decide_choice",),
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
    assert api_key == await seeded_api_key(), "the LLM call must use the Company's key"
    assert llm_payload.model == "gpt-test-welcome", llm_payload.model
    assert llm_payload.reasoning_effort == "low", llm_payload.reasoning_effort
    assert [m.role for m in llm_payload.messages] == ["user"], llm_payload.messages
    assert not llm_payload.tools, llm_payload.tools
    assert await welcome_rows() == [("17900000000000001", "comercial", "pendente")]
    print("  comercial comment → caption, classification, DM, Resposta pública, category: OK")


async def case_classification_request() -> None:
    await clear_welcomes()
    decisions: Decisions = []
    _, calls, alerts, _ = await _post_welcome(make_event(make_comment()), decisions=decisions)
    assert calls == FULL_FLOW, calls
    assert alerts == [], alerts

    assert len(decisions) == 1, decisions
    api_key, _, choices, input_text = decisions[0]
    assert api_key == await seeded_api_key(), "the Decisions call must use the Company's key"
    # dict equality ignores order: compare the items
    assert list(choices.items()) == list(DESCRIPTIONS.items()), choices
    assert "Mesa de sinuca oficial" in input_text, input_text
    assert "quanto custa?" in input_text, input_text
    print("  classification → Company's key, its five descriptions, comment and caption: OK")


async def _assert_stops_after_classification(expected_alerts: list[str], **kwargs: Any) -> None:
    await clear_welcomes()
    _, calls, alerts, llm_calls = await _post_welcome(make_event(make_comment()), **kwargs)
    assert calls == [("get_media", POST), ("decide_choice",)], (kwargs, calls)
    assert llm_calls == [], (kwargs, llm_calls)
    assert alerts == expected_alerts, (kwargs, alerts)
    assert await welcome_rows() == [], (kwargs, await welcome_rows())


async def _assert_turn_still_free() -> None:
    _, calls, alerts, _ = await _post_welcome(make_event(make_comment("17900000000000002")))
    assert calls == full_flow("17900000000000002"), calls
    assert alerts == [], alerts


async def case_silent_categories() -> None:
    for category in ("so_emoji", "outro"):
        await _assert_stops_after_classification([], category=category)
    print("  so_emoji or outro → nothing generated, sent or recorded, no alert: OK")


async def case_emoji_then_comercial_same_post() -> None:
    await _assert_stops_after_classification([], category="so_emoji")
    await _assert_turn_still_free()
    print("  so_emoji, then a comercial comment on the same post → the second gets the DM: OK")


async def case_classification_failure() -> None:
    await _assert_stops_after_classification(["INSTAGRAM_WELCOME_FAILED"], failing="decide_choice")
    await _assert_turn_still_free()
    print("  Decisions fails → nothing sent or recorded, alert; new comment → the flow runs: OK")


async def case_classification_outside_the_categories() -> None:
    for category in ("desconhecida", None):
        await _assert_stops_after_classification(["INSTAGRAM_WELCOME_FAILED"], category=category)
    print("  Decisions answers an unknown category or none → failure, alert, nothing sent: OK")


async def case_category_without_its_texts() -> None:
    await _assert_stops_after_classification([], category="reclamacao")
    await _assert_turn_still_free()
    print("  reclamacao without its DM prompt → nothing sent or recorded, turn kept: OK")


async def case_missing_description() -> None:
    await clear_welcomes()
    decisions: Decisions = []
    try:
        await set_descriptions(tuple(DESCRIPTIONS)[:-1])
        _, calls, alerts, llm_calls = await _post_welcome(
            make_event(make_comment()), decisions=decisions
        )
    finally:
        await set_descriptions()
    assert calls == [], calls
    assert llm_calls == [], llm_calls
    assert decisions == [], decisions
    assert alerts == [], alerts
    print("  Company without one of the five descriptions → no Meta or OpenAI call, no alert: OK")


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
        ("decide_choice",),
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
        ("decide_choice",),
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

    _, calls, alerts, _ = await _post_welcome(make_event(make_comment("17900000000000002")))
    assert calls == full_flow("17900000000000002"), calls
    assert alerts == [], alerts
    print("  caption fetch fails → nothing sent, alert; new comment → the flow runs: OK")


async def case_llm_failure() -> None:
    await clear_welcomes()
    resp, calls, alerts, _ = await _post_welcome(make_event(make_comment()), failing="openai")
    assert resp.status_code == 200, resp.status_code
    assert calls == [("get_media", POST), ("decide_choice",)], calls
    assert alerts == ["INSTAGRAM_WELCOME_FAILED"], alerts
    print("  OpenAI raises → no DM, no Resposta pública, alert: OK")


async def case_llm_returns_unusable_dm() -> None:
    for content in ("", "not json", '{"dm": "  ", "product": null}'):
        await clear_welcomes()
        _, calls, alerts, _ = await _post_welcome(
            make_event(make_comment()), llm_content=content
        )
        assert calls == [("get_media", POST), ("decide_choice",)], (content, calls)
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


def _messages(llm_payload: Any) -> list[tuple[str, str | None]]:
    return [(m.role, m.content) for m in llm_payload.messages]


def _sent_messages(llm_calls: list[tuple[str, Any]]) -> list[tuple[str, str | None]]:
    assert len(llm_calls) == 1, llm_calls
    return _messages(llm_calls[0][1])


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

    try:
        await set_prompt(False)
        await _assert_stops_after_classification([])
        await set_prompt(True)
        await stamp_config(ACCESS_TOKEN, "")
        await _assert_stops_after_classification([])
        await stamp_config(None, PUBLIC_REPLY)
        await _assert_nothing_leaves("no access token")
        await stamp_config(ACCESS_TOKEN, PUBLIC_REPLY)
        await set_descriptions(tuple(DESCRIPTIONS)[:-1])
        await _assert_nothing_leaves("one description missing")
    finally:
        await set_prompt(True)
        await set_descriptions()
        await stamp_config(ACCESS_TOKEN, PUBLIC_REPLY)

    # no turn was taken in any of the above
    _, calls, _, _ = await _post_welcome(make_event(make_comment()))
    assert calls == FULL_FLOW, calls
    print("  comercial without its texts → classified only; no token → nothing; turn kept: OK")


async def case_concurrent_deliveries() -> None:
    await clear_welcomes()
    _, calls, alerts, _ = await _post_welcome(
        make_event(make_comment("17900000000000001")),
        make_event(make_comment("17900000000000002")),
    )
    # both deliveries may read the caption and classify before one wins the reservation
    assert [c[0] for c in calls if c[0] not in ("get_media", "decide_choice")] == [
        "send_private_reply",
        "release_thread_control",
        "reply_to_comment",
    ], calls
    assert alerts == [], alerts
    print("  two concurrent deliveries for the same person and post → one DM: OK")


def elogio_event(comment_id: str = "17900000000000001", **overrides: Any) -> dict[str, Any]:
    return make_event(make_comment(comment_id, text=ELOGIO_TEXT, **overrides))


def elogio_flow(comment_id: str = "17900000000000001") -> ClientCalls:
    return [
        ("get_media", POST),
        ("decide_choice",),
        ("send_private_reply", comment_id, DM_TEXT),
        ("release_thread_control", "7770001"),
        ("generate_public_reply", "gpt-test-elogio-reply"),
        ("reply_to_comment", comment_id, REPLY_TEXT),
    ]


async def case_elogio() -> None:
    await clear_welcomes()
    resp, calls, alerts, llm_calls = await _post_welcome(elogio_event(), category="elogio")
    assert resp.status_code == 200, resp.status_code
    assert calls == elogio_flow(), calls
    assert alerts == [], alerts

    assert [api_key for api_key, _ in llm_calls] == [await seeded_api_key()] * 2, llm_calls
    (_, dm_call), (_, reply_call) = llm_calls
    assert dm_call.model == "gpt-test-elogio-dm", dm_call.model
    assert dm_call.reasoning_effort == "low", dm_call.reasoning_effort
    assert dm_call.response_format["json_schema"]["name"] == "instagram_welcome_dm"
    assert _messages(dm_call) == [("user", FILLED_ELOGIO_DM)], dm_call.messages
    assert reply_call.model == "gpt-test-elogio-reply", reply_call.model
    assert reply_call.reasoning_effort == "medium", reply_call.reasoning_effort
    assert reply_call.response_format["json_schema"]["name"] == "instagram_public_reply"
    assert _messages(reply_call) == [("user", FILLED_ELOGIO_REPLY)], reply_call.messages
    assert not dm_call.tools and not reply_call.tools
    assert await welcome_states() == [("pendente", None, None)], await welcome_states()
    assert await welcome_rows() == [("17900000000000001", "elogio", "pendente")]
    print("  elogio → elogio DM, then a Resposta pública generated by its prompt and published: OK")


async def case_elogio_from_a_customer() -> None:
    await clear_welcomes()
    await clear_customers()
    session_id = TEST_SESSION + "open"
    conversation = [("user", "oi, tem mesa de sinuca?"), ("assistant", "Temos sim! Qual tamanho?")]
    await seed_customer(session_id, [{"role": r, "content": c} for r, c in conversation])
    _, calls, alerts, llm_calls = await _post_welcome(
        elogio_event(**{"from": KNOWN_AUTHOR}), category="elogio"
    )
    assert calls == elogio_flow(), calls
    assert alerts == [], alerts
    (_, dm_call), (_, reply_call) = llm_calls
    assert _messages(dm_call) == [*conversation, ("user", FILLED_ELOGIO_DM)], dm_call.messages
    assert _messages(reply_call) == [("user", FILLED_ELOGIO_REPLY)], reply_call.messages

    rows = await session_rows(session_id)
    assert rows[-1] == ("assistant", DM_TEXT), rows
    assert (await customer_context(session_id))["origem_comentario_instagram"] == {
        "legenda_do_post": "Mesa de sinuca oficial",
        "comentario": ELOGIO_TEXT,
        "produto": "Mesa de sinuca oficial",
    }
    print("  elogio from a Customer → conversation in the DM only, DM and Origem in session: OK")


async def case_elogio_dm_failure() -> None:
    await clear_welcomes()
    _, calls, alerts, llm_calls = await _post_welcome(
        elogio_event(), category="elogio", failing="send_private_reply"
    )
    assert calls == [
        ("get_media", POST),
        ("decide_choice",),
        ("send_private_reply", "17900000000000001", DM_TEXT),
    ], calls
    assert [p.model for _, p in llm_calls] == ["gpt-test-elogio-dm"], llm_calls
    assert alerts == ["INSTAGRAM_WELCOME_FAILED"], alerts
    print("  elogio whose DM fails → Resposta pública neither generated nor published, alert: OK")


async def case_elogio_public_reply_failure() -> None:
    runs: list[dict[str, Any]] = [
        {"failing": "generate_public_reply"},
        {"failing": "reply_to_comment"},
        *({"reply_content": content} for content in ("", "not json", '{"reply": "  "}')),
    ]
    for run in runs:
        await clear_welcomes()
        _, calls, alerts, _ = await _post_welcome(elogio_event(), category="elogio", **run)
        published = run.get("failing") == "reply_to_comment"
        assert calls == (elogio_flow() if published else elogio_flow()[:-1]), (run, calls)
        assert alerts == ["INSTAGRAM_PUBLIC_REPLY_FAILED"], (run, alerts)
        assert await welcome_states() == [("pendente", None, None)], run

        _, calls, alerts, llm_calls = await _post_welcome(
            elogio_event("17900000000000002"), category="elogio"
        )
        assert (calls, alerts, llm_calls) == ([], [], []), (run, calls, alerts, llm_calls)
    print("  elogio Resposta pública not generated, unusable or not published → alert only: OK")


async def case_elogio_without_its_texts() -> None:
    try:
        for name in ELOGIO_PROMPTS:
            await set_prompt(False, name)
            await _assert_stops_after_classification([], category="elogio")
            await set_prompt(True, name)
            _, calls, alerts, _ = await _post_welcome(
                elogio_event("17900000000000002"), category="elogio"
            )
            assert calls == elogio_flow("17900000000000002"), (name, calls)
            assert alerts == [], (name, alerts)

        await clear_welcomes()
        await stamp_config(ACCESS_TOKEN, "")
        _, calls, alerts, _ = await _post_welcome(elogio_event(), category="elogio")
        assert calls == elogio_flow(), calls
        assert alerts == [], alerts
    finally:
        for name in ELOGIO_PROMPTS:
            await set_prompt(True, name)
        await stamp_config(ACCESS_TOKEN, PUBLIC_REPLY)
    print("  elogio missing either prompt → nothing sent or recorded, turn kept: OK")


HELENA_APIKEY = "test-helena-apikey"
ASSIGNEE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
COMPLAINT = "14 dias para obter uma resposta!! Um absurdo!"
FILLED_RECLAMACAO_PROMPT = (
    "Acolha a reclamação e pergunte o que aconteceu.\n"
    f"<dados>\nnome: Maria Sinuca\ncomentário: {COMPLAINT}\nlegenda: Mesa de sinuca oficial\n"
    "</dados>"
)
DM_ONLY_FLOW = [
    ("get_media", POST),
    ("decide_choice",),
    ("send_private_reply", "17900000000000001", DM_TEXT),
    ("release_thread_control", "7770001"),
]


def patch_assign(failing: bool = False) -> tuple[list[tuple[str, str, str]], Any]:
    """Each call is recorded as (session, user, apikey)."""
    assigned: list[tuple[str, str, str]] = []

    async def _stub(self: Any, session_id: str, user_id: str, apikey: str) -> dict[str, Any]:
        assigned.append((session_id, user_id, apikey))
        if failing:
            raise httpx.ConnectError("assign_session boom")
        return {"ok": True}

    return assigned, patch("app.helena.client.HelenaClient.assign_session", _stub)


async def customer_status(session_id: str) -> Any:
    result = await _execute('SELECT status FROM customers WHERE "sessionId" = :sid', sid=session_id)
    return result.scalar_one()


async def case_reclamacao_gets_dm_only() -> None:
    await clear_welcomes()
    await clear_customers()
    assigned, assign_patch = patch_assign()
    try:
        await stamp_config(ACCESS_TOKEN, "")
        with assign_patch:
            resp, calls, alerts, llm_calls = await _post_welcome(
                make_event(make_comment(text=COMPLAINT, **{"from": KNOWN_AUTHOR})),
                category="reclamacao",
            )
    finally:
        await stamp_config(ACCESS_TOKEN, PUBLIC_REPLY)
    assert resp.status_code == 200, resp.status_code
    assert calls == DM_ONLY_FLOW, calls
    assert alerts == [], alerts
    assert _sent_messages(llm_calls) == [("user", FILLED_RECLAMACAO_PROMPT)], llm_calls
    assert llm_calls[0][1].model == "gpt-test-reclamacao", llm_calls[0][1].model
    assert await welcome_rows() == [("17900000000000001", "reclamacao", "pendente")]
    assert assigned == [], assigned
    print("  reclamacao, no Resposta pública configured → DM from its own prompt only: OK")


async def _complaint_of_a_customer(
    failing_assign: bool = False,
) -> tuple[ClientCalls, list[str], list[tuple[str, Any]], list[tuple[str, str, str]]]:
    await clear_welcomes()
    await clear_customers()
    await seed_customer(TEST_SESSION + "open", [{"role": "user", "content": "oi, tem mesa?"}])
    assigned, assign_patch = patch_assign(failing_assign)
    with assign_patch:
        _, calls, alerts, llm_calls = await _post_welcome(
            make_event(make_comment(text=COMPLAINT, **{"from": KNOWN_AUTHOR})),
            category="reclamacao",
        )
    return calls, alerts, llm_calls, assigned


async def case_reclamacao_of_a_customer_escalates_at_send() -> None:
    session_id = TEST_SESSION + "open"
    calls, alerts, llm_calls, assigned = await _complaint_of_a_customer()
    assert calls == DM_ONLY_FLOW, calls
    assert alerts == [], alerts
    assert _sent_messages(llm_calls) == [
        ("user", "oi, tem mesa?"),
        ("user", FILLED_RECLAMACAO_PROMPT),
    ], llm_calls
    assert (await session_rows(session_id))[-1] == ("assistant", DM_TEXT)
    origem = (await customer_context(session_id))["origem_comentario_instagram"]
    assert origem["produto"] == "Mesa de sinuca oficial", origem
    assert await customer_status(session_id) is False
    assert assigned == [(session_id, ASSIGNEE_ID, HELENA_APIKEY)], assigned
    print("  reclamacao of a Customer → DM in the session, AI off, session assigned: OK")


async def case_reclamacao_assign_failure_keeps_the_ai_off() -> None:
    session_id = TEST_SESSION + "open"
    calls, alerts, _, assigned = await _complaint_of_a_customer(failing_assign=True)
    assert calls == DM_ONLY_FLOW, calls
    assert "HELENA_ASSIGN_FAILED" in alerts, alerts
    assert len(assigned) == 1, assigned
    assert await customer_status(session_id) is False
    assert (await session_rows(session_id))[-1] == ("assistant", DM_TEXT)
    assert await welcome_states() == [("pendente", None, None)], await welcome_states()
    print("  reclamacao of a Customer, Helena assignment fails → alert, AI stays off: OK")


async def case_reclamacao_escalation_failure_keeps_the_dm() -> None:
    session_id = TEST_SESSION + "open"

    async def _boom(self: Any, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("update_status boom")

    with patch("app.repositories.customer.CustomerRepository.update_status", _boom):
        calls, alerts, _, assigned = await _complaint_of_a_customer()
    assert calls == DM_ONLY_FLOW, calls
    assert alerts == ["INSTAGRAM_ESCALATION_FAILED"], alerts
    assert assigned == [], assigned
    assert (await session_rows(session_id))[-1] == ("assistant", DM_TEXT)
    assert await welcome_states() == [("pendente", None, None)], await welcome_states()
    print("  reclamacao of a Customer, escalation raises → alert, DM kept, still pendente: OK")


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
    await case_decide_choice_request()

    if not await db_ready():
        print(
            "\nSKIPPED (DB unreachable or migration 012 not applied): the cases that resolve "
            "the Company by instagram_account_id (first-level comment, comment_id alias, "
            "unknown account, batch) and the whole welcome flow. Point DATABASE_URL at a Postgres "
            "with migration 012 applied and the seeded company_id=5 to run them."
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
        await set_prompt(False, "instagram_welcome_dm_reclamacao")
        await set_descriptions()
        await case_eligible_comment_gets_dm_then_public_reply()
        await case_classification_request()
        await case_silent_categories()
        await case_emoji_then_comercial_same_post()
        await case_classification_failure()
        await case_classification_outside_the_categories()
        await case_category_without_its_texts()
        await case_missing_description()
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

        for name in ELOGIO_PROMPTS:
            await set_prompt(True, name)
        await case_elogio()
        await case_elogio_from_a_customer()
        await case_elogio_dm_failure()
        await case_elogio_public_reply_failure()
        await case_elogio_without_its_texts()
        await set_prompt(True, "instagram_welcome_dm_reclamacao")
        await stamp_helena(HELENA_APIKEY, ASSIGNEE_ID)
        await case_reclamacao_gets_dm_only()
        await case_reclamacao_of_a_customer_escalates_at_send()
        await case_reclamacao_assign_failure_keeps_the_ai_off()
        await case_reclamacao_escalation_failure_keeps_the_dm()
    finally:
        # the seeded Company may be a real one: leave its Instagram config as it was
        await restore_snapshot(snapshot)
    print("\nOK - all Instagram route cases passed")


if __name__ == "__main__":
    asyncio.run(main())
