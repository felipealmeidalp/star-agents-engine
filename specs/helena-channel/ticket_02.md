# 02 — Canal Helena ponta a ponta (webhook → IA → envio)

**What to build:** A lead sends a text message inside Helena; Helena fires a `MESSAGE_RECEIVED` webhook at the engine; the engine groups quick bursts from the same lead, runs them through the same AI pipeline that already serves Chatwoot (same tools, same context, same history), and sends the AI reply back into Helena as one or more messages with the same humanized spacing Chatwoot uses. The webhook answers immediately and does all its work in the background, so Helena never times out or retries.

**Blocked by:** 01 — Multi-tenancy do Helena (colunas, migração, lookup, settings)

**Spec:** [spec.md](./spec.md)

**Status:** ready-for-agent

## Context

This channel adds only four pieces and reuses the whole agnostic core, exactly as ADR 0001 prescribes. The new package `app/helena/` mirrors `app/chatwoot/`, plus one router registration in `main.py`.

**Core pieces reused (do not touch, do not reimplement):**

- `get_request_manager()` — the module-level `RequestManager` singleton. `ChatwootService` defines it at the top of `app/chatwoot/service.py` (lazy global). Reuse the *same* singleton by importing `get_request_manager` from `app.chatwoot.service` (one shared manager keeps locks/buffer state consistent across channels).
- `RequestManager.on_new_message(contact_id: int, message, session_id, company_id, db, on_send_messages, on_send_private_notes, dev_mode)` — the core entry point. `contact_id` is an **int** and keys the per-contact buffer + lock. `session_id` is the string that ties `customers`/`chat_history`. It returns a dict whose `"resposta"` key is the list of AI reply strings (or `None` if the message was discarded because a newer one is buffered).
- `CustomerRepository.upsert_api_customer(session_id, company_id, agent_id, sub_agent_id, fallback_agent_id, fallback_sub_agent_id, customer_context=None, custom_information_patch=None)` — creates/recovers a customer by pure `sessionId`, no Chatwoot fields. Helena reuses it as-is (ADR 0002).
- `calculate_humanized_delay(message: str) -> float` — the spacing formula, in `app/chatwoot/client.py`. Import it; do not re-derive the formula.
- `send_critical_alert(error_type, location, error, contact_id=None, company_id=None, extra=None)` — in `app/utils/alerter.py`. Fire-and-forget, never raises.
- `CompanyRepository.get_by_helena_token(token)` and the config constants (base URL / endpoint) come from **ticket 01** — assume they exist, use them, do not redefine.

**Chatwoot model to mirror, and what NOT to copy:**

- Route `app/routes/chatwoot.py` is the shape to follow for the handler: validate payload → return `{"status": "received"}` immediately → `background_tasks.add_task(process_webhook_background, ...)`. `process_webhook_background` opens its own DB session (`AsyncSessionLocal`), resolves the company by token, instantiates the service, calls `process_webhook`. Company lookup and all DB work happen in the background, never in the sync handler. **Do NOT copy** the Chatwoot retry/pool-exhaustion machinery (`_is_pool_exhaustion_error`, RabbitMQ re-enqueue, `MAX_WEBHOOK_RETRY_COUNT`), the inbox-trigger seeding, or the outgoing/private-note branches — the spec wants the handler lean. Keep a single `except Exception` in the background task that logs and fires the critical alert.
- `ChatwootService` is the shape to follow for the service, but it is fat (dev commands, attachments, follow-up, labels, assignment, AI-off gating). `HelenaService` is deliberately lean — **happy path only**. Mirror just the skeleton: instantiate `MessageBuffer` + `HelenaClient`, grab `get_request_manager()`, build the `on_send_messages` callback, call `self.request_manager.on_new_message(...)`, then send `response.get("resposta", [])` through the client. Ignore everything else.

**Incoming payload (`MESSAGE_RECEIVED`):**

```json
{
  "eventType": "MESSAGE_RECEIVED",
  "date": "...",
  "content": {
    "id": "...",
    "sessionId": "<uuid da conversa>",
    "text": "<mensagem do lead>",
    "type": "...",
    "direction": "...",
    "timestamp": "...",
    "details": { "from": "<telefone do lead>" }
  }
}
```

Pydantic ignores extra fields by default (like the Chatwoot schemas, plain `BaseModel` with optional fields) — that is the "tolerant validation" the spec asks for. Do not set `extra="forbid"`; keep every unused field optional.

