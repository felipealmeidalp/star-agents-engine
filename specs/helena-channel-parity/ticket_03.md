# 03 — `transfer_to_human` multi-canal (escalonamento Helena)

**What to build:** When the AI calls `transfer_to_human` on a Helena conversation, the AI stops (`customers.status = False`, done FIRST and always) and the Helena session is assigned to the company's fixed attendant (`companies.helena_assignee_id`) via `PUT /chat/v1/session/{sessionId}/assignee` with `stopBotInExecution:true`. An assignment failure keeps the AI stopped and fires a critical alert — the AI never comes back to answer. Chatwoot does not regress. This slice threads an opaque `channel` string from the channel services down to `ToolExecutionContext.channel`, branches the tool on it, adds one column (+ Alembic migration), and adds one method to `HelenaClient`.

**Blocked by:** None — can start immediately.

**Spec:** [spec.md](./spec.md)

**Status:** ready-for-agent

## Context

### The `channel` chain (opaque string through the agnostic core)

`ToolExecutionContext` is built in exactly ONE place: `ChatHandler._handle_tool_calls` in `app/services/chat_handler.py` (the `ToolExecutionContext(...)` construction, ~line 490). The core does not know the channel today, so `channel` must FLOW down as a plain string the core passes along with zero channel logic (ADR 0005). Add a `channel` parameter with a safe default `"chatwoot"` to each signature below, so existing callers and tests keep working unchanged:

- `RequestManager.on_new_message(...)` in `app/services/request_manager.py` — add `channel: str = "chatwoot"`. Thread it into the `_process_task(...)` call it creates (add a `channel=channel` kwarg there). NOTE the `DEV_MODE` bypass at the top: when `settings.dev_mode` it returns `await process_chat(...)` directly — pass `channel=channel` there too.
- `RequestManager._process_task(...)` — add `channel: str` param; pass it into the `process_chat_in_memory(...)` call.
- `process_chat_in_memory(...)` in `app/services/chat_processor.py` — add `channel: str = "chatwoot"`; pass it to `ChatHandler(...)`.
- `process_chat(...)` in `app/services/chat_processor.py` — add `channel: str = "chatwoot"`; pass it to `ChatHandler(...)` (this is the DEV_MODE path from `on_new_message`).
- `reprocess_chat(...)` in `app/services/chat_processor.py` — build a `ChatHandler` too, but has no channel source (re-enable flow). Leave it on the default: `ChatHandler(...)` without passing `channel` gets `"chatwoot"`. No signature change needed unless you want it explicit; skip for laziness.
- `ChatHandler.__init__(...)` in `app/services/chat_handler.py` — add `channel: str = "chatwoot"`; store `self.channel = channel`.
- `ChatHandler._handle_tool_calls` — pass `channel=self.channel` into the `ToolExecutionContext(...)` construction.

### `ToolExecutionContext.channel`

`app/models/schemas.py` → `ToolExecutionContext` (a `BaseModel` with `model_config = {"arbitrary_types_allowed": True}`). Add `channel: str = "chatwoot"` alongside the existing fields (`session_id`, `company_id`, `agent_id`, `sub_agent_id`, `customer_id`, `db`, `openai_api_key`, `chat_history`, `on_send_messages`, `conversation_turn`). Default keeps `.model_copy(update=...)` test helpers and any bare construction valid.

### The `transfer.py` branch shape

`app/services/tools/internal/transfer.py` → `TransferToHumanTool.execute`. The `update_status(status=False)` block (steps 1) is already channel-agnostic — it is the common part and runs FIRST, always, before any branch. After it, branch on `context.channel`. The existing Chatwoot path (get `responsible_team`, labels via `swap_label`, `_pick_human_assignee`, `_assign_and_verify`) becomes the `else`/`chatwoot` branch, unchanged. Add the Helena branch. All branches return the same success `ToolResult` ("Conversa transferida para atendimento humano com sucesso."). Mirror the existing defensive pattern (missing config → AI stays blocked, still return success).

