# 04 — Weekly asyncio sync loop in the lifespan

**What to build:** With nobody touching anything, every Saturday at midnight the additive sync runs on its own. A long-running asyncio task, started in the `lifespan` of `app/main.py` alongside the RabbitMQ consumers, sleeps until the next Saturday 00:00 (server timezone) and then fires the sync module (ticket 02) in additive mode. If a sync is already running (e.g. someone triggered the manual endpoint), the loop skips this round — it does not wait, does not queue — and goes back to sleep until the following Saturday. An exception inside a run is caught, logged/alerted, and the loop keeps going.

**Blocked by:** 02 — Módulo de sync de produtos (núcleo, dois modos)

**Spec:** [spec.md](./spec.md)

**Status:** ready-for-agent

## Context

This is thin wiring over ticket 02 — it does not reimplement any sync logic. It calls the additive-mode sync function of the module `app/services/product_sync/` (created in 02) and consults the same global lock that module exposes.

**How the lifespan runs tasks today.** `lifespan` in `app/main.py` is an `@asynccontextmanager`. On startup it tests the DB connection, connects to RabbitMQ and starts the consumers by awaiting `start_follow_up_consumer()` and `start_webhook_retry_consumer()`; on shutdown it closes the redis pool, closes RabbitMQ and disposes the engine. Note the difference from those consumers: `start_follow_up_consumer` / `start_webhook_retry_consumer` return right after registering a RabbitMQ callback — they are not long-running coroutines. The weekly loop *is* long-running, so it must be launched with `asyncio.create_task(...)`, its task handle kept, and on shutdown `.cancel()`'d and awaited (swallowing `CancelledError`) for a clean stop.

**Where the `start_...` lives.** Consumers live in their own packages (`app/followUp/`, `app/webhookRetry/`) whose `__init__.py` reexports the `start_...` (e.g. `from app.followUp.consumer import start_follow_up_consumer`). Follow the same shape for the loop: a module inside the sync package `app/services/product_sync/` (coherent with where ticket 02 put the module) exposing something like `start_weekly_sync()` — decide the exact name and reexport it from the package `__init__.py`. `app/main.py` imports it next to the other `start_...` imports.

**Next Saturday 00:00 + mp-vps timezone.** The Dockerfile is `FROM python:3.11-slim` and sets no `TZ`, so the container clock is **UTC**. The spec's `Further Notes` says "sábado 00:00 é do fuso do servidor; confirmar timezone do mp-vps na implementação" — so the target time must be computed in an *explicit* timezone, never naive local time. Prior art: `app/utils/alerter.py` already does `zoneinfo.ZoneInfo("America/Sao_Paulo")` for its timestamps. **Decision to register:** compute the next Saturday 00:00 with `zoneinfo`, in a timezone that is configurable (a setting, default `America/Sao_Paulo` — matching the alerter's convention — since the container is UTC and "Saturday midnight" is meant in the owner's local time). This keeps the offset correct even though the container is UTC. Put the timezone reasoning in one short comment, e.g. "sábado 00:00 no fuso do dono; container é UTC, daí o zoneinfo".

**Recomputed sleep (not one multi-day sleep).** A single `asyncio.sleep` spanning days is fragile (clock drift, wrong "next Saturday" after waking). **Decision to register:** each iteration recomputes the delta from "now" (in the configured tz) to the next Saturday 00:00 and sleeps exactly that delta; on wake it fires the sync, then loops and recomputes. When the current moment already *is* the target window, roll forward to the following Saturday so the loop never busy-loops. Cancellation must interrupt the sleep cleanly on shutdown.

**Skip under lock.** The global lock is defined in ticket 02. When the loop wakes and a sync is already running, it **skips the round** — it does not wait and does not enqueue. This differs from the manual endpoint (ticket 03), which answers 409. Consult the same lock object the sync module exposes; check-then-skip.

**Resilience.** An exception in a sync run must not kill the loop or the app. Wrap the run, log it, and alert fire-and-forget via `app/utils/alerter.py` → `send_critical_alert(error_type, location, error)` (the running-loop variant; `send_critical_alert_sync` is only for startup before the loop is spinning). Then continue to the next week. This mirrors the consumers, which never let an error take down the process.

## Tasks

**1. Weekly sync loop module + lifespan wiring** (loop module with `start_weekly_sync`; start as task on startup and cancel on shutdown; next-Saturday-00:00 calc with explicit tz; skip under global lock; per-run resilience/alert; recomputed sleep)
- [ ] Add a loop module inside the sync package `app/services/product_sync/` exposing a `start_weekly_sync()` (or equivalent name — register the chosen name), reexported from that package's `__init__.py`, following the `app/followUp` / `app/webhookRetry` `start_...` pattern.
- [ ] Implement the loop coroutine: recompute the delta to the next Saturday 00:00 using `zoneinfo` in a configurable timezone (default `America/Sao_Paulo`, matching `app/utils/alerter.py`), `asyncio.sleep` that delta, then act. Container is UTC — the tz-aware calc supplies the offset. One-line `why` comment on the tz, no docstring bloat.
- [ ] On wake, check the global lock from the sync module (ticket 02): if a sync is already running, skip this round (no wait, no queue) and loop back to sleep; otherwise call the sync function in **additive** mode.
- [ ] Wrap each run so an exception is caught, logged, and alerted fire-and-forget via `send_critical_alert(...)`, then continue to the next week — the loop must never die. Follow the consumers' resilience pattern.
- [ ] In `app/main.py` `lifespan`: import the `start_...` next to the other `start_...` imports; on startup launch the loop with `asyncio.create_task(...)` and keep the task handle; on shutdown `.cancel()` it and await it, swallowing `CancelledError`, in the existing shutdown block (near `close_redis_pool` / `close_rabbitmq_connection` / `engine.dispose`).

## Acceptance criteria

- [ ] On startup the loop is launched as a task (`asyncio.create_task`); on shutdown it is cancelled and awaited cleanly (no dangling task, `CancelledError` swallowed).
- [ ] The loop fires additive mode at Saturday 00:00 in the configured timezone; the "next Saturday 00:00" is computed tz-aware (not naive), so it is correct even though the container clock is UTC.
- [ ] The sleep is recomputed each iteration from the current time to the next Saturday 00:00, not a single multi-day sleep.
- [ ] If the global lock is already taken when the loop wakes, the round is skipped — no waiting, no queuing (distinct from the endpoint's 409).
- [ ] An exception during a sync run does not kill the loop nor the app; it is logged and alerted via `send_critical_alert`, and the loop proceeds to the next week.
- [ ] The `start_...` lives in a module inside `app/services/product_sync/` and is reexported from its `__init__.py`, matching the `app/followUp` / `app/webhookRetry` convention.

## Out of scope

- The sync core itself (ticket 02) — this ticket only calls it.
- The manual endpoint and its 409 response (ticket 03).
- The full re-render mode — the loop only fires additive mode.
- OS cron / RabbitMQ scheduling — the spec's decision is an asyncio loop in the lifespan; do not introduce either.
- Multi-worker / multi-replica coordination — deploy is 1 worker / 1 replica (confirmed in `docker-compose.yml`), so the loop never duplicates; distributed locking is not in scope.
