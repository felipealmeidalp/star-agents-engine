# Execution plan — Sync de produtos 7ball/Reebok

4 tickets em 3 ondas. Cada onda roda em paralelo; a próxima começa quando a atual é mergeada.

A cadeia é linear no início (01 → 02) e abre em leque no fim: 03 e 04 são wiring fino sobre 02 e rodam juntos.

> **Nota:** a seção `Depends On` da spec aponta para a própria spec — apenas sinaliza que a migration `008` já existe mas **não foi aplicada** ao banco. Isso não é dependência de outra spec; virou o ticket 01 (`alembic upgrade head` + Playwright no ambiente).

## Onda 1 — 1 ticket

| Ticket | Entrega | Touches |
|---|---|---|
| [01 — Apply migration 008 and add Playwright](./ticket_01.md) | Tabela `products` viva em produção + Playwright/Chromium na imagem | `pyproject.toml`, `poetry.lock`, `Dockerfile`; roda `alembic upgrade head` |

Sozinho na onda: sem worktree, roda no branch já em uso.

## Onda 2 — 1 ticket

Começa quando a onda 1 é mergeada.

| Ticket | Entrega | Blocked by | Touches |
|---|---|---|---|
| [02 — Product sync module (core, two modes)](./ticket_02.md) | Módulo de sync agnóstico ao gatilho, dois modos, lock global, testes | 01 | `app/services/product_sync/` (novo), `app/repositories/product.py` (novo), `tests/` |

Sozinho na onda: sem worktree, roda no branch já em uso.

## Onda 3 — 2 tickets em paralelo

Começa quando a onda 2 é mergeada.

| Ticket | Entrega | Blocked by | Touches |
|---|---|---|---|
| [03 — Authenticated manual sync endpoint](./ticket_03.md) | Rota `POST /api/products/sync` autenticada, 409 sob lock | 02 | `app/routes/products.py` (novo), `app/main.py`, `tests/` |
| [04 — Weekly asyncio sync loop in the lifespan](./ticket_04.md) | Loop no lifespan que dispara o sync aditivo todo sábado 00:00 | 02 | `app/services/product_sync/` (loop novo + `__init__.py`), `app/main.py` |

A coluna "Touches" é um aviso pro merge, não motivo pra serializar a onda.

O ticket 03 roda no branch já em uso, no lugar. O 04 ganha um worktree próprio:

```sh
# 03 — sem worktree: roda no branch já checked out
git worktree add ../product-sync-04 -b product-sync/04-weekly-loop
```

## Dependency graph

```
01 ──> 02 ──┬──> 03
            └──> 04
```

## Expected merge conflicts

Tickets da mesma onda cujo trabalho se sobrepõe. Rodam em paralelo mesmo assim — isto é só onde o orquestrador deve esperar resolver conflito ao mergear a onda 3:

- **03 e 04** — ambos editam `app/main.py`. Regiões distintas (03 registra o `products.router` na lista de `include_router`; 04 adiciona o import do `start_weekly_sync` e o `create_task`/`cancel` no `lifespan`), então o conflito é trivial, mas os dois tocam o mesmo arquivo.

## Critical path

`01 → 02 → 03` (ou `04`) — três ondas. O núcleo (02) é o gargalo: 03 e 04 só custam a onda 3 juntos, não uma cada.
