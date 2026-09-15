# 03 — Testes de comportamento externo da rota Helena

**What to build:** Behavioral tests that pin the Helena channel's external contract at a single seam — the route `POST /api/helena/{token}`. Feed a real `MESSAGE_RECEIVED` webhook payload in, and assert what comes out: that `HelenaClient` was called to POST the AI's reply messages to Helena's `send/text`, in the right order. The whole path between webhook-in and client-out runs for real; only the two true external edges — `HelenaClient` (the outbound httpx call) and OpenAI (`OpenAIService.chat_completion`) — are mocked. The tests prove the five cases from the spec's Testing Decisions section: text message dispatches, non-`MESSAGE_RECEIVED` is ignored, textless message is ignored, unknown token no-ops without raising, and an N-message reply produces N ordered POSTs.

**Blocked by:** 02 — Canal Helena ponta a ponta (webhook → IA → envio)

**Spec:** [spec.md](./spec.md)

**Status:** ready-for-agent

## Context

Read the spec's **Testing Decisions** section and both ADRs (`adr/0001-canais-reusam-nucleo-agnostico.md`, `adr/0002-sessionid-nativo-do-helena.md`) before starting. The core rule: test the channel's **external behavior** — what enters via the webhook and what leaves via the client — never the núcleo's internals (buffer, `ConversationTurn`), which are already covered elsewhere.

**No prior art to copy — build the route-test infra from scratch.** The spec says "the Chatwoot webhook tests are the direct model to follow." Those tests **do not exist in this repo.** The entire `tests/` directory contains exactly one file, `tests/test_categories_tool.py`, and it is not a webhook test at all — it is a standalone `asyncio.run(main())` script full of `assert`s that hits the real database. There is no `conftest.py`, no `pytest.ini`, no `[tool.pytest.ini_options]` in `pyproject.toml`, no `tox.ini`, and `pytest`/`pytest-asyncio` are not even listed in `pyproject.toml`'s dev dependencies (only `ruff` and `mypy` are). Do not waste time hunting for a Chatwoot test to clone — there isn't one. This ticket stands up the FastAPI route-test harness (test client, OpenAI mock, `HelenaClient` mock) for the first time.

Use `tests/test_categories_tool.py` only as a **style reference**: it shows the house conventions — `sys.path.insert(0, ".")`, `asyncio`, importing `AsyncSessionLocal` from `app.db.database`, `assert`-based checks. Since no pytest config exists, decide the harness shape and state it in the file: either (a) add `pytest` + `pytest-asyncio` to `pyproject.toml` dev deps and a minimal `[tool.pytest.ini_options]` with `asyncio_mode = "auto"` and a `conftest.py`, or (b) follow the existing repo convention and write a self-contained `asyncio.run(main())` script with `assert`s (no framework, matching `test_categories_tool.py`). Option (b) needs zero new dependencies and matches what's already there — prefer it unless a framework is explicitly wanted.

**How to reach the route.** The app is assembled in `app/main.py` (`app = FastAPI(...)`, routers included under prefix `/api`; ticket 02 adds `app.include_router(helena.router, prefix="/api")`, so the final path is `POST /api/helena/{token}`). Drive it in-process with `httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")` — `httpx` is already a dependency, no `TestClient`/Starlette test dep needed. **Avoid the lifespan** (it connects RabbitMQ and pings the DB at startup): either construct the transport so `lifespan` doesn't run, or don't trigger it. RabbitMQ failure at startup is non-fatal (logged, swallowed) but the connection attempt is still noise the test should skip.

**Startup / external services to sidestep.** `app/main.py` lifespan touches the DB (`SELECT 1`) and RabbitMQ (follow-up + webhook-retry consumers). The Helena reply path never uses RabbitMQ (follow-up is out of scope for the channel), so RabbitMQ just needs to not be started. The Redis buffer (`app/chatwoot/buffer.py`, via `MessageBuffer`) is the one to design around: set **`settings.dev_mode = True`** in the test. The `RequestManager` has a `dev_mode` bypass (`app/services/request_manager.py`, `on_new_message`, near the top) that, when `settings.dev_mode` is true, calls `process_chat(...)` **synchronously and returns its result** — skipping the Redis buffer and the background-task cancellation entirely, while still invoking the `on_send_messages` callback. That makes the reply dispatch deterministic and Redis-free, which is exactly what these tests need. The DB is still real: the pipeline writes `chat_history`, so the test needs a reachable DB the same way `test_categories_tool.py` uses `AsyncSessionLocal` — run against the dev/test Postgres, or, if isolating the DB is wanted, that's an extra step, but matching the existing script's real-DB approach is the lazy path. A company with a known `helena_token` must exist (seeded by the test or already present) so the token resolves.

