# Execution plan — Boas-vindas por comentário do Instagram

6 tickets in 4 waves. Each wave runs in parallel; the next wave starts when the current one is merged.

> **Depends on** [`specs/helena-channel/spec.md`](../helena-channel/spec.md) and [`specs/helena-channel-parity/spec.md`](../helena-channel-parity/spec.md) — both already shipped. This plan assumes them.

> **Prerequisite no spec covers:** the Meta app's App Review with Advanced Access. The comments webhook needs it for real traffic. Ticket 01 (item 7) checks whether an account with a role on the app receives comment events without it; until then, tickets 02–05 can be built and tested with mocks, but not validated against Meta.

## Wave 1 — 2 tickets in parallel

| Ticket | Delivers | Touches |
|---|---|---|
| [01 — Validação real do fluxo comentário → DM → sessão Helena](./ticket_01.md) | The 8 validation facts captured from a real run and the chosen matching key, in `validation.md`. **Manual (`needs-human`)**, no engine code | `specs/instagram-comment-welcome/validation.md` (new), Meta app panel, n8n, Helena panel |
| [02 — Webhook do Instagram autenticado com resolução de Company](./ticket_02.md) | `GET`/`POST /api/instagram` with handshake and mandatory HMAC, background parsing, Company resolved by `instagram_account_id`, hand-off stub `handle_comment`, migration 010 | `app.routes.instagram` (new), `app.services.instagram_welcome` (new), `app.config`, `Company` model, `CompanyRepository`, `app.main`, migration 010, `tests/test_instagram_route.py` (new), docs |

The "Touches" column is a heads-up for merge time, not a reason to split the wave.

Ticket 02 runs on the working branch, in place. Ticket 01 is done by the owner outside the codebase and needs no worktree — its only output is `validation.md`, committed on the working branch whenever it is ready.

```sh
# 02 — no worktree: runs on the branch already checked out
# 01 — manual, no worktree
```

Ticket 01 no longer gates ticket 04: the real runs of 2026-10-05/06 answered it (see ticket 04, "Validated on real traffic"). Wave 2 starts as soon as 02 is merged.

## Wave 2 — 1 ticket

Starts once ticket 02 is merged.

| Ticket | Delivers | Blocked by | Touches |
|---|---|---|---|
| [03 — Comentário elegível → DM de boas-vindas → Resposta pública](./ticket_03.md) | The whole outbound flow: `instagram_welcomes` table (migration 011), reservation by partial unique index, `InstagramClient` (3 calls), structured DM generation from the Company's prompt, DM → pendente → Resposta pública, failure alerts, the rest of the Seam 1 tests | 02 | `app.services.instagram_welcome`, `app.services.instagram_client` (new), `InstagramWelcome` model, `InstagramWelcomeRepository` (new), migration 011, `tests/test_instagram_route.py`, docs |

Alone in its wave: no worktree, runs on the working branch.

## Wave 3 — 2 tickets in parallel

Starts once wave 2 is merged.

| Ticket | Delivers | Blocked by | Touches |
|---|---|---|---|
| [04 — Casamento da Boas-vindas pendente no canal Helena](./ticket_04.md) | On the first message of a new Helena session, the DM becomes the first assistant message in ChatHistory, the Origem do comentário enters `customer_context`, the record goes to casada; echo protection; Seam 2 tests. Matches by the suffix of the author id against `details.from` | 03 | `HelenaService.process_webhook`, `InstagramWelcomeRepository`, `HelenaClient` (Path B only), `tests/test_helena_route.py`, docs |
| [05 — Renovação semanal do token do Instagram](./ticket_05.md) | Weekly loop in `lifespan` that renews and saves each Company's Instagram token, critical alert per failing Company, test of the function behind the loop | 03 | `InstagramClient` (4th call), `CompanyRepository`, new renewal module, `app.services.product_sync.scheduler` (helper made public), `app.main`, new test script, docs |

Ticket 04 runs on the working branch, in place. The other gets a worktree:

```sh
# 04 — no worktree: runs on the branch already checked out
git worktree add ../instagram-comment-welcome-05 -b instagram-comment-welcome/05-renovacao-token
```

## Wave 4 — 1 ticket

Starts once ticket 04 is merged.

| Ticket | Delivers | Blocked by | Touches |
|---|---|---|---|
| [06 — Comentário de quem já conversou: histórico na geração e DM no histórico](./ticket_06.md) | For a commenter who already has a Customer: previous conversation sent as chat turns to the DM generation, DM written into that session's ChatHistory at send time, Origem do comentário in the lead context, and the 7-day limit on ticket 04's match | 04 | `app.services.instagram_welcome`, `CustomerRepository`, `InstagramWelcomeRepository`, both route test scripts, docs |

Alone in its wave: no worktree, runs on the working branch.

## Dependency graph

```
01 ─────────────┐
                ├──> 04
02 ──> 03 ──────┤
                └──> 05
```

## Expected merge conflicts

Tickets in the same wave whose work overlaps. They still run in parallel — this is just where the orchestrator should expect to resolve conflicts when merging the wave:

- **04 and 05** — both add sections to the same `docs/file_reference` files (repositories, services, app index). Code does not overlap: 04 edits the Helena service and the welcome-record repository; 05 edits the Instagram client, `CompanyRepository`, the scheduler and `lifespan`.
- **01 and 02** — none.

## Critical path

02 → 03 → 04. Ticket 01 is manual and may wait on the App Review, but nothing waits on it any more.

## Notes for the orchestrator

- **Migrations 010 and 011 must not be run locally without the owner's explicit go** — the local `.env` points at the production database. The DB-dependent test cases of 02, 03 and 04 write to whatever database the script points at.
- If ticket 01 finds different field names in the real comment event or in Meta's responses, the adjustment lands in `parse_comments` (02) and `InstagramClient` (03); neither ticket waits for it.