```python
# after update_status(status=False) — the common, always-first step:
company = await company_repo.get_by_id(context.company_id)

if context.channel == "helena":
    assignee_id = company.helena_assignee_id if company else None
    apikey = company.helena_apikey if company else None
    if not assignee_id or not apikey:
        logger.warning("[TransferToHuman] Helena assignee/apikey missing for company %d; "
                       "AI blocked, assignment skipped.", context.company_id)
        return _success()  # status already False
    try:
        # ponytail: content.sessionId assumed == the {id} the Helena session API wants;
        #           fallback is resolve-by-phone (GET /core/v1/contact/phoneNumber/{phone})
        await self.client.assign_session(context.session_id, assignee_id, apikey)
    except Exception as e:
        send_critical_alert("HELENA_ASSIGN_FAILED", "transfer.py:execute", e,
                            company_id=context.company_id,
                            extra=f"session={context.session_id}, assignee={assignee_id}")
        # status stays False — never let the AI resume on assign failure
    return _success()

# else: current Chatwoot path (labels + _pick_human_assignee + _assign_and_verify), unchanged
```

`_success()` is just the existing final `ToolResult(...)` — reuse it, no new abstraction. `assignee_id` is `company.helena_assignee_id` (the new column, a UUID); pass it through as-is (str/UUID) as the `userId` in the body. `HelenaClient` is instantiated in the tool the same way `ChatwootClient()` is (a plain `HelenaClient()`), or reuse `self.client` if the tool grows one — a bare `HelenaClient()` matches the Chatwoot path.

### New column `companies.helena_assignee_id`

`app/models/tables.py` → `Company`. Declare it mirroring `helena_apikey` (which is `Mapped[Optional[str]] = mapped_column(String, nullable=True)`). The Helena `userId` is a UUID, but a plain nullable `String` is the laziest correct choice (Helena hands us the id as text and we pass it straight into the body) — matching `helena_apikey`'s `String`, not `helena_token`'s `UUID`:

```python
helena_assignee_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
```

Alembic migrations live in `migrations/versions/` (`alembic.ini` at repo root). Use `migrations/versions/20260302_000002_006_add_helena_channel_columns.py` as the template — same idempotent `information_schema.columns` guard, `revision`/`down_revision` chain, `upgrade`/`downgrade`. New revision `007`, `down_revision = "006"`. `upgrade`: add `helena_assignee_id` (`sa.String()`, nullable). `downgrade`: `op.drop_column("companies", "helena_assignee_id")`. No index needed (it is not a lookup key). `CompanyRepository.get_by_id` returns the `Company` ORM row, so the tool reads `company.helena_assignee_id` and `company.helena_apikey` off it directly.

### `HelenaClient.assign_session`

`app/helena/client.py` → new async method, modeled on `send_text` (httpx pattern, Bearer header, `response.raise_for_status()`). URL: `settings.helena_base_url` is already `https://api.helena.run/chat` and `helena_send_text_path` is `/v1/message/send`, so the assignee path relative to base_url is `/v1/session/{session_id}/assignee` (full: `.../chat/v1/session/{sessionId}/assignee`, matching the spec contract). Inline the path — no new setting warranted for one URL:

```python
async def assign_session(self, session_id: str, user_id: str, apikey: str) -> dict[str, Any]:
    """Assign a Helena session to a fixed attendant, stopping Helena's own bot."""
    url = f"{settings.helena_base_url}/v1/session/{session_id}/assignee"
    headers = {"Authorization": f"Bearer {apikey}", "Content-Type": "application/json"}
    # ponytail: doc lists both `id` and `userId` on the agent object — using `userId`;
    #           confirm on first real escalation.
    payload = {"userId": user_id, "options": {"stopBotInExecution": True}}
    async with httpx.AsyncClient(timeout=self.timeout) as client:
        response = await client.put(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()
```

Two `ponytail:` integration assumptions to confirm on the first real escalation (Further Notes): (a) `content.sessionId` == the `{id}` the session API expects (fallback: resolve by phone); (b) body wants `userId`, not `id`.

### The two services pass `channel=`

- `app/helena/service.py` → `HelenaService.process_webhook` calls `self.request_manager.on_new_message(...)` — add `channel="helena"`.
- `app/chatwoot/service.py` → `ChatwootService.process_webhook` calls `on_new_message(...)` — add `channel="chatwoot"` (explicit; equals the default, but makes the contract visible).

## Tasks

**1. Coluna e migração**

- [ ] Add `helena_assignee_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)` to the `Company` ORM in `app/models/tables.py`, next to `helena_apikey`.
- [ ] Create Alembic migration `007` in `migrations/versions/` (copy revision `006` as template): idempotent `add_column("companies", sa.Column("helena_assignee_id", sa.String(), nullable=True))` guarded by an `information_schema.columns` check; `down_revision = "006"`; `downgrade` drops the column. No index.

