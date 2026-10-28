# 01 — Escalonamento humano no Helena reutilizável

**What to build:** Nothing changes for the lead, the attendant or the AI. When the AI calls `transfer_to_human` on the Helena channel, the same thing still happens: the Gate de IA is turned off first, then the session is assigned to the Company's fixed attendant. With no attendant or no Helena key configured, the AI is still turned off and only a log is written. If the assignment fails, a critical alert still fires and the AI stays off. The change is internal. This Escalonamento humano no Helena moves out of the tool into one module-level function. The function takes only a db session, a Company id and a Helena `sessionId`, so code that is not a tool can call it. Ticket 04 calls it from the Helena casamento and from the DM send path for a reclamação.

**Blocked by:** None — can start immediately.

**Spec:** [spec.md](./spec.md)

**Status:** ready-for-agent

## Context

**Why this exists.** The spec section "Escalonamento humano reusável" and user stories 31, 37 and 38 need the Helena escalation outside the tool. Ticket 04 escalates a reclamação without the AI deciding. It does this in two places: `HelenaService.process_webhook` (the casamento) and the Instagram welcome send path, when the commenter is already a Customer. Neither place has a `ToolExecutionContext`. Both need "Gate de IA off + assign the session" in one call, with the same failure rules the tool has today.

**What the tool does today (`TransferToHumanTool.execute`, module `app.services.tools.internal.transfer`).** These steps run in order:

1. Without `context.db`, it returns a failed `ToolResult`.
2. It calls `CustomerRepository.update_status(session_id, company_id, new_status=False)` and logs `[TransferToHuman] Customer status set to False ...`. This step runs for every channel and comes before any channel branch, so the AI stops even if the CRM call fails.
3. It loads the Company with `CompanyRepository.get_by_id(company_id)`.
4. When `context.channel == "helena"`, it reads `company.helena_assignee_id` and `company.helena_apikey`:
   - If either is missing, it logs a warning (`Helena assignee/apikey missing ...; AI blocked, assignment skipped.`) and returns success.
   - Otherwise it calls `HelenaClient().assign_session(session_id, helena_assignee_id, helena_apikey)`.
   - If that call raises, it calls `send_critical_alert("HELENA_ASSIGN_FAILED", "transfer.py:execute", e, company_id=..., extra=f"session=..., assignee=...")` and does not touch the status again.
   - Every Helena path returns success.
5. Otherwise it runs the Chatwoot path, which uses the same `company` from step 3: it checks the `Agent.responsible_team`, the Chatwoot config and the `cw_conversation_id`, swaps labels, then calls `_pick_human_assignee` and `_assign_and_verify`.

No other module calls `assign_session` or `update_status`.

**Decided: where the function lives.** It is a module-level `async` function in the same module as the tool, `app.services.tools.internal.transfer`, not in the Helena package. Two reasons:

- `tests/test_transfer_tool.py` must pass without edits. Its "assign raises" case patches `app.services.tools.internal.transfer.send_critical_alert` and asserts `HELENA_ASSIGN_FAILED` was recorded. A function defined in any other module looks up its own `send_critical_alert`, so that patch would miss and the case would fail. The test patches `HelenaClient.assign_session` on the class, which works from any module.
- Everything the function needs is already imported in this module: `CustomerRepository`, `CompanyRepository`, `HelenaClient` and `send_critical_alert`. The only new import is `AsyncSession`.

**Decided: the signature.** It takes the Company id, not the `Company`, and re-reads the Company with `CompanyRepository.get_by_id`, as the tool does today. In `HelenaService.process_webhook` the casamento's `rollback` expires the `company` object, and the code says only the `company_id` local is safe after it. The name follows the existing `verb_noun` style of module-level helpers such as `send_critical_alert` and `resolve_attachment_message`.

```python
async def escalate_helena_session(db: AsyncSession, company_id: int, session_id: str) -> None
```

**Decided: the status update moves into the function.** The callers in ticket 04 need "off + assign" in one call, so the function runs `update_status(..., new_status=False)` itself, first. In the tool, the Helena branch moves above the status update and just calls the function. The Chatwoot branch keeps its own `update_status` as its first step. So both channels still turn the Gate de IA off before any CRM call, and the status is written once per call.

**Decided: failure rules, unchanged from today.**

