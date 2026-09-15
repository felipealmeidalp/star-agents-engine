# Execution plan — Canal Helena (MVP)

3 tickets in 3 waves. Each wave runs in parallel; the next wave starts when the current one is merged. Here every wave holds a single ticket (a purely linear chain), so each ticket runs alone, in place, on the working branch — no worktrees.

## Wave 1 — 1 ticket

| Ticket | Delivers | Touches |
|---|---|---|
| [01 — Multi-tenancy do Helena: colunas, migração, lookup e settings](./ticket_01.md) | `helena_token`/`helena_apikey` columns + migration 006, `CompanyRepository.get_by_helena_token`, Helena base URL/endpoint in config | `app/models/tables.py`, `app/repositories/company.py`, `migrations/versions/`, `app/config.py` |

Alone in its wave: no worktree, runs on the working branch.

## Wave 2 — 1 ticket

Starts once wave 1 is merged.

| Ticket | Delivers | Blocked by | Touches |
|---|---|---|---|
| [02 — Canal Helena ponta a ponta (webhook → IA → envio)](./ticket_02.md) | New `app/helena/` package (schemas, client, service) + `app/routes/helena.py`, registered in `main.py` — full vertical slice webhook → AI → send | 01 | `app/helena/` (new), `app/routes/helena.py` (new), `app/main.py` |

Alone in its wave: no worktree, runs on the working branch.

## Wave 3 — 1 ticket

Starts once wave 2 is merged.

| Ticket | Delivers | Blocked by | Touches |
|---|---|---|---|
| [03 — Testes de comportamento externo da rota Helena](./ticket_03.md) | Route-seam tests for the 5 spec cases, mocking `HelenaClient` + OpenAI | 02 | `tests/` (new `test_helena_route.py`), possibly `pyproject.toml` |

Alone in its wave: no worktree, runs on the working branch.

## Dependency graph

```
01 ──> 02 ──> 03
```

## Expected merge conflicts

None. Each wave has a single ticket, so there is no intra-wave overlap to reconcile.

## Critical path

`01 → 02 → 03` — the whole plan is the critical path. Wall-clock is the sum of the three, with no parallelism to exploit (each ticket genuinely gates the next: 02 needs the columns/lookup/config from 01; 03 tests the route/service/client that 02 builds).

## Research note

The spec cites "the Chatwoot webhook tests as the direct model to follow (prior art)" for ticket 03, but **those tests do not exist in the repo** — `tests/` holds only `test_categories_tool.py`, a standalone `asyncio.run` script (no pytest config, no `conftest.py`, pytest not in deps). Ticket 03 therefore stands up the route-test harness from scratch and documents the choice in-file. No sibling spec covers this gap; it is handled inside ticket 03.
