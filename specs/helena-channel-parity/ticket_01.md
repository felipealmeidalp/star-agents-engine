# 01 — Gate de IA no HelenaService

**What to build:** When a Helena conversation has been transferred to a human (its `customers.status` is `False`), the AI stops answering that lead. The lead can keep sending messages and they are still saved to `chat_history` so the human agent sees the full context, but no AI reply goes out on the Helena channel. This mirrors the Chatwoot behaviour exactly, using the same `customers.status` field, and the check happens BEFORE any call to the AI so a silenced conversation burns no tokens and fires no tools.

**Blocked by:** None — can start immediately.

**Spec:** [spec.md](./spec.md)

**Status:** ready-for-agent

## Context

The gate lives in `app/helena/service.py`, in `HelenaService.process_webhook`. The behaviour to mirror is the Chatwoot "1.5" status-check block in `app/chatwoot/service.py` (`ChatwootService.process_webhook`, right after it loads the customer): `if customer.status is False:` it saves the lead's message via `insert_user_message` and returns early with `{"status": "ai_deactivated", ...}`. IMPORTANT — the Chatwoot block ALSO calls `_update_follow_up_and_schedule`; Helena has NO follow-up (it is the lean channel), so that part is NOT replicated. Only the save-and-return part is.

Key facts found during research:

- **The check is `is False`, not falsy.** `Customer.status` is `Boolean, nullable=True, default=True` (see `app/models/tables.py`). A brand-new customer can have `status = None` (or `True`) — that means AI active. Only an explicit `False` (set by `transfer_to_human`) silences the AI. Use `if status is False:` exactly, matching Chatwoot; `None`/`True` must fall through to normal processing.

- **Reading the status back.** `HelenaService` today calls `self.customer_repo.upsert_api_customer(...)` but throws away the returned `(Customer, is_new)` tuple and never reads the customer's status. Use `CustomerRepository.get_status(session_id, company_id) -> bool | None` — a single scalar select, no full ORM load — called after the `upsert_api_customer` (the row must exist first, because `insert_user_message` looks the customer up by session and raises `ValueError` if it is missing). `upsert_api_customer` already returns the customer, but `get_status` is the leaner, Chatwoot-consistent read and avoids reasoning about a stale ORM `status` from the upsert path; either works, prefer `get_status`.

- **Which repo has `insert_user_message`.** It is on `ChatHistoryRepository` (`app/repositories/chat_history.py`), signature `insert_user_message(session_id: str, message: str, company_id: int)`. It is NOT on `CustomerRepository`. `HelenaService.__init__` currently only builds `self.customer_repo = CustomerRepository(db)` — it must also build `self.chat_history_repo = ChatHistoryRepository(db)`, exactly as `ChatwootService.__init__` does.

- **Where the gate sits in the flow.** `process_webhook` order today: `no_text` early return → read ORM attrs (`helena_apikey`, `phone`) → `no_phone` / `no_apikey` early returns → `upsert_api_customer` → `on_new_message` → send. The gate goes AFTER `upsert_api_customer` (so the customer row exists for `insert_user_message`) and BEFORE `on_new_message` (so no AI call happens for a silenced conversation). Placing it right after the upsert, before the `on_send_messages` closure and `on_new_message`, is the exact spot.

- **The return dict.** Mirror Chatwoot's shape but Helena-flavoured: `{"status": "ai_deactivated", "session_id": session_id}`. (Helena's other returns already use this `{"status": ..., "session_id": ...}` style: `ignored`/`buffered`/`processed`.)

- **The message being saved.** Save the same `text` that would have gone into `on_new_message` — i.e. `payload.content.text` (already bound to the local `text` at the top of `process_webhook`). Attachments are a separate ticket; the gate saves whatever text the MVP already resolves.

Domain terms: **Gate de IA** = the boolean `customers.status`; `True`/`None` = AI answers, `False` = transferred to human, AI silenced (per CONTEXT.md). **sessionId** = `payload.content.sessionId` = `Customer.sessionId`, the conversation key. The one who sets `status=False` is the `transfer_to_human` tool (other tickets); this ticket only reads and honours it.

