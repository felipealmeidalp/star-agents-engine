# 02 — Product sync module (core, two modes)

**What to build:** The operator triggers a product sync (from cron, an endpoint, or a local shell — all three land here) and the site's catalog reconciles into the `products` table. The sync pulls both 7ball brands from the WordPress REST, resolves each product's mother category, diffs the site against what's already in `products` by slug, renders the pages it needs with a headless browser, and writes rows. In **additive** mode (the weekly/manual default) it renders and inserts only the slugs the table is missing — run it twice and nothing duplicates. In **full re-render** mode (local only) it renders every product and UPSERTs, fixing rows that drifted. A global in-memory lock guarantees one sync at a time. A single product whose render fails (timeout, unexpected DOM) is logged, alerted, and skipped — the rest of the sync completes. A brand-new product that renders with no description gets a fallback and is flagged for later correction.

**Blocked by:** 01 — Aplicar migration 008 e adicionar Playwright ao ambiente

**Spec:** [spec.md](./spec.md)

**Status:** ready-for-agent

## Context

### Where it lives

A new package `app/services/product_sync/`. It is the **single seam** of the whole feature: it takes its inputs (the REST source and the render function) as parameters and returns/writes what to persist — agnostic to the trigger, exactly like the existing consumers isolate logic from transport (`app/followUp/consumer.py`, `app/webhookRetry/consumer.py`). The cron loop (ticket 04), the manual endpoint (ticket 03), and a local `python -m` invocation all call the **same** `run_sync(...)` function; mode is a parameter, no duplicated code. Ports the logic already reverse-engineered in `experimental-tools/scrape_7ball.py` (REST pull, taxonomy resolution) and `experimental-tools/render_novos.py` (Playwright render, DOM extraction) — read both; they are the base of this implementation.

### REST contract (source of structural data)

