# 06 — Comentário de quem já conversou: histórico na geração e DM no histórico

**What to build:** When a Comentário elegível comes from a person who already has a Customer in the Company, two things change. First, the DM de boas-vindas is generated knowing that conversation: the previous messages go to the model as real chat turns (`user` / `assistant`), before the message that asks for the DM. Second, once the DM is sent, it is written right away as an assistant message into that same session's ChatHistory, and the Origem do comentário goes into that Customer's lead context — so if the person answers inside a session that is still open, the AI already knows what it said and which post the person came from. The person is found by the same rule ticket 04 uses, in the opposite direction: the last 13 digits of the comment's author id against the sender id stored on the Customer. Only the most recent Customer of that person is used. The welcome record stays pendente, so an answer that opens a new Helena session is still matched by ticket 04. A person with no Customer is treated exactly as today.

**Blocked by:** 04 — Casamento da Boas-vindas pendente no canal Helena (this ticket reuses its suffix rule and its Origem do comentário shape, and changes its lookup).

**Spec:** [spec.md](./spec.md)

**Status:** ready-for-agent

## Context

**Why this exists.** Ticket 04 only acts on the first message of a *new* Helena session. Real traffic on 2026-10-05 showed the other case: a person with an open session answers the DM inside that session, where the AI knows nothing about the DM, and the welcome record is never matched. This ticket covers that case at the moment the DM is sent, when the engine can already tell who the person is.

**Where the person's id is on the Customer.** `HelenaService.process_webhook` stores `details.from` in `Customer.custom_information["phone"]`. On Instagram that value is `+` followed by the last 13 digits of the person's Instagram id (see ticket 04, "Validated on real traffic"). In Company 5, 153 of 154 Customers have it. A check with a real id (`2581680435579158` → `+1680435579158`) returned two Customers of the same person, one per Helena session.

**Decided: the lookup.** Company, `custom_information->>'phone'` equal to `"+" + author_id[-13:]`, most recent by creation date, limit 1. It runs only when the author id is all digits and at least 13 long. No Customer → nothing in this ticket runs. There is no index on that JSON key; the table is small today.

**Decided: only the most recent Customer.** Older sessions of the same person are ignored, for the history and for the write.

**Decided: the history goes as chat turns, not as a placeholder.** `ChatHistoryRepository.get_history_with_orphan_handling(session_id, company_id)` already returns the last 20 rows in order. Keep only rows with role `user` or `assistant` and non-empty text content; tool rows and assistant rows that only carry tool calls are dropped, because the generation call has no tools and an orphan tool message is rejected by the API. The request becomes:

```
[ ...previous turns (user / assistant)...,  user: <the Company's prompt, filled> ]
```

The filled prompt stays the last `user` message, as ticket 03 decided: still no `system`/`developer` message, no tools, the same strict JSON schema. With no Customer the request is the single message it is today.

**The prompt row has to say what the earlier turns are.** `[NOME]`, `[COMENTARIO]` and `[LEGENDA]` do not change. The text of the Company's `instagram_welcome_dm` prompt needs one sentence telling the model that earlier messages, when present, are the previous conversation with this person and that the DM must continue from it instead of introducing itself again. That row lives in the production database; changing it is the owner's step, not code.

**Decided: what is written when the DM is sent.** After the DM is accepted by Meta and the record is pendente, in one commit:

- a `ChatHistory` row in the found Customer's session: `role="assistant"`, `content` = the DM text, `agent_id` / `sub_agent_id` / `company_id` from the Customer, `isHuman=False`;
- the Origem do comentário merged into that Customer's `customer_context`, in the exact shape ticket 04 decided (`origem_comentario_instagram`, `produto` omitted when null). Reuse what ticket 04 wrote to build it; do not write a second builder.

It is written even when the Customer's Gate de IA is closed: nothing is sent to anyone, and the history stays complete for when the AI is turned back on.

**Decided: the record stays pendente.** At send time the engine cannot know whether the answer will land in the old session or open a new one. Leaving the record pendente lets ticket 04 match a new session. When that happens the DM ends up in both sessions' histories; they are different `sessionId`s and the old one is no longer read.

**Decided: a Boas-vindas pendente is only matched for 7 days.** Without a limit, a person who answered inside the open session leaves a record pendente forever, and a new session weeks later would be matched to an old DM. Ticket 04's lookup gets one more filter: creation date within the last 7 days — the window Meta gives to answer a private reply. This replaces ticket 04's "it does not expire".

**Decided: failure handling.** Neither step may cost the welcome:

- lookup or history read fails before the generation → `logger.exception`, generate with the single message, as if the person had no Customer;
- the write after the DM fails → `logger.exception`, `rollback`, continue to the Resposta pública. The record is already pendente at that point; do not mark it falhou.

