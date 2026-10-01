# 02 — Webhook do Instagram autenticado com resolução de Company

**What to build:** The engine gets its own Instagram webhook, `GET`/`POST /api/instagram`, that the owner can register in the Meta app panel. Meta's verification handshake is answered (verify token checked, challenge echoed). Every event must carry a valid HMAC-SHA256 signature of the raw body; a missing signature, a wrong signature, or an unconfigured secret all reject the call before anything is parsed — nobody can forge a comment and make a Company send a DM. A valid event is acknowledged to Meta immediately and processed in the background with its own DB session: the batch is parsed item by item, non-comment events are ignored without error, the Company is resolved by the Instagram account id of each entry, and an account no Company has registered is discarded with a log line only. Each first-level comment of a known Company arrives, parsed, at one hand-off function that for now only logs — ticket 03 fills it in. A Company with no Instagram account id configured sees no change at all.

**Blocked by:** None — can start immediately.

**Spec:** [spec.md](./spec.md)

**Status:** ready-for-agent

## Context

**Domain terms** (from the glossary, use them exactly): Company, Customer, sessionId, Comentário elegível, DM de boas-vindas, Boas-vindas pendente, Origem do comentário, Resposta pública, Helena, Canal. This feature is **not** a new Canal (ADR 0001): the engine only listens to comments and, in later tickets, makes outbound calls to Meta; the conversation stays in the Helena channel, which is already shipped (`helena-channel` and `helena-channel-parity` specs are built — treat them as existing code).

**What must not be touched.** The existing Meta route (`GET`/`POST /api/meta` in `app.routes.meta`) is the WhatsApp → Chatwoot proxy and belongs to a different Meta app. Do not edit it, its settings (`meta_app_secret`, `meta_verify_token`, `meta_dedup_ttl`, `meta_forward_timeout`, `meta_cache_ttl`), or `app.services.meta_webhook`. The new route is a separate module with separate settings.

**Existing pieces to reuse (do not reimplement):**

- `verify_signature(payload: bytes, signature_header: str, app_secret: str) -> bool` in `app.services.meta_webhook` — validates the `X-Hub-Signature-256` header (`sha256=<hex>`) with `hmac.compare_digest`. Import it as is. **Pitfall:** called with an empty secret it still computes a valid HMAC with an empty key, which an attacker can compute too. The existing Meta route skips verification when the secret is empty; the new route must do the opposite and reject when the secret is empty, *before* calling the helper.
- The handshake shape of `meta_webhook_verify` in `app.routes.meta`: reads `hub.verify_token` and `hub.challenge` from `request.query_params`, returns the challenge as `text/plain`, or `Response(content="Forbidden", status_code=403)`. **Pitfall:** that route accepts everything when the verify token is not configured, and both the missing query param and the unset setting default to `""`. The new route must reject when the setting is empty.
- The background pattern of `process_webhook_background` in `app.routes.helena`: the handler does no DB work, schedules the function with `BackgroundTasks.add_task`, the function opens its own session with `async with AsyncSessionLocal() as db` (from `app.db.database`), resolves the Company through `CompanyRepository(db)`, aborts with a log only when no Company matches, and wraps the work in an `except Exception` that calls `logger.exception` and `send_critical_alert(error_type, location, error, contact_id=None, company_id=None, extra=None)` from `app.utils.alerter`.
- `CompanyRepository.get_by_cw_account_id` in `app.repositories.company` — the plain `select(Company).where(...)` + `scalar_one_or_none()` lookup to mirror.
- Company columns in `app.models.tables`: `helena_token` shows the `unique=True, index=True` declaration; `helena_apikey` shows a plain nullable `String`. `String` and `Text` are already imported there.
- Settings in `app.config`: one `Settings` class, the `meta_app_secret: str = ""` / `meta_verify_token: str = ""` pair is the declaration style to copy. `.env.example` documents each setting as a commented block.
- Router registration in `app.main`: `from app.routes import chat, chatwoot, health, helena, meta, products, voe`, then one `app.include_router(<module>.router, prefix="/api", tags=[...])` per module.
- Test prior art: `tests/test_helena_route.py` (self-contained `asyncio.run(main())` script with plain `assert`s, no pytest; drives the app in process through `httpx.ASGITransport(app=app)` so the lifespan never runs; sets placeholder `DATABASE_URL`/`API_KEY` with `os.environ.setdefault` before importing the app; `db_reachable()` probe so DB-dependent cases skip with a clear message instead of passing falsely; stamps test values onto the seeded Company `id=5`) and `tests/test_products_sync_route.py` (patches the names the route module imported, `patch.object(settings, ...)` for settings).
- **Why tests see background work synchronously:** `httpx.ASGITransport` awaits the whole ASGI call, so a `BackgroundTasks` task has finished by the time `client.post(...)` returns. That is what the Helena test relies on. `settings.dev_mode = True` in that test only makes `RequestManager` run synchronously and without Redis; this ticket does not touch `RequestManager`, so the new test does not need `dev_mode`.

