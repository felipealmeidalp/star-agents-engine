# 04 — Casamento da Boas-vindas pendente no canal Helena

**What to build:** When a person who received a DM de boas-vindas answers it, the conversation enters through the Helena channel as a brand-new session. On the first message of that session — the one that turns the `sessionId` into a Customer — the engine recognises the person as the owner of a Boas-vindas pendente and, before the lead's message reaches the AI, seeds the conversation: the DM becomes the first assistant message in the session's ChatHistory, the Origem do comentário (caption, comment text and, when there is one, the product) goes into the lead context that already enters the system prompt, and the record stops being pending (state casada, with the `sessionId` and the match date). The AI then answers knowing what it already said and which post the person came from, without introducing itself again. With more than one Boas-vindas pendente for the same person, the most recent wins and the others stay untouched; a Boas-vindas pendente is consumed only once. If the incoming message is our own DM coming back through Helena (same text), the match is still done but the message is not sent to the AI and not written to history. Any error in the matching is logged and the message follows the normal flow without the comment context. Everything else is unchanged and free: sessions that already exist, Companies without Instagram configured and WhatsApp leads never match and pay no extra call or query.

**Blocked by:** 01 — Validação real do fluxo comentário → DM → sessão Helena; 03 — Comentário elegível → DM de boas-vindas → Resposta pública.

**Spec:** [spec.md](./spec.md)

**Status:** ready-for-agent

## Context

**What is already there (do not rebuild, do not guard).** The Helena channel and its Gate de IA / Escalonamento humano are shipped (`specs/helena-channel`, `specs/helena-channel-parity`). Ticket 02 delivered the Company Instagram columns. Ticket 03 delivered the welcome-record table (Company, comment id, post id, author id and @username, comment text, caption, product nullable, DM text, recipient id returned by Meta, state as plain text — em processamento / pendente / casada / falhou —, matched `sessionId`, created and matched dates), its ORM model and its repository. This ticket only reads and updates that record. **Read the real table, column, model, state-string and repository names from the code of tickets 02 and 03** — none of them is repeated or fixed here.

**The matching key is not decided in this ticket.** Ticket 01 writes `specs/instagram-comment-welcome/validation.md`; its last section, "Chave do casamento", states exactly one outcome. Outcome (1) → implement Path A below. Outcome (2) → implement Path B below. Outcome (3) → the design goes back to ADR 0001/0002 and this ticket does not start. Exactly one path is implemented, never both, never a fallback from one to the other.

**Research done ahead of ticket 01: [helena-api.md](./helena-api.md).** Real calls to Helena showed two things that correct the path descriptions below. Path A: `details.from` is the person's Instagram id truncated like a phone number (`+0324193345627` for id `1090324193345627`), so the comparison with Meta's id would be by suffix, not equality. Path B: the session lookup returns the channel type and the `contactId` but `contactDetails.instagram` comes `null`; the username is only in `GET /core/v1/contact/{contactId}` (same host, `/core` base instead of `/chat`), so Path B takes two reads, not one. `validation.md` still decides which path is built.

**The only code that changes behaviour is `HelenaService.process_webhook`** (module `app.helena.service`). Its order today:

1. early return when there is neither text nor attachment;
2. ORM attributes of `company` are read into locals before any `await` (`helena_apikey`, `company_id`, and `phone = payload.content.details.from_`) — the code comment explains why: touching an ORM attribute after an await can trigger a lazy load outside the greenlet;
3. early return `no_apikey`;
4. attachment resolution → `message` (the text the core receives; plain text wins over an attachment);
5. `self.customer_repo.upsert_api_customer(...)` — **this is where the Customer is created**;
6. Gate de IA: `self.customer_repo.get_status(...)`; when `status is False` the lead's message is saved with `self.chat_history_repo.insert_user_message(...)` and the method returns `ai_deactivated`;
7. `self.request_manager.on_new_message(...)` — the hand-off to RequestManager/MessageBuffer;
8. `self.client.send_messages(...)` with the reply.