**Outgoing payload (`send/text`):**

```
POST https://api.helena.run/chat/v1/send/text
Authorization: Bearer {helena_apikey}
{ "sessionId": "<uuid>", "text": "<mensagem>" }
```

Returns 200 with `{ id, sessionId, status: "QUEUED", ... }`. Rate limit: 1000 req / 2 min.

**Router registration:** `app/main.py` line ~125 imports `from app.routes import chat, chatwoot, health, meta, voe` and registers each with `app.include_router(<mod>.router, prefix="/api", tags=[...])` (lines ~127-131). Add `helena` to that import and one `app.include_router(helena.router, prefix="/api", tags=["Helena"])` line — final route is `POST /api/helena/{token}`.

## Tasks

**1. Schemas do payload (`app/helena/schemas.py`)**

- [ ] Create `app/helena/schemas.py` with plain Pydantic `BaseModel` classes for the `MESSAGE_RECEIVED` payload: an envelope model with `eventType: str`, `date`, and `content`; a content model with `sessionId: str`, `text: str | None = None`, and `details` (a model with `from`); every other field (`id`, `type`, `direction`, `timestamp`) optional. Do not use `extra="forbid"` — extra fields must be ignored so Helena adding fields never breaks the parser.
- [ ] Because `from` is a Python keyword, map it with a field alias (e.g. `from_: str | None = Field(default=None, alias="from")`) and enable `populate_by_name`/alias so `details.from` in JSON parses.

**2. HelenaClient (`app/helena/client.py`)**

- [ ] Create `HelenaClient` (httpx async) with `send_text(session_id, text, apikey)` doing a single `POST {base_url}/chat/v1/send/text` with header `Authorization: Bearer {apikey}` and body `{ "sessionId": session_id, "text": text }`, using the base URL / endpoint constants from ticket 01 in `config.py`. Mirror `ChatwootClient.send_message`'s structure (own httpx `AsyncClient`, `raise_for_status`, return `response.json()`).
- [ ] Add `send_messages(session_id, messages, apikey)` copying `ChatwootClient.send_messages` behavior: iterate the list, first message sent with no delay, each subsequent message preceded by `await asyncio.sleep(calculate_humanized_delay(msg))`, then `send_text`. Import `calculate_humanized_delay` from `app.chatwoot.client` — do not reimplement the formula. No `delayTyping`.
- [ ] On a send failure inside `send_messages`, call `send_critical_alert("HELENA_SEND_FAILED", "helena/client.py:send_messages", e, extra=...)` and keep going, matching how `ChatwootClient.send_messages` catches per-message and alerts with `CHATWOOT_CLIENT_SEND_FAILED`.
- [ ] Add a `ponytail:` comment on the `send_text` body noting the open question to confirm on the first real send: that `send/text` accepts replying with only `sessionId` (no `to`); if Helena requires `to`, fall back to the lead phone from `details.from`.

**3. HelenaService (`app/helena/service.py`)**

- [ ] Create `HelenaService(db)` mirroring the lean skeleton of `ChatwootService.__init__`: hold the db session, `CustomerRepository`, `MessageBuffer()`, `HelenaClient()`, and `self.request_manager = get_request_manager()` (imported from `app.chatwoot.service` so the singleton is shared). No dev commands, no follow-up, no labels.
- [ ] Add a single private helper (function or method) that derives the stable integer buffer/lock key from `content.sessionId` (a UUID): hash the string and take the first bytes as an int that fits in BigInteger (e.g. `int.from_bytes(hashlib.sha1(session_id.encode()).digest()[:8], "big")`). This is the `contact_id: int` required by `on_new_message`. See ADR 0001.
- [ ] Implement `async process_webhook(self, payload, company)` (happy path only): read `session_id = payload.content.sessionId` and `text = payload.content.text`; if `text` is empty/None, return early without processing (message with no text is ignored silently); read `helena_apikey` off `company` into a local var before any await (avoid SQLAlchemy lazy-load outside the greenlet, as `ChatwootService.process_webhook` does with `cw_apikey`).
- [ ] In `process_webhook`, upsert the customer via `self.customer_repo.upsert_api_customer(session_id=session_id, company_id=company.id, agent_id=None, sub_agent_id=None, fallback_agent_id=company.standard_agent_id, fallback_sub_agent_id=company.standard_sub_agent_id, custom_information_patch={"phone": payload.content.details.from_})`. `Customer.sessionId` becomes `content.sessionId` (ADR 0002); the lead phone is stored as metadata, not a key.
- [ ] In `process_webhook`, define an `async def on_send_messages(messages: list[str])` callback that calls `self.client.send_messages(session_id, messages, helena_apikey)`, then call `response = await self.request_manager.on_new_message(contact_id=<derived int>, message=text, session_id=session_id, company_id=company.id, db=self.db, on_send_messages=on_send_messages, on_send_private_notes=None, dev_mode=False)`.
- [ ] After `on_new_message` returns, if `response` is `None` (message discarded — newer one buffered) return without sending; otherwise send `response.get("resposta", [])` through `self.client.send_messages(session_id, messages, helena_apikey)` (same path the callback uses), matching how `ChatwootService` sends `response.get("resposta", [])` at the end.