No critical alert in either case.

**Database warning.** The local `.env` points at the production database. Run the tests only against a throwaway Postgres; ticket 04's "Database warning" has the recipe.

## Tasks

**1. Find the person's Customer**

- [x] Add one method to `CustomerRepository` that returns the most recent Customer of a Company whose `custom_information["phone"]` equals a given value, or `None`. It does not commit.
- [x] In `handle_comment`, after the reservation and before the generation, build the key (`"+" + author_id[-13:]`, only when the author id is all digits and at least 13 long) and call it. Keep the Customer's `sessionId`, `agent_id`, `sub_agent_id` and `customer_context` in locals right away — the flow has awaits and a possible rollback ahead.
  - Implemented as: only the Customer's `id` is kept; `write_dm_to_session` re-reads the Customer and the record by id, so the `customer_context` merge never starts from a value read before the DM was generated.
- [x] Follow the repo's code-comment rule: comment only a non-obvious why, in one terse line.

**2. Generate with the conversation**

- [x] When a Customer was found, load its history with `get_history_with_orphan_handling`, keep the `user` / `assistant` rows with non-empty text, and send them as `OpenAIMessage`s before the filled prompt.
- [x] Wrap lookup and history read in one `try/except Exception` that logs, rolls back and falls back to the single-message request.
- [x] Change nothing else in the generation: model, reasoning effort, response format and placeholder substitution stay as they are.

**3. Write the DM into the session**

- [x] Add one method to `InstagramWelcomeRepository` (or reuse ticket 04's, if it can take a flag for "do not mark casada") that adds the assistant `ChatHistory` row and the `customer_context` merge and commits once.
- [x] Call it in `handle_comment` right after `mark_pending`, only when a Customer was found, inside its own `try/except Exception` (log, rollback, continue).
- [x] Do not change the record's state, matched `sessionId` or match date here.

**4. Limit the match to 7 days**

- [x] In the lookup ticket 04 added for the pending record, add the filter "created within the last 7 days".
- [x] In `ticket_04.md`, replace the Out of scope line "Expiry of a Boas-vindas pendente. It does not expire." with a pointer to this ticket.

**5. Tests — `tests/test_instagram_route.py`**

- [x] Case — person with a Customer: seed a Customer with that `phone` and a history holding user, assistant and tool rows. Assert the messages that reached the LLM stub are the user/assistant turns in order followed by the filled prompt as the last `user` message, with no tool row; assert that session's ChatHistory gained one assistant row with the DM text; assert `customer_context` holds `origem_comentario_instagram`; assert the record is still pendente.
- [x] Case — person without a Customer: one message reaches the LLM stub and no ChatHistory row is written.
- [x] Case — two Customers with the same `phone`: only the most recent one's history is sent and only it receives the DM row.
- [x] Case — lookup raises: the DM is still sent with the single-message request and the Resposta pública is published.
- [x] Case — the write after the DM raises: the Resposta pública is still published and the record is pendente.
- [x] In `tests/test_helena_route.py`, add a case for task 4: a pending record older than 7 days is not matched on a new session.
- [x] Run both scripts against a throwaway Postgres and `ruff check` / `mypy` on the modules touched.

**6. Documentation and prompt**

- [x] Update `docs/file_reference/` for `handle_comment` (the two new steps and their failure behaviour) and for the new repository methods.
- [x] Hand the owner the sentence to add to the `instagram_welcome_dm` prompt of each Company that uses the feature. Do not write to the production database from this ticket.

## Acceptance criteria

- [x] A comment from a person with a Customer produces a generation request whose earlier messages are that Customer's user/assistant turns, with the filled prompt as the last `user` message and no tool message.
- [x] After the DM is sent, the same session's ChatHistory has the DM as an assistant message and the Customer's lead context has the Origem do comentário.
- [x] The welcome record of that person is pendente, and a later new Helena session within 7 days is still matched by ticket 04.
- [x] A pending record older than 7 days is never matched.
- [x] With several Customers for the same person, only the most recent is read and written.
- [x] A comment from a person without a Customer behaves exactly as before this ticket.
- [x] An error in the lookup, in the history read or in the write never prevents the DM, the conversation release or the Resposta pública.

## Out of scope

- **Asking Helena whether the old session is still open.** The engine writes to the most recent Customer either way.
- **Summarising the history or sending more than the 20 rows the repository already returns.**
- **History from older sessions of the same person.**
- **An index on `custom_information->>'phone'`.** Add it when the Customer table makes the lookup slow.
- **Using the Customer's saved name to fill `[NOME]`.** The earlier turns already carry it.
- **Marking the record casada when the person answers inside the open session.** It stays pendente and stops matching after 7 days.
- **Changing `ContextBuilder`, the Helena route or the Gate de IA.**