## Tasks

**1. Wire the ChatHistoryRepository into HelenaService**

- [x] In `HelenaService.__init__` (`app/helena/service.py`), import `ChatHistoryRepository` from `app.repositories.chat_history` and add `self.chat_history_repo = ChatHistoryRepository(self.db)`, matching how `ChatwootService.__init__` builds it.

**2. Insert the gate in process_webhook**

- [x] After the existing `await self.customer_repo.upsert_api_customer(...)` call and BEFORE the `on_send_messages` closure / `await self.request_manager.on_new_message(...)`, read the customer status: `status = await self.customer_repo.get_status(session_id, company.id)`.
- [x] If `status is False`: log that AI is deactivated for this session (mirror the Chatwoot log line, e.g. `"[HelenaService] AI deactivated for session %s (status=False), saving user message"`), call `await self.chat_history_repo.insert_user_message(session_id=session_id, message=text, company_id=company.id)`, and `return {"status": "ai_deactivated", "session_id": session_id}`.
- [x] Use the strict `is False` comparison — do NOT gate on `None` or `True`; those must fall through to the normal `on_new_message` path unchanged.
- [x] Do NOT call `_update_follow_up_and_schedule` or any follow-up logic — Helena has no follow-up; that Chatwoot step is deliberately not replicated.

**3. Behavioural test at the route seam (mirror Seam 1)**

- [x] In `tests/test_helena_route.py`, add a gate case following the existing `_run_pipeline` / `patch_send_text` / `make_payload` model. Before posting, set the seeded company's customer to `status = False` for `SESSION_ID` (the customer row is created by `upsert_api_customer` during processing, so the case must first create/upsert the customer with `status=False` — e.g. run one normal pipeline pass to create the row, then `UPDATE customers SET status = false WHERE "sessionId" = :sid AND company_id = :cid`, or seed the row directly before the gated POST).
- [x] Assert the gated behaviour: `resp.status_code == 200`, `calls == []` (NO `HelenaClient.send_text`), and that `OpenAIService.chat_completion` was never invoked (wrap the `chat_completion` stub in a counter / `unittest.mock` spy and assert its call count is 0 — the existing `fake_openai_response` stub can be replaced with a `MagicMock`-tracked async wrapper for this case).
- [x] Register the new case in `main()` alongside the other DB-dependent cases (it needs the live DB, same as cases a/c/d/e), so it SKIPs cleanly when no DB is reachable.

## Acceptance criteria

- [ ] With `customers.status = False` for the session, posting a `MESSAGE_RECEIVED` to `POST /api/helena/{token}` results in: the lead's message present in `chat_history` (role=`user`) for that session, ZERO `HelenaClient.send_text` calls, and ZERO `OpenAIService.chat_completion` calls.
- [ ] With `customers.status` `True` or `None`, the same POST processes normally and dispatches the AI reply (the existing `case_a_text_dispatches` still passes — no regression).
- [ ] The gate check runs after `upsert_api_customer` and before `on_new_message`; `insert_user_message` never raises `ValueError` for a missing session (the row exists by then).
- [ ] Runnable check that fails if the gate breaks: `DATABASE_URL=postgresql+asyncpg://... API_KEY=x python tests/test_helena_route.py` — the new gate case asserts `calls == []` and `chat_completion.call_count == 0`; if the gate is removed, the AI runs, a send fires, and both asserts fail.

## Out of scope

Deliberately left alone — not forgotten:

- **Follow-up scheduling.** Helena has none; the Chatwoot `_update_follow_up_and_schedule` step is NOT replicated in the gate.
- **Re-enabling the AI.** Setting `status` back to `True` stays a manual DB action, as in Chatwoot. No event or command re-enables the AI here.
- **Setting `status=False` (escalation).** That is the `transfer_to_human` tool — a separate ticket. This ticket only reads and honours an already-set `status`.
- **Attachments** (audio transcription, image/video/file descriptive phrase) — separate ticket. The gate saves the MVP-resolved `text` as-is.