**Where to mock OpenAI.** The single call point is `OpenAIService.chat_completion` (`app/services/openai.py`, class `OpenAIService`), invoked from `app/services/chat_handler.py`. `chat_completion` returns an `OpenAIResponse` whose `content` the handler parses with `json.loads(content).get("resposta")` — so to produce a reply broken into N messages, the mock must return a response whose content is the JSON string `{"resposta": ["msg 1", "msg 2", ...]}` with **no tool_calls** (`has_tool_calls` false), so the pipeline takes the plain-text finish path and dispatches those N messages. Mock the method (patch `OpenAIService.chat_completion`, or the instance's method) rather than the OpenAI SDK internals — it's the cleanest, single seam. `test_categories_tool.py` does not mock OpenAI (it exercises tools against the DB), so there's no existing example; this is the pattern to establish.

**Where to mock HelenaClient.** Patch `HelenaClient` (`app/helena/client.py`, created in ticket 02) so no real httpx call leaves the process. The observable assertion is on the send method the `HelenaService` callback calls — `send_messages(sessionId, messages, helena_apikey)` (which internally loops `send_text` per message with the humanized delay). Mock at whichever of `send_text`/`send_messages` gives the cleanest per-message call record; the spec's ordering assertion ("N POSTs in order") is naturally expressed by patching `send_text` and asserting the sequence of `text=` arguments, or by asserting `send_messages` received the ordered list. Keep the mock's delay a no-op (patch/skip the `calculate_humanized_delay` sleep) so tests don't actually wait seconds between messages.

**Fixture payload.** There is **no captured `MESSAGE_RECEIVED` payload in the repo** — `docs/n8n_workflow.json` contains nothing Helena-related, and no `.json`/`.md` fixture exists. Build the fixture inline from the shape the spec's "Estrutura" section describes: envelope `{ eventType, date, content }` where `content` carries `id`, `sessionId` (a UUID — the conversation key), `text`, `type`, `direction`, `timestamp`, and `details.from` (the lead's phone). One valid `MESSAGE_RECEIVED` dict reused across cases, with per-case tweaks (drop `text` for case c, change `eventType` for case b).

Domain terms, exact: `POST /api/helena/{token}`, `MESSAGE_RECEIVED`, `HelenaClient`, `send/text`, `sessionId`, `helena_token`, `helena_apikey`.

## Tasks

**1. Test harness and fixtures**

- [x] Stand up the route-test file under `tests/` (e.g. `tests/test_helena_route.py`), following the style of `tests/test_categories_tool.py` (`sys.path.insert(0, ".")`, `asyncio`, `assert`-based) since no pytest config exists — or, if a framework is wanted, add `pytest`/`pytest-asyncio` + minimal `[tool.pytest.ini_options]` (`asyncio_mode = "auto"`) + `conftest.py` and state the choice at the top of the file.
- [x] Build an in-process client against the app: `httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")` importing `app` from `app.main`, constructed so the lifespan (RabbitMQ/DB startup) does not run.
- [x] Set `settings.dev_mode = True` in the test so the `RequestManager` dev_mode bypass processes synchronously and skips Redis/buffer, while still calling `on_send_messages`.
- [x] Write one reusable `MESSAGE_RECEIVED` payload dict from the spec's Estrutura shape (`{eventType, date, content:{id, sessionId, text, type, direction, timestamp, details:{from}}}`); note in a comment that no real captured payload existed so this was built from the spec.
- [x] Ensure a company with a known `helena_token` exists (seed it or rely on the dev DB), and provide the OpenAI and `HelenaClient` mocks: patch `OpenAIService.chat_completion` to return an `OpenAIResponse` with content `{"resposta": [...]}` and no tool_calls; patch `HelenaClient` (`send_text`/`send_messages`) to record calls and no-op the delay.

**2. Test cases (the five from the spec)**

- [x] **(a) Text `MESSAGE_RECEIVED` dispatches a send** — POST a valid text payload to `POST /api/helena/{token}` with a known token; assert `HelenaClient` was called to send the AI reply to `send/text` (at least one POST with the mocked reply text, to the payload's `sessionId`).
- [x] **(b) Non-`MESSAGE_RECEIVED` event is ignored** — POST a payload with `eventType` other than `MESSAGE_RECEIVED`; assert `HelenaClient` was **not** called (zero sends) and the route responds without error.
- [x] **(c) Message without text is ignored** — POST a `MESSAGE_RECEIVED` whose `content.text` is missing/empty; assert `HelenaClient` was **not** called (zero sends) and no exception.
- [x] **(d) Token that resolves to no company no-ops** — POST a valid text payload to a token that matches no company; assert `HelenaClient` was **not** called and the route returns cleanly with no exception raised.
- [x] **(e) Reply of N messages produces N ordered POSTs** — mock OpenAI to return `{"resposta": ["m1","m2","m3"]}`; assert `HelenaClient` sent exactly N messages to `send/text` in that exact order.

## Acceptance criteria

- [x] All five cases (a)–(e) run and pass, driving the real path from `POST /api/helena/{token}` through to the mocked `HelenaClient`, with only `HelenaClient` and `OpenAIService.chat_completion` mocked.
- [x] The assertions are on the channel's **external behavior** — the calls made to `HelenaClient` (count, target `sessionId`, message text, order) — and never on núcleo internals (buffer contents, `ConversationTurn` shape).
- [x] The tests do not perform any real network I/O to Helena or OpenAI, and do not require Redis or RabbitMQ to be running (dev_mode bypass covers Redis; RabbitMQ is not started).
- [x] The file states its harness choice (framework vs. `asyncio.run` script) and notes that the `MESSAGE_RECEIVED` fixture was built from the spec because no captured payload existed.

## Out of scope

- Testing núcleo internals — the buffer, `MessageBuffer` grouping, `ConversationTurn` shape, `RequestManager` cancellation logic — all already covered by the core.
- Any case beyond the five listed (a)–(e); do not invent additional scenarios (e.g. direction filtering, dedup, session events — all explicitly out of the MVP).
- Real integration tests against the live Helena API or a live OpenAI call — both edges are mocked.
- The multi-tenant lookup, migration, or `HelenaClient`/`HelenaService`/route implementation themselves — delivered by tickets 01 and 02; this ticket only tests the behavior at the route seam.
