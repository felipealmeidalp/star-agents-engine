# Execution plan — Categoria do comentário

5 tickets in 2 waves. Each wave runs in parallel; the next wave starts when the current one is merged.

> **Depends on** [`specs/instagram-comment-welcome/spec.md`](../instagram-comment-welcome/spec.md) and [`specs/helena-channel-parity/spec.md`](../helena-channel-parity/spec.md) — both already shipped. This plan assumes they are in the codebase.

## Wave 1 — 3 tickets in parallel

| Ticket | Delivers | Touches |
|---|---|---|
| [01 — Escalonamento humano no Helena reutilizável](./ticket_01.md) | `escalate_helena_session(db, company_id, session_id)` extracted from `transfer_to_human`; tool behaviour unchanged | `app.services.tools.internal.transfer` |
| [02 — Classificar cada comentário: comercial como hoje, emoji e outro calados](./ticket_02.md) | Decisions classification before the reservation; comercial as today; emoji/outro silent; turn and failure rules; `category` column (migration 012) | welcome service, `OpenAIService`, `PromptRepository`, `InstagramWelcomeRepository`, `InstagramWelcome`, migrations, `tests/test_instagram_route.py` |
| [05 — Textos da 7ball no banco](./ticket_05.md) | The five category descriptions and three new prompts of Company 5, in production | production `prompts` table only (no repo files) |

The "Touches" column is a heads-up for merge time, not a reason to split the wave.

Ticket 01 runs on the working branch, in place. The rest get a worktree each:

```sh
# 01 — no worktree: runs on the branch already checked out
git worktree add ../instagram-comment-category-02 -b instagram-comment-category/02-classificacao
git worktree add ../instagram-comment-category-05 -b instagram-comment-category/05-textos-7ball
```

Ticket 05 writes to the production database and needs the user's approval of the texts before the INSERT. Its worktree will have no code to merge.

## Wave 2 — 2 tickets in parallel

Starts once wave 1 is merged.

| Ticket | Delivers | Blocked by | Touches |
|---|---|---|---|
| [03 — Elogio: DM leve e Resposta pública gerada](./ticket_03.md) | `elogio` entry: DM by its prompt, then a generated Resposta pública (`PUBLIC_REPLY_RESPONSE_FORMAT`, field `reply`) | 02 | welcome service (`handle_comment`, `CATEGORY_DM_PROMPTS`, Resposta pública step), `tests/test_instagram_route.py` |
| [04 — Reclamação: DM sem resposta pública e escalonamento automático](./ticket_04.md) | `reclamacao` entry: DM by its prompt, no Resposta pública, escalation at send time (existing Customer) and at the casamento in Helena | 01, 02 | welcome service, `InstagramWelcomeRepository.write_dm_to_session`, `HelenaService.process_webhook`, `tests/test_instagram_route.py`, `tests/test_helena_route.py` |

Ticket 03 runs on the working branch, in place. Ticket 04 gets a worktree:

```sh
# 03 — no worktree: runs on the branch already checked out
git worktree add ../instagram-comment-category-04 -b instagram-comment-category/04-reclamacao
```

## Dependency graph

```
01 ─────────────┐
02 ──┬──> 03    ├──> 04
     └──────────┘
05   (no edges; must be in the DB before 02 is deployed)
```

## Expected merge conflicts

- **03 and 04** — both add an entry to `CATEGORY_DM_PROMPTS` and rework the block after `mark_pending` in `handle_comment` (03: the generated Resposta pública; 04: no Resposta pública, escalation after `write_dm_to_session`). Both extend the OpenAI mock in `_post_welcome` of `tests/test_instagram_route.py` and the `instagram_welcome.py` section of `docs/file_reference/app/services/services.md`.
- Wave 1 has no code overlap: 01 only touches the transfer tool, 05 touches no files.

## Deploy order

1. Ticket 05's descriptions in the production DB **before** 02 is live: without the five descriptions, 7ball has no comment processed at all.
2. Migration 012 applied **before** the code from 02 is live: the ORM selects `category` on every read of `instagram_welcomes`, including the casamento in Helena.
3. Shipping 02 alone is safe: elogio and reclamação stay silent (no DM, turn not spent) until 03 and 04 land.

## Critical path

02 → 04 (01 runs alongside 02 in wave 1, so it adds no wall-clock). Two waves is the minimum.