**Decisions already made for this ticket (names follow the existing `helena_*` / `meta_*` patterns):**

| What | Name |
|---|---|
| Route module | `app.routes.instagram`, `router = APIRouter()`, paths `GET /instagram` and `POST /instagram`, registered with prefix `/api` and tag `Instagram` → `GET`/`POST /api/instagram` |
| Global settings | `instagram_app_secret: str = ""`, `instagram_verify_token: str = ""` (env `INSTAGRAM_APP_SECRET`, `INSTAGRAM_VERIFY_TOKEN`) |
| Company columns | `instagram_account_id` (`String`, nullable, unique, indexed), `instagram_access_token` (`String`, nullable), `instagram_public_reply` (`Text`, nullable — holds the Resposta pública) |
| Unique index | `ix_companies_instagram_account_id` |
| Repository lookup | `CompanyRepository.get_by_instagram_account_id(instagram_account_id: str) -> Company \| None` |
| Service module | `app.services.instagram_welcome`, holding the parsed-comment model `InstagramComment`, the parser `parse_comments`, and the hand-off `handle_comment` |
| Migration | revision `010`, `down_revision = "009"`, file `20261001_000000_010_add_instagram_columns_to_companies.py` |
| Test | `tests/test_instagram_route.py` |

The current migration head is `009` (`20260929_000000_009_add_reasoning_effort_columns.py`); its raw-SQL idempotent style (`op.execute("ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...")`, `DROP COLUMN IF EXISTS` in the downgrade) is the one to copy. This ticket adds no Postgres enum (under asyncpg, enum creation in migrations breaks with `DuplicateObjectError`; the spec keeps all state as plain text, and the welcome-record table is not part of this ticket anyway).

The service module lives under `app.services` and not in a channel package like `app.helena`, because the feature is not a Canal. One module is enough for this ticket; ticket 03 grows it.

**Event shape.** This is the shape Meta documents for comment webhooks; no real event has been captured yet (ticket 01 does that in parallel and may adjust field names):

```json
{
  "object": "instagram",
  "entry": [
    {
      "id": "<Instagram account id of the Company>",
      "time": 1759312800,
      "changes": [
        {
          "field": "comments",
          "value": {
            "id": "<comment id>",
            "parent_id": "<parent comment id, only on replies>",
            "text": "<comment text>",
            "from": { "id": "<author id>", "username": "<author username>" },
            "media": { "id": "<post id>", "media_product_type": "FEED" }
          }
        }
      ]
    }
  ]
}
```

Meta uses two names for the comment id depending on the login type: `id` and `comment_id`. The parser accepts either. `parent_id` is optional. Entries of other products (for example messaging events, which carry `messaging` instead of `changes`) and changes whose `field` is not `comments` are non-comment events.

**Parsed comment handed to ticket 03** — `InstagramComment`, a plain Pydantic `BaseModel`, all ids as strings:

```python
class InstagramComment(BaseModel):
    comment_id: str
    media_id: str
    author_id: str
    author_username: str | None = None
    text: str = ""
    parent_id: str | None = None
```

