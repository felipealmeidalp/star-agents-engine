# 03 — Elogio: DM leve e Resposta pública gerada

**What to build:** Someone praises a post ("Esta mesa é simplesmente maravilhosa") and the Decisions API classifies the comment as `elogio`. That person gets a light DM de boas-vindas instead of the sales DM. It is written by the Company's elogio DM prompt and behaves exactly like the comercial DM in every other way: the same strict JSON (`dm` + `product`), the same `[NOME]` / `[COMENTARIO]` / `[LEGENDA]` substitution, the same previous conversation for someone who is already a Customer, and the same product extraction for the Origem do comentário. The record becomes a Boas-vindas pendente as usual. Only after that does the engine generate a short Resposta pública with the Company's own public reply prompt ("Incrível, né? …") and publish it under the comment. The public reply prompt does not get the previous conversation, because its text is public. If the DM fails, no reply is generated or published, and the alert is the same as today. If generating or publishing the reply fails, only an alert fires and the welcome stays pendente. A Company that is missing either of the two elogio prompts gets nothing for an elogio: nothing is sent, nothing is written, and the person keeps their turn on the post.

**Blocked by:** 02 — Classificar cada comentário: comercial como hoje, emoji e outro calados

**Spec:** [spec.md](./spec.md)

**Status:** ready-for-agent

## Context

**Where this ticket plugs in.** Ticket 02 rebuilds `handle_comment` (module `app.services.instagram_welcome`) around the Categoria do comentário. The new order is: already-DM check → caption → classify → `so_emoji` / `outro` end → category-without-its-texts end → reserve with category → name → generate the DM with the category's prompt → send → release → `mark_pending` → write to the Customer session → Resposta pública. Ticket 02 also adds a per-category action table to the welcome service. In it, `comercial` is wired (DM prompt `instagram_welcome_dm`, Resposta pública = the Company's fixed `instagram_public_reply`), while `elogio` and `reclamacao` are left unwired, so today they behave as "category without its texts". **This ticket fills in only the `elogio` entry, plus the Resposta pública step it needs.** Get the real names of the table, of the category stub in the tests and of the category column from ticket 02's code. Nothing below re-specifies them. The base this ticket starts from is today's `handle_comment`. Ticket 02 keeps these pieces and only reorders them:

- `DM_RESPONSE_FORMAT`: strict JSON schema `instagram_welcome_dm` with `dm` (string) and `product` (string or null). The elogio DM uses it as is.
- Placeholder substitution: one `values` dict `{"NOME": name, "COMENTARIO": comment.text, "LEGENDA": caption}` applied in a **single pass** with `re.sub(r"\[(NOME|COMENTARIO|LEGENDA)\]", lambda m: values[m.group(1)], prompt)`. A single pass means that a placeholder typed inside third-party text is never substituted again.
- `_previous_conversation(db, company_id, comment)` returns `(customer_id | None, turns)`. The turns go before the filled prompt in the DM request only.
- The generation call: `OpenAIService(api_key).chat_completion(OpenAIPayload(model=config.model, reasoning_effort=config.reasoning_effort, messages=[...], response_format=...))`, with `api_key` from `CompanyRepository.get_openai_api_key` and no tools. Text comes from `response.choices[0].message.content`, then `json.loads`. `OpenAIService` already maps `response_format` to the Responses API `text.format` and needs no change.
- The DM path's failure handling, from the reservation up to `mark_pending`: rollback, `mark_failed`, alert `INSTAGRAM_WELCOME_FAILED` with the step, `return`. This path ends the function **before** the Resposta pública step. That alone gives "elogio whose DM fails → reply neither generated nor published", so no new code is needed for it, only a test.
- The Resposta pública step at the end: `InstagramClient.reply_to_comment(comment_id, text, access_token)` in its own `try/except Exception` that only fires `send_critical_alert("INSTAGRAM_PUBLIC_REPLY_FAILED", "instagram_welcome.py:handle_comment", e, company_id=..., extra=f"comment=... media=...")`. The record stays pendente. It runs after the Customer session write (`write_dm_to_session`), whose own failure is only logged.

**Prompt rows (pinned across all tickets).** Both live in `prompts`, `reason = "instagram_welcome"`, one row per Company, and are read with `PromptRepository.get_prompt_with_config(company_id, name, "instagram_welcome")`. Each row has its own `model` and `reasoning_effort`. Neither needs a schema change or a new repository method.

- `instagram_welcome_dm_elogio`: the elogio DM. Same placeholders, same output as `instagram_welcome_dm`.
- `instagram_public_reply_elogio`: the elogio Resposta pública. Same placeholders, its own output format (below).

