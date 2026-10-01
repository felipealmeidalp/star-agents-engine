# 05 — Renovação semanal do token do Instagram

**What to build:** The Instagram access token of a Company stops expiring every 60 days without anyone touching it. Once a week, an asyncio loop in the app `lifespan` — same mould as the Sync semanal — goes through every Company with Instagram configured, exchanges its long-lived token for another 60-day one through the Meta call, and saves the new token on the Company. When the renewal of a Company fails, the maintainer gets a critical alert naming that Company through the existing alerter, and the remaining Companies are still renewed. With no Company configured, the loop wakes up, finds nothing and goes back to sleep.

**Blocked by:** 03 — Comentário elegível → DM de boas-vindas → Resposta pública (which itself comes after 02).

**Spec:** [spec.md](./spec.md)

**Status:** ready-for-agent

## Context

**What already exists when this ticket starts (from 02 and 03 — read the real names in the code, do not guess them).**

- 02 added the Instagram columns to `Company` in `app.models.tables`: the Instagram account id, the Instagram access token and the Resposta pública text. This ticket reads the first two and overwrites the token.
- 03 created the Instagram API client with three calls (read the post caption, send the DM, reply to the comment). This ticket **adds the fourth call to that same client** — it does not create a client. Copy how the three existing calls build the request, set the timeout and raise on an error response.

**The pattern to copy: the Sync semanal loop** (`app.services.product_sync.scheduler`).

- `_weekly_loop()` is a `while True` that recomputes the delay on every turn with `_seconds_until_next_saturday(datetime.now(tz))`, logs it, `await asyncio.sleep(delay)`, then runs the work inside `try/except Exception` that does `logger.exception(...)` plus `send_critical_alert(...)`, so one bad run never kills the loop. The delay helper is tz-aware and always returns > 0.
- `start_weekly_sync()` is just `asyncio.create_task(_weekly_loop())` and returns the task; the caller keeps the handle.
- `_run_additive_sync()` is the thin wrapper that opens its own `AsyncSessionLocal()` session and calls the tested function (`run_sync`) with the repository injected. The loop holds no logic.
- In `lifespan` (`app.main`), the task is started after the RabbitMQ block and, on shutdown, cancelled and awaited inside `try/except asyncio.CancelledError`.

**Toggle: none.** The Sync semanal start is gated by `settings.product_sync_enabled` (default off) because it launches Playwright against a site. This loop needs no gate: the spec asks for none, and with no Company configured it is a no-op. Start it unconditionally.

**Schedule: the same slot as the Sync semanal — Saturday 00:00 in `settings.product_sync_timezone`.** Reuse the existing delay helper instead of writing a second "next weekly slot" calculation. Why this slot is good enough: a 60-day token leaves about eight weekly attempts before it expires, so the hour is irrelevant, and the renewal is one small HTTP call per Company, so sharing the slot with the product sync costs nothing. Do not add a setting for the day, hour or timezone.

Do not replace the slot with "run at startup, then every 7 days": deploys restart the app often, which would either renew on every deploy or never reach day 7.

**"Company with Instagram configured", for this ticket:** a Company whose Instagram account id and Instagram access token are both filled. The Resposta pública text and the boas-vindas prompt do not matter for keeping a token alive.

**The alerter** (`app.utils.alerter`): `send_critical_alert(error_type, location, error, contact_id=None, company_id=None, extra=None)` is fire-and-forget, never raises, and needs a running event loop (it creates tasks). Passing `company_id` is what names the Company: it becomes the `*Company:*` line of the WhatsApp message and the `company_id` of the `error_logs` row. Use one fixed `error_type` for a failed renewal, in the same naming as the alerts 03 added.

Known and accepted: the alerter rate-limits the WhatsApp message per `error_type` (`settings.alert_rate_limit_seconds`, 5 minutes, in memory). If two Companies fail in the same run, both are persisted to `error_logs` with their `company_id`, but only the first WhatsApp message goes out. Do not work around it (no per-Company `error_type`, no alerter change).

**The repository** (`app.repositories.company`, `CompanyRepository`): there is no method that lists Companies and none that updates a single column by itself. The write pattern to copy is in `add_contact_to_allowed_list`: `update(Company).where(Company.id == company_id).values(...)` followed by `await self.db.commit()`. Committing per Company is what keeps one Company's failure from undoing another's saved token.

**Tests in this repo are scripts, not pytest.** `tests/test_product_sync.py` is the model: a self-contained `asyncio.run(main())` with plain asserts, `DATABASE_URL` and `API_KEY` set with `os.environ.setdefault` before importing the app, an in-memory `FakeRepo` standing in for the repository, and one `case_*` function per behaviour printing `OK`. `tests/test_helena_route.py` shows how to replace an outgoing client call and `send_critical_alert` with `unittest.mock.patch` (the alerter is patched at the name imported by the module under test). No database is needed here — which matters, because the local `.env` points at production.

**Single replica.** Like the Sync semanal and the in-memory locks of the engine, this loop assumes one replica. With two, each would renew every token once a week. Do not add a distributed lock.

**Domain terms:** Company, Sync semanal, Resposta pública, DM de boas-vindas. Say "loop", never cron, agendador or job.

## Tasks