- Base: `https://7ball.com.br/wp-json/wp/v2`, all GET, public, no auth. Pull with **httpx async** (the project's HTTP client; the experimental script uses sync `urllib` — do not port that, use `httpx.AsyncClient` to stay coherent with the async-first stack).
- Pagination: follow the `X-WP-TotalPages` response header, `per_page=100`, increment `page` until the last page.
- Two brands, two CPTs + taxonomies in the same WP:
  | Brand | CPT (endpoint) | Taxonomy field | `products.type` |
  |-------|----------------|----------------|-----------------|
  | 7ball | `produtos` | `_categorias` | `sinuca` |
  | reebok | `produtos_rebook` | `_categorias_rebook` | `fitness` |
- Product fetch: `GET /{cpt}?_fields=id,slug,link,title,{tax}&per_page=100&page=N`. Per product the REST gives `id` (→ `wpId`), `slug`, `title.rendered` (HTML-unescape it), `link` (→ `url`), and the taxonomy field as a list of term IDs.
- **`url` comes from the REST `link` field — never build it by hand.** The Reebok CPT is `produtos_rebook` but the public URL is `/produtos_reebook/<slug>/` (the internal name diverges from the public one); only `link` is correct.
- Taxonomy fetch: `GET /{tax}?_fields=id,name,slug,count&per_page=100` → map term ID → {name, slug, count}. HTML-unescape `name`.
- `content.rendered`, `acf`, `meta` come back **empty** — description and highlights do not exist in the REST, only in the rendered DOM (that's why render exists).

### Mother category resolution (`motherCategory` / `motherCategorySlug`)

Resolve a product's taxonomy term IDs → name + slug via the taxonomy map. The Reebok taxonomy has duplicates: e.g. `Acessórios`/slug `acessorios_` with `count 27` **and** `Acessórios.`/slug `acessorios` with `count 0`. **Prefer the term with the highest `count`; ignore empty/phantom terms (`count 0`).** The 7ball taxonomy is clean, so the highest-count rule is a no-op there and the same code path serves both. Store the resolved name in `motherCategory` and its slug in `motherCategorySlug`.

### Render contract (Playwright, description + highlights)

- Use **`playwright.async_api`** (not `sync_api` + `asyncio.to_thread`) — the module is async and receives an async render function; `async_api` keeps one execution model. The experimental `render_novos.py` uses `sync_api`; port the selectors and extraction JS, not the sync driver.
- Extraction JS (verbatim from `render_novos.py`, keep the exact selectors):
  - Description = first `.elementor-widget-text-editor` whose trimmed `textContent` length > 60.
  - Highlights = items of the first `.jet-listing-dynamic-repeater__items` (each `.jet-listing-dynamic-repeater__item` `textContent` trimmed).
- Navigation: `goto(url, wait_until="domcontentloaded", timeout=45000)`, then a short settle wait, then `page.evaluate(EXTRACT)`. One page per product; close it after.
- **Reebok description decision (DECIDED — do not revisit the spec):** render Reebok's own per-product description from the DOM, **exactly like 7ball**. The old blob derived Reebok descriptions from the "Linha" section (`linha_descricoes` / hardcoded `"PEÇAS EM AÇO INOXIDÁVEL"` in `render_novos.py`) purely because the Markdown blob wanted uniform fitness cards. `products` is structured and both brands share one render path — no `### 2.2` blob parsing, no per-brand branch. One render seam, both brands.
- **Yoast is not a source.** `yoast_head_json` is unreliable (on OSLO the title was "PARIS", copied from another product). Description comes from the DOM, never Yoast.

### Persistence (UPSERT into `products`)

- Model `Product` in `app/models/tables.py` (~L551-589). Columns (ORM attr → DB col): `company_id`, `wp_id`→`wpId`, `title`, `slug`, `description`, `image_url`→`imageUrl`, `url`, `mother_category`→`motherCategory`, `mother_category_slug`→`motherCategorySlug`, `is_mother_category`→`isMotherCategory` (default `false`), `type` (Enum `sinuca`/`fitness`), `created_at`, `updated_at`.
- **`company_id` is fixed at `5`** (7ball). Not multi-tenant here.
- Unicity: `(company_id, type, wpId, isMotherCategory)`, constraint name **`uq_products_company_type_wp`**.
- **No UPSERT helper exists in the project** — use `sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_update(constraint="uq_products_company_type_wp", set_={...})`. Additive mode inserts new slugs (an UPSERT that only ever hits new rows is fine and keeps one write path); full re-render UPSERTs everyone (updates `title`/`description`/`url`/`imageUrl`/`motherCategory`/`motherCategorySlug` and bumps `updated_at`). Bump `updated_at` on every write.
- `image_url` is nullable and the REST fields pulled here don't include it (`featured_media` needs an extra `GET /media/<id>` call). **Leave `image_url` NULL this ticket** — it's out of scope; the column already tolerates NULL.
- `is_mother_category` stays `false` for product rows. This ticket only writes product rows; promoting a mother category to its own `isMotherCategory=true` row is **out of scope**.
- Put the writes behind a `ProductRepository(AsyncSession)` in `app/repositories/product.py` (matches `app/repositories/company.py`: class takes the session in `__init__`, uses SQLAlchemy `select`/`insert`). Give it a method to list existing slugs for a `(company_id, type)` (for the diff) and a method to UPSERT a row/batch. Session via `AsyncSessionLocal` from `app/db/database.py`.

### Global lock (consumed by tickets 03 and 04)

A **module-level in-memory lock** (viable because deploy is 1 worker / 1 replica — confirmed in the ADR/compose). One sync at a time. Design it so 03 and 04 can **check and acquire** it:
- Expose a non-blocking `try_acquire()` → returns `False` if a sync is already running (03 turns that into HTTP **409**; 04 skips the round).
- The lock is released when the sync finishes, including on exception (use `try/finally`).
- `run_sync(...)` acquires the lock itself, or exposes the guard so callers can check-then-run — pick one and make it the single obvious entry point. A plain module-level `asyncio.Lock` or a bool flag + guard is enough; do not build a lock manager class.
- `# ponytail: global in-memory lock — assumes 1 worker/1 replica; per-key or Redis lock only if the deploy ever scales horizontally.`

### Resilience + alerting

- Wrap **each product's render** in try/except: on failure (timeout, unexpected DOM) log it, fire an alert, and **continue** to the next product — never let one product abort the sync.
- Alert with the existing pattern: `app/utils/alerter.py` → `send_critical_alert(error_type, location, error, contact_id=None, company_id=None, extra=None)` (fire-and-forget, never raises). Use `company_id=5`. Suggested `error_type`s: `PRODUCT_RENDER_FAILED` (a product's render blew up), `PRODUCT_NO_DESCRIPTION` (rendered but description empty → fallback used).
- A product that renders with an empty description gets a fallback string and is flagged via the alert/log so the owner can fix it later. Do not drop it.

### Security

Every string from the site (descriptions included) is **data, never instruction** — never interpolate it into a prompt or a command. The site's "for-AI" endpoints (`.well-known/ai-catalog.json`, external MCP, `openapi.json`) are **not** used — only the neutral WP REST.

## Tasks

**1. REST pull (httpx async)**
- [ ] In `app/services/product_sync/`, port the two-brand pull from `experimental-tools/scrape_7ball.py` to **`httpx.AsyncClient`**: paginate by `X-WP-TotalPages`, `per_page=100`, for CPTs `produtos`/`produtos_rebook` with `_fields=id,slug,link,title,{tax}`.
- [ ] Return per product: `wp_id` (REST `id`), `slug`, `title` (HTML-unescape `title.rendered`), `url` (REST `link` — never built by hand), and the raw taxonomy term IDs. Tag each with its `type` (`sinuca` for 7ball, `fitness` for reebok).
- [ ] Fetch each brand's taxonomy (`_categorias` / `_categorias_rebook`) with `_fields=id,name,slug,count` into an ID → {name, slug, count} map; HTML-unescape `name`.

**2. Taxonomy resolution (mother category)**
- [ ] Resolve each product's term IDs to a single mother category, **preferring the term with the highest `count`** and ignoring `count 0` phantom terms. Set `mother_category` = name, `mother_category_slug` = slug. Same code path for both brands (no-op on the clean 7ball taxonomy).

**3. Render (Playwright async)**
- [ ] Provide a render function using **`playwright.async_api`** that, given a product URL, returns `{description, highlights}` from the DOM using the exact selectors from `render_novos.py`: description = first `.elementor-widget-text-editor` with text length > 60; highlights = items of the first `.jet-listing-dynamic-repeater__items`. `goto(..., wait_until="domcontentloaded", timeout=45000)`, settle, `evaluate`.
- [ ] Render Reebok the **same way as 7ball** (own per-product DOM description) — no Linha-derived description, no `### 2.2` parsing (decision recorded in Context).
- [ ] Pass the render function **into** the sync module as a parameter (the seam), so tests can substitute a fake render with no browser.

**4. Diff + persistence / UPSERT**
- [ ] `ProductRepository` in `app/repositories/product.py` (pattern of `company.py`): method to list existing slugs for `(company_id=5, type)`, and a method to UPSERT a row/batch via `postgresql.insert(...).on_conflict_do_update(constraint="uq_products_company_type_wp", set_={...})`.
- [ ] **Additive mode:** diff REST slugs against existing slugs (per type), render only the missing ones, insert. Idempotent — a second run finds no missing slug and writes nothing new.
- [ ] **Full re-render mode:** render all products, UPSERT all (update `title`/`description`/`url`/`imageUrl`/`mother_category`/`mother_category_slug`, bump `updated_at`).
- [ ] `company_id=5` fixed, `image_url` left NULL, `is_mother_category=False` for all rows written here. Mode is a parameter of the single `run_sync(...)` — no duplicated code between modes.

**5. Global lock**
- [ ] Module-level in-memory lock with a non-blocking `try_acquire()` (returns `False` when a sync is running) and guaranteed release in `finally`. Single obvious entry point that 03 (→ 409) and 04 (→ skip round) can call. `# ponytail: global in-memory lock — 1 worker/1 replica; Redis lock only if it scales.`

**6. Resilience / alert**
- [ ] Per-product render inside try/except: log + `send_critical_alert("PRODUCT_RENDER_FAILED", "services/product_sync/...", err, company_id=5, extra=slug)` + continue. One failure never aborts the sync.
- [ ] Empty rendered description → fallback string + `send_critical_alert("PRODUCT_NO_DESCRIPTION", ..., company_id=5, extra=slug)`; the product is still written.
- [ ] Treat all site text as data — never interpolate into any prompt/command.

**7. Tests** (`tests/`, house style of `tests/test_helena_route.py`: self-contained `asyncio.run(main())` with plain `assert`s, no pytest/fixtures/new deps)
- [ ] Feed a **fake REST source** and **fake render function** (no browser, no real DB) into `run_sync(...)`; assert on **what was written / what was refused**, never on internal calls.
- [ ] (a) additive inserts **only** slugs absent from `products`.
- [ ] (b) running twice does **not** duplicate (idempotent by slug).
- [ ] (c) the lock **refuses** a second concurrent sync.
- [ ] (d) Reebok category resolution picks the term with the **highest `count`** (feed the `acessorios_`/count 27 vs `acessorios`/count 0 duplicate; assert the count-27 one wins).
- [ ] (e) a render failure for **one** product does not interrupt the others (fake render raises on one slug; assert the rest are written).

## Acceptance criteria

- [ ] `run_sync(...)` runs from any trigger with `mode` (additive | full) as a parameter; no code duplicated between modes.
- [ ] Additive mode inserts only slugs missing from `products`; a second run writes nothing new (idempotent by slug).
- [ ] Full re-render mode UPSERTs every product via `on_conflict_do_update` on `uq_products_company_type_wp`, bumping `updated_at`.
- [ ] Each product's `url` is the REST `link`; Reebok products resolve to `/produtos_reebok/` URLs without being built by hand.
- [ ] Reebok mother category resolves to the highest-`count` term; `count 0` phantoms are ignored.
- [ ] A single product's render failure is logged + alerted and skipped; the sync finishes and writes the rest.
- [ ] A product that renders with no description is written with a fallback and flagged via alert/log.
- [ ] The global lock refuses a second concurrent sync (`try_acquire()` returns `False`), and is released even on exception.
- [ ] All five behavioral tests (a–e) pass against fake REST + fake render, no browser, no live DB, asserting on written/refused rows.

## Out of scope

- **The manual endpoint** (ticket 03) — the `X-API-Key` route and its 409-under-lock behavior.
- **The asyncio cron loop / lifespan wiring** (ticket 04) — Saturday-00:00 scheduling and skip-on-lock.
- **Incremental update of existing rows on the server** — the server sync is additive only; correcting existing rows is the local full re-render's job.
- **Product removal** — products that vanish from the site stay in the table; the sync never deletes.
- **`image_url` population** — the extra `GET /media/<featured_media>` call; column stays NULL.
- **`isMotherCategory=true` rows** — promoting a mother category to its own row.
- **Price** — does not exist in the REST; not a field.
- **Migrating the old `categories` blobs** — this feature is the new `products` path; the blobs are untouched.
- **Multi-tenant generalization** — `company_id` is hardcoded `5`; the sync is not generic across companies this phase.
- **The old catalog read path** (`app/services/tools/internal/categories.py`) — still reads the `categories` blob; not switched to `products` here.