- If `helena_assignee_id` or `helena_apikey` is missing, the status is still turned off, the existing warning is logged, and the function returns.
- If `assign_session` raises, the function fires the `HELENA_ASSIGN_FAILED` critical alert and returns. The status stays `False`.
- An exception from `update_status` or `get_by_id` is not caught and reaches the caller, as it reaches `ToolHandler` from the tool today.
- The alert location becomes `"transfer.py:escalate_helena_session"`. The test only checks the error type.
- The two log lines move with the code as they are.

**Facts the callers in ticket 04 need. Do not act on them in this ticket.**

- `update_status` commits the session it receives. Once the function returns, `status=False` is durable, and a later `rollback` by the caller does not undo it. Any change the caller has pending on that session is committed with it.
- `update_status` is a plain `UPDATE ... WHERE sessionId AND company_id`. If no Customer row matches, it silently does nothing, so the caller must pass the `sessionId` of an existing Customer.
- **Import cycle.** Importing `app.services.tools.internal.transfer` loads `app.helena.client`. That runs the Helena package's `__init__`, which imports `app.helena.service`. This was checked: `app.helena.service` is in `sys.modules` right after importing the transfer module. So `app.helena.service` cannot import the function at module top. It would fail with `ImportError` whenever the transfer module is imported first, which is what `tests/test_transfer_tool.py` does through `ToolHandler`. `HelenaService` must import it inside the method, the same lazy-import pattern `ToolHandler` uses for the internal tools. `app.services.instagram_welcome` can import it at module top, because that chain was checked and has no cycle.

**Existing tests that cover this ticket.** `tests/test_transfer_tool.py` drives the tool through `ToolHandler.execute_all` against a real database, with `HelenaClient.assign_session` patched. It has four cases:

- Helena assigns: status is `False` and there is one `assign_session` call with the Company's assignee and key.
- Chatwoot: status is `False` and there is no `assign_session` call.
- Helena with no assignee: status is `False`, the assignment is skipped, and the tool succeeds.
- Helena with the assignment raising: status is `False`, the alert fires, and the tool succeeds.

The function is reached only through the tool here, so these cases are its check. No new test is needed. `tests/test_helena_route.py` has no transfer case: its case (h) sets `status=False` straight in the database.

**Environment pitfalls.**

- The local `.env` points at the **production** database, and a worktree has no `.env`. Run the DB-backed test only against a disposable Postgres passed in `DATABASE_URL`, which overrides `.env`.
  - The recipe from earlier waves: Homebrew `postgresql@17` in the scratchpad, TCP only, with `unix_socket_directories=''`. Create the schema with `Base.metadata.create_all`, run `alembic stamp` at `main`'s head, and seed `companies(id=5)`.
- **Never run `tests/test_transfer_tool.py` against production.** Company 5 in production is a real client (7ball), and the test's cleanup sets Company 5's `helena_assignee_id` and `helena_apikey` to `NULL`.
- Always run tests with `ALERT_ENABLED=false`. An unpatched `send_critical_alert` sends a real WhatsApp.
- When no database is reachable, the test prints `SKIPPED`. A skip is not a pass.
- `/docs` is gitignored, so `docs/file_reference/` exists only on the main checkout. An agent in a worktree cannot apply the doc update. It puts the doc text in its final report, and the text is applied on the main checkout.