**Division of the eligibility rule.** This ticket only drops replies to other comments (a comment with `parent_id`), so that what reaches the hand-off is always a first-level comment. The other two conditions of a Comentário elegível — the author is not the Company's own account, and the author has not been welcomed on that post yet — belong to ticket 03, which is why `author_id` and `media_id` are passed through untouched. Redelivery of the same event by Meta is also handled in 03 (by the reservation), not here.

**Open point owned by ticket 01:** which secret from the Meta panel signs the events (the app secret or the Instagram product secret) is unconfirmed. Whichever it is, its value goes in `instagram_app_secret`; no code depends on the answer.

**Database risk.** The local `.env` points at the production database. Alembic reads `settings.database_url`, so running the migration locally applies it in production. The same is true of any test that writes to the database.

## Tasks

**1. Global settings**

- [x] In the `Settings` class of `app.config`, add a block for this feature with `instagram_app_secret: str = ""` and `instagram_verify_token: str = ""`, declared like the `meta_app_secret` / `meta_verify_token` pair. Do not reuse or rename the `meta_*` settings.
- [x] In `.env.example`, add a commented block for `INSTAGRAM_APP_SECRET` and `INSTAGRAM_VERIFY_TOKEN` in the same style as the "Meta WhatsApp webhook" block, stating that they belong to the Instagram comments app and are not the WhatsApp proxy values.

**2. Company columns and migration 010**

- [x] In the `Company` class of `app.models.tables`, add an "Instagram" block after the Helena block with `instagram_account_id` declared like `helena_token` but as `String` (`Mapped[Optional[str]]`, `nullable=True, unique=True, index=True`), `instagram_access_token` declared like `helena_apikey` (`String`, nullable), and `instagram_public_reply` as `Text`, nullable.
- [x] Create the migration file `20261001_000000_010_add_instagram_columns_to_companies.py` in the migrations versions folder with `revision = "010"`, `down_revision = "009"`, `branch_labels` and `depends_on` set to `None`, and the header layout of revision 009.
- [x] In `upgrade()`, use `op.execute` with idempotent raw SQL, as revision 009 does: `ALTER TABLE companies ADD COLUMN IF NOT EXISTS` for `instagram_account_id VARCHAR`, `instagram_access_token VARCHAR` and `instagram_public_reply TEXT`, then `CREATE UNIQUE INDEX IF NOT EXISTS ix_companies_instagram_account_id ON companies (instagram_account_id)`.
- [x] In `downgrade()`, `DROP INDEX IF EXISTS ix_companies_instagram_account_id`, then `DROP COLUMN IF EXISTS` for the three columns.
- [x] Do **not** run `alembic upgrade` (or any other command that writes to the database) on your own: the local `.env` points at the production database. Deliver the migration file, tell the owner it is ready, and run it only after the owner's explicit go.

**3. Repository lookup**

- [x] In `CompanyRepository` (`app.repositories.company`), add `async def get_by_instagram_account_id(self, instagram_account_id: str) -> Company | None`, mirroring `get_by_cw_account_id`: `select(Company).where(Company.instagram_account_id == instagram_account_id)` and `scalar_one_or_none()`.

**4. Parser and hand-off (`app.services.instagram_welcome`)**

- [x] Create the module `app.services.instagram_welcome` with the `InstagramComment` model exactly as given in Context.
- [x] Add `parse_comments(body: dict) -> list[tuple[str, InstagramComment]]`, returning one `(instagram_account_id, comment)` pair per comment found, in payload order. It walks `body.get("entry", [])` and each entry's `changes` (absent `changes` counts as empty), using plain dict access — no alias or validator machinery.
- [x] In `parse_comments`, skip any change whose `field` is not `"comments"` without raising.
- [x] In `parse_comments`, take the account id from the entry's `id`, the comment id from `value["id"]` or, when absent, `value["comment_id"]`, the post id from `value["media"]["id"]`, the author from `value["from"]["id"]` and `value["from"]["username"]`, the text from `value["text"]` (empty string when absent) and the parent from `value["parent_id"]` (`None` when absent). Convert every id to `str`.
- [x] In `parse_comments`, when an item is malformed (entry without `id`, or comment without comment id, post id or author id, or any unexpected type), log a warning and continue with the next item: one bad item never drops the rest of the batch and never raises.
- [x] Add the hand-off `async def handle_comment(db: AsyncSession, company: Company, comment: InstagramComment) -> None`. In this ticket its whole body is one `logger.info` with the Company id, the comment id and the post id. Do not log the comment text, the author username or the access token. No class, no interface, no placeholder branches — ticket 03 replaces the body.

