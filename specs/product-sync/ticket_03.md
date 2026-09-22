# 03 — Authenticated manual sync endpoint

**What to build:** An operator does an authenticated `POST` (header `X-API-Key`) and the additive product sync fires. If a sync is already running, the request gets `409` on the spot instead of starting a second one. The route lives in `app/routes/`, is protected by `verify_api_key`, and is registered in `app/main.py` like the other routers.

**Blocked by:** 02 — Módulo de sync de produtos (núcleo, dois modos)

**Spec:** [spec.md](./spec.md)

**Status:** ready-for-agent

## Context

**This is thin wiring on top of ticket 02.** No sync logic here — the endpoint just authenticates, checks/acquires the global lock, and triggers the additive mode of the sync module built in `app/services/product_sync/`. Spec: `Endpoint manual` + `Lock global`, User Stories 4, 7, 20.

**Route pattern (use it).** Routes are `APIRouter()` modules in `app/routes/` (`chat.py`, `chatwoot.py`, `health.py`, `helena.py`, `meta.py`, `voe.py`). Each exports `router = APIRouter()` with `@router.post(...)` async handlers. `health.py` is the simplest example; `voe.py` is the closest example of an auth-protected route. They are registered in `app/main.py` by extending the import line `from app.routes import chat, chatwoot, health, helena, meta, voe` and adding `app.include_router(<mod>.router, prefix="/api", tags=[...])`. The `prefix="/api"` is applied at registration, so the module path is `/products/sync` and the final route is `POST /api/products/sync`.

**Auth (use it).** `verify_api_key` from `app.dependencies` (defined in `app/dependencies/auth.py`) reads the `X-API-Key` header, compares against `settings.api_key`, and raises `401` when the header is missing or wrong. Apply it exactly as `voe.py` does: `_api_key: str = Depends(verify_api_key)` in the handler signature. No new auth code.

**Global lock + additive trigger.** The sync module from ticket 02 owns the single-run guarantee and exposes the additive sync function and the in-memory global lock. The endpoint's whole job: try to acquire that lock; if it's already held, return `409` immediately ("sync já em andamento"); if acquired, fire the additive sync. Reference ticket 02 by its real symbol names once they land — don't invent a parallel lock here, reuse the one the module exports.

**Decision — background, not inline (DECIDED).** The additive sync is slow (headless render of new products). Blocking the request until it finishes would make the operator wait and risks the client timing out. So: acquire the lock and fire the sync in the background (`asyncio.create_task`, or FastAPI `BackgroundTasks`), then respond `202 Accepted` right away. The `409` is the only synchronous refusal — it happens at the lock check, before anything is scheduled, which satisfies the spec's "disparar o manual enquanto já há um sync rodando seja recusado na hora". The background task must own lock release (release in a `finally`) so a crash mid-sync never wedges the lock.

**Test pattern (use it).** `tests/test_helena_route.py` is the house style: a self-contained `asyncio.run(main())` script with plain `assert`s, no pytest/conftest, driving the route via `httpx.ASGITransport(app=app)` (lifespan not triggered → no RabbitMQ/DB ping). Mock the sync module (patch its additive function to a no-op / control the lock state) — never run a real sync, no browser, no DB. Set `os.environ.setdefault("API_KEY", ...)` and `DATABASE_URL` placeholder at the top like that file does, so `app.config.Settings` imports.

## Tasks

**1. Manual sync endpoint (route + auth; registration; lock/409; test)**
- [x] Create `app/routes/products.py` with `router = APIRouter()` and one handler `@router.post("/products/sync")`, async, signature `_api_key: str = Depends(verify_api_key)` — mirror how `voe.py` applies the dependency. Import `verify_api_key` from `app.dependencies`.
- [x] In the handler, acquire the global lock exposed by `app/services/product_sync/` (ticket 02). If already held, `raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="sync já em andamento")`. If acquired, schedule the additive sync in the background (`asyncio.create_task` or `BackgroundTasks`) and return `202` with a small confirmation body. The background wrapper releases the lock in a `finally`.
- [x] Register the router in `app/main.py`: add `products` to `from app.routes import chat, chatwoot, health, helena, meta, voe` and add `app.include_router(products.router, prefix="/api", tags=["Products"])` alongside the others.
- [x] Add `tests/test_products_sync_route.py` in the style of `tests/test_helena_route.py` (self-contained `asyncio.run(main())`, plain asserts, `httpx.ASGITransport`, sync module mocked). Cover: (a) no `X-API-Key` → `401`; (b) lock already held → `409` (patch the lock to appear taken); optionally (c) valid key + free lock → `202` and the additive function was scheduled.

## Acceptance criteria

- [x] A request without `X-API-Key` gets `401`.
- [x] A request with a valid `X-API-Key` triggers the additive mode of the sync.
- [x] With the global lock already held, the endpoint responds `409` immediately (before scheduling any work).
- [x] The route is registered in `app/main.py` with `prefix="/api"`, so it answers at `POST /api/products/sync`.
- [x] The background task releases the lock even if the sync raises.
- [x] `tests/test_products_sync_route.py` proves the `401` and `409` cases with the sync module mocked (no real sync, no browser).

## Out of scope

- The sync core itself (ticket 02 — REST fetch, render, diff, persistence, the lock implementation).
- The weekly cron loop (ticket 04 — the `lifespan` scheduler).
- The full re-render mode (runs locally, no endpoint).
- Any render / REST / persistence logic — this ticket only authenticates, guards on the lock, and triggers.
