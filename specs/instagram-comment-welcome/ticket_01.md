# 01 — Validação real do fluxo comentário → DM → sessão Helena

**What to build:** Nothing in the engine. A person runs the whole flow once by hand against real systems — a real comment on a real Instagram post, a DM de boas-vindas sent by a manual API call, a Resposta pública published by a manual API call, the lead answering the DM, the conversation showing up in Helena — and writes down the 8 facts the spec lists under "Further Notes → Ticket de validação". From facts 2 to 4 the ticket states which key matches a Boas-vindas pendente to a Helena session (the "casamento"), or states that the design must go back to ADR 0001/0002. The output is one file, `specs/instagram-comment-welcome/validation.md`.

**Blocked by:** None — can start immediately.

**Spec:** [spec.md](./spec.md)

**Status:** needs-human — it needs the Meta app panel, a real Instagram account, a second Instagram account to comment with, and the Helena panel; no agent can do those.

## Context

**Why this exists.** The Customer is identified by the Helena `sessionId`, which only exists after Helena opens the session. So the DM de boas-vindas is stored as a Boas-vindas pendente and matched to the session on its first message. Which key does the matching is unknown until a real run (ADR 0001). This ticket blocks ticket 04 (Casamento da Boas-vindas pendente no canal Helena), which implements only the path confirmed here.

**Already researched: [helena-api.md](./helena-api.md).** Real calls to Helena on an existing Instagram session answered part of items 3 and 4 (the session lookup works on the fabriks host and returns the channel type; the username is on the contact, not on the session; `details.from` is the Instagram id truncated) and list the six questions only a real comment can still answer. Read it before starting.

**The decision rule (already decided, spec section "Casamento no canal Helena"):**

1. If the sender identifier Helena sends on the event (`content.details.from`), in an Instagram session, equals the recipient id Meta returns when the DM is sent → match by it, no extra call.
2. Otherwise → look the session up in Helena asking for the contact details; if the channel is Instagram and the contact's username equals the commenter's username (no `@`, case-insensitive) → match by username.
3. If neither holds → the casamento goes back to the design (ADR 0001) and ADR 0002 is re-evaluated (the only foreseen reason to switch to Facebook Login is exactly this).

**What the engine does today on the Helena side (research, so you do not have to search):**