**5. Route (`app.routes.instagram`)**

- [x] Create the module `app.routes.instagram` with `router = APIRouter()` and a module logger, like `app.routes.helena`.
- [x] Add the handshake `GET /instagram`: read `hub.verify_token` and `hub.challenge` from `request.query_params`. If `settings.instagram_verify_token` is empty or differs from the received token, log a warning and return `Response(content="Forbidden", status_code=403)`. Otherwise return the challenge as `Response(content=challenge, media_type="text/plain")`.
- [x] Add the event handler `POST /instagram` taking `request: Request` and `background_tasks: BackgroundTasks`. Read the raw body with `await request.body()` first.
- [x] In the event handler, if `settings.instagram_app_secret` is empty, log an error and return 403 without reading anything else (fail closed).
- [x] In the event handler, read the `X-Hub-Signature-256` header (default `""`) and call the existing `verify_signature(raw_body, signature, settings.instagram_app_secret)` imported from `app.services.meta_webhook`. On `False` (covers both missing and wrong signature), log a warning and return 403. Never log the secret or the received signature.
- [x] In the event handler, only after the signature passes, parse the raw body with `json.loads`. If it is not valid JSON or not a JSON object, log a warning and return `{"status": "ignored"}` with status 200 — a signed but unparseable body is acknowledged, not retried.
- [x] In the event handler, schedule `background_tasks.add_task(process_webhook_background, body)` and return `{"status": "received"}`. No parsing of the batch and no DB access in the handler.
- [x] Add `async def process_webhook_background(body: dict) -> None`, following `process_webhook_background` in `app.routes.helena`. It calls `parse_comments(body)`; if the result is empty, it logs at info level that the event has no comments and returns without opening a DB session.
- [x] In `process_webhook_background`, open one session with `async with AsyncSessionLocal() as db` and process the pairs one by one, in order. For each pair: if the comment has a `parent_id`, log at info level and skip it; otherwise resolve the Company with `CompanyRepository(db).get_by_instagram_account_id(account_id)`; if it is `None`, log a warning with the account id and skip it (no exception, no alert); otherwise `await handle_comment(db, company, comment)`.
- [x] In `process_webhook_background`, wrap the processing of each pair in its own `try/except Exception` that calls `logger.exception` and `send_critical_alert("INSTAGRAM_WEBHOOK_UNHANDLED_ERROR", "instagram.py:process_webhook_background", e, extra=f"account={account_id} comment={comment.comment_id}")`, then continues with the next pair, so one failing comment never loses the others. Wrap the whole function body in an outer `try/except Exception` with the same log and alert (without the comment in `extra`) for failures outside the loop.
- [x] In `app.main`, add `instagram` to the `from app.routes import ...` line and add `app.include_router(instagram.router, prefix="/api", tags=["Instagram"])` next to the other routers.

**6. Tests (`tests/test_instagram_route.py`)**

