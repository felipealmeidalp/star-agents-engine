# 02 — Anexos no HelenaService (áudio + descritivo)

**What to build:** A lead on the Helena channel sends media instead of (or alongside) text and the AI no longer goes blind. An **audio** attachment is transcribed with Whisper and the transcript becomes the `message` that reaches the AI — the lead never has to type. An **image / video / file** with no text becomes a Portuguese descriptive phrase ("O usuário enviou uma imagem") so the conversation does not stall as if nothing arrived. **Text has precedence**: a message carrying both text and an attachment uses the text and ignores the attachment, exactly like the Chatwoot channel. If transcription fails, the lead gets an error message asking to resend/type and the existing `AUDIO_TRANSCRIPTION_FAILED` critical alert fires. A message with no text and no attachment is still ignored (today's behaviour, preserved).

**Blocked by:** None — can start immediately.

**Spec:** [spec.md](./spec.md)

**Status:** ready-for-agent

## Context

Today `HelenaService.process_webhook` (`app/helena/service.py`) has an early return `if not text: return {"status":"ignored","reason":"no_text"}` — any attachment-only message is silently dropped. This ticket makes it resolve the attachment into a text `message` **before** calling `request_manager.on_new_message`, keeping the core string-in/string-out (ADR 0003 — no vision, `on_new_message`/`process_chat_in_memory`/`OpenAIMessage.content` untouched).

**Reference logic (Chatwoot) — extract, do not duplicate.** `ChatwootService._handle_attachments` and `ChatwootService._transcribe_audio_attachment` (`app/chatwoot/service.py`) already implement the exact rules: text precedence → no attachment skips → audio transcribes → non-audio becomes a descriptive phrase, with a `type_labels` map (`image` → "uma imagem", `video` → "um vídeo") and an extension-extraction fallback (parse the URL path, uppercase the extension if `<= 5` chars → `f"um arquivo {ext}"`, else `"um arquivo"`), all wrapped as `f"O usuário enviou {description}"`. These are private methods coupled to the Chatwoot payload and to `self._send_responses` (the Chatwoot client).

**Where the shared helper lives + signature.** Extract the **pure** part (no client, no payload) into a new module `app/utils/attachments.py`, next to `content_formatter.py`. Two channel-agnostic functions:

- `def describe_attachment(file_type: str | None, url: str | None) -> str` — the `type_labels` map + extension fallback + `f"O usuário enviou {description}"`. Same string for both channels.
- `async def resolve_attachment_message(text: str | None, file_type: str | None, url: str | None, transcribe: Callable[[str], Awaitable[str]]) -> tuple[str | None, bool]` — the orchestration mirroring `_handle_attachments`, returning `(message, should_continue)`. Text precedence (returns `(text, True)` when text is non-empty); no url → `(text, False)` (skip); `file_type == "audio"` → `await transcribe(url)`, empty transcript → raise so the caller runs the failure path; non-audio → `(describe_attachment(...), True)`. `transcribe` is injected so the helper stays free of `OpenAIService`/company/key wiring.

**What stays channel-specific (does NOT move into the helper):** the error-message-to-lead and the alert use the channel's own client. In `HelenaService` the failure path sends the Portuguese error phrase via `HelenaClient` (`send_text`/`send_messages` with the lead's `to`/`apikey` already resolved in `process_webhook`) and fires `send_critical_alert("AUDIO_TRANSCRIPTION_FAILED", "helena/service.py:process_webhook", e, contact_id=..., company_id=company.id)` — same error_type as Chatwoot, Helena location. Reuse the same error strings as Chatwoot ("Desculpa, tive um problema ao processar seu áudio…" / "Não consegui entender o áudio…").

**Transcription reuse.** Do NOT reimplement transcription. The helper's `transcribe` callback wraps `OpenAIService(api_key=...).transcribe_audio(url)` (already channel-agnostic, takes a URL — `app/services/openai.py`). The key comes from `company_repo.get_openai_api_key(company.id)` (`CompanyRepository`, `app/repositories/company.py`); `HelenaService.__init__` must gain `self.company_repo = CompanyRepository(db)` (it has none today; ChatwootService already wires it this way).

**Tolerant HelenaContent schema.** The real Helena attachment payload has NOT been captured yet, so `HelenaContent` (`app/helena/schemas.py`) gains **optional** attachment fields with a minimal shape: a file URL and a type/mimetype to tell audio from the rest — mirroring `ChatwootAttachment` (`file_type`, `data_url`) in `app/chatwoot/schemas.py`. The envelope already ignores extras, so keep no `extra="forbid"`. Mark the field(s) with a `ponytail:` comment saying the shape is assumed and to confirm on the first real attachment payload.

## Tasks

**1. Schema tolerante de anexo em HelenaContent**