**2. Cadeia `channel` pelo núcleo**

- [ ] `app/models/schemas.py`: add `channel: str = "chatwoot"` to `ToolExecutionContext`.
- [ ] `app/services/chat_handler.py`: add `channel: str = "chatwoot"` to `ChatHandler.__init__`, store `self.channel`, and pass `channel=self.channel` into the `ToolExecutionContext(...)` construction in `_handle_tool_calls`.
- [ ] `app/services/chat_processor.py`: add `channel: str = "chatwoot"` to `process_chat_in_memory` and `process_chat`, forwarding it to `ChatHandler(...)` in each.
- [ ] `app/services/request_manager.py`: add `channel: str = "chatwoot"` to `on_new_message`; forward it to the DEV_MODE `process_chat(...)` call, and to `_process_task(...)` (add `channel` param there → into `process_chat_in_memory(...)`).

**3. HelenaClient — atribuição de sessão**

- [ ] Add `assign_session(self, session_id, user_id, apikey)` to `app/helena/client.py`: `PUT {settings.helena_base_url}/v1/session/{session_id}/assignee`, header `Authorization: Bearer {apikey}`, body `{"userId": user_id, "options": {"stopBotInExecution": True}}`, `raise_for_status()`. Mark both `ponytail:` assumptions (sessionId==id; userId vs id).

**4. Branch multi-canal em `transfer_to_human`**

- [ ] In `app/services/tools/internal/transfer.py` `TransferToHumanTool.execute`: keep `update_status(status=False)` as the always-first common step; after fetching `company`, branch `if context.channel == "helena":` → read `helena_assignee_id`/`helena_apikey`, skip-and-succeed if either missing, else `await HelenaClient().assign_session(...)`; on exception fire `send_critical_alert("HELENA_ASSIGN_FAILED", ...)` and keep `status=False`; return the same success `ToolResult`. Leave the Chatwoot path as the `else` branch, unchanged.

**5. Services passam o canal**

- [ ] `app/helena/service.py`: pass `channel="helena"` in `HelenaService.process_webhook`'s `on_new_message(...)` call.
- [ ] `app/chatwoot/service.py`: pass `channel="chatwoot"` in `ChatwootService.process_webhook`'s `on_new_message(...)` call.

## Acceptance criteria

- [ ] Seam 2 (helena): `ToolHandler.execute_all([transfer_to_human call], ToolExecutionContext(channel="helena", ...))` sets `customers.status = False` (real `update_status` against the test DB) AND calls `HelenaClient.assign_session` (mocked) with the company's `helena_assignee_id` and `stopBotInExecution: true` in the body; tool returns `success=True`.
- [ ] `channel="chatwoot"` still runs the labels/assignment path (`swap_label` + `_pick_human_assignee` + `_assign_and_verify`) — no regression; `assign_session` is NOT called.
- [ ] Missing `helena_assignee_id` (or `helena_apikey`): `status=False`, no `assign_session` call, tool returns `success=True`.
- [ ] `assign_session` raises: `status=False` stays False, `send_critical_alert("HELENA_ASSIGN_FAILED", ...)` fired, tool returns `success=True`.
- [ ] Migration `007` applies and reverts cleanly (`alembic upgrade head` / `alembic downgrade -1`); `helena_assignee_id` present after upgrade, gone after downgrade.

Test harness matches the house style (`tests/test_helena_route.py`, `tests/test_categories_tool.py`): self-contained `asyncio.run(main())` with plain `assert`s, `unittest.mock.patch` on `HelenaClient.assign_session`, real DB for `update_status`, no pytest/conftest. SKIP cleanly if no DB is reachable.

## Out of scope

- Labels/tags no Helena (Helena tags a contact, not a session).
- Escalonamento por departamento / roteamento dinâmico — o destino é um `userId` fixo por empresa (ADR 0004); a IA não escolhe.
- Religar a IA (`status=True` continua manual no banco).
- Intervenção pelo painel do Helena / evento `SESSION_UPDATE` — não assinado nem parseado (ADR 0004).
- Confirmação definitiva do id de sessão da API do Helena e do campo `userId` do body (as duas `ponytail:` assumptions).
- Redistribuição quando o atendente está offline.
- Gate de IA no `HelenaService` e anexos — outros tickets desta spec.
