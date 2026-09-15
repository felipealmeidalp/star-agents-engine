# 01 — Multi-tenancy do Helena: colunas, migração, lookup e settings

**What to build:** A company can be configured with a Helena identity: a `helena_token` (the UUID that appears in the webhook URL `POST /api/helena/{token}`) and a `helena_apikey` (the Bearer used to send replies back to Helena). Given a token from an incoming webhook URL, the engine can resolve exactly which company owns it — the same multi-tenant routing the Chatwoot channel already does with `cw_token`. This ticket delivers the storage, the schema migration, the lookup query, and the fixed Helena base URL/endpoint config that every later Helena ticket builds on. No webhook route, service, or client is created here.

**Blocked by:** None — can start immediately

**Spec:** [spec.md](./spec.md)

**Status:** ready-for-agent

## Context

The Helena channel mirrors the existing Chatwoot channel one-to-one for multi-tenancy. Follow the Chatwoot precedents exactly — do not invent new shapes.

- **ORM model** — `app/models/tables.py`, class `Company` (`__tablename__ = "companies"`). The Chatwoot columns to mirror are declared there: `cw_token` is `Mapped[Optional[PyUUID]] = mapped_column(UUID(as_uuid=True), nullable=True, unique=True, index=True)` and `cw_apikey` is `Mapped[Optional[str]] = mapped_column(String, nullable=True)`. All imports needed already exist at the top of the file: `PyUUID` (`from uuid import UUID as PyUUID`), `UUID` (`from sqlalchemy.dialects.postgresql import UUID`), `String`, `Mapped`, `mapped_column`. Add the two Helena columns alongside the Chatwoot block, no new imports required.

- **Repository** — `app/repositories/company.py`, class `CompanyRepository`. The model method is `get_by_cw_token(self, cw_token: str) -> Company | None`: it parses the string with `UUID(...)` inside `try/except ValueError: return None`, then `select(Company).where(Company.cw_token == token_uuid)` and returns `scalar_one_or_none()`. Note this file imports `from uuid import UUID` (not `PyUUID`). Mirror it as `get_by_helena_token`, filtering on `Company.helena_token`.

- **Migrations** — `migrations/versions/`. The current head revision is **005** (`20260302_000001_005_add_rag_result_to_chat_history.py`); the new revision is **006** with `down_revision = "005"`. The header/style model to copy: revision `005` for the plain `revision`/`down_revision`/`branch_labels`/`depends_on` block and the idempotent column-add (query `information_schema.columns` and `return` early if the column already exists before `op.add_column`); revision `004` (`20260302_000000_004_add_unique_cw_contact_id.py`) for the idempotent index pattern (query `pg_indexes` by `indexname` and `return` early before `op.create_index`). File name follows the existing scheme: `20260302_000002_006_add_helena_channel_columns.py`.

- **Config / base URL** — `app/config.py` holds a single Pydantic `Settings` class (`settings` singleton). Fixed per-channel URLs already live here as settings fields (e.g. `alert_evo_api_url`, the `meta_*` block). Chatwoot's base URL is the exception — it is a *per-tenant* column (`cw_base_url` on `Company`), because each Chatwoot install has its own host. Helena's base URL is fixed (`https://api.helena.run`) and the endpoint is `/chat/v1/send/text`, so they belong in `config.py` (as `Settings` fields with those defaults, or as module-level constants), **not** as a Company column. Prefer `Settings` fields to match the Meta/alerting precedent.

Domain terms: `companies` (table), `helena_token` (UUID, matches the `{token}` in the webhook URL), `helena_apikey` (Bearer for outbound `send/text`), `sessionId` (Helena's native conversation id — not this ticket's concern).

## Tasks

**1. ORM columns on `Company`**

- [x] In `app/models/tables.py`, add `helena_token` to the `Company` class, mirroring the `cw_token` declaration exactly: `Mapped[Optional[PyUUID]] = mapped_column(UUID(as_uuid=True), nullable=True, unique=True, index=True)`.
- [x] Add `helena_apikey` mirroring `cw_apikey`: `Mapped[Optional[str]] = mapped_column(String, nullable=True)`.
- [x] Place both next to the existing Chatwoot integration block for readability; confirm no new imports are needed (`PyUUID`, `UUID`, `String` are already imported).

**2. Alembic migration 006**

- [x] Create `migrations/versions/20260302_000002_006_add_helena_channel_columns.py` with `revision = "006"`, `down_revision = "005"`, and `branch_labels`/`depends_on` set to `None`, matching the header of revision 005.
- [x] In `upgrade()`, add `helena_token` (`UUID(as_uuid=True)`, nullable) idempotently: check `information_schema.columns` for `table_name = 'companies' AND column_name = 'helena_token'` and `return` early if present, following the revision 005 pattern.
- [x] Add `helena_apikey` (`String`, nullable) idempotently with the same existence check.
- [x] Create the unique index on `helena_token` idempotently: check `pg_indexes` by `indexname` and `return`/skip if it already exists, following the revision 004 pattern (SQLAlchemy's `index=True, unique=True` on the column implies an index named `ix_companies_helena_token` — create it explicitly in the migration so the DB matches the ORM).
- [x] In `downgrade()`, drop the index and both columns.

**3. Repository lookup**

- [x] In `app/repositories/company.py`, add `async def get_by_helena_token(self, helena_token: str) -> Company | None` to `CompanyRepository`, mirroring `get_by_cw_token`: parse with `UUID(helena_token)` inside `try/except ValueError: return None`, then `select(Company).where(Company.helena_token == token_uuid)` and return `scalar_one_or_none()`.
- [x] Give it a Google-style docstring consistent with `get_by_cw_token`.

**4. Helena base URL / endpoint config**

- [x] In `app/config.py`, add the fixed Helena base URL (`https://api.helena.run`) and send endpoint (`/chat/v1/send/text`) as `Settings` fields with those defaults (e.g. `helena_base_url`, `helena_send_text_path`), matching how `alert_evo_api_url` / the `meta_*` fields are declared. Do not add a Company column for these.

## Acceptance criteria

- [x] `alembic upgrade head` applies revision 006 on a fresh DB and on a DB already at 005, and running it twice is a no-op (idempotent), leaving `companies` with `helena_token` (unique-indexed UUID) and `helena_apikey` (string) columns.
- [x] `alembic downgrade -1` from 006 removes both columns and the index.
- [x] The `Company` ORM model exposes `helena_token` and `helena_apikey` attributes matching the migrated columns.
- [x] `CompanyRepository.get_by_helena_token(token)` returns the matching `Company` for a valid token string, `None` for an unknown token, and `None` (no exception) for a non-UUID string.
- [x] `settings.helena_base_url` and the send endpoint resolve to `https://api.helena.run` and `/chat/v1/send/text` without any env var set.

## Out of scope

- The webhook route `POST /api/helena/{token}`, `HelenaService`, `HelenaClient`, and the Helena payload schemas — later tickets.
- Wiring `get_by_helena_token` into any request flow, or using `helena_apikey` to send anything.
- Populating `helena_token`/`helena_apikey` values for real companies (data/seed concern, not schema).
- Any change to `Customer`, `chat_history`, buffer, or the AI pipeline.