- Helena base URL the engine uses: `https://api.chat.fabriks.com.br/chat` (`settings.helena_base_url`). The glossary entry **Helena** in CONTEXT.md still says `api.helena.run/chat/v1/send/text`; that is stale, the code is the truth.
- The engine calls only two Helena endpoints, both with `Authorization: Bearer {helena_apikey}` (the Company's `helena_apikey`): `POST /v1/session/{sessionId}/message` and `PUT /v1/session/{sessionId}/assignee`. **There is no session lookup call in the Helena client today** — item 4 below is the first time it is exercised. Helena's API reference is at `helena.readme.io`. The helena-channel-parity spec mentions `GET /chat/v2/session?ContactId=...` and `GET /core/v1/contact/phoneNumber/{phone}` as fallbacks never used in code; whether a session GET answers on the fabriks host is to confirm.
- Inbound route: `POST /api/helena/{token}`, Company resolved by `helena_token`. Only `eventType == "MESSAGE_RECEIVED"` is processed. The route does **not** filter by `content.direction` (a test payload came with `direction="FROM_HUB"` on a message from the lead), which is why item 5 matters.
- Fields the engine parses on `MESSAGE_RECEIVED`: envelope `eventType`, `date`, `content`; inside `content`: `sessionId`, `text`, `id`, `type`, `direction`, `timestamp`, `details.from`, `attachment_url`, `attachment_type`, `channel`. Everything else is ignored by the parser, so **the engine's logs do not hold the full event** — capture the raw event in n8n.
- `content.channel` is not native to Helena: an n8n pre-processor sitting between Helena and the engine injects it (numeric channel id, used for the `allowed_inbox` gate). The raw Helena event is the one arriving at that n8n workflow.
- `details.from` is stored on the Customer as `custom_information.phone`. The service code comments say `details.from` is absent on Instagram/Facebook sessions; the spec says a captured event carried `+0324193345627`, phone-shaped but not a phone, possibly an Instagram id. These two disagree — item 3 settles it.

**What the engine does today on the Meta side:**

- The existing route `GET/POST /api/meta` is the WhatsApp proxy to Chatwoot and belongs to another Meta app. It is not touched and its secrets (`meta_app_secret`, `meta_verify_token`) are not reused.
- Signature header Meta uses: `X-Hub-Signature-256`, value `sha256=<hex>`, HMAC-SHA256 of the **raw** request body with the secret. Handshake: GET with `hub.mode`, `hub.verify_token`, `hub.challenge`; answer is the challenge as plain text.

**Meta app decisions already made (ADR 0002):** one Meta app, **Instagram Login** (not Facebook Login), three permissions: `instagram_business_basic`, `instagram_business_manage_comments`, `instagram_business_manage_messages`. Helena connects the same Instagram account through Facebook Login, so the user ids each app sees may differ — that is the risk items 2 and 3 measure.

**Prerequisite no spec ticket covers: App Review with Advanced Access.** The spec states the comments webhook requires the app in Live mode with Advanced Access. Getting the three permissions approved is the owner's job and is not delivered by tickets 02–05. Item 7 checks whether an account with a role on the app receives comment events without it; if it does, this ticket and the development of 02–04 can proceed while the review runs; if it does not, the review gates everything.

**Meta API details are "to confirm".** Neither the spec nor the code confirms hosts, API version, or exact parameter names for Instagram Login. The call shapes in the tasks are the expected ones; if a call fails, adjust it against Meta's Instagram Login documentation and record the shape that worked. The spec does confirm: the DM is a message send whose recipient is identified by the comment id, text only, one DM per comment, up to 7 days after the comment, and Meta returns a recipient id and a message id.

**Domain terms:** Company, Customer, `sessionId`, Comentário elegível, DM de boas-vindas, Boas-vindas pendente, Origem do comentário, Resposta pública, Helena, Canal.

## Tasks

**1. Meta app and accounts**

- [ ] Pick the test setup and write it at the top of the notes: the Instagram professional account that will receive comments (it must be connected to the Helena of a Company whose `helena_apikey` you have), and a second, personal Instagram account that will act as the commenter. Note whether the commenter account has a role on the Meta app (it matters for item 7).
- [ ] In the Meta app panel, add the Instagram product with **Instagram Login** ("API setup with Instagram login"), not Facebook Login.
- [ ] Add the three permissions to the app: `instagram_business_basic`, `instagram_business_manage_comments`, `instagram_business_manage_messages`. Record the access level each one shows (Standard or Advanced).
- [ ] Record the app's mode (Development or Live) and whether App Review for Advanced Access has been submitted, approved, or not started.
- [ ] Give the Instagram professional account a role on the app (Instagram tester, or the role the panel offers) and accept the invite from the Instagram account.
- [ ] Connect the Instagram professional account through the Instagram Login flow in the panel and obtain a long-lived access token. Record the Instagram account id the panel shows for it (two ids may be shown — record both and which one is labelled what).
- [ ] Note both secrets shown in the panel without writing their values anywhere in the repo: the app secret (App settings → Basic) and the Instagram app secret (Instagram product settings). Keep them at hand for item 8.

**2. Webhook pointed at n8n**

- [ ] Create an n8n workflow with a Webhook node that answers `GET` by returning the `hub.challenge` query parameter as plain text, and `POST` with 200. Enable the option that keeps the **raw body** of the POST — a re-serialised JSON will not match the signature in item 8.
- [ ] Make the workflow store, for every POST: all request headers (in particular `X-Hub-Signature-256`) and the raw body, byte for byte.
- [ ] In the Meta app panel, register the n8n URL as the Instagram webhook callback with a verify token of your choice and confirm the handshake succeeds.
- [ ] Subscribe the webhook to the `comments` field only.
- [ ] Check whether the Instagram account must also be subscribed to the app by an API call for events to flow (to confirm; expected shape below). Record whether it was needed.

  ```
  POST https://graph.instagram.com/<version>/me/subscribed_apps?subscribed_fields=comments&access_token=<token>
  ```

**3. Item 7 — comment event without Advanced Access** (do this first; it decides whether the rest can run now)

- [ ] With the app in its current state (before Advanced Access is approved), have the commenter account post a first-level comment on a post of the Instagram professional account.
- [ ] Record: did the event reach n8n (yes/no), how long after the comment, the app mode and permission access levels at that moment, and whether the commenter had a role on the app.
- [ ] If nothing arrived, repeat once with a commenter that has a role on the app (or the opposite of what you tried) and record that result too.
- [ ] If no variation delivers the event, stop here: record that App Review with Advanced Access gates the validation, and resume from task 4 once it is approved.

**4. Item 1 — the real comment event**

- [ ] From the event captured in n8n, save the full raw JSON (it goes into `validation.md`).
- [ ] Record the envelope: the value of `object`, the shape of each `entry` item, and which field carries the Instagram account id of the receiving account (expected `entry[].id`, to confirm) — and whether it equals one of the account ids recorded in task 1.
- [ ] Record the name of the field that carries the comment id inside the change value (`id` or `comment_id` — the spec expects either).
- [ ] Record whether a parent-comment field is present on a first-level comment (absent, null, or filled) and its exact name.
- [ ] Record the author fields: the name of the author id field, the author username field, and their values for the commenter.
- [ ] Record the field that carries the post id and the comment text field.
- [ ] Have the commenter reply to another comment on the same post and record how that event differs (the parent-comment field name and value).
- [ ] Post two comments in quick succession and record whether Meta delivered them as one call with several entries/changes or as separate calls.

**5. Item 2 — the DM and what Meta returns**

- [ ] Read the post's caption by hand and record the response. Expected shape (to confirm):

  ```
  GET https://graph.instagram.com/<version>/<post id from the event>?fields=caption,permalink&access_token=<token>
  ```

- [ ] Send the DM de boas-vindas by hand as a private reply to the comment, with any short text you will recognise later. Expected shape (to confirm):

  ```
  POST https://graph.instagram.com/<version>/<instagram account id>/messages
  Authorization: Bearer <token>
  Content-Type: application/json

  {"recipient": {"comment_id": "<comment id from the event>"}, "message": {"text": "<DM text>"}}
  ```

- [ ] Record the full response body: the field names and values of the recipient id and the message id.
- [ ] Record whether the recipient id returned equals the author id that came in the comment event (item 1). Write both values side by side.
- [ ] Record where the DM landed on the commenter's Instagram (inbox or "Solicitações") and whether the commenter follows the account.
- [ ] Send a second private reply to the same comment and record the error Meta returns (confirms one DM per comment).

**6. Item 6 — Resposta pública and the event it generates**

- [ ] Publish a reply to the comment by hand. Expected shape (to confirm):

  ```
  POST https://graph.instagram.com/<version>/<comment id from the event>/replies
  Authorization: Bearer <token>

  message=<Resposta pública text>
  ```

- [ ] Record the response body (the id of the created reply).
- [ ] Record whether this reply produced a new `comments` event in n8n. If it did, save the raw JSON and record: the author id (is it the receiving account's own id?), the author username, and the parent-comment field and its value. These are the two fields the eligibility rule uses to discard it.

**7. Item 5 — does Helena see the DM sent from outside**

- [ ] In the n8n workflow that pre-processes Helena events before the engine (the one that injects `content.channel`), make sure the raw incoming Helena event is stored before any transformation.
- [ ] Right after the DM from task 5 is sent and **before** the commenter answers, open the Helena panel and record: does a conversation/session exist for the commenter, and is the DM visible in it.
- [ ] Record whether Helena fired any event for that DM. If it did, save the raw JSON and record `eventType`, `content.direction`, `content.text`, `content.sessionId` and `content.details.from`.
- [ ] State plainly whether that event is a `MESSAGE_RECEIVED` (the engine would treat it as a lead message — the echo case the spec protects against) or something else (the engine ignores it).

**8. Item 3 — the Helena event when the lead answers**

- [ ] Have the commenter answer the DM with a short text.
- [ ] Save the raw Helena event captured in n8n (before pre-processing) and the pre-processed version the engine receives.
- [ ] Record `eventType`, `content.sessionId`, `content.direction`, `content.type`, and whether `content.details` exists and what `content.details.from` holds, exactly as sent.
- [ ] Record every other field in the raw event that identifies the sender or the channel (contact id, username, channel id/type), with name and value — anything outside the fields listed in Context is ignored by the engine today and may be a better key.
- [ ] Compare `content.details.from` with the recipient id from task 5 and with the author id from task 4. Write the three values side by side, noting any prefix or formatting difference (the captured precedent was `+0324193345627`).
- [ ] In the Helena panel, conclude/close that session, then have the commenter send another message so Helena opens a new session. Record the new `content.sessionId` and whether `content.details.from` is the same as in the first session.

**9. Item 4 — Helena session lookup**

- [ ] Using the `sessionId` from task 8 and the Company's `helena_apikey` as Bearer, call Helena's session lookup on the host the engine uses today, asking for the contact details. The endpoint and the parameter that includes contact details are to confirm in `helena.readme.io`; the starting point, by analogy with the two session endpoints the engine already calls:

  ```
  GET https://api.chat.fabriks.com.br/chat/v1/session/<sessionId>
  Authorization: Bearer <helena_apikey>
  ```

- [ ] Record the exact URL and query parameters that worked, and the HTTP status. If it does not answer on `api.chat.fabriks.com.br`, record which host does.
- [ ] Save the response JSON and record: the field that says the channel type and its value for this Instagram session, and the field that carries the contact's username and its value.
- [ ] Compare that username with the commenter's username from the comment event, after removing `@` and lowercasing both. Record whether they are equal.
- [ ] Run the same lookup for a WhatsApp session of the same Company and record the channel type value, so the Instagram value can be told apart.

**10. Item 8 — which secret signs the events**

- [ ] Take one comment event captured in n8n: its raw body saved to a file exactly as received, and its `X-Hub-Signature-256` value.
- [ ] Compute the HMAC-SHA256 of that file with the app secret and compare to the header value after `sha256=`:

  ```
  openssl dgst -sha256 -hmac "<secret>" body.json
  ```

- [ ] Repeat with the Instagram app secret.
- [ ] Record which of the two matched (by name, never the value). If neither matches, re-check that n8n kept the raw body before concluding anything.

**11. Known risk — Conversation Routing on the client account**

- [ ] On the client's Instagram account (the real one that will use the feature, not only the test account), check whether Conversation Routing is configured and record what it is set to.
- [ ] With our app and Helena both connected to that account, confirm that the lead's answer to the DM still reaches Helena (task 8 on this account, or a note that task 8 was already run on it). Record any difference in behaviour.

**12. Write the findings**

- [ ] Create `specs/instagram-comment-welcome/validation.md` with these headings, in this order:

  ```
  # Validação — comentário → DM → sessão Helena
  ## Ambiente do teste
  ## 1. Evento de comentário (Instagram Login)
  ## 2. Envio da DM e id do destinatário
  ## 3. Evento do Helena na resposta da DM
  ## 4. Consulta de sessão do Helena
  ## 5. O Helena enxerga a DM enviada por fora?
  ## 6. A Resposta pública gera evento de comentário?
  ## 7. Evento de comentário sem Advanced Access
  ## 8. Segredo que assina os eventos
  ## Conversation Routing
  ## Chamadas à Meta que funcionaram
  ## Chave do casamento
  ```

- [ ] Under "Ambiente do teste": date of the run, app mode, access level of each permission, App Review status, which accounts were used (handles only).
- [ ] Under each numbered heading: the answer in one or two sentences first, then the raw JSON captured. Replace access tokens, secrets and the `helena_apikey` with placeholders; ids and usernames stay.
- [ ] Under "Chamadas à Meta que funcionaram": the exact host, API version, path and body of the three calls (caption, DM, reply) as they finally worked, plus the account subscription call if it was needed.
- [ ] Under "Chave do casamento": apply the decision rule from Context and state exactly one outcome — (1) match by `content.details.from` == recipient id returned by Meta; (2) match by username via Helena session lookup, naming the endpoint, the channel-type value and the username field; or (3) neither works, the design goes back to ADR 0001 and ADR 0002 is re-evaluated. Quote the values that justify it.
- [ ] If the outcome is (1), state whether `content.details.from` was stable across the two sessions of task 8. If it was not, say so and fall to outcome (2) or (3).

## Acceptance criteria

- [ ] `specs/instagram-comment-welcome/validation.md` exists with all the headings above, and each of the 8 numbered sections has an explicit answer backed by a captured payload or response (or, for item 7, an explicit "did not arrive").
- [ ] A real comment produced a real DM on the commenter's Instagram and a real Resposta pública under the comment, both sent by hand, and the commenter's answer appeared as a session in Helena.
- [ ] The recipient id returned by Meta, the author id from the comment event and Helena's `content.details.from` are written side by side with a verdict on whether they coincide.
- [ ] The Helena session lookup was called for the Instagram session, and the host, path, channel-type field and username field are recorded — or it is recorded that no such lookup answers.
- [ ] "Chave do casamento" states exactly one of the three outcomes of the decision rule, enough for ticket 04 to be implemented without another test.
- [ ] The secret that signs the events is named, and the exact field names for comment id and parent comment are recorded.
- [ ] The App Review / Advanced Access status and the result of item 7 are recorded, making clear whether tickets 02–04 can be tested before approval.
- [ ] The Conversation Routing state of the client account is recorded.
- [ ] No token, app secret or `helena_apikey` value appears in the file.

## Out of scope

- Any engine code: the Instagram webhook route, the Instagram API client, the boas-vindas service and table, the casamento step, the token renewal loop (tickets 02–05).
- Submitting or conducting the App Review itself. This ticket only records its status and what works without it.
- Changing the Helena channel, the n8n pre-processor's behaviour, or the existing `/api/meta` WhatsApp proxy. The n8n changes here are capture-only.
- Generating the DM with an LLM or with the Company's prompt — the test DM is any hand-written text.
- Token renewal: one long-lived token obtained by hand is enough for the run.
- Updating ADR 0001/0002, the spec, CONTEXT.md or the file reference. If the outcome is (3), this ticket says so and stops; the redesign is a separate conversation.
- Running any migration (the local `.env` points at the production database).