**4. Rota + registro no main (`app/routes/helena.py`, `app/main.py`)**

- [ ] Create `app/routes/helena.py` with `router = APIRouter()` and `@router.post("/helena/{token}")` handler that: parses the JSON body, validates it into the envelope schema tolerantly, and returns `{"status": "received"}` immediately. If `eventType != "MESSAGE_RECEIVED"`, return `{"status": "received"}` (or an `ignored` status) without scheduling anything.
- [ ] For a `MESSAGE_RECEIVED` event, schedule `background_tasks.add_task(process_webhook_background, payload=..., token=token)` and return `{"status": "received"}` — no DB work in the sync handler.
- [ ] Implement `process_webhook_background(payload, token)` in `app/routes/helena.py`: open its own session with `async with AsyncSessionLocal() as db`, resolve `company = await CompanyRepository(db).get_by_helena_token(token)`; if `None`, log and return (abort silently, no exception); otherwise call `await HelenaService(db).process_webhook(payload, company)`. Wrap the body in one `try/except Exception` that logs and fires `send_critical_alert(...)` (keep the critical alert on error; skip the Chatwoot pool-exhaustion/RabbitMQ retry logic).
- [ ] In `app/main.py`, add `helena` to `from app.routes import chat, chatwoot, health, meta, voe` and add `app.include_router(helena.router, prefix="/api", tags=["Helena"])` alongside the other routers — final route `POST /api/helena/{token}`.
- [ ] Add a `ponytail:` comment where the MVP processes every `MESSAGE_RECEIVED` without filtering on `content.direction`, noting that if spurious events appear (AI replying to itself), a direction filter should be reintroduced once a genuine lead-message payload confirms the right value (see Further Notes).

## Acceptance criteria

- [ ] A `POST /api/helena/{token}` with a `MESSAGE_RECEIVED` text payload returns `{"status": "received"}` immediately, and in the background results in one or more `POST https://api.helena.run/chat/v1/send/text` calls carrying the AI reply messages (from `response["resposta"]`) in order, each with `sessionId` = `content.sessionId`.
- [ ] A reply broken into N messages produces N `send/text` POSTs in order: the first with no delay, each following one preceded by `calculate_humanized_delay` sleep.
- [ ] An event whose `eventType != "MESSAGE_RECEIVED"` is answered with received and triggers no send and no exception.
- [ ] A `MESSAGE_RECEIVED` with empty/missing `content.text` is ignored silently (no AI call, no send).
- [ ] A `{token}` that resolves to no company aborts the background silently (log only), no send, no exception.
- [ ] The customer row is created/updated by `upsert_api_customer` with `sessionId = content.sessionId`, agent/sub-agent falling back to the company's `standard_agent_id`/`standard_sub_agent_id`, and the lead phone from `details.from` stored in `custom_information`.
- [ ] A send failure to Helena fires `send_critical_alert("HELENA_SEND_FAILED", ...)`.

## Out of scope

Deliberately excluded (see spec Out of Scope): human escalation and any AI on/off gate (`status=False`); Helena session events (`SESSION_NEW`/`SESSION_UPDATE`/`SESSION_COMPLETE`) and human-takeover detection; dev commands (`#resetar`, `#mudar_agente`); scheduled follow-up (RabbitMQ) on Helena; audio transcription and attachment handling (no-text messages are ignored); labels / assignment / conversation attribution; native `delayTyping` (we use local `sleep`); webhook dedup by message `id`; webhook signature verification (security is the URL token); perpetual per-phone history (history is per Helena session, ADR 0002); the Chatwoot pool-exhaustion/RabbitMQ retry machinery. And: **tests — they live in ticket 03.**
