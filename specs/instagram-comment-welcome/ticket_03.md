# 03 — Comentário elegível → DM de boas-vindas → Resposta pública

**What to build:** Someone makes a Comentário elegível on a post of a Company that has Instagram configured. Within seconds that person gets a DM de boas-vindas written by the LLM from the Company's own prompt (personalised with their @username and the post caption), and only after the DM went out the Company's fixed Resposta pública appears under the comment. Each person gets at most one DM de boas-vindas per post, no matter how many times they comment or how many times Meta redelivers the event; commenting on a different post gets them a new one. If anything fails before the DM is out, nothing is published, the owner gets the critical alert, and the person can be approached again on a new comment in the same post. If only the Resposta pública fails, the owner gets the alert and the Boas-vindas pendente stays valid. A Company without the Instagram configuration or without the welcome prompt sees no change at all.

**Blocked by:** 02 — Webhook do Instagram autenticado com resolução de Company

**Spec:** [spec.md](./spec.md)

**Status:** ready-for-agent

## Context

**Where this ticket plugs in.** Ticket 02 delivers the Instagram webhook route (handshake, mandatory HMAC, tolerant parsing, background task with its own DB session, Company lookup by Instagram account id, settings, the three Company columns) and ends in a thin hand-off: a service function that receives the resolved `Company` and one parsed comment and only logs. This ticket **replaces the hand-off stub delivered by ticket 02** with the real flow. Keep the stub's signature so the route and its background task do not change. Read ticket 02's code for the exact names of: the stub, the parsed-comment model and its fields (comment id, post id, author id, author username, comment text, optional parent id), and the three `Company` attributes (Instagram account id, access token, Resposta pública text). Do not rename or re-specify any of them. As specified in ticket 02 (the code wins if it differs): the stub is `handle_comment(db, company, comment: InstagramComment)` in `app.services.instagram_welcome`; `InstagramComment` has `comment_id`, `media_id`, `author_id`, `author_username`, `text`, `parent_id`; the `Company` columns are `instagram_account_id`, `instagram_access_token`, `instagram_public_reply`; the Seam 1 script is `tests/test_instagram_route.py`, using the seeded Company `id=5`; the route's background task already drops replies before the hand-off and already wraps each `handle_comment` call in its own `try/except` with alert. Ticket 01 (manual validation) may have adjusted Meta field names; its findings win over anything assumed here.

**Shape (ADR 0001).** This is not a new Canal. The engine makes three outbound calls to Meta and stops; the conversation that follows belongs to the Helena canal (ticket 04). No Customer is created here, nothing is written to ChatHistory, no `RequestManager`/`MessageBuffer`/Redis is involved.

**Existing helpers to REUSE (do not reimplement):**

- `PromptRepository.get_prompt_with_config(company_id, name, reason)` → `PromptWithConfig(prompt, model, temperature, reasoning_effort)` or `None`. This is how every other prompt gets model and reasoning effort from the `prompts` table (model defaults to `"gpt-4o-mini"` when null). No new repository method is needed.
- `CompanyRepository.get_openai_api_key(company_id)` → the Company's OpenAI key; raises `ValueError` when the Company has none.
- `OpenAIService(api_key).chat_completion(OpenAIPayload(...))` → `OpenAIResponse`; text is in `response.choices[0].message.content`. `OpenAIPayload` already carries `model`, `reasoning_effort`, `messages` (list of `OpenAIMessage`), `tools` and `response_format`. The service already maps `response_format={"type": "json_schema", "json_schema": {name, strict, schema}}` to the Responses API `text.format`, already sends `reasoning` only when `reasoning_effort` is set, and never sends temperature. It is non-streaming. It raises `OpenAIError` subclasses. Nothing in `OpenAIService` needs to change.
- Structured-output precedent to copy for the schema shape: the `routing_decision` / `classification_result` calls in `RagTool` (they call `AsyncOpenAI` directly; here go through `OpenAIService` as the spec demands, but the `response_format` dict has the same shape).
- Placeholder style: plain `str.replace` on square-bracket markers, as `RagTool` does (`[userQuestion]`, `[QUESTION]`, `[CHAT_HISTORY]`, `[OBJECAO]`), and the filled prompt sent as a single `user` message.
- `send_critical_alert(error_type, location, error, contact_id=None, company_id=None, extra=None)` from the alerter module. Fire-and-forget, never raises, always persists to `error_logs`, and rate-limits the WhatsApp message to one per `error_type` every 5 minutes. This is the only throttle on alerts when a viral post hits Meta's 750 DMs/hour limit; add none.
- `HelenaClient` is the model for the new client: plain class, `timeout` in `__init__`, one `httpx.AsyncClient` per call, `raise_for_status()`, return `response.json()`, credential passed per call (not stored on the instance).
- `AsyncSessionLocal` is configured with `expire_on_commit=False`; still follow the house habit (`HelenaService.process_webhook`) of reading `Company` attributes into locals before the first `await`.