**How "this session has just become a Customer" is known.** `CustomerRepository.upsert_api_customer` already returns `tuple[Customer, bool]` — the Customer and `is_new`. `HelenaService` discards that return today. The smallest change is to keep it: `customer, is_new = await self.customer_repo.upsert_api_customer(...)`. No repository change is needed. `is_new` is `True` only on the insert branch; on an existing row and on the insert race (`IntegrityError` branch) it is `False`, which is exactly "first message of a session that just became a Customer". The route modules `chat` and `voe` already unpack this tuple the same way.

**Where the lead's message is saved and handed over — the seeding must land before all three.**

- Gate de IA branch (step 6): `insert_user_message` in the service itself.
- Development mode (`settings.dev_mode`, the one the tests use): `RequestManager.on_new_message` calls `process_chat`, which calls `ChatHistoryRepository.insert_user_message` first and then builds the context.
- Production: `RequestManager.on_new_message` puts the message in the MessageBuffer and the turn is saved at the end; `ContextBuilder` reads the history from the database and appends the in-memory message after it.

So the matching step goes **immediately after `upsert_api_customer` (step 5) and before the Gate de IA read (step 6)**. A brand-new Customer has `status` default `True`, so the gate can never be closed on that first message; placing the step before it just guarantees the DM is in ChatHistory before any of the three saves above. History is read by `ChatHistoryRepository.get_history_with_orphan_handling` ordered by `created_at ASC, id ASC`, so a DM row inserted before the lead's message is rendered before it with no extra work — leave `created_at` at its default.

