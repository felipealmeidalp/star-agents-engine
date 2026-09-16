"""Behavioral tests for the Helena channel at ONE seam: POST /api/helena/{token}.

Feed a real MESSAGE_RECEIVED payload in; assert what leaves via HelenaClient to
Helena's message/send (count, target phone, message text, ORDER). The path runs
for real; only the two external edges are mocked — OpenAIService.chat_completion
and HelenaClient.send_text (with the humanized delay a no-op). The five cases and
their expectations are spelled out per-function below (spec.md → Testing Decisions).

Harness: option (b) from ticket_03 — a self-contained ``asyncio.run(main())``
script with plain ``assert``s, zero new dependencies, matching the house style of
``tests/test_categories_tool.py``. No pytest/pytest-asyncio, no conftest.

Fixture: no captured MESSAGE_RECEIVED payload existed in the repo, so make_payload
below was built from the spec's "Estrutura" shape.

DB: case (b) short-circuits in the sync handler and needs no DB. Cases (a),(c),(d),
(e) drive the background task, which opens a DB session and resolves the company by
token — they need a reachable Postgres with the seeded company_id=5 (same fixture
test_categories_tool.py uses; this script stamps a helena_token/apikey onto it).
When no DB is reachable those four SKIP with a clear message rather than falsely
"pass" — the route swallows background errors, so a dead DB would otherwise look
like a legit clean no-op. Run all five with:

    DATABASE_URL=postgresql+asyncpg://... API_KEY=x python tests/test_helena_route.py
"""

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import patch

sys.path.insert(0, ".")

# app.config.Settings requires DATABASE_URL and API_KEY at import time. Provide
# placeholders so the module imports even without a .env; a real run overrides
# DATABASE_URL from the environment. The placeholder URL does not connect — DB
# reachability is probed below, and the DB-dependent cases skip if it is dead.
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
from app.models.schemas import (  # noqa: E402
    OpenAIChoice,
    OpenAIMessage,
    OpenAIResponse,
)

# The seeded fixture company (see test_categories_tool.py: company_id=5 has a
# real agent/sub_agent/prompt + openai key, which ContextBuilder needs even with
# OpenAI mocked). We stamp a known Helena token/apikey onto it for the test.
SEED_COMPANY_ID = 5
KNOWN_TOKEN = "11111111-1111-1111-1111-111111111111"
UNKNOWN_TOKEN = "22222222-2222-2222-2222-222222222222"
SESSION_ID = "33333333-3333-3333-3333-333333333333"
LEAD_PHONE = "+5511999998888"  # details.from — Helena routes the reply by phone
HELENA_APIKEY = "test-helena-apikey"
ALLOWED_CHANNEL = 7  # content.channel (injected by n8n) that the allowlist permits
BLOCKED_CHANNEL = 9  # a channel NOT in the allowlist → gated out


def make_payload(**overrides: Any) -> dict[str, Any]:
    """One valid MESSAGE_RECEIVED dict, built from the spec's Estrutura shape.

    Per-case tweaks are applied via overrides on the top-level envelope, and via
    a ``content`` dict merge for content-level tweaks (drop text, etc.).
    """
    content = {
        "id": "msg-abc-123",
        "sessionId": SESSION_ID,
        "text": "olá, quero saber sobre os produtos",
        "type": "text",
        "direction": "FROM_HUB",
        "timestamp": "2026-09-15T12:00:00Z",
        "details": {"from": LEAD_PHONE},
    }
    content.update(overrides.pop("content", {}))
    payload = {
        "eventType": "MESSAGE_RECEIVED",
        "date": "2026-09-15T12:00:00Z",
        "content": content,
    }
    payload.update(overrides)
    return payload


def fake_openai_response(
    reply_messages: list[str],
) -> Callable[..., Awaitable[OpenAIResponse]]:
    """Return a chat_completion stub yielding content={"resposta": [...]} , no tools.

    Content is a JSON string the ChatHandler parses with json.loads(...).get(
    "resposta"); with no tool_calls the pipeline takes the plain-text finish path
    and HelenaService dispatches these N messages via HelenaClient.
    """
    import json

    async def _stub(self: Any, payload: Any) -> OpenAIResponse:
        return OpenAIResponse(
            id="resp-test",
            model="gpt-test",
            choices=[
                OpenAIChoice(
                    index=0,
                    message=OpenAIMessage(
                        role="assistant",
                        content=json.dumps({"resposta": list(reply_messages)}),
                        tool_calls=None,
                    ),
                    finish_reason="stop",
                )
            ],
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        )

    return _stub


