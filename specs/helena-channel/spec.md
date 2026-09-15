# Canal Helena (MVP)

## Problem Statement

Hoje o engine só recebe conversas pelo canal Chatwoot. Um cliente novo usa o Helena CRM como plataforma de atendimento e quer que a IA atenda os leads que chegam por lá: quando alguém manda uma mensagem no Helena, a IA precisa gerar a resposta e devolvê-la pelo próprio Helena — sem passar pelo Chatwoot. Esta é a primeira validação, então o escopo é o caminho básico de conversa, nada além disso.

## Solution

Um canal Helena que roda em paralelo ao Chatwoot, com endpoint e caminho próprios, mas reusando todo o núcleo de processamento já existente.

Do ponto de vista do usuário (o operador do Helena/o lead):

1. O lead manda uma mensagem de texto no Helena.
2. O Helena dispara um webhook `MESSAGE_RECEIVED` para o engine.
3. O engine agrupa mensagens rápidas do mesmo lead, processa com a IA (a mesma que atende o Chatwoot, com as mesmas tools e contexto), e gera a resposta.
4. A resposta — que o agente quebra em várias mensagens — é enviada de volta ao Helena, uma após a outra, com o mesmo espaçamento humanizado que o Chatwoot já usa.
5. O histórico da conversa fica registrado no banco igual a qualquer outra conversa.

## Depends On

Nenhuma outra spec. Constrói sobre código já existente (RequestManager, MessageBuffer, pipeline de IA, `upsert_api_customer`).

## User Stories

1. Como operador do Helena, quero que a IA responda automaticamente as mensagens de texto que chegam no Helena, para atender leads sem intervenção humana.
2. Como lead, quero receber a resposta da IA dentro do próprio Helena/WhatsApp, para continuar a conversa no canal que já uso.
3. Como lead, quero que a resposta chegue quebrada em mensagens ritmadas (não um paredão de texto), para a conversa parecer natural.
4. Como operador, quero que várias mensagens que eu mande em sequência rápida sejam tratadas como uma só entrada da IA, para a IA não responder cada fragmento isoladamente.
5. Como operador de outra empresa, quero que o Helena da minha empresa use minhas credenciais e meu agente, isolado das outras empresas, para manter a multi-tenancy.
6. Como mantenedor, quero que o canal Helena reuse o núcleo de processamento do Chatwoot, para não duplicar a lógica de buffer, cancelamento e pipeline de IA.
7. Como mantenedor, quero que o webhook responda imediatamente e processe em background, para o Helena não sofrer timeout nem reenviar o evento.
8. Como mantenedor, quero que o histórico da conversa Helena seja gravado em `chat_history` amarrado ao `sessionId` do Helena, para a conversa ter continuidade e as métricas de token serem registradas.
9. Como lead que abre uma conversa nova no Helena (nova sessão), quero começar do zero, para o contexto não misturar atendimentos diferentes.
10. Como mantenedor, quero que eventos que não sejam `MESSAGE_RECEIVED` sejam ignorados sem erro, para o endpoint não quebrar com eventos de sessão/contato/pagamento.
11. Como mantenedor, quero que uma mensagem sem texto (ex. só anexo) seja ignorada silenciosamente no MVP, para não fazer a IA processar entrada vazia.
12. Como mantenedor, quero que uma falha no envio ao Helena dispare o alerta crítico já existente, para eu saber quando uma resposta não chegou ao lead.
13. Como mantenedor, quero que a company do webhook seja resolvida pelo token na URL, para o roteamento multi-tenant ser o mesmo padrão do Chatwoot.

## Implementation Decisions

### Estrutura — novo pacote `app/helena/`, espelhando `app/chatwoot/`

Um pacote próprio com quatro peças, análogas às do Chatwoot:

- **`app/helena/schemas.py`** — modelos Pydantic do payload `MESSAGE_RECEIVED`. Envelope `{ eventType, date, content }`; o `content` traz `id`, `sessionId`, `text`, `type`, `direction`, `timestamp`, e `details` (com `from` = telefone do lead). Campos não usados no MVP são opcionais/ignorados. Validação tolerante: o parser não pode quebrar se o Helena mandar campos extras.
- **`app/helena/client.py`** — `HelenaClient` (httpx async) com `send_text(...)` (um POST) e `send_messages(...)` (loop com delay). Base URL `https://api.helena.run`, endpoint `POST /chat/v1/send/text`, header `Authorization: Bearer {helena_apikey}`, body `{ "sessionId": ..., "text": ... }`. **Reusa `calculate_humanized_delay`** de `app/chatwoot/client.py` (não reimplementar a fórmula).
- **`app/helena/service.py`** — `HelenaService.process_webhook(payload, company)`, enxuto: só o caminho feliz.
- **`app/routes/helena.py`** — `POST /helena/{token}`, registrado no `main.py` com prefixo `/api` (rota final `POST /api/helena/{token}`), ao lado dos routers chatwoot/meta/voe.

### Rota e background

Espelha `routes/chatwoot.py`: valida o payload, responde `{"status": "received"}` na hora, e joga o processamento para uma `BackgroundTask`. A resolução da company e todo o trabalho de DB acontecem no background, não no handler síncrono. Eventos com `eventType != "MESSAGE_RECEIVED"` são ignorados (retorna received sem processar).

### Resolução de company (multi-tenant)

- Duas colunas novas na tabela `companies`: `helena_token` (UUID, unique, index — casa com o `{token}` da URL) e `helena_apikey` (String — o Bearer de envio). **Migração Alembic** nova.
- `CompanyRepository.get_by_helena_token(token)` — análogo a `get_by_cw_token`. Se não achar company, aborta o background silenciosamente (só log).