The 7ball rows (Company 5) are inserted by ticket 05, not here. This ticket ships no seed, migration or data.

**Decided: "elogio without its texts" means either row is missing.** Both rows are read before the reservation, at the point where ticket 02 reads the category's DM prompt. If either is `None`: `logger.info` (Company id + category, no comment text), `return`. Nothing is sent, nothing is written, the turn is not spent. That is ticket 02's "category without its texts" rule applied to a category with two texts. Without this, a Company missing only the reply prompt would get an elogio DM with no reply, or worse, would have a reservation written for a flow that cannot finish. Keep the reply prompt's `PromptWithConfig` in a local for after the DM; it is a `NamedTuple`, so a rollback in between cannot expire it. Elogio does **not** read the Company's fixed `instagram_public_reply`; ticket 02 made that text a requirement of `comercial` only.

**Decided: the public reply's strict JSON.** A new module constant next to `DM_RESPONSE_FORMAT`, same shape, one string field:

```python
PUBLIC_REPLY_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "instagram_public_reply",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"reply": {"type": "string"}},
            "required": ["reply"],
            "additionalProperties": False,
        },
    },
}
```

The reply text is `json.loads(content)["reply"]`. If it is blank after `.strip()`, that is an error, the same rule as an empty `dm`. The engine does not truncate; the prompt is what keeps the text short.

**Decided: how the reply is generated.** It is the same call as the DM, with three differences: the reply prompt's `model` / `reasoning_effort`, `PUBLIC_REPLY_RESPONSE_FORMAT`, and **a single `user` message holding the filled reply prompt, with no previous turns**, because the text is public. Reuse the `api_key` and the `values` dict already computed for the DM. Do not fetch the key or the name again. Fill the prompt with the same single-pass substitution: move the existing `re.sub` expression into one small module-level function and call it for both prompts, so the regex is not written twice. If ticket 02 already did this, reuse its function. The previous conversation and `customer_id` are not touched by the reply.

**Decided: when the reply is generated.** Inside the existing Resposta pública step, so only after the DM went out, the record was marked pendente and the Customer session write ran. The order in the spec is `... → grava pendente → quem comentou já é Customer → grava DM e Origem na sessão → elogio: gera e publica a resposta`. For `elogio` the step is: generate the text, then `reply_to_comment` with it, both inside the step's existing `try/except`. For `comercial` it stays the fixed text, unchanged.

**Decided: the alert.** Reuse `INSTAGRAM_PUBLIC_REPLY_FAILED` for both a failed generation (OpenAI error, invalid JSON, blank `reply`) and a failed publication. One `try/except Exception`, the alert, nothing else: no `mark_failed`, no rollback (the step touches no database), no retry. The record stays pendente and the person's turn stays spent. `logger.exception` already shows which of the two calls failed. Do not log the generated text.

**What does not change for elogio.** The record's category is written as `elogio` by ticket 02's reservation. `write_dm_to_session` writes the elogio DM and the Origem do comentário (with the product the elogio DM call extracted) exactly as for comercial. The casamento in the Helena channel treats elogio like comercial (spec, "Casamento no canal Helena"). Nothing in `HelenaService`, `InstagramWelcomeRepository`, `InstagramClient`, `OpenAIService` or `PromptRepository` changes.

**Tests: how `tests/test_instagram_route.py` works.** It is a script (`asyncio.run(main())`, plain `assert`s, no pytest) that posts signed events through `httpx.ASGITransport`. `_post_welcome(*payloads, failing=None, media=None, llm_content=None)` patches the `InstagramClient` methods at class level and records each call into `calls` through `_record(name, *args)`. `_record` raises `httpx.ConnectError` when `failing` equals `name` or `f"{name}:{args[0]}"`. It also patches `OpenAIService.chat_completion` with `_chat_completion(self, llm_payload)`, which appends `(self.client.api_key, llm_payload)` to `llm_calls` and today returns the same DM JSON for every call (or `llm_content`). Finally it patches `send_critical_alert` in `app.services.instagram_welcome` and in the route, appending the alert type to `alerts`. Ticket 02 adds the Decisions stub and a way to choose the category per call; use it with `elogio`. Other helpers: `set_prompt(present)` seeds or removes the `instagram_welcome_dm` row (model `gpt-test-welcome`, effort `low`); `take_snapshot` / `restore_snapshot` save and put back Company 5's Instagram columns and prompt rows; `clear_welcomes`, `clear_customers`, `seed_customer(session_id, history)` seeds a Customer for `KNOWN_AUTHOR` (phone `KNOWN_PHONE`); `welcome_states()` returns `(status, session_id, matched_at)` for `POST`; `case_commenter_with_customer` is the model for asserting previous turns. Two LLM calls happen in one elogio flow, so the stub has to tell them apart by `llm_payload.response_format["json_schema"]["name"]`.