- [x] Add optional attachment fields to `HelenaContent` (`app/helena/schemas.py`): a file URL and a type/mimetype (e.g. `attachment_url: str | None = None`, `attachment_type: str | None = None`), enough to distinguish audio from image/video/file. Keep them optional; do not add `extra="forbid"`.
- [x] Add a `ponytail:` comment above the fields: the shape is assumed (no real payload captured yet), confirm/adjust on the first real Helena attachment webhook; compare to `ChatwootAttachment` (`file_type`, `data_url`).

**2. Helper compartilhado de anexo (pure, channel-agnostic)**

- [x] Create `app/utils/attachments.py` with `describe_attachment(file_type, url) -> str`: the `type_labels` map (`image`→"uma imagem", `video`→"um vídeo"), the URL-extension fallback (`urlparse` the path, upper-case a `<= 5`-char extension → `f"um arquivo {ext}"`, else `"um arquivo"`), wrapped as `f"O usuário enviou {description}"` — the exact strings from `ChatwootService._handle_attachments`.
- [x] Add `resolve_attachment_message(text, file_type, url, transcribe) -> tuple[str | None, bool]`: text precedence → `(text, True)`; no url → `(text, False)`; audio → `await transcribe(url)` (empty transcript raises so the caller alerts); non-audio → `(describe_attachment(file_type, url), True)`. Keep it free of any client/company/key.
- [x] Refactor `ChatwootService._handle_attachments`/`_transcribe_audio_attachment` to call the shared helper (`describe_attachment` for the phrase; `resolve_attachment_message` or at least `describe_attachment` for the branch logic), so the descriptive strings live in exactly one place. Chatwoot's error-message-to-lead + `send_critical_alert` stay in the Chatwoot service (they use `self._send_responses`).
- [x] Leave one runnable check (`assert`-based `__main__`/`demo()` in `app/utils/attachments.py`, house style of the existing self-contained test scripts): `describe_attachment("image", None) == "O usuário enviou uma imagem"`, `describe_attachment("video", None) == "O usuário enviou um vídeo"`, `describe_attachment(None, "https://x/y/doc.pdf") == "O usuário enviou um arquivo PDF"`, `describe_attachment(None, None) == "O usuário enviou um arquivo"`.

**3. Integração no HelenaService**

- [x] In `HelenaService.__init__` add `self.company_repo = CompanyRepository(db)`.
- [x] In `process_webhook`, replace the `if not text: return {...no_text...}` early return with attachment resolution BEFORE `on_new_message`: read the attachment fields off `payload.content` into locals (respecting the ORM-attributes-into-locals-before-await pattern already in the file), build the `transcribe` callback wrapping `OpenAIService(api_key=await self.company_repo.get_openai_api_key(company.id)).transcribe_audio(url)`, then `message, should_continue = await resolve_attachment_message(text, file_type, url, transcribe)`.
- [x] If `should_continue` is False (no text + no attachment), return the same `{"status":"ignored","reason":"no_text",...}` shape as today (behaviour preserved). Feed the resolved `message` (not the raw `text`) into `request_manager.on_new_message`.
- [x] Wrap the audio branch so a transcription failure (exception or empty transcript) sends the Portuguese error message to the lead via `HelenaClient` (reuse the resolved `to`/`apikey`, same strings as Chatwoot) and fires `send_critical_alert("AUDIO_TRANSCRIPTION_FAILED", "helena/service.py:process_webhook", e, contact_id=..., company_id=company.id)`, then returns without calling the AI.

## Acceptance criteria

- [x] **Seam 1 (route `POST /api/helena/{token}`):** an audio payload with `transcribe_audio` mocked → the mocked transcript is the `message` that enters the pipeline (assert it reaches `on_new_message` / the AI input), not the raw payload. Runnable in the `tests/test_helena_route.py` harness style (self-contained `asyncio.run(main())`, mock `OpenAIService.transcribe_audio` + `HelenaClient.send_text`). [case_h; asserts message==transcript at on_new_message]
- [x] An image/video/file payload with no text → the descriptive phrase ("O usuário enviou uma imagem" / "…um vídeo" / "…um arquivo") is the `message` that enters the pipeline. [case_i]
- [x] A payload with both text and an attachment → the text is used and the attachment is ignored (assert transcribe/describe are not what reached the AI). [case_j]
- [x] A transcription failure (mock `transcribe_audio` to raise, or return empty) → an error message is sent to the lead via `HelenaClient` AND `send_critical_alert` fires with `AUDIO_TRANSCRIPTION_FAILED`; no AI call happens. [case_k]
- [x] A payload with no text and no attachment → still returns `{"status":"ignored","reason":"no_text",...}` with zero sends (current behaviour preserved). [case_c, unchanged]
- [x] `python app/utils/attachments.py` self-check passes (fails if the descriptive-phrase/extension logic breaks).

## Out of scope

- **Vision / multimodal** — ADR 0003; the núcleo stays string-only, the AI never receives the media itself.
- **Definitive Helena attachment payload parsing** — the schema enters tolerant and minimal (url + type), marked `ponytail:` to confirm against a real captured payload.
- **The AI on/off gate (`customers.status`)** and **`transfer_to_human` escalation** — separate tickets in this spec.