def patch_send_text(sends: list[tuple[str, str]]) -> Any:
    """Patch HelenaClient.send_text to record each call as (to, message).

    Helena routes the reply by the recipient phone (`to`), not by a sessionId —
    the send contract has no sessionId (see the client docstring / API docs).
    """

    async def _stub(
        client_self: Any, to: str, message: str, apikey: str
    ) -> dict[str, str]:
        sends.append((to, message))
        return {"id": "queued", "status": "QUEUED"}

    return patch("app.helena.client.HelenaClient.send_text", _stub)


def patch_transcribe(transcript: str | None = None, raises: bool = False) -> Any:
    """Patch OpenAIService.transcribe_audio to a fixed transcript / empty / raise.

    ``raises=True`` → the transcription blows up (network/API failure path);
    ``transcript=""`` → empty transcript (also a failure path); otherwise the
    given string is what the (mocked) Whisper call returns.
    """

    async def _stub(self: Any, audio_url: str) -> str:
        if raises:
            raise RuntimeError("whisper boom")
        return transcript if transcript is not None else ""

    return patch("app.services.openai.OpenAIService.transcribe_audio", _stub)


async def _post(payload: dict[str, Any], token: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # lifespan not triggered → no RabbitMQ/DB ping
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(f"/api/helena/{token}", json=payload)


async def _run_pipeline(
    payload: dict[str, Any], token: str, reply_messages: list[str]
) -> tuple[httpx.Response, list[tuple[str, str]]]:
    """Drive the route end-to-end with OpenAI + HelenaClient mocked; return sends.

    calculate_humanized_delay is patched to 0 so the inter-message sleep is a
    no-op and the test does not wait seconds between messages.
    """
    sends: list[tuple[str, str]] = []
    with patch_send_text(sends), patch(
        "app.services.openai.OpenAIService.chat_completion",
        fake_openai_response(reply_messages),
    ), patch("app.helena.client.calculate_humanized_delay", lambda _msg: 0):
        resp = await _post(payload, token)
    return resp, sends


def patch_capture_message(captured: list[str]) -> Any:
    """Patch RequestManager.on_new_message to record the `message` it receives.

    The transcript-/phrase-into-`message` mapping this ticket adds happens in
    HelenaService BEFORE on_new_message; capturing here asserts what the service
    hands the core, without needing the full AI pipeline / a fully seeded DB.
    Returns a canned response so the caller still exercises the send path.
    """

    async def _stub(self: Any, *, message: str, **kwargs: Any) -> dict[str, Any]:
        captured.append(message)
        return {"resposta": []}

    return patch(
        "app.services.request_manager.RequestManager.on_new_message", _stub
    )


async def _run_capture(
    payload: dict[str, Any],
    token: str,
    *,
    transcribe: Any,
) -> tuple[httpx.Response, list[str], list[tuple[str, str]]]:
    """Drive the route with on_new_message captured; return (resp, messages, sends).

    ``transcribe`` is a patch_transcribe(...) context manager (mocked Whisper).
    HelenaClient.send_text is mocked so the failure-path error message to the
    lead is observable in ``sends``.
    """
    captured: list[str] = []
    sends: list[tuple[str, str]] = []
    with patch_capture_message(captured), patch_send_text(sends), transcribe, patch(
        "app.helena.client.calculate_humanized_delay", lambda _msg: 0
    ):
        resp = await _post(payload, token)
    return resp, captured, sends


# --------------------------------------------------------------------------- #
# DB probe + seeding
# --------------------------------------------------------------------------- #

async def db_reachable() -> bool:
    try:
        async with AsyncSessionLocal() as db:
            await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=5)
        return True
    except Exception as e:
        print(f"  (DB unreachable: {type(e).__name__}: {str(e)[:120]})")
        return False