**1. Fourth call on the Instagram API client**

- [ ] Add the token renewal call to the Instagram API client created in 03: it receives the Company's current long-lived Instagram access token and returns the new one. The spec describes it only as "exchange the long-lived token for another 60-day one" — confirm the endpoint, parameters and response field against Meta's Instagram Login docs before writing it.
- [ ] Make it fail the same way the three existing calls fail (same exception on an error response or timeout), and also fail when the response comes back without a token, so the caller never saves an empty value.

**2. Repository**

- [ ] Add to `CompanyRepository` a method that returns the Companies whose Instagram account id and Instagram access token are both filled.
- [ ] Add to `CompanyRepository` a method that overwrites the Instagram access token of one Company by id and commits, following the `update(...)` + `commit()` pattern of `add_contact_to_allowed_list`.

**3. The function the loop calls**

- [ ] Create a module for the renewal next to the Instagram client and service from 03, holding an async function that receives a `CompanyRepository` and renews every Company the list method returns: call the client's renewal with the Company's current token, then save the returned token with the update method.
- [ ] Wrap each Company in its own `try/except Exception`: on failure, `logger.exception(...)`, call `send_critical_alert` with the fixed renewal `error_type`, the location string, the error and `company_id=company.id`, leave that Company's token untouched, and continue with the next Company.
- [ ] Never write a token, old or new, to a log line or to the alert's `error`/`extra`.

**4. The loop and the lifespan**

- [ ] In `app.services.product_sync.scheduler`, drop the leading underscore of `_seconds_until_next_saturday` (updating its uses in that module, including the `__main__` self-check) so the renewal module can import it without reaching into a private name. Change nothing else there.
- [ ] In the renewal module, add the loop as a copy of `_weekly_loop`: compute the delay with that helper and `ZoneInfo(settings.product_sync_timezone)`, log it, sleep, then open an `AsyncSessionLocal()` session and call the function from theme 3 with a `CompanyRepository` on it. Keep the outer `try/except Exception` with `logger.exception` + `send_critical_alert` (no `company_id`) for failures outside a single Company, such as the list query itself.
- [ ] Add a start function in the mould of `start_weekly_sync()` that creates the task and returns it.
- [ ] In `lifespan`, start the loop unconditionally next to the Sync semanal start, print the started line in the same style, and on shutdown cancel and await the task exactly as `weekly_sync_task` is handled.

**5. Test**

- [ ] Create a test script in the house style of `tests/test_product_sync.py` that drives the function from theme 3 with an in-memory fake repository (list + update token) and with the client's renewal call and `send_critical_alert` replaced via `unittest.mock.patch`. No database, no network.
- [ ] Case success: one Company configured, the renewal returns a new token → the fake repository holds the new token for that Company and no alert was sent.
- [ ] Case failure: the renewal raises → the Company keeps its old token and `send_critical_alert` was called once with that Company's id as `company_id`.
- [ ] Case isolation: two Companies, the renewal raises for the first and succeeds for the second → the second holds its new token and the alert names only the first.
- [ ] Run the script and the `scheduler` self-check (`python -m app.services.product_sync.scheduler`) and confirm both print their OK lines.

**6. Code comments and docs**

- [ ] Follow the repo's code-comment rule in everything this ticket adds: no comment that restates what the code says; comment only a non-obvious why, in one terse line (for example, why the slot is shared with the Sync semanal).
- [ ] Update `docs/file_reference`: the renewal module and the fourth client call in the same reference file where 03 documented the Instagram client and service; the two new `CompanyRepository` methods in the repositories reference; and the `lifespan` description in the app reference, which today mentions neither the Sync semanal nor this loop.

## Acceptance criteria

- [ ] On startup the app logs that the Instagram token renewal loop started and how many seconds remain until the next run, with no setting needed to turn it on.
- [ ] Shutting the app down cancels the loop without an error or a pending-task warning.
- [ ] Calling the renewal function with a Company that has Instagram configured and a client that returns a new token leaves that Company with the new token saved.
- [ ] Calling it with a client that fails for a Company leaves that Company's token unchanged and sends one critical alert carrying that Company's id.
- [ ] With two Companies where the first fails, the second is still renewed.
- [ ] With no Company that has Instagram configured, the function makes no call to Meta, sends no alert and returns normally.
- [ ] No token value appears in any log line or alert.
- [ ] The new test script passes without a database; `tests/test_product_sync.py` and the `scheduler` self-check still pass.
- [ ] `docs/file_reference` describes the renewal module, the fourth client call, the two repository methods and the loop in `lifespan`.

## Out of scope

- Obtaining the first token or connecting the account — manual, written straight to the database (spec, Out of Scope).
- Storing or checking the token's expiry date, renewing "only when close to expiring", or retrying a failed renewal before the next weekly run. The weekly run is the retry.
- A toggle, a setting for the slot, or a manual endpoint to trigger the renewal.
- A distributed lock or anything else for more than one replica.
- Changing the alerter (including its per-`error_type` rate limit) or the Sync semanal's behaviour beyond making the delay helper importable.
- The webhook, the DM de boas-vindas and the Resposta pública (03) and the casamento da Boas-vindas pendente no canal Helena (04).
- New migrations or columns — 02 already delivered the ones this ticket uses.