**How an assistant message is saved.** `ChatHistoryRepository.insert_assistant_message(session_id, content, company_id, *, input_tokens, input_cached_tokens, output_tokens, model, is_human=False)` looks the Customer up by `sessionId` + `company_id` and writes a `ChatHistory` row with `sessionId`, `role="assistant"`, `content`, `agent_id` and `sub_agent_id` copied from the Customer, `company_id`, `isHuman=False`. Assistant content is stored as plain text (`format_content_for_storage` already flattens the model's JSON), so the DM text goes in as is. **It commits on its own**, as does every method of `ChatHistoryRepository` and `CustomerRepository`.

**What "the lead context that already enters the system prompt" is.** The JSON column `Customer.customer_context`. `ContextBuilder._build_system_prompt` appends, as the last section and only when the column is not empty:

```
## Contexto sobre o Lead
<json.dumps(customer_context, ensure_ascii=False, indent=2)>
```

The tool `salvar_contexto` (`SaveContextTool`) writes to the same column through `CustomerRepository.merge_customer_context`, a shallow merge of top-level keys (`produto`, `cidade`, `nome`, `telefone`) that also commits on its own. Its sibling column `custom_information` does **not** enter the prompt. The Origem do comentário is written into `customer_context` and nothing new is rendered — `ContextBuilder` is not touched.

**Decided: shape of the Origem do comentário inside `customer_context`.** One nested key, so the shallow merge of `salvar_contexto` (which owns a top-level `produto`) can never overwrite it:

```json
{
  "origem_comentario_instagram": {
    "legenda_do_post": "<caption as stored in the record, may be an empty string>",
    "comentario": "<comment text>",
    "produto": "<product>"
  }
}
```

The `produto` key is **omitted** (not `null`, not `""`) when the record's product is null. It is merged into whatever `customer_context` the Customer already has (`{**(customer.customer_context or {}), **origem}` — reassign the dict, never mutate it in place, or SQLAlchemy does not see the change on a JSON column).

**Decided: "in the same transaction" means one commit.** The three writes — the `ChatHistory` assistant row, the `customer_context` merge and the record going to casada — are added to the session and committed once. Because `insert_assistant_message` and `merge_customer_context` each commit, they cannot be called here; write the same fields they write, directly, in one method with a single `commit()`. The `Customer` returned by `upsert_api_customer` is the object to update and the source of `agent_id` / `sub_agent_id` for the `ChatHistory` row (both are non-null foreign keys there).

**Decided: consume-once is a row lock.** The lookup of the Boas-vindas pendente selects with `with_for_update(skip_locked=True)`, ordered by the creation date descending, limit 1. The lock is held until the single commit, so two sessions can never consume the same record, and the state filter (pendente only) keeps a casada record from ever matching again. "Most recent wins, the others stay as they are" is that `ORDER BY ... DESC LIMIT 1` plus touching only the returned row.

**Decided: when the matching is attempted (cheapest check first, so the free cases stay free).**

1. `is_new` is `True` — otherwise nothing runs (existing sessions pay nothing).
2. The Company has Instagram configured — decided as "the Instagram account id column delivered by ticket 02 is filled", read from the already-loaded `company` object into a local together with the other locals at step 2 above, before any `await`. No query (Companies without Instagram pay nothing).
3. There is a Boas-vindas pendente — in Path A the lookup query itself answers it; in Path B one existence query runs before the call to Helena.

**Decided: echo protection.** After a match is found, compare the incoming `payload.content.text` with the record's DM text, both `.strip()`-ed, exact equality. Equal → do the record → casada transition and the `customer_context` merge, add the DM to ChatHistory exactly once (the seeded assistant row is the only row), and return before the Gate de IA and before `on_new_message`, with `{"status": "ignored", "reason": "welcome_echo", "session_id": session_id}` (same dict style as the other returns of `process_webhook`). Whether Helena really delivers our DM as a received message is item 5 of `validation.md`; the protection is implemented either way.

**Decided: failure handling.** The whole matching step sits in one `try/except Exception`: `logger.exception(...)`, `await self.db.rollback()`, and the flow continues to the Gate de IA as if nothing had matched. No critical alert — the spec asks for a log only. Two pitfalls:

- the rollback is required, otherwise a failed statement leaves the session's transaction aborted and the rest of `process_webhook` fails too;
- a rollback expires every ORM object in the session (the session factory uses `expire_on_commit=False`, but that does not cover rollback), so `company.id` read after it would lazy-load outside the greenlet. The method already has the `company_id` local; the code after the matching step must use it instead of `company.id`.

**What the Helena payload exposes.** `HelenaWebhookPayload.content` (`HelenaContent`) parses `sessionId`, `text`, `id`, `type`, `direction`, `timestamp`, `details`, `attachment_url`, `attachment_type`, `channel`; everything else in the event is dropped by the parser. The sender identifier is `content.details.from`, exposed as `payload.content.details.from_` (`details` itself may be absent) and held today in the local `phone`, which is stored in `Customer.custom_information["phone"]`. There is **no channel type** in the payload: `content.channel` is a numeric id injected by the n8n pre-processor for the `allowed_contacts` allowlist in the route, not "Instagram"/"WhatsApp". A code comment in the service says `details.from` is absent on Instagram sessions, while the spec reports a captured `+0324193345627`; `validation.md` section 3 settles which is true and what prefix/format the value has.

**What the Helena client has (relevant to Path B).** `HelenaClient` has `send_text` (`POST {settings.helena_base_url}/v1/session/{session_id}/message`), `assign_session` (`PUT {settings.helena_base_url}/v1/session/{session_id}/assignee`) and `send_messages`. Host: `settings.helena_base_url`, default `https://api.chat.fabriks.com.br/chat`. Authentication: `Authorization: Bearer <company.helena_apikey>`. Each method opens an `httpx.AsyncClient(timeout=self.timeout)`, calls `raise_for_status()` and returns `response.json()`. There is no session-lookup method yet.

**Why WhatsApp leads of the same Company never match.** Path A: a WhatsApp `details.from` is a phone number and never equals a recipient id returned by Meta. Path B: the session lookup returns the channel type and anything other than the Instagram value recorded in `validation.md` is "no match".

**How `tests/test_helena_route.py` works (the direct model for the tests).** It is a script (`asyncio.run(main())`, plain `assert`s, no pytest). `main()` sets `settings.dev_mode = True`, so `RequestManager.on_new_message` runs `process_chat` synchronously and without Redis. It calls the app in process with `httpx.ASGITransport` (`_post`, route `POST /api/helena/{token}`), against a real database. The test Company is the seeded Company `SEED_COMPANY_ID = 5` (it has a real agent, sub-agent, prompt and OpenAI key); `seed_company_token()` stamps `helena_token`, `helena_apikey` and `allowed_contacts` onto it with a raw `UPDATE`. Outgoing edges are patched: `patch_send_text(sends)` replaces `HelenaClient.send_text` and records `(session_id, message)`; `fake_openai_response(reply_messages)` replaces `OpenAIService.chat_completion` with a stub whose signature is `(self, payload)`; `_run_pipeline(payload, token, reply_messages)` wires both and returns `(resp, sends)`. **What reached OpenAI is that `payload` argument**: an `OpenAIPayload` whose `messages` is a list of `OpenAIMessage` (`role`, `content`) — index 0 is the system prompt (where "## Contexto sobre o Lead" appears), followed by the history. `case_h_status_false_gates_ai` shows how to wrap the stub to observe calls (`_counting_completion`). `make_payload(content={...})` merges overrides into `content` (an override of `details` replaces the whole dict). All existing cases share one fixed `SESSION_ID`, whose Customer survives between runs — so it is not a new session; the new cases need a fresh `sessionId` each (`uuid.uuid4()`). The file already has two functions named `case_h_*`; do not add to that — use unused letters. Cases that need the database are registered in `main()` after the `db_reachable()` check so they skip cleanly without one.

**Database warning.** The local `.env` points at the production database. The test stamps Company 5 and inserts welcome records; run it only with `DATABASE_URL` pointing at a development Postgres, as the test's own docstring says.

**Not a pattern to copy.** The Chatwoot seed-by-trigger is the closest precedent and is broken (wrong configuration name, history written under a key the Customer no longer uses). Do not read it as a reference.

**Documentation state.** `docs/file_reference/` has no entry at all for the Helena channel today (no file mentions it), and the **Helena** entry of `CONTEXT.md` describes a send address the code does not use. Only the part this ticket changes is documented here.

Domain terms used, as defined in `CONTEXT.md`: Company, Customer, `sessionId`, ChatHistory, Gate de IA, Canal, Helena, Comentário elegível, DM de boas-vindas, Boas-vindas pendente, Origem do comentário, Resposta pública.

## Tasks

**1. Take the confirmed key**

- [ ] Read `specs/instagram-comment-welcome/validation.md` in full; in its section "Chave do casamento" find the single stated outcome. Outcome (1) → do task group 3A and skip 3B. Outcome (2) → do task group 3B and skip 3A. Outcome (3) → stop, do not implement anything, and report that the design went back to ADR 0001/0002.
- [ ] Read the code delivered by tickets 02 and 03 and note the real names of: the Company column that holds the Instagram account id; the welcome-record ORM model and its columns for DM text, caption, comment text, product, author @username, recipient id returned by Meta, state, matched `sessionId`, creation date and match date; the exact strings used for the states pendente and casada; the welcome-record repository class and its existing methods. Use those names everywhere below.
- [ ] Follow the repo's code-comment rule in everything this ticket writes: no comment that restates what the code says; comment only a non-obvious why (a restriction, a pitfall), in one terse line; no inflated docstrings repeating signatures or parameter names.

**2. Know that the session just became a Customer, at no cost**

- [ ] In `HelenaService.process_webhook`, keep the return of `self.customer_repo.upsert_api_customer(...)`: `customer, is_new = await ...`. Change nothing in `CustomerRepository`.
- [ ] Next to the other locals read before the first `await` (`helena_apikey`, `company_id`, `phone`), read the Company's Instagram account id column (delivered by ticket 02) into a local. "Instagram configured" for this ticket is that local being filled.
- [ ] In `HelenaService.__init__`, build the welcome-record repository delivered by ticket 03 with `self.db`, next to the other repositories.
- [ ] Replace the uses of `company.id` that come after the `upsert_api_customer` call (the Gate de IA read, `insert_user_message`, `on_new_message`) with the existing `company_id` local, so a rollback in the matching step cannot make them lazy-load.

**3A. Path A — match by recipient id (only if `validation.md` states outcome 1)**

- [ ] Add one method to the welcome-record repository that returns the most recent Boas-vindas pendente for a Company and a recipient id, or `None`: filter by Company, state pendente and recipient id equal to the given value; order by creation date descending; limit 1; `with_for_update(skip_locked=True)`. It does not commit.
- [ ] In the matching step, derive the key from `payload.content.details.from_` (the existing `phone` local), applying exactly the normalisation `validation.md` section 3 shows is needed to make it equal to the recipient id Meta returned (for example a leading `+`) — and nothing beyond what that section shows. When the value is absent, there is no match and no query.
- [ ] Call the lookup with `company_id` and that key. `None` → no match; continue the normal flow. No call to Helena is made on this path.
- [ ] Update the code comment in `process_webhook` that says `details.from` is absent on Instagram sessions so it states what `validation.md` section 3 found, in one line.

**3B. Path B — match by username via Helena session lookup (only if `validation.md` states outcome 2)**

- [ ] Add one method to `HelenaClient` that looks a session up asking for the contact details, modelled on `send_text` / `assign_session` (Bearer `apikey`, `httpx.AsyncClient(timeout=self.timeout)`, `raise_for_status()`, return `response.json()`). Use the exact host, path and query parameters recorded as working in `validation.md` section 4; when that host is the one the engine already uses, build the URL from `settings.helena_base_url` like the other two methods.
- [ ] Add one method to the welcome-record repository that says whether the Company has any Boas-vindas pendente (an existence query, no rows loaded).
- [ ] Add one method to the welcome-record repository that returns the most recent Boas-vindas pendente for a Company and a username, or `None`: filter by Company, state pendente and author username equal to the given one, comparing both sides without `@` and lower-cased; order by creation date descending; limit 1; `with_for_update(skip_locked=True)`. It does not commit.
- [ ] In the matching step, first run the existence query; when the Company has no Boas-vindas pendente, stop there — no call to Helena.
- [ ] Otherwise call the new `HelenaClient` method with `session_id` and the `apikey` local. Read the channel type and the contact's username from the fields named in `validation.md` section 4. When the channel type is not the Instagram value recorded there, or the username is missing → no match; continue the normal flow.
- [ ] Normalise the username with `.lstrip("@").lower()` and call the lookup with `company_id` and it. `None` → no match; continue the normal flow.

**4. The matching step in `HelenaService.process_webhook` (common to both paths)**

- [ ] Insert the step immediately after the `upsert_api_customer` call and before the Gate de IA read (`get_status`). It runs only when `is_new` is `True` and the Instagram account id local is filled; in every other case no line of it executes — no query, no HTTP call.
- [ ] Add one method to the welcome-record repository that performs the match in a single transaction, receiving the record returned by the lookup, the `Customer` returned by `upsert_api_customer` and a flag telling whether to write the DM into ChatHistory: (a) `self.db.add(ChatHistory(...))` with `sessionId` = the Customer's `sessionId`, `role="assistant"`, `content` = the record's DM text, `agent_id` and `sub_agent_id` from the Customer, `company_id`, `isHuman=False` — the same fields `insert_assistant_message` writes, token fields and `model` left empty, `created_at` left at its default; (b) reassign `customer.customer_context` to the existing dict merged with the Origem do comentário in the shape decided in Context, omitting `produto` when the record's product is null; (c) set the record's state to casada, its matched `sessionId` to the Customer's `sessionId` and its match date to now, using the same date convention the model uses for its creation date; (d) one `await self.db.commit()`. Do not call `insert_assistant_message` or `merge_customer_context` — each commits on its own.
- [ ] Touch only the record returned by the lookup. Other Boas-vindas pendentes of the same person keep their state.
- [ ] Echo protection: after a record is found, compare `(payload.content.text or "").strip()` with the record's DM text `.strip()`-ed. When equal, perform the match (the DM is written to ChatHistory once, as the seeded assistant row) and `return {"status": "ignored", "reason": "welcome_echo", "session_id": session_id}` right there — before the Gate de IA, before `insert_user_message`, before `on_new_message` — after logging at info level that the incoming message was the engine's own DM de boas-vindas.
- [ ] When the texts differ, perform the match and let the method continue unchanged into the Gate de IA and `on_new_message` with the same `message` as today.
- [ ] Wrap the whole matching step (lookup, Helena call in Path B, match, echo decision) in one `try/except Exception`: on error call `logger.exception` with the `sessionId` and Company id, `await self.db.rollback()`, and continue into the Gate de IA as if nothing had matched. Do not fire `send_critical_alert`, do not re-raise, do not retry.
- [ ] Log at info level when a match happens (`sessionId`, Company id, the record's id) following the `[HelenaService]` prefix the module already uses. Do not log the DM text, the caption or the comment text.
- [ ] Do not change `ContextBuilder`, `ChatHistoryRepository`, `CustomerRepository`, `RequestManager`, the Helena route, or the `channel="helena"` argument passed to `on_new_message`.

**5. Tests — Seam 2, in `tests/test_helena_route.py`**

- [ ] Add a seed helper that stamps the Instagram columns delivered by ticket 02 onto Company `SEED_COMPANY_ID` (a clearly fake account id, token and Resposta pública text), in the same style as `seed_company_token`, and call it from `main()` after `seed_company_token()`.
- [ ] Add a helper that deletes the welcome records this test created (identified by a fixed fake author id / username constant of the test, scoped to `SEED_COMPANY_ID`) and inserts one record in state pendente for that Company, taking the DM text, caption, comment text, product (nullable), author username and recipient id as arguments. Use the ORM model delivered by ticket 03 through `AsyncSessionLocal`, not hand-written column names.
- [ ] Add a helper that patches `OpenAIService.chat_completion` with a stub that appends each received `payload.messages` to a list and then delegates to `fake_openai_response(...)`, modelled on `_counting_completion` in `case_h_status_false_gates_ai`; run it together with `patch_send_text` and the `calculate_humanized_delay` patch that `_run_pipeline` uses.
- [ ] Every new case uses its own fresh `sessionId` (`str(uuid.uuid4())`) through `make_payload(content={"sessionId": ...})`, so the session is new. Path A: the payload carries `details.from` equal to the pending record's recipient id (in the format `validation.md` section 3 shows Helena sends). Path B: the new `HelenaClient` lookup method is patched to return the response shape recorded in `validation.md` section 4, with the Instagram channel value and the pending record's username.
- [ ] Case — first message of a new session that matches: with a pending record that has a product, post one message. Assert the messages that reached OpenAI contain an `assistant` message whose content is the DM text positioned before the `user` message with the lead's text, and that the system message (index 0) contains "## Contexto sobre o Lead" with the caption, the comment text and the product.
- [ ] Case — product null: same as above with a pending record whose product is null. Assert the system message contains the caption and the comment text and does not contain the `produto` key of the Origem do comentário.
- [ ] Case — second message of the same session: after a first matching message, post a second message with the same `sessionId`. Assert that in the messages that reached OpenAI on the second call the DM text appears in exactly one `assistant` message.
- [ ] Case — new session that does not match: with a pending record present for the Company, post a first message from a new session whose key does not correspond to it (Path A: a different `details.from`; Path B: the patched lookup returns a different username). Assert a reply was sent, no `assistant` message precedes the lead's message, and the system message does not contain `origem_comentario_instagram`.
- [ ] Case — Company without Instagram configured: set the Company's Instagram account id column to `NULL`, patch the welcome-record repository lookup (and, in Path B, the existence query and the `HelenaClient` lookup method) with a spy, post a first message from a new session. Assert a reply was sent and the spies were never called; then restore the Instagram columns with the seed helper.
- [ ] Case — message with the same text as the pending DM: with a pending record present, post a first message from a new matching session whose `text` equals the DM text. Assert `OpenAIService.chat_completion` was called zero times, `HelenaClient.send_text` was called zero times, and `chat_history` holds exactly one row for that `sessionId` (role `assistant`, content = the DM text).
- [ ] Case — error in the matching: with a pending record present, patch the welcome-record repository lookup to raise, post a first message from a new session. Assert the status code is 200, a reply was sent through `HelenaClient.send_text`, and the system message does not contain `origem_comentario_instagram`.
- [ ] Register the seven cases in `main()` after the existing database-dependent cases, each printing its one-line `OK` like the others, and extend the skip message so it still reads correctly when no database is reachable.
- [ ] Run the script against a development Postgres (`DATABASE_URL=postgresql+asyncpg://... API_KEY=x python tests/test_helena_route.py`) — never against the database the local `.env` points to, which is production — and confirm the pre-existing cases still pass.
- [ ] Run `ruff check` (line length 100) and `mypy` on the modules touched and fix what they report in the new code.

**6. Documentation**

- [ ] In `docs/file_reference/`, document what this ticket changed: in the repositories reference, the new method(s) of the welcome-record repository (in the section ticket 03 created for it); for `HelenaService.process_webhook`, the matching step — when it runs, what it writes, the echo return, the failure behaviour. There is no Helena entry in `docs/file_reference/` today: if ticket 03 did not create one, add a short section for the Helena service describing the current order of `process_webhook` including the matching step, and register it in the index of `app.md`. In Path B, also document the new `HelenaClient` method there. Do not backfill the rest of the Helena channel documentation.

## Acceptance criteria

- [ ] `validation.md` states outcome (1) or (2) and the code implements only the corresponding path; there is no code for the other path and no fallback between them.
- [ ] First message of a new Helena session from a person with a Boas-vindas pendente, in a Company with Instagram configured: the request that reaches OpenAI has the DM de boas-vindas as an `assistant` message before the lead's message, and "## Contexto sobre o Lead" carries the caption, the comment text and the product.
- [ ] When the record's product is null, the lead context has no `produto` key under `origem_comentario_instagram`.
- [ ] After the match, the record is in state casada with the `sessionId` of that session and a match date; the ChatHistory row, the `customer_context` change and the state change were committed together.
- [ ] A second message in the same session does not add the DM again, and a later new session of the same person does not match the already-casada record.
- [ ] With two Boas-vindas pendentes for the same person, the most recent one is casada and the other is still pendente.
- [ ] A first message whose text equals the pending DM text makes the match but produces zero OpenAI calls, zero sends to Helena, and a single ChatHistory row (the DM as assistant).
- [ ] An exception anywhere in the matching step is logged, and the lead still gets the AI's reply, without the comment context.
- [ ] A session that already has a Customer runs no matching code: no welcome-record query and no extra Helena call.
- [ ] A Company without the Instagram account id runs no welcome-record query and no extra Helena call, and behaves exactly as before.
- [ ] A WhatsApp lead of a Company with Instagram configured and Boas-vindas pendentes is never matched.
- [ ] `python tests/test_helena_route.py` against a development database passes all pre-existing cases and the seven new Seam 2 cases.
- [ ] `docs/file_reference/` describes the matching step and the new repository (and, in Path B, client) methods.

## Out of scope

- **Choosing the matching key, or implementing both paths.** Ticket 01 decides; one path is built. If neither key works, the design returns to ADR 0001/0002.
- **The welcome-record table, its model, its base repository, the Company Instagram columns and their migrations.** Delivered by tickets 02 and 03; this ticket adds only the lookup/match methods it needs.
- **Receiving comments, generating and sending the DM de boas-vindas, the Resposta pública.** Ticket 03.
- **Token renewal.** Ticket 05, independent.
- **The Chatwoot channel.** The matching exists only in the Helena channel.
- **Fixing or reusing the Chatwoot seed-by-trigger.** It is broken and is a separate ticket; it is not a model for this one.
- **Rendering changes.** `ContextBuilder` and the system prompt layout are untouched; the Origem do comentário rides on the existing "## Contexto sobre o Lead" section.
- **Tool context.** `channel` stays `"helena"`; no tool changes.
- **Sessions already open.** A person who already has a conversation with the AI gets the DM but nothing is seeded into the ongoing session; their record stays pendente.
- **Expiry of a Boas-vindas pendente.** It does not expire.
- **Echo on any message other than the first of a new session**, and any filter by `content.direction` in the route.
- **Validating the product against the catalogue**, or any new prompt instruction telling the AI not to introduce itself again.
- **Alerts or retries for a failed match.** Log only.
- **Backfilling the missing Helena documentation** in `docs/file_reference/` or correcting the **Helena** entry in `CONTEXT.md` beyond what this ticket changes.