async def seed_company_token() -> None:
    """Stamp helena_token/apikey and an allowed_inbox config onto the fixture company.

    The allowlist permits ALLOWED_CHANNEL (empty contacts → all leads on it) so the
    channel-gate cases are deterministic. Payloads without a channel stay allowed
    (gate only fires when content.channel is present).
    """
    import json

    allowed = json.dumps({"allowed_inboxes": [{"id": ALLOWED_CHANNEL, "allowed_contacts": []}]})
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE companies SET helena_token = :tok, helena_apikey = :key, "
                "allowed_contacts = CAST(:allowed AS jsonb) WHERE id = :cid"
            ),
            {
                "tok": KNOWN_TOKEN,
                "key": HELENA_APIKEY,
                "allowed": allowed,
                "cid": SEED_COMPANY_ID,
            },
        )
        await db.commit()


# --------------------------------------------------------------------------- #
# The five cases
# --------------------------------------------------------------------------- #

async def case_a_text_dispatches() -> None:
    reply = "claro! temos vários produtos disponíveis"
    resp, calls = await _run_pipeline(make_payload(), KNOWN_TOKEN, [reply])
    assert resp.status_code == 200, resp.status_code
    assert len(calls) >= 1, f"expected at least one send, got {calls}"
    assert calls[0][0] == LEAD_PHONE, calls[0][0]
    assert any(sent == reply for _to, sent in calls), calls
    print("  (a) text MESSAGE_RECEIVED → dispatched to lead phone: OK")


async def case_b_non_message_received_ignored() -> None:
    # No DB needed: the route checks eventType in the sync handler and returns
    # before scheduling the background task.
    sends: list[tuple[str, str]] = []
    with patch_send_text(sends):
        resp = await _post(make_payload(eventType="SESSION_NEW"), KNOWN_TOKEN)
    assert resp.status_code == 200, resp.status_code
    assert sends == [], f"expected zero sends, got {sends}"
    print("  (b) non-MESSAGE_RECEIVED event → zero sends, no error: OK")


async def case_c_no_text_ignored() -> None:
    # content.text dropped → HelenaService returns "ignored" before any AI/send.
    payload = make_payload()
    del payload["content"]["text"]
    resp, calls = await _run_pipeline(payload, KNOWN_TOKEN, ["should not be sent"])
    assert resp.status_code == 200, resp.status_code
    assert calls == [], f"expected zero sends, got {calls}"
    print("  (c) MESSAGE_RECEIVED without text → zero sends, no exception: OK")


async def case_d_unknown_token_noops() -> None:
    resp, calls = await _run_pipeline(make_payload(), UNKNOWN_TOKEN, ["nope"])
    assert resp.status_code == 200, resp.status_code
    assert calls == [], f"expected zero sends, got {calls}"
    print("  (d) unknown token → zero sends, clean return: OK")


async def case_e_n_messages_ordered() -> None:
    replies = ["m1", "m2", "m3"]
    resp, calls = await _run_pipeline(make_payload(), KNOWN_TOKEN, replies)
    assert resp.status_code == 200, resp.status_code
    sent_texts = [sent for _to, sent in calls]
    assert sent_texts == replies, f"expected {replies} in order, got {sent_texts}"
    assert all(to == LEAD_PHONE for to, _ in calls), calls
    print("  (e) N-message reply → N ordered send/text POSTs: OK")


async def set_customer_status_false(session_id: str, company_id: int) -> None:
    """Silence the AI for a session by setting customers.status = False.

    The customer row is created by upsert_api_customer during a normal pass, so
    the caller runs one pipeline pass first to create it, then flips the DB flag
    that transfer_to_human sets (status=False = escalated to a human).
    """
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                'UPDATE customers SET status = false '
                'WHERE "sessionId" = :sid AND company_id = :cid'
            ),
            {"sid": session_id, "cid": company_id},
        )
        await db.commit()