### Customer e sessão

- `Customer.sessionId` = `content.sessionId` do payload (UUID nativo do Helena). Ver ADR 0002.
- Reusa **`CustomerRepository.upsert_api_customer(session_id=..., ...)`** — já cria/recupera customer por `sessionId` puro sem campos Chatwoot, com fallback de agent/sub_agent da company (`standard_agent_id` / `standard_sub_agent_id`). Não criar método novo.
- Telefone do lead (`details.from`) é gravado como metadado do customer (via `custom_information` no upsert) para referência; não é a chave.

### Buffer / RequestManager

- Reusa o `RequestManager` singleton (`get_request_manager()`) e o `MessageBuffer`, sem alteração neles.
- A chave inteira exigida por `on_new_message(contact_id: int, ...)` e pelo buffer é derivada do `sessionId`: um inteiro estável a partir do hash do UUID (ex. `int` dos primeiros bytes de um hash, cabendo em BigInteger). Ver ADR 0001. Encapsular essa derivação numa função única no `HelenaService` (ou util), para o buffer/lock agruparem corretamente as mensagens da mesma sessão.

### Envio da resposta

- O `HelenaService` passa ao `on_new_message` um callback `on_send_messages` que chama `HelenaClient.send_messages(sessionId, messages, helena_apikey)`.
- `send_messages`: primeira mensagem sem delay; as seguintes com `await asyncio.sleep(calculate_humanized_delay(msg))` antes do POST — cópia do comportamento de `ChatwootClient.send_messages`. **Sem `delayTyping`** (decisão do usuário: mais simples).
- A resposta final da IA (`response["resposta"]`, lista) também é enviada por esse mesmo caminho, igual ao Chatwoot.
- Falha de envio → `send_critical_alert("HELENA_SEND_FAILED", ...)`, seguindo o padrão dos outros canais.

### Contrato do payload de envio (Helena `send/text`)

```
POST https://api.helena.run/chat/v1/send/text
Authorization: Bearer {helena_apikey}
{ "sessionId": "<uuid da conversa>", "text": "<mensagem>" }
```
Responde 200 com `{ id, sessionId, status: "QUEUED", ... }`. Rate limit informado: 1000 req / 2 min.

## Testing Decisions

- **Seam único: a rota `POST /api/helena/{token}`.** O teste injeta um payload `MESSAGE_RECEIVED` real (o capturado do n8n serve de fixture) e verifica o comportamento observável: que o `HelenaClient` foi chamado para enviar as mensagens da resposta ao `send/text`, na ordem certa. O `HelenaClient` (a chamada httpx de saída) e a chamada à OpenAI são mockados; o resto do caminho roda de verdade.
- Testar **comportamento externo**, não internos: o que entra pelo webhook e o que sai pelo client. Não testar formato interno do buffer nem da ConversationTurn (já cobertos pelo núcleo).
- Casos que valem teste: (a) `MESSAGE_RECEIVED` de texto → dispara envio; (b) evento não-`MESSAGE_RECEIVED` → ignorado, nenhum envio; (c) mensagem sem texto → ignorada; (d) token que não resolve company → nenhum envio, sem exceção; (e) resposta quebrada em N mensagens → N POSTs na ordem.
- **Prior art:** os testes do webhook Chatwoot (mesmo shape: monta payload, mocka o client de saída e a OpenAI, dispara a rota, verifica as chamadas de envio) são o modelo direto a seguir.

## Out of Scope

Deliberadamente fora deste MVP — não foram esquecidos:

- **Escalonamento para atendimento humano** e qualquer gate de IA ligada/desligada (`status=False`). Sem isso, a IA sempre responde.
- **Eventos de sessão do Helena** (`SESSION_NEW`, `SESSION_UPDATE`, `SESSION_COMPLETE`) — inclusive detectar atendente humano assumindo. Ficam para uma fase futura (o payload desses eventos ainda precisa ser capturado).
- **Dev commands** (`#resetar`, `#mudar_agente`).
- **Follow-up agendado** (RabbitMQ) no canal Helena.
- **Transcrição de áudio** e tratamento de anexos (imagem/vídeo/arquivo). Mensagem sem texto é ignorada.
- **Labels / assignment / atribuição** de conversa no Helena.
- **`delayTyping` nativo** do Helena. Usamos `sleep` local.
- **Deduplicação de webhook por `id` da mensagem** (o `MessageBuffer` já trata concorrência por rajada; dedup explícito de reentrega do Helena não entra agora).
- **Assinatura/verificação de webhook** do Helena (não há segredo de assinatura documentado; a segurança do MVP é o token na URL).
- **Histórico perpétuo por telefone** — o histórico é por sessão do Helena (ADR 0002).

## Further Notes

- **Confirmar no primeiro envio real** (marcar com comentário `ponytail:` no código): que o `send/text` aceita responder só com `sessionId` (sem `to`), e que a conversa recebe as mensagens na ordem. Se o Helena exigir `to`, cair para o telefone de `details.from`.
- O payload de `MESSAGE_RECEIVED` capturado em teste veio com `direction: "FROM_HUB"` numa mensagem que o próprio usuário enviou marcando "delivered". O evento só dispara para mensagens efetivamente recebidas de leads, então o MVP processa todo `MESSAGE_RECEIVED` sem filtrar por `direction`. Se aparecerem eventos espúrios (a IA respondendo a si mesma), reintroduzir um filtro de direção — capturar o payload de uma mensagem genuína de lead confirma o valor certo.
- Base URL do Helena (`https://api.helena.run`) e endpoint podem ir para `config.py` como constantes/settings, seguindo o padrão dos outros canais.