**Decisions already made for this ticket:**

- **Table name:** `instagram_welcomes`. **ORM model:** `InstagramWelcome` in the tables module. **Repository:** `InstagramWelcomeRepository` in a new `instagram_welcome` module under repositories (imported directly from its module, like `ProductRepository`). **Client:** `InstagramClient` in a new module `app.services.instagram_client`, next to the hand-off stub (ticket 02 created a single service module, not a package).
- **Column naming: snake_case.** The codebase is mixed (`customers.sessionId`, `products.imageUrl` are camelCase; `companies.helena_*`, `prompts`, `error_logs` are snake_case). A previous UPSERT bug came from camelCase column names in `excluded`; with snake_case the ORM attribute equals the column name and that trap does not exist.
- **State is plain `TEXT`, not a Postgres enum.** Under asyncpg, `sa.Enum(...).create(checkfirst=True)` + `create_table` raises `DuplicateObjectError` (see migration 008's workaround). Irrelevant here only because the column is text; keep it text. Column name `status` (house convention), values exactly:

  ```
  em_processamento ──► pendente ──► casada      (casada is written by ticket 04, never here)
         │
         └──────────► falhou
  ```

- **Schema:**

  | Column | Type | Null | Notes |
  |---|---|---|---|
  | `id` | BigInteger PK, autoincrement | no | |
  | `company_id` | BigInteger FK `companies.id` | no | |
  | `comment_id` | Text | no | id of the comment |
  | `media_id` | Text | no | id of the post |
  | `commenter_id` | Text | no | id of the comment author |
  | `commenter_username` | Text | yes | stored as received, without `@` |
  | `comment_text` | Text | yes | |
  | `caption` | Text | yes | filled when the DM is sent; `""` for a post without caption |
  | `product` | Text | yes | free text from the caption, `NULL` when the caption cites none |
  | `dm_text` | Text | yes | |
  | `recipient_id` | Text | yes | recipient id Meta returns on the DM send |
  | `status` | Text | no | see state machine |
  | `session_id` | Text | yes | the matched Customer `sessionId`; written by ticket 04 |
  | `created_at` | TIMESTAMP(timezone=True), server default `now()` | no | |
  | `matched_at` | TIMESTAMP(timezone=True) | yes | written by ticket 04 |

- **Uniqueness = partial unique index**, so that failed records do not count:

  ```
  uq_instagram_welcomes_company_media_commenter
    UNIQUE (company_id, media_id, commenter_id) WHERE status <> 'falhou'
  ```

  This is expressible in the migration style used here: migration 004 already creates a partial unique index with `op.create_index(..., unique=True, postgresql_where=text(...))`. Following that precedent the index lives in the migration only (the ORM declares no indexes for it).
- **Reservation is the eligibility check.** "Author has no welcome already sent on that post", the race between two concurrent comments, and Meta redelivery are all the same single statement: PostgreSQL `insert(InstagramWelcome)...on_conflict_do_nothing(index_elements=["company_id", "media_id", "commenter_id"], index_where=text("status <> 'falhou'")).returning(InstagramWelcome.id)` followed by a commit. A returned id means the turn is ours; no row returned means someone already holds it and the flow ends silently. No prior `SELECT`.
- **Prompt row:** `name="instagram_welcome_dm"`, `reason="instagram_welcome"`, one row per Company in `prompts`, `model` and `reasoning_effort` set on the row. **Placeholders:** `[USERNAME]` (the author's username prefixed with `@`) and `[LEGENDA]` (the post caption, possibly empty). The row is written directly in the database by the owner; this ticket ships no seed, screen or endpoint for it.
- **Third-party text as data, never as instruction.** The filled prompt goes as one `user` message (never `system`/`developer`), with no tools, and the output is constrained by a strict JSON schema. Substitute `[USERNAME]` first and `[LEGENDA]` last, so a caption containing the literal text `[USERNAME]` is not re-substituted. The prompt row should place the two markers inside a block the prompt itself labels as data; record that convention in the documentation (task 7).
- **Structured output shape:**

  ```json
  {
    "type": "json_schema",
    "json_schema": {
      "name": "instagram_welcome_dm",
      "strict": true,
      "schema": {
        "type": "object",
        "properties": {
          "dm": {"type": "string"},
          "product": {"type": ["string", "null"]}
        },
        "required": ["dm", "product"],
        "additionalProperties": false
      }
    }
  }
  ```

- **Call order and failure handling:**

  ```
  feature on?      no → log, return                       (no record, no alert)
  eligible?        no → log, return                       (no record, no alert)
  reserve          lost → return silently
  ┌ fetch caption
  │ generate DM         any failure → status = falhou,
  │ send DM                            alert INSTAGRAM_WELCOME_FAILED,
  └ mark pendente                      nothing published, return
  publish Resposta pública   failure → alert INSTAGRAM_PUBLIC_REPLY_FAILED only;
                                       the record stays pendente
  ```

  A failure writing `pendente` after the DM was accepted by Meta takes the same `falhou` path (the spec does not single it out; this is the simplest consistent reading).
- **Feature on** = the Company's access token and Resposta pública text are non-empty (the account id is necessarily filled, since ticket 02 resolved the Company by it) **and** the prompt row exists. Feature off is not an error: log and return.
- **No retry, no truncation, no throttle.** Meta limits the DM text length and 750 such DMs per hour per account; both surface as a failed send → `falhou` + alert. The prompt is what keeps the DM short.
- **Migration number: 011.** The latest migration in the repo is 009; ticket 02 takes 010 (the three Company columns); this ticket takes 011 with `down_revision = "010"`. Confirm ticket 02's actual revision id before writing it. File name follows `YYYYMMDD_HHMMSS_011_add_instagram_welcomes_table.py`.

**Meta calls (Instagram Login).** The spec describes them only as: read the post by id asking for caption and link; send a text message whose recipient is identified by the comment id (one DM per comment, up to 7 days after it; returns the recipient id and the message id); create a reply to the comment by id. Host, API version, paths, parameter and response field names are **not** confirmed by the spec or by any code in this repo — confirm each against Meta's Instagram Login docs / ticket 01 findings before writing the request. The existing Meta route in the repo is the WhatsApp proxy of another app; it is not a reference for these calls and is not touched.

**Tests.** House style is a self-contained `asyncio.run(main())` script with plain `assert`s, no pytest. The Helena route test is the direct model: drives the app in-process through `httpx.ASGITransport` (background tasks finish before the POST returns, so no sleeps), patches only the outbound client methods and `OpenAIService.chat_completion` at class level with `unittest.mock.patch`, patches `send_critical_alert` at the name the service module imported, uses the real database, and SKIPs the DB-dependent cases with a clear message when the database is unreachable. Ticket 02 already created the Seam 1 script with the handshake and signature cases, a signed-POST helper and a test Company; extend that script.

**Database warning.** The local `.env` points at the production database. Running the migration locally applies it to production, and the DB-dependent test cases write to production.

## Tasks

**1. Tabela `instagram_welcomes` (migration 011 + ORM)**

- [ ] Create migration `011` (`down_revision = "010"`, after confirming ticket 02's revision id) that creates `instagram_welcomes` with exactly the columns, types and nullability of the schema table in Context, using `op.create_table`. Make it idempotent the way migrations 006/008 are: return early when `information_schema.tables` already has the table.
- [ ] In the same migration, create the partial unique index `uq_instagram_welcomes_company_media_commenter` on `(company_id, media_id, commenter_id)` with `unique=True` and `postgresql_where=sa.text("status <> 'falhou'")`, following migration 004.
- [ ] Write `downgrade()` dropping the index and then the table.
- [ ] Keep `status` as `sa.Text()`. Do not introduce a Postgres enum or a CHECK constraint.
- [ ] Add the `InstagramWelcome` ORM model to the tables module with the same columns (attribute name equal to column name, all snake_case). Add one terse comment on the model saying the uniqueness is a partial index declared only in migration 011 (failed records do not count).
- [ ] Do NOT run the migration locally: the local `.env` points at production. Running it requires the owner's explicit go; say so when handing the work back.

**2. `InstagramWelcomeRepository`**

- [ ] Create `InstagramWelcomeRepository(db)` in a new `instagram_welcome` module under repositories, in the shape of `ProductRepository` (holds the session, commits inside each write method).
- [ ] Add `reserve(company_id, comment_id, media_id, commenter_id, commenter_username, comment_text) -> int | None`: PostgreSQL `insert` with `status="em_processamento"`, `on_conflict_do_nothing(index_elements=["company_id", "media_id", "commenter_id"], index_where=text("status <> 'falhou'"))`, `returning(InstagramWelcome.id)`, then commit. Return the new id, or `None` when the conflict swallowed the insert.
- [ ] Add `mark_pending(welcome_id, caption, product, dm_text, recipient_id) -> None`: one `update` setting those four columns and `status="pendente"`, then commit.
- [ ] Add `mark_failed(welcome_id) -> None`: one `update` setting `status="falhou"`, then commit.
- [ ] Add nothing else. Reading pending records and moving to `casada` belong to ticket 04.

**3. `InstagramClient`**

- [ ] Create `InstagramClient` modelled on `HelenaClient`: `timeout` in `__init__`, one `httpx.AsyncClient` per call, `raise_for_status()`, return `response.json()`, the Company's access token passed as an argument on every call. Keep the API base URL (host + version) as one module-level constant; add no new setting.
- [ ] Add `get_media(media_id, access_token) -> dict`: reads the post by id asking for caption and link. Confirm host, path and field names against Meta's Instagram Login docs / ticket 01 findings.
- [ ] Add `send_private_reply(comment_id, text, access_token) -> dict`: sends a text-only message whose recipient is identified by the comment id; returns Meta's response (recipient id and message id). Confirm path, body shape and response field names against Meta's Instagram Login docs / ticket 01 findings.
- [ ] Add `reply_to_comment(comment_id, text, access_token) -> dict`: creates a reply to the comment by id. Confirm path and parameter name against Meta's Instagram Login docs / ticket 01 findings.
- [ ] Let HTTP errors propagate (`httpx.HTTPStatusError` / `httpx.RequestError`); the client does not alert and does not retry. Never log the access token.
- [ ] Do not add the token-refresh call; it belongs to ticket 05.

**4. Serviço de boas-vindas (replace the hand-off stub delivered by ticket 02)**

- [ ] Replace the body of the hand-off stub delivered by ticket 02 with the flow below, keeping its signature. Read the Company's id, Instagram account id, access token and Resposta pública text into locals before the first `await`.
- [ ] Feature gate: if the access token or the Resposta pública text is empty, log and return. Then fetch the prompt with `PromptRepository.get_prompt_with_config(company_id, "instagram_welcome_dm", "instagram_welcome")`; if it returns `None`, log and return. No record, no alert, no outbound call in either case.
- [ ] Eligibility: if the parsed comment has a parent comment id, log and return. If the comment's author id equals the Company's Instagram account id, log and return. If the comment id, the post id or the author id is missing, log and return (it cannot be reserved). No record, no alert, no outbound call.
- [ ] Reservation: call `InstagramWelcomeRepository.reserve(...)` before any external call. If it returns `None`, return silently (debug-level log at most): this covers the second comment by the same person on the same post, the lost race, and Meta redelivery.
- [ ] Fetch the caption with `InstagramClient.get_media`. A missing or null caption becomes `""` and the flow continues. An error in the call is a failure of the flow (next-to-last task of this group). The link is requested as the spec says but is not persisted; use it only in log lines.
- [ ] Build the prompt text from `PromptWithConfig.prompt` with `str.replace`: `[USERNAME]` → `"@" + username` first, `[LEGENDA]` → caption last.
- [ ] Generate the DM de boas-vindas with exactly one call: `OpenAIService(await CompanyRepository(db).get_openai_api_key(company_id)).chat_completion(OpenAIPayload(model=config.model, reasoning_effort=config.reasoning_effort, messages=[OpenAIMessage(role="user", content=<filled prompt>)], response_format=<the shape in Context>))`. No `tools`, no streaming, no system message.
- [ ] Parse `response.choices[0].message.content` with `json.loads`. Empty content, invalid JSON or an empty `dm` is a failure of the flow. Normalise an empty or whitespace-only `product` to `None`. Do not validate the product against the catalog and do not truncate the DM.
- [ ] Send the DM with `InstagramClient.send_private_reply(comment_id, dm, access_token)` and take the recipient id from Meta's response (field name per ticket 01 findings). If the send succeeded but the response carries no recipient id, log a warning and continue with `None`.
- [ ] Call `mark_pending(welcome_id, caption, product, dm, recipient_id)`.
- [ ] Failure path for everything from the caption fetch through `mark_pending`: one `try/except Exception` around those steps that rolls the session back, calls `mark_failed(welcome_id)` inside its own `try/except` (a DB failure there must not swallow the alert), fires `send_critical_alert("INSTAGRAM_WELCOME_FAILED", <module:function>, e, company_id=company_id, extra=<comment id, post id and which step failed>)`, and returns without publishing anything.
- [ ] Only after `mark_pending` succeeded, publish the Resposta pública with `InstagramClient.reply_to_comment(comment_id, <Company's Resposta pública text>, access_token)` in a separate `try/except Exception` that fires `send_critical_alert("INSTAGRAM_PUBLIC_REPLY_FAILED", <module:function>, e, company_id=company_id, extra=<comment id and post id>)` and does nothing else: the record stays `pendente`.
- [ ] The function must never raise: in a batch, one comment's failure cannot stop the next comment. Process comments one at a time, as ticket 02's background task hands them over.
- [ ] Add no automatic retry, no rate limiter, no queue, no Customer creation, no ChatHistory write.

**5. Regra de comentários no código**

- [ ] Follow the repo's code-comment rule in everything this ticket writes: no comment that restates what the code says; comment only a non-obvious why (an external constraint, a workaround, a trap), in one terse line. No inflated docstrings repeating signature, types or parameter names.

**6. Testes do Seam 1 (extend the script ticket 02 created)**

- [ ] Add helpers to the Seam 1 script: a patch of the three `InstagramClient` methods at class level that records every call, in order, into one shared list (so order across methods is assertable) and can be told to raise on a chosen method; a `chat_completion` stub returning an `OpenAIResponse` whose content is the JSON `{"dm": ..., "product": ...}`; a patch of `send_critical_alert` at the name the welcome service module imported, recording `error_type`.
- [ ] Add a setup step that prepares the test Company from the script itself: fills the three Instagram columns on the test Company ticket 02's script uses, inserts (or updates) its `instagram_welcome_dm` / `instagram_welcome` prompt row, and deletes the `instagram_welcomes` rows of that Company for the post ids the script uses, so the script is re-runnable.
- [ ] Keep the Helena test's behaviour for an unreachable database: the DB-dependent cases SKIP with a clear message instead of passing falsely.
- [ ] Case: Comentário elegível → exactly one DM send followed by exactly one Resposta pública, in that order; the DM text is the `dm` the OpenAI stub returned; the reply text is the Company's fixed Resposta pública; both target the comment id of the event.
- [ ] Case: comment authored by the Company's own account → no client call, OpenAI not called.
- [ ] Case: reply to another comment (parent id present) → no client call, OpenAI not called.
- [ ] Case: same person, same post, second comment (different comment id) → no client call for the second.
- [ ] Case: the same event delivered twice → one DM only, one Resposta pública only.
- [ ] Case: same person on another post → a new DM and a new Resposta pública.
- [ ] Case: DM send fails → Resposta pública not published, `INSTAGRAM_WELCOME_FAILED` alert fired; then a new comment by the same person on the same post → the flow runs again and the DM is sent.
- [ ] Case: Resposta pública fails → `INSTAGRAM_PUBLIC_REPLY_FAILED` alert fired, DM was sent, and a further comment by the same person on the same post triggers no client call (observable proof the welcome stayed pendente).
- [ ] Case: Instagram account that no Company has → no client call, no exception, 200 from the route. If ticket 02 already has this case, extend it to assert on the new client patch.
- [ ] Case: one POST carrying two eligible comments → two DMs and two Respostas públicas.
- [ ] Case (story 30): caption fetch fails → no DM, no Resposta pública, `INSTAGRAM_WELCOME_FAILED` alert fired.
- [ ] Case (story 27): OpenAI stub raises → no DM, no Resposta pública, `INSTAGRAM_WELCOME_FAILED` alert fired.
- [ ] Case (story 21): post with no caption (the media stub returns no caption) → DM sent and Resposta pública published.
- [ ] Case (story 16): Company without the `instagram_welcome_dm` prompt row, and Company with an empty Resposta pública text → no client call, OpenAI not called, no alert.
- [ ] Assert only on what leaves through the patched client, the OpenAI stub and the alert patch. Do not assert on the record's columns, on internal call order of functions or on the prompt content.
- [ ] The DB-dependent cases need migration 011 applied to the database the script points at, and they write to it. With the local `.env` that database is production: do not run them (nor the migration) without the owner's explicit go.

**7. Documentação**

- [ ] Update the models reference under `docs/file_reference/` with the `InstagramWelcome` model: columns, the four `status` values, and the note that uniqueness is a partial index living only in migration 011.
- [ ] Update the repositories reference with `InstagramWelcomeRepository` and its three methods, stating that `reserve` returning `None` means "turn already taken".
- [ ] Update the services reference (and the Instagram section ticket 02 added to the file reference, if any) with `InstagramClient` (three calls) and the welcome flow: feature gate, eligibility, call order, the two alert types, no retry.
- [ ] Document the prompt row convention where the prompts are documented: `name="instagram_welcome_dm"`, `reason="instagram_welcome"`, placeholders `[USERNAME]` and `[LEGENDA]`, output forced to `{dm, product}`, model and reasoning effort from the row, and the guidance that the prompt must ask for a short message and must present the two markers inside a block it labels as data.
- [ ] Add an `instagram_welcomes` section to `docs/database_schema_documentation.md`: purpose (one row per Comentário elegível processed), columns, the state machine, the partial unique index and what it guarantees, and that a Boas-vindas pendente never expires. List the index in the document's unique-constraints section.

## Acceptance criteria

- [ ] A signed POST to the Instagram webhook route carrying one Comentário elegível for a configured Company results in one caption read, one LLM call, one DM send by comment id, and then one reply to the comment with the Company's Resposta pública, in that order.
- [ ] The DM text is exactly the `dm` field of the LLM's structured response; the LLM call used the Company's OpenAI key, the model and reasoning effort of the Company's `instagram_welcome_dm` prompt row, a single `user` message, no tools and no streaming.
- [ ] After a successful flow the record is `pendente` and holds the caption, the product (or `NULL`), the DM text and the recipient id Meta returned.
- [ ] A comment by the Company's own account, a reply to another comment, and a second comment by the same person on the same post each produce no outbound call.
- [ ] The same event delivered twice produces one DM and one Resposta pública.
- [ ] The same person commenting on a different post gets a new DM de boas-vindas.
- [ ] Two concurrent deliveries for the same Company + post + author produce one DM: the loser of the reservation exits silently.
- [ ] A failure fetching the caption, generating the DM or sending the DM leaves the record `falhou`, fires `INSTAGRAM_WELCOME_FAILED` with the Company id, and publishes nothing; a later comment by that person on that post runs the flow again.
- [ ] A failure publishing the Resposta pública fires `INSTAGRAM_PUBLIC_REPLY_FAILED` and leaves the record `pendente`.
- [ ] A post without caption still gets the DM and the Resposta pública.
- [ ] A Company missing the access token, the Resposta pública text or the welcome prompt row produces no record, no outbound call and no alert.
- [ ] A batch with two eligible comments produces two DMs; a failure on the first does not prevent the second.
- [ ] Migration 011 creates `instagram_welcomes` with `status` as text and the partial unique index, is idempotent, and has a working downgrade. It has not been run against production without the owner's go.
- [ ] The Seam 1 script passes with every case listed in task group 6.
- [ ] `docs/file_reference/` and `docs/database_schema_documentation.md` describe the new table, repository, client and flow.

## Out of scope

- The webhook route, handshake, HMAC verification, payload parsing, background task, Company lookup by Instagram account id, the three Company columns and their migration, and the settings: all ticket 02.
- The casamento on the Helena canal: reading a Boas-vindas pendente, writing the DM into ChatHistory, writing the Origem do comentário into the lead context, moving the record to `casada`, filling `session_id` and `matched_at`, the echo protection. All ticket 04. This ticket only creates the columns they will use.
- The token-refresh call on `InstagramClient` and the weekly renewal loop: ticket 05.
- Creating a Customer or touching `customers` / `chat_history` when the DM is sent (ADR 0001).
- Automatic retry of a failed DM or Resposta pública; any rate limiter for Meta's 750 DMs/hour; truncating a DM that exceeds Meta's length limit.
- A sweeper for records left in `em_processamento` by a process crash mid-flow. The spec does not ask for one; such a record keeps that person's turn on that post taken.
- Expiring a Boas-vindas pendente.
- Validating the product against `products`; display name in the DM; media in the DM; keyword or per-post filters; variations of the Resposta pública.
- Seeding the prompt row, or any screen/endpoint to configure token, account id, Resposta pública text or prompt: they are written directly in the database.
- Any change to the existing Meta WhatsApp proxy route, to `OpenAIService`, to `PromptRepository` or to the Helena canal.