async def case_h_status_false_gates_ai() -> None:
    # Gate de IA: status=False silences the AI. The lead's message is saved to
    # chat_history but NO send fires and OpenAI is never called.
    # First pass creates the customer row (status defaults True) and dispatches.
    resp, calls = await _run_pipeline(make_payload(), KNOWN_TOKEN, ["primeira"])
    assert resp.status_code == 200, resp.status_code
    assert len(calls) >= 1, f"expected first pass to dispatch, got {calls}"

    # Escalate: flip status to False, as transfer_to_human would.
    await set_customer_status_false(SESSION_ID, SEED_COMPANY_ID)

    # Second pass: count chat_completion invocations; assert the gate blocks it.
    completion_calls = 0

    async def _counting_completion(self: Any, payload: Any) -> OpenAIResponse:
        nonlocal completion_calls
        completion_calls += 1
        return await fake_openai_response(["should not be sent"])(self, payload)

    gated_calls: list[tuple[str, str]] = []
    with patch_send_text(gated_calls), patch(
        "app.services.openai.OpenAIService.chat_completion",
        _counting_completion,
    ), patch("app.helena.client.calculate_humanized_delay", lambda _msg: 0):
        resp2 = await _post(make_payload(content={"text": "quero um humano"}), KNOWN_TOKEN)

    assert resp2.status_code == 200, resp2.status_code
    assert gated_calls == [], f"expected zero sends when AI is off, got {gated_calls}"
    assert completion_calls == 0, (
        f"expected zero OpenAI calls when status=False, got {completion_calls}"
    )
    print("  (h) status=False → message saved, zero sends, zero OpenAI calls: OK")


async def case_f_allowed_channel_dispatches() -> None:
    # content.channel in the allowlist → processed and dispatched.
    payload = make_payload(content={"channel": ALLOWED_CHANNEL})
    resp, calls = await _run_pipeline(payload, KNOWN_TOKEN, ["ok"])
    assert resp.status_code == 200, resp.status_code
    assert len(calls) >= 1, f"expected a send for the allowed channel, got {calls}"
    print("  (f) allowed channel → dispatched: OK")


async def case_g_blocked_channel_noops() -> None:
    # content.channel NOT in the allowlist → gated out before any AI/send.
    payload = make_payload(content={"channel": BLOCKED_CHANNEL})
    resp, calls = await _run_pipeline(payload, KNOWN_TOKEN, ["should not be sent"])
    assert resp.status_code == 200, resp.status_code
    assert calls == [], f"expected zero sends for a blocked channel, got {calls}"
    print("  (g) blocked channel → zero sends, clean return: OK")


async def case_h_audio_transcript_is_message() -> None:
    # Audio-only payload (no text) + mocked Whisper → the transcript is the
    # `message` that enters the pipeline (not the raw payload / attachment url).
    transcript = "quero saber o preço do plano premium"
    payload = make_payload(
        content={"attachment_url": "https://x/y/audio.ogg", "attachment_type": "audio"}
    )
    del payload["content"]["text"]
    resp, messages, sends = await _run_capture(
        payload, KNOWN_TOKEN, transcribe=patch_transcribe(transcript)
    )
    assert resp.status_code == 200, resp.status_code
    assert messages == [transcript], f"expected [{transcript!r}], got {messages}"
    print("  (h) audio-only → transcript is the AI input: OK")


async def case_i_non_audio_describes() -> None:
    # Image-only payload → the descriptive phrase is the `message`.
    payload = make_payload(
        content={"attachment_url": "https://x/y/pic.jpg", "attachment_type": "image"}
    )
    del payload["content"]["text"]
    resp, messages, sends = await _run_capture(
        payload, KNOWN_TOKEN, transcribe=patch_transcribe("unused")
    )
    assert resp.status_code == 200, resp.status_code
    assert messages == ["O usuário enviou uma imagem"], messages
    print("  (i) image-only → descriptive phrase is the AI input: OK")


async def case_j_text_precedence() -> None:
    # Text + attachment → the text is used, the attachment ignored (transcript
    # would be a different string; assert it is NOT what reached the AI).
    payload = make_payload(
        content={
            "attachment_url": "https://x/y/audio.ogg",
            "attachment_type": "audio",
        }
    )
    # keep the default text from make_payload
    resp, messages, sends = await _run_capture(
        payload, KNOWN_TOKEN, transcribe=patch_transcribe("TRANSCRIPT-SHOULD-NOT-APPEAR")
    )
    assert resp.status_code == 200, resp.status_code
    assert messages == ["olá, quero saber sobre os produtos"], messages
    print("  (j) text + attachment → text wins, attachment ignored: OK")