- [x] Create `tests/test_instagram_route.py` in the house style of `tests/test_helena_route.py`: `asyncio.run(main())`, plain `assert`s, placeholder `DATABASE_URL`/`API_KEY` set with `os.environ.setdefault` before importing the app, requests through `httpx.ASGITransport(app=app)`, one printed line per passing case. No pytest, no new dependency.
- [x] Add a helper that posts an event signed the way Meta does: serialize the payload to bytes once, compute `"sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()`, and send those same bytes with `content=raw` and the `X-Hub-Signature-256` header (not `json=`, which would re-serialize and break the signature). Set the settings per case with `patch.object(settings, "instagram_app_secret", ...)` / `patch.object(settings, "instagram_verify_token", ...)`.
- [x] Observe the hand-off by patching the name the route imported (`app.routes.instagram.handle_comment`) with an async recorder that stores `(company.id, comment)`; observe alerts by patching `app.routes.instagram.send_critical_alert` with a recorder.
- [x] Case, no DB: handshake with the right `hub.verify_token` returns 200 and the body is exactly the `hub.challenge` sent.
- [x] Case, no DB: handshake with a wrong `hub.verify_token` returns 403.
- [x] Case, no DB: handshake with `instagram_verify_token` unset and no `hub.verify_token` param returns 403.
- [x] Case, no DB: event without the signature header returns 403 and the hand-off is never called.
- [x] Case, no DB: event with a signature computed with another secret returns 403 and the hand-off is never called.
- [x] Case, no DB: with `instagram_app_secret` unset, an event signed with the empty string as secret returns 403 and the hand-off is never called.
- [x] Case, no DB: a validly signed non-comment event (one change with `field` other than `comments`, plus one entry with `messaging` and no `changes`) returns 200, with zero hand-off calls and zero alerts.
- [x] Case, no DB: a validly signed reply to another comment (`parent_id` present) returns 200 with zero hand-off calls.
- [x] Reuse the `db_reachable()` approach of the Helena test: when the database is unreachable, print which cases were skipped and why, and exit successfully having run the no-DB cases.
- [x] For the DB cases, stamp a fixed fake account id (for example `test-ig-account-0001`) onto the seeded Company `id=5` with an `UPDATE companies SET instagram_account_id = ...`, and set it back to `NULL` in a `finally` — the column is unique and the database behind the local `.env` is production. These cases need migration 010 applied, so run them only after the owner has approved and applied it.
- [x] Case, DB: a validly signed first-level comment for the stamped account, using `id` as the comment id name, returns 200 and the hand-off is called once with Company `5` and an `InstagramComment` carrying the comment id, post id, author id, author username and text from the payload, with `parent_id` `None`.
- [x] Case, DB: the same event using `comment_id` instead of `id` reaches the hand-off with the same `comment_id`.
- [x] Case, DB: a validly signed comment for an account id no Company has returns 200 with zero hand-off calls, zero alerts and no exception.
- [x] Case, DB: a batch with two first-level comments for the stamped account and one malformed comment between them (no `media`) returns 200 and the hand-off is called exactly twice, in payload order.

**7. Documentation**

- [ ] In the file reference for routes (`routes.md`), add an `instagram.py` section in the format of the `meta.py` section: the handshake, the mandatory signature and the three rejection causes, the immediate acknowledgement, and the background pipeline (parse → drop replies → resolve Company by `instagram_account_id` → hand-off).
- [ ] In the file reference for services (`services.md`), add an `instagram_welcome.py` section describing `InstagramComment`, `parse_comments` and `handle_comment` as the hand-off that ticket 03 fills in.
- [ ] In the file reference for repositories (`repositories.md`), add `get_by_instagram_account_id(instagram_account_id)` to the `CompanyRepository` methods table.
- [ ] In the file reference for models (`models.md`), add the `instagram_*` columns to the `Company` row.
- [ ] In the file reference index (`app.md`), add the two new settings to the `config.py` section and the Instagram router to the routers listed for `main.py`.
- [ ] In the database schema documentation, add the three columns and the unique index `ix_companies_instagram_account_id` to the `companies` section, stating that `instagram_account_id` is the key that resolves the Company for Instagram comment events.
- [ ] Document only what this ticket adds; the reference is already stale for the Helena channel and fixing that is not part of this ticket.

**8. Code hygiene**

- [x] Follow the repo's comment rule in every file touched: no comment or docstring that restates what the code already says; comment only a non-obvious why (for example, why an empty secret is rejected before calling `verify_signature`), in one terse line. Docstrings, where the project convention asks for one, stay at one line — no repeated signature, types or parameter names.
- [x] Run Ruff (line length 100) and MyPy on the files touched and fix what they report.
- [x] Run `python tests/test_instagram_route.py` and confirm every no-DB case passes; report the DB cases as pending if migration 010 has not been applied yet.

