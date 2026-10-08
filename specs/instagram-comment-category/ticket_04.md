# 04 — Reclamação: DM sem resposta pública e escalonamento automático

**What to build:** Someone complains under a post ("14 dias para obter uma resposta!! Um absurdo!") and the Decisions API classifies the comment as `reclamacao`. That person gets a DM de boas-vindas written by the Company's complaint prompt, which tries to understand the problem, and gets nothing in public: no Resposta pública under the comment. Apart from the prompt, the complaint DM works exactly like the comercial one: the same strict JSON (`dm` + `product`), the same `[NOME]` / `[COMENTARIO]` / `[LEGENDA]` substitution, the same previous conversation for someone who is already a Customer, and the same product extraction for the Origem do comentário. When the person answers, the AI never speaks. The conversation is already with a human in Helena: Gate de IA off and the session assigned to the Company's fixed attendant, the same Escalonamento humano that `transfer_to_human` does, but without the AI deciding it. Two moments make sure of that:

- **At send time.** If the commenter is already a Customer, their existing Helena session is escalated right after the DM is written into it. A reply inside that old conversation goes to the attendant.
- **At the matching (casamento).** If the reply opens a new Helena session, the matching of the Boas-vindas pendente escalates that session before anything else. The person's first message is written to the ChatHistory after our DM and the AI is not called. This also happens when the incoming message is the echo of our own DM.

The attendant sees the DM and the person's message in the history. If the assignment in Helena fails, an alert fires and the AI stays off. A Company without a Helena attendant or apikey still gets the AI turned off for the complainer, only without the assignment. Comercial, elogio and old welcomes without a category keep today's matching. A Company without the complaint prompt sends nothing for a complaint, writes nothing, and the person keeps their turn on the post.

**Blocked by:**

- 01 — Escalonamento humano no Helena reutilizável
- 02 — Classificar cada comentário: comercial como hoje, emoji e outro calados

**Spec:** [spec.md](./spec.md). This ticket covers user stories 26–39 and 54–56, the sections "Casamento no canal Helena" and "Escalonamento no envio" (under "Ordem das chamadas"), the Seam 1 cases "reclamação", "reclamação de quem já é Customer" and "… com falha ao atribuir", and all of Seam 2.

**Status:** ready-for-agent

## Context

**What exists when this ticket starts (from 01 and 02). Read the real names in the code; do not guess them.**

- **01** added the module-level function `escalate_helena_session(db: AsyncSession, company_id: int, session_id: str) -> None` in `app.services.tools.internal.transfer`, next to the tool. In one call it:
  - turns the Gate de IA off first, with `CustomerRepository.update_status(..., new_status=False)`, which commits the session it receives;
  - re-reads the Company with `CompanyRepository.get_by_id`;
  - calls `HelenaClient().assign_session(session_id, helena_assignee_id, helena_apikey)`.
  
  Its failure rules: without attendant or apikey it only turns the AI off and logs a warning. If the assignment raises, the function catches it, fires `send_critical_alert("HELENA_ASSIGN_FAILED", "transfer.py:escalate_helena_session", ...)` and returns, with the AI still off. Exceptions from `update_status` or `get_by_id` are not caught and reach the caller. `update_status` is a plain `UPDATE ... WHERE sessionId AND company_id`, so the caller must pass the `sessionId` of an existing Customer. This ticket only calls the function and never changes it.