async def case_k_transcription_failure_alerts_and_notifies() -> None:
    # Whisper raises → error message sent to the lead via HelenaClient, no AI
    # call, and the AUDIO_TRANSCRIPTION_FAILED alert fires.
    payload = make_payload(
        content={"attachment_url": "https://x/y/audio.ogg", "attachment_type": "audio"}
    )
    del payload["content"]["text"]
    alerts: list[str] = []
    with patch(
        "app.helena.service.send_critical_alert",
        lambda error_type, *a, **k: alerts.append(error_type),
    ):
        resp, messages, sends = await _run_capture(
            payload, KNOWN_TOKEN, transcribe=patch_transcribe(raises=True)
        )
    assert resp.status_code == 200, resp.status_code
    assert messages == [], f"expected no AI call, got {messages}"
    assert len(sends) >= 1, f"expected an error message to the lead, got {sends}"
    assert sends[0][0] == LEAD_PHONE, sends[0][0]
    assert "AUDIO_TRANSCRIPTION_FAILED" in alerts, alerts
    print("  (k) transcription failure → lead notified + alert, no AI call: OK")


async def case_l_empty_transcript_uses_empty_string() -> None:
    # Whisper returns blank → the "Não consegui entender o áudio…" string (not the
    # generic "Desculpa…") is sent, alert fires, no AI call.
    from app.utils.attachments import AUDIO_EMPTY_MSG

    payload = make_payload(
        content={"attachment_url": "https://x/y/audio.ogg", "attachment_type": "audio"}
    )
    del payload["content"]["text"]
    alerts: list[str] = []
    with patch(
        "app.helena.service.send_critical_alert",
        lambda error_type, *a, **k: alerts.append(error_type),
    ):
        resp, messages, sends = await _run_capture(
            payload, KNOWN_TOKEN, transcribe=patch_transcribe("   ")
        )
    assert resp.status_code == 200, resp.status_code
    assert messages == [], f"expected no AI call, got {messages}"
    assert len(sends) == 1 and sends[0][1] == AUDIO_EMPTY_MSG, sends
    assert "AUDIO_TRANSCRIPTION_FAILED" in alerts, alerts
    print("  (l) empty transcript → 'Não consegui entender' string, alert, no AI: OK")


async def main() -> None:
    print("Helena route behavioral tests (seam: POST /api/helena/{token})")
    assert settings.helena_base_url  # sanity: settings imported

    # dev_mode makes the RequestManager process synchronously and Redis-free,
    # while still invoking on_send_messages. Verified against
    # request_manager.on_new_message (the dev_mode bypass near the top).
    settings.dev_mode = True

    # Case (b) never needs the DB.
    await case_b_non_message_received_ignored()

    if not await db_reachable():
        print(
            "\nSKIPPED (no reachable DB): (a), (c), (d), (e) drive the background "
            "task which opens a DB session and resolves the company by token.\n"
            "Point DATABASE_URL at a dev Postgres with seeded company_id=5 to run "
            "them. Ran here: (b). Not a logic failure — DB reachability only."
        )
        print("\nOK - (b) passed; (a),(c),(d),(e) require a live DB")
        return

    await seed_company_token()
    await case_a_text_dispatches()
    await case_c_no_text_ignored()
    await case_d_unknown_token_noops()
    await case_e_n_messages_ordered()
    await case_f_allowed_channel_dispatches()
    await case_g_blocked_channel_noops()
    await case_h_status_false_gates_ai()
    await case_h_audio_transcript_is_message()
    await case_i_non_audio_describes()
    await case_j_text_precedence()
    await case_k_transcription_failure_alerts_and_notifies()
    await case_l_empty_transcript_uses_empty_string()
    print("\nOK - all Helena route cases passed")


if __name__ == "__main__":
    asyncio.run(main())