## Acceptance criteria

- [x] `GET /api/instagram` with `hub.verify_token` equal to `INSTAGRAM_VERIFY_TOKEN` returns 200 with the `hub.challenge` value as plain text; with a wrong token, or with `INSTAGRAM_VERIFY_TOKEN` unset, it returns 403.
- [x] `POST /api/instagram` returns 403, and nothing is parsed, scheduled or handed off, when the `X-Hub-Signature-256` header is missing, when it does not match the HMAC-SHA256 of the raw body, or when `INSTAGRAM_APP_SECRET` is not configured (even if the caller signs with an empty secret).
- [x] A validly signed event is answered with 200 before any DB access; Company resolution and the hand-off happen in a background task with its own DB session.
- [x] A validly signed first-level comment for an account whose id is in a Company's `instagram_account_id` reaches `handle_comment` exactly once, with that Company and the parsed comment id, post id, author id, author username and text — whether Meta names the comment id `id` or `comment_id`.
- [x] A batch with several comments reaches `handle_comment` once per valid first-level comment, in order; a malformed item or an exception on one comment does not prevent the others from being processed.
- [x] A reply to another comment never reaches `handle_comment`.
- [x] A validly signed event that is not a comment returns 200, with no hand-off, no exception and no alert.
- [x] A validly signed comment from an account no Company has registered returns 200 and produces only a log line: no hand-off, no exception, no alert.
- [x] `companies` has `instagram_account_id` (unique index `ix_companies_instagram_account_id`), `instagram_access_token` and `instagram_public_reply`, all nullable; migration 010 can be applied twice without error and its downgrade removes the index and the three columns. The migration was applied only with the owner's explicit go.
- [x] `GET`/`POST /api/meta` and the `meta_*` settings behave exactly as before; a Company without `instagram_account_id` sees no behaviour change.
- [x] `python tests/test_instagram_route.py` passes: all no-DB cases always, the DB cases once migration 010 is applied, leaving `instagram_account_id` of Company `5` back at `NULL`.
- [ ] The file reference (routes, services, repositories, models, index) and the database schema documentation describe what this ticket added.

## Out of scope

- **Everything that happens after the hand-off** — ticket 03: the welcome-record table and its migration, the rest of the eligibility rule (author is the Company's own account; author already welcomed on that post), the reservation and idempotency against Meta redelivering the same event, the Instagram API client, fetching the caption, generating and sending the DM de boas-vindas, publishing the Resposta pública, and the alerts of that flow. `handle_comment` only logs here.
- **Deciding whether the feature is on for a Company** (account id + access token + Resposta pública + welcome prompt all present) — ticket 03. This ticket resolves the Company by `instagram_account_id` alone; `instagram_access_token` and `instagram_public_reply` are only stored, never read.
- **Deduplication in Redis.** `MetaDeduplicator` and `MetaWebhookCache` belong to the WhatsApp proxy and are not used here; no cache of the account → Company lookup either.
- **Matching the Boas-vindas pendente in the Helena channel** — ticket 04. Nothing in `app.helena` or the Helena route changes.
- **Weekly renewal of the Instagram access token** — ticket 05. No lifespan loop is added.
- **Real-flow validation** — ticket 01 (manual, in parallel). If it shows different field names in the real comment event or a different signing secret, the adjustment is a follow-up to `parse_comments` or to the value of `INSTAGRAM_APP_SECRET`, not a reason to add configurable field names now.
- **Any change to the existing Meta route**, including making its signature mandatory.
- **Operational steps owned by the owner:** setting `INSTAGRAM_APP_SECRET` and `INSTAGRAM_VERIFY_TOKEN` in the production environment, deploying, registering `/api/instagram` as the callback URL in the Meta app panel, and filling the three `instagram_*` columns for a real Company. No configuration endpoint or screen.
- **Fixing the stale parts of the file reference and glossary** about the Helena channel.