**Code comment rules (the repo owner's).**

- Do not comment what the code already says.
- Comment only a non-obvious *why*: a decision, an external constraint, a workaround or a pitfall.
- When a comment is needed, write one concise line. No docstrings that repeat the signature, the types or the parameter names.

**Domain terms:** Gate de IA, Escalonamento humano, Helena, Company, Customer, `sessionId`.

## Tasks

**1. Extract the function**

- [x] In `app.services.tools.internal.transfer`, add the module-level `escalate_helena_session(db, company_id, session_id)` with the signature above, and import `AsyncSession` from `sqlalchemy.ext.asyncio`.
- [x] As the function's first step, call `CustomerRepository(db).update_status(session_id, company_id, new_status=False)`, then the existing `[TransferToHuman] Customer status set to False ...` info log.
- [x] Load the Company with `CompanyRepository(db).get_by_id(company_id)` and read `helena_assignee_id` and `helena_apikey`, treating a missing Company as both missing. If either is missing, log the existing warning and return.
- [x] Call `HelenaClient().assign_session(session_id, helena_assignee_id, helena_apikey)`. On any exception, call `send_critical_alert("HELENA_ASSIGN_FAILED", "transfer.py:escalate_helena_session", e, company_id=company_id, extra=f"session={session_id}, assignee={helena_assignee_id}")` and return.
- [x] Keep the one-line why-comment that the status stays `False` on an assignment failure. Do not catch exceptions from `update_status` or `get_by_id`.

**2. The tool calls the function**

- [x] In `TransferToHumanTool.execute`, right after the `context.db` guard: when `context.channel == "helena"`, call `escalate_helena_session(context.db, context.company_id, context.session_id)` and return `_success()`.
- [x] Delete the old Helena block from `execute`.
- [x] Keep the Chatwoot path exactly as it is, starting with its `update_status` call and log, then `get_by_id`, then the rest. Do not change `_assign_and_verify` or `_pick_human_assignee`.
- [x] Fix the numbered step comments in `execute` that the move makes wrong (the "1. Common, always-first step" and "2. Branch by channel" comments, and the "company fetched above" remark). Follow the comment rules in Context: at most one line each, and only where they explain a why.

**3. Verify**

- [x] Run `tests/test_transfer_tool.py` unchanged against a disposable Postgres with `companies(id=5)` seeded, with `DATABASE_URL=<disposable> API_KEY=x ALERT_ENABLED=false`. All four cases must print OK, not SKIPPED.
- [x] Run `ruff check` and `mypy` on `app/services/tools/internal/transfer.py`.
- [x] Check with `git diff --stat` that the code diff touches only `app/services/tools/internal/transfer.py`, and nothing under `tests/`.

**4. Documentation**

- [x] (done-by-report) Rewrite the `transfer.py` section of `docs/file_reference/app/services/tools/internal/internal.md`. The current section is stale: it names the tool `transferir_para_humano` and calls it a placeholder. The new section covers:
  - the tool name `transfer_to_human`;
  - the Gate de IA off as the first step;
  - the Helena branch delegating to `escalate_helena_session`;
  - the Chatwoot branch (labels `atendimento-ia` → `atendimento-humano`, a random human from `Agent.responsible_team`, the verified assignment);
  - the alerts `HELENA_ASSIGN_FAILED`, `TRANSFER_CHATWOOT_OPS_FAILED`, `TRANSFER_ASSIGNMENT_VERIFICATION_FAILED` and `TRANSFER_TEAM_MEMBERS_FAILED`;
  - a short entry for `escalate_helena_session`: its arguments, that it commits through `update_status`, its failure rules, and that `app.helena.service` must import it lazily.
- [x] If you are working in a worktree, put the full section text in your final report instead of editing `/docs`.

## Acceptance criteria

- [x] `tests/test_transfer_tool.py`, unedited, passes all four cases against a disposable database.
- [x] `transfer_to_human` on the Helena channel turns the Gate de IA off before any Helena call, then assigns the session once to the Company's `helena_assignee_id` with its `helena_apikey`.
- [x] With no `helena_assignee_id` or no `helena_apikey`, the Gate de IA is off, Helena is not called, a warning is logged, and the tool reports success.
- [x] When the assignment fails, `HELENA_ASSIGN_FAILED` fires, the Gate de IA stays off, and the tool reports success.
- [x] `transfer_to_human` on the Chatwoot channel behaves as before and never calls Helena.
- [x] `escalate_helena_session(db, company_id, session_id)` can be awaited with no `ToolExecutionContext`. In one call it turns the Gate de IA off and assigns the session, with the same failure rules.
- [ ] `ruff check` and `mypy` pass on the module.

## Out of scope

- **Calling the function from `HelenaService` or from the Instagram welcome send path.** Ticket 04 does that.
- **Any change to the Chatwoot escalation**, including moving it out of the tool.
- **Changing `HelenaClient.assign_session`, `CustomerRepository.update_status`, the alert type or the log text.**
- **New tests.** The existing transfer cases cover the function. There is no new case for "assignee present, key missing".
- **Exporting the function** from the Helena package or from the internal tools package `__init__`.
- **Turning the Gate de IA back on** after the human is done. It stays manual.