**Domain terms** (as in `CONTEXT.md`): Categoria do comentário, Comentário elegível, DM de boas-vindas, Boas-vindas pendente, Resposta pública, Origem do comentário, Customer, Company.

**Code comment rules (from the repo owner, mandatory).** Do not comment what the code already says. Comment only a non-obvious *why*, such as a restriction, a pitfall or a workaround. When a comment is needed, keep it to one concise line. No inflated docstrings that repeat the signature, types or parameter names.

**Environment pitfalls.**

- The local `.env` points at the **production** database. Never run the test script or `alembic` without `DATABASE_URL` pointing at a disposable Postgres. Worktrees have no `.env` at all.
- Disposable database for `tests/test_instagram_route.py`: Postgres in a container or Homebrew `postgresql@17` in the scratchpad (TCP only, `unix_socket_directories=''`). Run `Base.metadata.create_all` for every table **except** `instagram_welcomes`, insert an `alembic_version` row at `010`, run `alembic upgrade head` (this creates `instagram_welcomes` with its partial unique index, plus ticket 02's category column), then seed Company 5 including the `"openAi_apiKey"` column.
- Always run with `ALERT_ENABLED=false`. The alerter is on by default with a real key, and an unpatched `send_critical_alert` sends a real WhatsApp message.
- Company 5 in production is a real client (7ball). The test already restores what it stamps on Company 5 in a `finally`, so keep that for the new prompt rows.
- `/docs` is gitignored, so in a worktree `docs/file_reference/` does not exist. The doc task asks for the text in the final report, to be applied on the main checkout.
- Ticket 04 (reclamação) edits the same `handle_comment`, the same action table and the same test script in parallel, filling in the `reclamacao` entry. Expect a merge conflict there. Resolve it by keeping both entries.

## Tasks

**1. Before writing code**

- [ ] Read ticket 02's merged code in `app.services.instagram_welcome` and `tests/test_instagram_route.py` and note the real names of: the per-category action table, the point before the reservation where the category's DM prompt is read, the Resposta pública step, the Decisions stub in `_post_welcome` and how a test picks the category, and any test helper ticket 02 added to seed or count prompts and welcome rows. Use those names everywhere below.
- [ ] Follow the code comment rules in Context in everything this ticket writes.

**2. Wire `elogio` in the welcome service**

- [ ] Add `PUBLIC_REPLY_RESPONSE_FORMAT` to the welcome service module, next to `DM_RESPONSE_FORMAT`, exactly as decided in Context.
- [ ] In the per-category action table, wire `elogio`: its DM prompt name is `instagram_welcome_dm_elogio` and its Resposta pública is generated from `instagram_public_reply_elogio`. If the table can only say "fixed text" or "none" for the reply, add the third option ("generated from this prompt name") in the table's existing shape. Do not add a new structure or class.
- [ ] At the point before the reservation where the category's DM prompt is read, also read the reply prompt with `PromptRepository.get_prompt_with_config(company_id, "instagram_public_reply_elogio", "instagram_welcome")` when the category's reply is generated. If either row is `None`, take ticket 02's "category without its texts" exit: `logger.info`, `return`, no reservation. Keep the reply's `PromptWithConfig` in a local.
- [ ] Make sure the elogio DM goes through ticket 02's DM generation unchanged: the category's prompt row (`instagram_welcome_dm_elogio`) for `model`, `reasoning_effort` and text, `DM_RESPONSE_FORMAT`, the previous turns from `_previous_conversation`, and `product` into `mark_pending`. No elogio-specific branch in the DM generation.
- [ ] Move the single-pass placeholder `re.sub` into one module-level function (or reuse the one ticket 02 left) and use it for the DM prompt and the reply prompt with the same `values` dict.
- [ ] In the Resposta pública step, for a category whose reply is generated, inside the step's existing `try/except`: build the filled reply prompt, call `OpenAIService(api_key).chat_completion(OpenAIPayload(model=<reply config>.model, reasoning_effort=<reply config>.reasoning_effort, messages=[OpenAIMessage(role="user", content=<filled reply prompt>)], response_format=PUBLIC_REPLY_RESPONSE_FORMAT))`, read `json.loads(response.choices[0].message.content or "")["reply"]`, raise `ValueError` when it is blank after `.strip()`, then `InstagramClient.reply_to_comment(comment_id, <reply>, access_token)`. Reuse the `api_key` and `values` locals from the DM. No previous turns and no `tools`.
- [ ] Keep the step's `except` as it is: `logger.exception` and `send_critical_alert("INSTAGRAM_PUBLIC_REPLY_FAILED", ...)` with the same location and `extra`. No `mark_failed`, no rollback, no retry. Do not log the generated reply.
- [ ] Leave the `comercial` path (fixed text) and the DM path's failure handling exactly as ticket 02 left them.

**3. Tests: Seam 1, in `tests/test_instagram_route.py`**

- [ ] Add constants for the elogio test: an elogio DM prompt template and a reply prompt template that both use `[NOME]`, `[COMENTARIO]` and `[LEGENDA]` inside a `<dados>` block like `WELCOME_PROMPT`, their filled versions for the comment text the elogio cases use, distinct models (`gpt-test-elogio-dm`, `gpt-test-elogio-reply`) and a `REPLY_TEXT` (e.g. `"Incrível, né? Te chamei no direct!"`).
- [ ] Seed and remove the two elogio prompt rows the way `set_prompt` handles `instagram_welcome_dm`. Generalise that helper with the prompt name, model and text, or reuse what ticket 02 left; do not copy it. Seed both rows in `main()` before the elogio cases.
- [ ] Extend `take_snapshot` / `restore_snapshot` (as ticket 02 left them) so the two elogio rows of Company 5 are saved before the run and put back in the `finally`.
- [ ] In `_post_welcome`, make `_chat_completion` answer by `llm_payload.response_format["json_schema"]["name"]`. For `instagram_public_reply`, first call `_record("generate_public_reply", llm_payload.model)`, so the reply generation shows up in `calls` in order and can be failed with `failing="generate_public_reply"`, then return `reply_content` if given, else `json.dumps({"reply": REPLY_TEXT})`. Any other format keeps today's behaviour (`llm_content` or the DM JSON, `failing == "openai"` raising). Add the `reply_content: str | None = None` parameter.
- [ ] If ticket 02's "category without its texts" case uses `elogio` as its example, it breaks once `elogio` is wired. Move it to the missing-prompt case below (or to a category that is still unwired when this ticket merges) so the rule stays tested.
- [ ] Case: elogio. The category stub returns `elogio`; post one comment. Assert `calls` is the caption read, `send_private_reply` with `DM_TEXT`, `release_thread_control`, `("generate_public_reply", "gpt-test-elogio-reply")`, then `reply_to_comment` with `REPLY_TEXT`, in that order, plus whatever ticket 02 records for the classification. Assert `alerts == []`. Assert `llm_calls` has two entries, both with the Company's OpenAI key. The first has model `gpt-test-elogio-dm`, effort and format `instagram_welcome_dm`, and its messages are `[("user", <filled elogio DM prompt>)]`. The second has model `gpt-test-elogio-reply` and format `instagram_public_reply`, and its messages are `[("user", <filled reply prompt>)]`. Assert `welcome_states() == [("pendente", None, None)]`.
- [ ] Case: elogio from someone who is already a Customer. Seed a Customer for `KNOWN_AUTHOR` with a short user/assistant history, as `case_commenter_with_customer` does, and post an elogio. Assert the DM call's messages are the previous turns followed by the filled elogio DM prompt, and the reply call's messages are exactly `[("user", <filled reply prompt>)]`, with no previous turn. Assert the session got the DM row and `customer_context` has `origem_comentario_instagram` with the product.
- [ ] Case: elogio whose DM fails. Post with `failing="send_private_reply"`. Assert `calls` ends at `send_private_reply` (no `generate_public_reply`, no `reply_to_comment`), `llm_calls` holds only the DM call, and `alerts == ["INSTAGRAM_WELCOME_FAILED"]`.
- [ ] Case: elogio whose Resposta pública fails. For each `failing` in `("generate_public_reply", "reply_to_comment")`, and for each unusable `reply_content` in `("", "not json", '{"reply": "  "}')`, run `clear_welcomes()` and post an elogio. Assert the DM was sent and released, and `alerts == ["INSTAGRAM_PUBLIC_REPLY_FAILED"]`. Check the calls too: with `failing="reply_to_comment"` the `reply_to_comment` call is recorded and raised; in every other run `calls` holds no `reply_to_comment`. Assert `welcome_states() == [("pendente", None, None)]`. After the first iteration, post a second comment from the same person on the same post and assert no client call: the turn stayed spent.
- [ ] Case: elogio without its texts. For each of the two elogio prompt rows, remove only that row and post an elogio. Assert no `send_private_reply`, no `reply_to_comment`, `llm_calls == []`, `alerts == []` and `welcome_states() == []`. Then seed both rows again and post a new comment from the same person on the same post: assert the full elogio flow runs, because the turn was not spent. Also assert that an elogio still runs the full flow with the Company's `instagram_public_reply` empty (`stamp_config(ACCESS_TOKEN, "")`), and restore it after.
- [ ] Register the new cases in `main()` after ticket 02's cases, inside the existing `try/finally`, each printing its one-line `OK` like the others.
- [ ] Run the script against a disposable Postgres (`DATABASE_URL=postgresql+asyncpg://... API_KEY=x ALERT_ENABLED=false python tests/test_instagram_route.py`, see Context) and confirm every pre-existing case, including ticket 02's, still passes. Never run it against the database the local `.env` points to.
- [ ] Run `ruff check` (line length 100) and `mypy` on the modules touched and fix what they report in the new code.

**4. Documentation**

- [ ] In `docs/file_reference/app/services/services.md`, section `instagram_welcome.py`: document `PUBLIC_REPLY_RESPONSE_FORMAT`; in the flow, the elogio entry (DM by `instagram_welcome_dm_elogio`, both elogio rows required before the reservation, the Resposta pública generated by `instagram_public_reply_elogio` after the session write, a single message with no previous turns, the same placeholder substitution); in "Falhas", that a failed generation or publication of the elogio reply fires `INSTAGRAM_PUBLIC_REPLY_FAILED` and leaves the record `pendente`.
- [ ] In `docs/file_reference/app/repositories/repositories.md`, in the prompt convention block for the Instagram welcome, add the two elogio rows (`name`, `reason = "instagram_welcome"`, own `model` / `reasoning_effort`, same placeholders inside a block the prompt labels as data, the output of each: `{dm, product}` and `{reply}`) and that a Company missing either one gets nothing for an elogio.
- [ ] If working in a worktree (no `/docs`), put the exact text of both doc changes in the final report so it can be applied on the main checkout.

## Acceptance criteria

- [ ] An `elogio` comment gets a DM written by the `instagram_welcome_dm_elogio` row (its model and reasoning effort), with `DM_RESPONSE_FORMAT`, the same placeholder substitution and, for an existing Customer, the previous turns.
- [ ] The elogio DM's product reaches the record and the Origem do comentário exactly as for comercial. The DM and the Origem are written to an existing Customer's session.
- [ ] After the DM is sent and the record is pendente, a Resposta pública is generated by the `instagram_public_reply_elogio` row with `PUBLIC_REPLY_RESPONSE_FORMAT`, from a single `user` message with no previous conversation, and published with `reply_to_comment`, in that order.
- [ ] An elogio whose DM fails generates no reply and publishes none. The alert is `INSTAGRAM_WELCOME_FAILED`, as today.
- [ ] A failure generating the reply (error, invalid JSON, blank `reply`) or publishing it fires only `INSTAGRAM_PUBLIC_REPLY_FAILED`. The record stays `pendente` and the person's turn stays spent.
- [ ] A Company missing either elogio prompt row gets, for an elogio, no DM, no reply, no LLM call and no record. A later comment from the same person on the same post is still served once the rows exist.
- [ ] An elogio does not depend on the Company's fixed `instagram_public_reply`.
- [ ] The comercial flow and every pre-existing case in `tests/test_instagram_route.py` behave as before. The new cases pass against a disposable Postgres.
- [ ] `docs/file_reference/` describes the elogio entry, `PUBLIC_REPLY_RESPONSE_FORMAT`, the reply failure behaviour and the two prompt rows (or the report carries that text when `/docs` was not available).

## Out of scope

- **The classification, the `category` column, the new `handle_comment` order and the action table itself.** Ticket 02 delivers them; this ticket only fills in the `elogio` entry.
- **Reclamação** (its DM, no Resposta pública, the reusable Escalonamento humano, the casamento change). Ticket 04.
- **The 7ball data** (the five descriptions and the elogio / reclamação prompts for Company 5). Ticket 05. This ticket writes nothing to any real database.
- **Changes to the casamento in the Helena channel for elogio.** It behaves like comercial.
- **A new alert type, a retry, or marking the record falhou when the reply fails.**
- **Previous conversation, category or Customer data in the public reply prompt.** The text is public.
- **Truncating or validating the reply text** beyond rejecting a blank one. The prompt keeps it short.
- **Liking the comment.** ADR 0002.
- **Changes to `OpenAIService`, `InstagramClient`, `PromptRepository`, `InstagramWelcomeRepository` or `CONTEXT.md`.** The glossary already describes the elogio Resposta pública.