- **Import cycle (from 01).** Importing the transfer module loads `app.helena.client`, which runs the Helena package `__init__`, which imports `app.helena.service`. So `app.helena.service` must import `escalate_helena_session` **inside the method**, the lazy-import pattern `ToolHandler` already uses for the internal tools. `app.services.instagram_welcome` can import it at module top; 01 checked that chain.
- **02** added these pieces:
  - the nullable text column `category` on `instagram_welcomes` (`InstagramWelcome.category`), written at reservation and null on old rows;
  - the Decisions classification and the new order of `handle_comment`;
  - a per-category action table in the welcome service, where `comercial` is wired (DM prompt `instagram_welcome_dm`, Resposta pública = the Company's fixed `instagram_public_reply`) and `reclamacao` is left unwired, so today it behaves as "category without its texts".
  
  02 also made `instagram_public_reply` a requirement of `comercial` only. It reads the category's DM prompt before the reservation and applies the rule "category chosen without its texts → log, nothing sent, nothing written, turn not spent". **This ticket fills in the `reclamacao` entry.** By naming the complaint prompt in that entry, reclamação gets 02's rule with no extra code.
- **02's test harness** in `tests/test_instagram_route.py` stubs the Decisions call and offers a way to choose the category per call. It also seeds the five category descriptions on Company 5 and restores them. Use it with `reclamacao`.

**The welcome service at this ticket's spot.** In `handle_comment` (`app.services.instagram_welcome`), 02 keeps today's tail. This ticket hooks into it:

- `_previous_conversation(db, company_id, comment)` returns `(customer_id | None, turns)`. It finds the commenter's most recent Customer with `CustomerRepository.find_latest_by_phone(company_id, "+" + author_id[-13:])`. The only writer of that `custom_information.phone` key is `HelenaService.process_webhook` (`custom_information_patch={"phone": details.from}`). So **the Customer found always lives in Helena, and its `sessionId` is the Helena sessionId** that the escalation targets.
- After `mark_pending`, outside the big `try`, the code runs `if customer_id is not None:`. That block calls `InstagramWelcomeRepository.write_dm_to_session(welcome_id, customer_id)` in its own `try`. A failure there only does `logger.exception` and a rollback under `suppress`: no alert, and the welcome stays pendente. The Resposta pública step comes after it.
- The service holds only `customer_id`, not the sessionId. `write_dm_to_session` already loads that Customer (`db.get_one(Customer, customer_id, populate_existing=True)`). **Decided:** it returns `customer.sessionId`. That costs no extra query and leaves `_previous_conversation` alone, which 02 and 03 may also touch.

**Decided: Escalonamento no envio.**

- It runs only when `write_dm_to_session` returned without raising, and only for `reclamacao`. Three reasons:
  - The spec puts it after the DM is written into the Customer's session.
  - The sessionId comes from that write.
  - A failed write means the database is failing, and the escalation's first step is a database write too.
  
  The matching still escalates if the reply opens a new session. This ceiling is accepted.
- Call `escalate_helena_session(db, company_id, session_id)` with the `company_id` local the function already has.
- **Assignment failure:** 01's function handles it (`HELENA_ASSIGN_FAILED`, AI off) and nothing reaches `handle_comment`.
- **Any other failure** (it raised) gets its own `try/except Exception`:
  - `logger.exception` naming the comment;
  - `db.rollback()` under `suppress(Exception)`, because the route reuses the same session for the next comment of the batch and a failed transaction would poison it;
  - `send_critical_alert("INSTAGRAM_ESCALATION_FAILED", "instagram_welcome.py:handle_comment", e, company_id=company_id, extra=f"comment={comment_id} media={media_id}")`.
  
  Nothing undoes the DM: no `mark_failed` and no state change. The welcome stays pendente, so the matching can still escalate a new session.
- Reclamação has no Resposta pública, so the escalation is the last thing `handle_comment` does for it.

**Decided: escalation at the matching.** This is in `HelenaService.process_webhook` (`app.helena.service`), inside `if is_new and instagram_account_id:` and its `try`. Today the block computes `is_own_dm`, calls `self.welcome_repo.match(welcome, customer)` (which commits), writes an info log, and then runs `if is_own_dm: return {"status": "ignored", "reason": "welcome_echo", ...}`. That return happens **before** the Gate de IA. So the escalation goes between the match log and the echo return:

```python
await self.welcome_repo.match(welcome, customer)
logger.info(...)  # matched
if welcome.category == "reclamacao":
    from app.services.tools.internal.transfer import escalate_helena_session

    await escalate_helena_session(self.db, company_id, session_id)
if is_own_dm:
    return {...}  # welcome_echo
```

After that, the Gate de IA runs unchanged. `get_status` reads the column with a fresh SELECT and sees `False`. The block then writes the lead's message with `ChatHistoryRepository.insert_user_message` and returns `ai_deactivated`, so the AI is not called. "Message in the history, AI not called" is today's Gate-off path; there is nothing new to write for it.

- `welcome.category` can be read after `match`'s commit, because the session factory uses `expire_on_commit=False`.
- **Failure.** The call sits inside the existing `try`. An assignment failure never reaches it: 01 catches it, fires the alert and leaves the AI off. Anything else falls into today's `except`: `logger.exception`, rollback, and the message goes on to the Gate as today, where the AI may answer. No new alert is added there. The spec says: "Falha antes disso cai na regra de hoje do casamento".
- **Other categories.** For `category is None` (rows from before 02), `comercial` and `elogio`, the `if` is false and everything stays as today.
- **`match` is unchanged.** For reclamação it still seeds the DM as the first assistant line and writes the Origem do comentário into `customer_context`. That is what the attendant reads (user story 32).

**How the two moments add up.**

- **Customer who replies in the old session.** `upsert_api_customer` returns `is_new=False`, so no matching runs. The Gate is already off (from send time), so the message is saved and the AI stays silent.
- **Reply that opens a new session** (Customer or not). The welcome is still pendente, the matching escalates the new session, and the Gate does the rest.

**Domain terms** (as in `CONTEXT.md`): Categoria do comentário, DM de boas-vindas, Resposta pública, Boas-vindas pendente, Origem do comentário, Gate de IA, Escalonamento humano, Customer, `sessionId`, Helena, Company, ChatHistory.

- Category values in code are exactly `so_emoji`, `comercial`, `elogio`, `reclamacao`, `outro`.
- The complaint DM prompt is the `prompts` row with `reason = "instagram_welcome"` and `name = "instagram_welcome_dm_reclamacao"`, read with `PromptRepository.get_prompt_with_config`. It has its own `model` and `reasoning_effort` and needs no schema change.
- The 7ball rows (Company 5) are inserted by ticket 05, not here.

**Tests (scripts with plain asserts, not pytest).**

- **Seam 1: `tests/test_instagram_route.py`.**
  - `_post_welcome(*payloads, failing=None, media=None, llm_content=None)` patches the `InstagramClient` methods at class level and records them in `calls`. `failing` makes a named method raise `httpx.ConnectError`.
  - It also patches `OpenAIService.chat_completion`, recording `(api_key, payload)` in `llm_calls`, and patches `send_critical_alert` in the welcome service and in the route, recording into `alerts`.
  - `seed_customer(session_id, history)` creates a Customer of `KNOWN_AUTHOR` with `custom_information={"phone": KNOWN_PHONE}`.
  - `session_rows(session_id)`, `welcome_states()`, `clear_customers()` and `clear_welcomes()` exist.
  - `take_snapshot` / `restore_snapshot` put back what the script overwrites on Company 5. Today that does **not** include `helena_apikey` or `helena_assignee_id`.
  - `case_commenter_with_customer` is the model for an existing-Customer case.
  - `case_session_write_failure_keeps_the_welcome` must keep passing as is.
- **Seam 2: `tests/test_helena_route.py`.** It uses a real database with Company 5 and sets `settings.dev_mode = True` (synchronous, no Redis). `HelenaClient.send_text` and `OpenAIService.chat_completion` are patched.
  - `seed_pending_welcome(...)` writes a Boas-vindas pendente for `WELCOME_AUTHOR_ID`.
  - `new_session_payload(session_id, text=...)` uses `details.from = WELCOME_FROM`, so it matches.
  - `_run_recording` returns `(resp, sends, requests)`, where `requests` are the message lists that reached OpenAI.
  - Case (r) shows how to read the `chat_history` rows of a session.
  - `seed_company_token` stamps `helena_apikey = HELENA_APIKEY`.
  - Cases (m)–(u) seed welcomes without a category, so they already prove that old rows keep today's behaviour.
- **Stubbing `assign_session`.** Use `patch("app.helena.client.HelenaClient.assign_session", stub)`. It patches at class level, so it works wherever the client is created. The mould is `patch_assign` in `tests/test_transfer_tool.py`.
- **Seeing the assignment alert.** `HELENA_ASSIGN_FAILED` is sent from the transfer module. A test that wants to see it patches `app.services.tools.internal.transfer.send_critical_alert`.
- `tests/test_transfer_tool.py` must keep passing unchanged.

**Environment pitfalls.**

- The local `.env` points at the **production** database, and worktrees have no `.env`. Never run a test or `alembic` against it. DB-backed tests run against a disposable Postgres passed in `DATABASE_URL`.
- **Database for `tests/test_instagram_route.py`.**
  - Start a disposable Postgres. Homebrew `postgresql@17` in the scratchpad works: TCP only, with `unix_socket_directories=''`.
  - Run `Base.metadata.create_all` for every table **except** `instagram_welcomes`.
  - Set `alembic_version` to `010` and run `alembic upgrade head`. That creates `instagram_welcomes` with its partial unique index, plus 02's `category` column.
  - Seed Company 5, including the `"openAi_apiKey"` column.
- **Database for `tests/test_helena_route.py`.** It also needs Company 5's standard agent and sub-agent, because ContextBuilder runs.
- Always run with `ALERT_ENABLED=false`. Otherwise an unpatched `send_critical_alert` sends a real WhatsApp message.
- Company 5 in production is a real client (7ball). Every test that stamps columns on it restores them in `finally`.
- `/docs` is gitignored, so `docs/file_reference/` does not exist in a worktree. In that case the doc task asks for the exact text in the final report, to be applied on the main checkout.
- **Merge conflict with 03.** Ticket 03 edits the same tail of `handle_comment` in parallel (the `elogio` entry and the generated Resposta pública), the same action table and the same test script. Expect a merge conflict. Keep both: the reclamação escalation after the session write, and 03's elogio branch in the Resposta pública step.

**Code comment rules (from the repo owner, mandatory).**

- Do not comment what the code already says.
- Comment only a non-obvious *why*. Examples here: why the matching escalates before the echo return, and why the import in `HelenaService` is inside the method.
- When a comment is needed, write one concise line. No inflated docstrings that repeat the signature, the types or the parameter names.

## Tasks

**1. Before writing code**

- [x] Read the merged code of 01 and 02. Note the real names of:
  - the reusable escalation;
  - the per-category action table and how `comercial` is wired in it;
  - the point before the reservation where the category's DM prompt is read;
  - the Resposta pública step;
  - the category column;
  - the Decisions stub in `_post_welcome` and how a test picks the category;
  - any helper 02 added to seed or restore prompt rows.
  
  Use those names everywhere below.
- [x] Follow the code comment rules in Context in everything this ticket writes.

**2. The `reclamacao` entry**

- [x] In the action table in `app.services.instagram_welcome`, wire `reclamacao` the way 02 wired `comercial`, with DM prompt `instagram_welcome_dm_reclamacao` and no Resposta pública.
- [x] Reuse the comercial DM path as it is: placeholders, `_previous_conversation` turns, `DM_RESPONSE_FORMAT`, and `product` passed to `mark_pending`. Write no generation code for reclamação.
- [x] Make sure a reclamação never reaches `InstagramClient.reply_to_comment`.
- [x] Make sure a reclamação never requires `companies.instagram_public_reply` to be filled.

**3. Escalonamento no envio**

- [x] Make `InstagramWelcomeRepository.write_dm_to_session` return the `sessionId` of the Customer it already loads (`-> str`). Nothing else in the method changes.
- [x] In `handle_comment`, keep the existing write block and its `except` as they are.
- [x] Only when `write_dm_to_session` returned without raising and the category is `reclamacao`, await `escalate_helena_session(db, company_id, <the returned sessionId>)`. Import it at module top from `app.services.tools.internal.transfer`.
- [x] Wrap that call in its own `try/except Exception` with:
  - `logger.exception` naming the comment;
  - `db.rollback()` under `suppress(Exception)`;
  - `send_critical_alert("INSTAGRAM_ESCALATION_FAILED", "instagram_welcome.py:handle_comment", e, company_id=company_id, extra=f"comment={comment_id} media={media_id}")`.
  
  No `mark_failed`, and no change to the welcome's state.

**4. Escalation at the matching**

- [x] In `HelenaService.process_webhook`, inside the matching `try`, add `if welcome.category == "reclamacao":` right after `self.welcome_repo.match(welcome, customer)` and its log line, before the `if is_own_dm:` return. Inside it:
  - import `escalate_helena_session` from `app.services.tools.internal.transfer` (one-line comment: import cycle through the Helena package);
  - await `escalate_helena_session(self.db, company_id, session_id)`.
  
  Add one comment line for why it sits before the echo return.
- [x] Leave the rest of the matching block, its `except` and the Gate de IA block untouched.

**5. Seam 1 tests (`tests/test_instagram_route.py`)**

- [x] Add `helena_apikey` and `helena_assignee_id` of Company 5 to what `take_snapshot` reads and `restore_snapshot` writes back.
- [x] Add a helper that stamps both columns with test constants (`HELENA_APIKEY`, `ASSIGNEE_ID`).
- [x] Add a way to seed and remove the `instagram_welcome_dm_reclamacao` row for Company 5. Give it a distinct prompt text with the three placeholders and a distinct model (e.g. `gpt-test-reclamacao`). Include it in the snapshot and restore, like the other prompt rows.
- [x] In `_post_welcome`, also patch `app.services.tools.internal.transfer.send_critical_alert`, recording into the same `alerts` list.
- [x] Add an `assign_session` recorder in the mould of `patch_assign` from `tests/test_transfer_tool.py`, plus a raising variant.
- [x] Add a reader for `customers.status` of a session.
- [x] **Case reclamação.** Commenter without a Customer, category `reclamacao`, `assign_session` stubbed. Assert:
  - `calls` are `get_media`, `send_private_reply` (with `DM_TEXT`) and `release_thread_control`, and there is no `reply_to_comment`;
  - there is exactly one generation call, with the complaint row's model, and the complaint prompt's fixed text is in its last `user` message;
  - the welcome is `pendente`;
  - `alerts == []`;
  - `assign_session` was not called.
- [x] **Case reclamação of an existing Customer.** Run `seed_customer(TEST_SESSION + "open", ...)`, stamp the Helena columns, category `reclamacao`. Assert:
  - the session's last row is `("assistant", DM_TEXT)`;
  - that session's `status` is `False`;
  - there is exactly one `assign_session` call, `(TEST_SESSION + "open", ASSIGNEE_ID, HELENA_APIKEY)`;
  - there is no `reply_to_comment`;
  - `alerts == []`.
- [x] **Same case with `assign_session` raising.** Assert:
  - `"HELENA_ASSIGN_FAILED"` is in `alerts`;
  - `status` is `False`;
  - the DM is still the last row of the session;
  - the welcome is `pendente`;
  - there is no `reply_to_comment`.
- [x] Register the three cases in `main()` inside the block where the Company is configured, which is covered by the `finally: restore_snapshot`.

**6. Seam 2 tests (`tests/test_helena_route.py`)**

- [x] Add a keyword `category: str | None = None` to `seed_pending_welcome` and write it into the row. The default `None` keeps cases (m)–(u) as the old-row coverage.
- [x] Add an `ASSIGNEE_ID` constant, an `assign_session` recorder (the mould is `patch_assign` in `tests/test_transfer_tool.py`), a reader for `customers.status` of a session, and a helper that returns the `(role, content)` rows of a session (lift the query from case (r)).
- [x] **Case reclamação, first message of a new session.** Seed a welcome with `category="reclamacao"` and run `_run_recording(new_session_payload(sid, text=lead_text), [...])` with the recorder. Assert:
  - `requests == []` and `sends == []`;
  - the session rows are `[("assistant", WELCOME_DM), ("user", lead_text)]`;
  - `status` is `False`;
  - the recorder holds exactly `[(sid, ASSIGNEE_ID, HELENA_APIKEY)]`.
- [x] **Case reclamação with the echo of our own DM** (`text=f" {WELCOME_DM}\n"`). Assert:
  - `requests == []` and `sends == []`;
  - the session rows are `[("assistant", WELCOME_DM)]`;
  - `status` is `False`;
  - there is one `assign_session` call for that session.
- [x] **Case today's behaviour.** For each of `"comercial"`, `"elogio"` and `None`, seed the welcome, send a lead message on a new session, and assert:
  - one OpenAI request whose turns are `[("assistant", WELCOME_DM), ("user", lead_text)]`;
  - the stubbed reply was sent;
  - `assign_session` was not called;
  - `status` is `True`.
- [x] In `main()`, read Company 5's `helena_assignee_id` before stamping `ASSIGNEE_ID`. Run the three new cases after case (u) and restore the old value in a `finally`.

**7. Run**

- [x] Run `tests/test_instagram_route.py`, `tests/test_helena_route.py` and `tests/test_transfer_tool.py` against the disposable Postgres with `DATABASE_URL=<disposable> API_KEY=x ALERT_ENABLED=false`. Each must print its final OK line, not SKIPPED.
- [x] Confirm that `tests/test_transfer_tool.py` has no edits.
- [x] Run `ruff check` and `mypy` on the three changed app modules.

**8. Docs (`docs/file_reference/`)**

- [x] In `app/services/services.md`, section `instagram_welcome.py`:
  - in the flow, the `reclamacao` entry (prompt `instagram_welcome_dm_reclamacao`, no Resposta pública);
  - after the session write step, the Escalonamento no envio: only for reclamação, only after a successful write, `escalate_helena_session` with the returned sessionId;
  - in "Falhas", `INSTAGRAM_ESCALATION_FAILED` (other failures, no undo) and `HELENA_ASSIGN_FAILED` (from the transfer module, AI off);
  - `escalate_helena_session` added to the dependencies.
- [x] In `app/helena/helena.md`:
  - in step 6 of the `process_webhook` order and in "Casamento da Boas-vindas pendente", the escalation of a reclamação between `match` and the echo return, and its lazy import;
  - in the "Eco" paragraph, that the echo of a complaint DM still leaves the session escalated;
  - in "Falha", that an assignment failure only alerts while any other escalation failure falls into the existing `except`;
  - `escalate_helena_session` added to the dependencies.
- [x] In `app/repositories/repositories.md`: `write_dm_to_session` now returns the Customer's `sessionId`, in both the method table and its section. Add the escalation to "Usado por".
- [x] If `docs/` does not exist in your checkout (worktree), put the exact text of each edit in your final report instead.

## Acceptance criteria

- [x] A comment classified as `reclamacao` gets one DM generated from `instagram_welcome_dm_reclamacao`, and no Resposta pública is published.
- [x] The complaint DM gets the previous conversation of an existing Customer and the same placeholders as comercial, and returns `dm` + `product`. The product reaches the Boas-vindas pendente like comercial.
- [x] For a complainer who is already a Customer: right after the DM is written to their session, that session has `status=False` and is assigned in Helena to the Company's `helena_assignee_id`.
- [x] Same with the assignment failing: `HELENA_ASSIGN_FAILED` fires, `status` is `False`, and the DM is still in the session.
- [x] A complainer who is not a Customer causes no Helena call at send time.
- [x] The first Helena message of a new session that matches a reclamação Boas-vindas pendente: OpenAI is not called and nothing is sent. The ChatHistory holds the DM and then the lead's message. `status` is `False` and the session is assigned to the attendant.
- [x] A complaint's echo of our own DM: the session is escalated, the AI is not called, and the ChatHistory holds only the DM.
- [x] Welcomes with category `comercial`, `elogio` or null match as today: DM in the history, the AI answers, no assignment, `status` stays `True`.
- [x] A Company without attendant or Helena apikey still gets the AI turned off for the complainer, without an assignment. This comes from 01's function; no code here.
- [x] A Company without the `instagram_welcome_dm_reclamacao` row: a complaint sends nothing and writes nothing, and a later comment by the person on that post is still handled. This is 02's rule.
- [x] `tests/test_instagram_route.py` and `tests/test_helena_route.py` pass in full against a disposable database. `tests/test_transfer_tool.py` passes unchanged.
- [x] `docs/file_reference/` describes the reclamação entry, both escalation points and the new return of `write_dm_to_session`, or the final report carries that text.

## Out of scope

- **The reusable escalation itself, `transfer_to_human`, `HelenaClient.assign_session` and `CustomerRepository.update_status`.** Ticket 01 owns them.
- **The classification, the `category` column and its migration, the shape of the action table, and the "category without its texts" rule.** Ticket 02 owns them.
- **Elogio and its generated Resposta pública.** Ticket 03 owns them.
- **The 7ball data**, including the complaint prompt text and the five descriptions. Ticket 05 inserts it.
- **A Resposta pública for reclamação**, generated or fixed.
- **Escalating at send time when `write_dm_to_session` fails**, retrying a failed escalation, or escalating older sessions of the same person. Only the most recent Customer that `_previous_conversation` finds is escalated.
- **A new alert at the matching** for a non-assignment failure. Today's matching rule (log, rollback, normal flow) applies.
- **The category in the Origem do comentário or in the AI context.**
- **Turning the AI back on after the human is done.** It stays manual.
- **Chatwoot**, and classifying anything other than Instagram comments.
