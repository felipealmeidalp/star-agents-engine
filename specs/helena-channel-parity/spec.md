# Canal Helena — paridade parcial com o Chatwoot (gate de IA, anexos, escalonamento)

## Problem Statement

O canal Helena entrou no ar como MVP (spec [`helena-channel`](../helena-channel/spec.md)): recebe `MESSAGE_RECEIVED`, processa com a IA e responde por telefone. Mas ele é o caminho feliz e nada mais. Três coisas que o Chatwoot já faz faltam no Helena, e o cliente precisa delas:

1. **A IA não tem como ser desligada.** No Chatwoot, transferir para humano silencia a IA (`customers.status = False`) e o serviço checa isso antes de responder. O `HelenaService` **não checa** `status` — então mesmo que a conversa seja escalada, a IA continua respondendo por cima do atendente humano.
2. **Anexos são ignorados.** Se o lead manda um áudio, uma imagem ou um arquivo sem texto, o Helena descarta silenciosamente ("no_text"). O Chatwoot transcreve áudio (Whisper) e descreve os demais anexos em texto.
3. **Não há escalonamento para humano.** A tool `transfer_to_human` é Chatwoot-only (labels, times, `ChatwootClient`) e nem sabe em que canal está. No Helena, a IA não tem como passar a conversa para um atendente.

## Solution

Levar essas três capacidades ao Helena, reusando o que já existe e sem tocar no que o núcleo não precisa saber.

Do ponto de vista do operador e do lead:

1. **Liga/desliga da IA:** quando a conversa é escalada para um humano, a IA para de responder — na hora, na próxima mensagem daquela sessão. Enquanto estiver desligada, as mensagens do lead continuam sendo guardadas no histórico, mas a IA não responde. (Religar a IA continua sendo uma ação manual no banco — fora de escopo, ver Out of Scope.)
2. **Anexos:** o lead pode mandar um áudio e a IA entende (o áudio vira texto por transcrição, igual ao Chatwoot). Se mandar imagem/vídeo/arquivo, a IA recebe uma frase dizendo o que foi enviado ("O usuário enviou uma imagem") — não enxerga a mídia, mas não fica cega ao fato de que algo chegou.
3. **Escalonamento:** quando a IA decide (via `transfer_to_human`) que a conversa precisa de um humano, ela: (a) para de responder, e (b) atribui a sessão no Helena ao atendente configurado para aquela empresa, com o bot do Helena parado. O atendente assume a partir do painel do Helena.

## Depends On

- [`specs/helena-channel/spec.md`](../helena-channel/spec.md) — o canal Helena base (rota `POST /api/helena/{token}`, `HelenaService`, `HelenaClient`, colunas `helena_token`/`helena_apikey`, `upsert_api_customer` por `sessionId`). Esta spec é escrita assumindo que o MVP já está no ar (está: rota registrada em `main.py`, pacote `app/helena/` completo). Implementar esta antes seria construir sobre um `HelenaService` que não existe.

## User Stories

### Gate de IA (liga/desliga)

1. Como atendente, quando eu recebo uma conversa escalada pela IA, quero que a IA pare de responder aquele lead, para não competir comigo no atendimento.
2. Como lead cuja conversa foi para um humano, quero falar só com a pessoa, para não receber respostas automáticas no meio do atendimento humano.
3. Como mantenedor, quero que uma mensagem que chega enquanto a IA está desligada seja gravada no histórico mesmo sem resposta da IA, para o atendente ver o que o lead disse e o contexto não se perder.
4. Como mantenedor, quero que o gate de IA do Helena seja o mesmo campo (`customers.status`) e o mesmo comportamento do Chatwoot, para não ter duas semânticas de "IA desligada" no sistema.
5. Como mantenedor, quero que a checagem do gate aconteça antes de qualquer chamada à IA, para não gastar token nem disparar tools numa conversa desligada.

### Anexos

6. Como lead, quero mandar um áudio no Helena e ser entendido, para não precisar digitar tudo.
7. Como lead, quero que, ao mandar uma imagem, um vídeo ou um arquivo, a IA ao menos saiba que enviei algo, para a conversa não travar como se eu não tivesse mandado nada.
8. Como mantenedor, quero que a transcrição de áudio do Helena reuse a mesma função do Chatwoot (Whisper `transcribe_audio`), para não ter dois caminhos de transcrição.
9. Como mantenedor, quero que o texto tenha precedência sobre o anexo (mensagem com texto + anexo usa o texto), para manter a paridade exata com o Chatwoot.
10. Como mantenedor, quero que uma falha de transcrição avise o lead e dispare o alerta crítico já existente, para eu saber quando um áudio não foi entendido.
11. Como mantenedor, quero que o anexo seja resolvido em texto no serviço do canal, antes do núcleo, para o núcleo continuar só-texto e não precisar entender mídia.

### Escalonamento humano

12. Como IA, quando o lead pede um humano ou o fluxo determina, quero chamar `transfer_to_human` e que a conversa vá para um atendente no Helena, para o lead ser atendido por uma pessoa.
13. Como empresa no Helena, quero configurar qual atendente recebe as conversas escaladas, para o escalonamento cair na pessoa certa.
14. Como atendente, quero que a sessão escalada chegue atribuída a mim no Helena com o bot parado, para eu assumir sem a automação interferindo.
15. Como mantenedor, quero que `transfer_to_human` funcione tanto no Chatwoot quanto no Helena, ramificando pelo canal, para ter uma única tool de escalonamento.
16. Como mantenedor, quero que o escalonamento pare a IA localmente (`status=False`) na hora, sem depender de nenhum evento de volta do Helena, para a IA não responder mais uma vez por atraso de webhook.
17. Como mantenedor, quero que uma falha na chamada de atribuição ao Helena ainda deixe a IA desligada e dispare alerta crítico, para o pior caso ser "IA parada aguardando humano", nunca "IA respondendo sozinha achando que transferiu".

## Implementation Decisions

### 1. Gate de IA no HelenaService

- `HelenaService.process_webhook` passa a checar `customer.status` **antes** de `on_new_message`, espelhando o passo "1.5" do `ChatwootService` (o bloco que hoje vive em `chatwoot/service.py` logo após recuperar o customer): se `status is False`, grava a mensagem do lead no histórico (`insert_user_message`) e retorna sem chamar a IA. O Helena é enxuto e não tem follow-up, então a parte de `_update_follow_up_and_schedule` do Chatwoot **não** é replicada.
- O `HelenaService` hoje faz `upsert_api_customer` mas não lê o customer de volta. Passa a recuperar o `status` do customer (via `get_status` ou lendo o customer após o upsert) para poder checar o gate.
- Reusa `customers.status`, `CustomerRepository.get_status` e `update_status` como estão. Nenhuma coluna nova para o gate.

### 2. Anexos no HelenaService

- **Schema:** `HelenaContent` ganha um campo de anexo. O payload real de anexo do Helena ainda não foi capturado — o schema deve ser tolerante (campos opcionais, sem `extra="forbid"`) e marcado com `ponytail:` para ajustar quando um payload real chegar. Mínimo necessário: uma URL do arquivo e um tipo/mimetype que permita distinguir áudio dos demais.
- **Pré-processamento:** replicar a lógica de `_handle_attachments` do Chatwoot, adaptada ao payload Helena: texto tem precedência; sem texto e sem anexo → ignora (comportamento atual); áudio → transcreve; imagem/vídeo/arquivo → frase descritiva em português. O resultado é a string `message` que vai para `on_new_message`.
- **Transcrição:** reusar `OpenAIService.transcribe_audio(url)`, que já é agnóstico de canal. A API key sai de `company_repo.get_openai_api_key(company.id)`, como no Chatwoot. Não reimplementar transcrição.
- **Reuso vs. cópia:** `_handle_attachments` e `_transcribe_audio_attachment` hoje são métodos privados do `ChatwootService`, acoplados ao payload e ao client do Chatwoot. O caminho limpo é extrair a lógica pura (dado URL de áudio → texto; dado tipo de anexo → frase) para um helper compartilhado que cada canal chama, em vez de duplicar. A frase descritiva por tipo é a mesma string dos dois canais.
- **Falha de transcrição:** avisa o lead (mensagem de erro pelo `HelenaClient`) e dispara `send_critical_alert("AUDIO_TRANSCRIPTION_FAILED", ...)`, como o Chatwoot.
- **Núcleo intacto:** nada disso toca `on_new_message`/`process_chat_in_memory`/`OpenAIMessage` — o núcleo continua recebendo `message: str`. Ver ADR 0003.

### 3. Escalonamento — `transfer_to_human` multi-canal

- **`ToolExecutionContext` ganha `channel: str`** (`"chatwoot"`/`"helena"`). Ver ADR 0005. O contexto é montado num único ponto, `chat_handler.py`, dentro do núcleo agnóstico — que hoje não conhece o canal. Portanto o `channel` precisa **fluir do serviço do canal até o `ChatHandler`**: `HelenaService`/`ChatwootService` → `RequestManager.on_new_message` → `process_chat_in_memory` → `ChatHandler` → `ToolExecutionContext(channel=...)`. O núcleo carrega esse `channel` como um valor opaco (uma string que ele repassa), sem nenhuma lógica de canal — é a única concessão de "saber o canal" que o núcleo faz, e serve para qualquer tool futura.
- **`transfer_to_human` ramifica por `context.channel`:**
  - A parte comum (agnóstica) roda sempre, primeiro: `update_status(status=False)`. É o que garante a User Story 16/17 — a IA para mesmo se a chamada ao CRM falhar depois.
  - `channel == "chatwoot"`: o caminho atual (labels + `_pick_human_assignee` + `_assign_and_verify`), inalterado.
  - `channel == "helena"`: atribui a sessão via API do Helena (ver abaixo). Sem labels (o Helena aplica tags a contato, não a sessão — fora de escopo) e sem sorteio de time.
- **Config do destino:** nova coluna `companies.helena_assignee_id` (o `userId` UUID do atendente Helena). Migração Alembic nova, espelhando o par `helena_token`/`helena_apikey`. Um atendente fixo por empresa. Se faltar `helena_assignee_id`, a tool ainda desliga a IA (status=False) e retorna sucesso, só pulando a atribuição (mesmo padrão defensivo do Chatwoot quando falta config). Ver ADR 0004.
- **Chamada ao Helena:** `HelenaClient` ganha um método de atribuição que faz
  `PUT {base_url}/chat/v1/session/{sessionId}/assignee` com header `Authorization: Bearer {helena_apikey}` e body:

  ```json
  { "userId": "<helena_assignee_id>", "options": { "stopBotInExecution": true } }
  ```

  O `{sessionId}` é o `Customer.sessionId` (= `content.sessionId` do webhook). Assumimos que esse id é o mesmo que a API de sessão espera — marcar `ponytail:` para confirmar no primeiro escalonamento real; se falhar, resolver a sessão por telefone (`GET /core/v1/contact/phoneNumber/{phone}` → `GET /chat/v2/session?ContactId=...`). Ver ADR 0004.
- **Falha na atribuição:** captura, mantém `status=False`, dispara `send_critical_alert("HELENA_ASSIGN_FAILED", ...)`, e a tool ainda retorna sucesso (a IA está parada — o pior caso é a atribuição não ter caído, que o atendente resolve no painel). Nunca deixa a IA voltar a responder por causa de falha de atribuição.

### Schema changes

- `companies.helena_assignee_id` (String/UUID, nullable) — migração Alembic nova.
- `HelenaContent` — campo(s) de anexo, opcionais, parsing tolerante.
- `ToolExecutionContext.channel: str` — novo campo (default seguro, ex. `"chatwoot"`, ou obrigatório preenchido pelos dois services).

### API contracts

- **Entrada (Helena → engine):** `MESSAGE_RECEIVED` como hoje, agora podendo trazer anexo (schema a confirmar com payload real).
- **Saída — atribuição (engine → Helena):** `PUT /chat/v1/session/{sessionId}/assignee`, Bearer, body `{ userId, options: { stopBotInExecution: true } }`. Responde 200. Mesmo token do envio de mensagem.
- **Saída — envio de mensagem:** inalterado (`POST /v1/message/send` por telefone).

## Testing Decisions

- **Testar comportamento externo, não internos.** O que entra pelo webhook / o que a IA dispara, e o que sai pelos clients (mockados).
- **Seam 1 — a rota `POST /api/helena/{token}`** (o mesmo do ticket 03 do MVP), para gate e anexos:
  - `status=False` → mensagem gravada no histórico, **nenhum** envio ao Helena, nenhuma chamada à OpenAI de complet's.
  - `status=True` + payload de áudio → a transcrição (mockada em `transcribe_audio`) vira a `message` que chega à IA; verifica que o texto transcrito é o que entra no pipeline.
  - payload de imagem/vídeo/arquivo sem texto → a frase descritiva vira a `message`.
  - texto + anexo → usa o texto, ignora o anexo.
  - falha de transcrição → dispara alerta e manda a mensagem de erro ao lead.
- **Seam 2 — a tool `transfer_to_human` via `ToolHandler.execute_all`**, com `ToolExecutionContext(channel="helena", ...)`:
  - seta `status=False` e chama o `assignee` do Helena com o `helena_assignee_id` da company e `stopBotInExecution:true`.
  - `channel="chatwoot"` continua fazendo o caminho de labels/assignment (não regrediu).
  - sem `helena_assignee_id` → `status=False`, nenhuma chamada de atribuição, sucesso.
  - atribuição falha → `status=False` mantido, alerta crítico, sucesso.
  - O `HelenaClient` (chamada httpx de saída) é mockado; o `update_status` roda de verdade contra o db de teste.
- **Prior art:** os testes do webhook Chatwoot (montam payload, mockam client de saída e OpenAI, disparam a rota, verificam envios) são o modelo para o Seam 1; os testes existentes de `transfer_to_human` no Chatwoot são o modelo para o Seam 2.

## Out of Scope

Deliberadamente fora — não esquecidos:

- **Religar a IA automaticamente.** Voltar `status=True` continua sendo ação manual no banco, como no Chatwoot. Nenhum evento nem comando religa a IA nesta spec.
- **Intervenção pelo painel do Helena.** Um humano assumindo a conversa direto no painel (sem passar pela tool) **não** desliga a IA. Não assinamos nem parseamos `SESSION_UPDATE`. A IA só é desligada pela própria `transfer_to_human`. Ver ADR 0004.
- **Vision / multimodal.** A IA não enxerga imagens; imagem/vídeo/arquivo viram frase descritiva. Suportar vision de verdade mexe no núcleo (`OpenAIMessage.content`) e é outra spec. Ver ADR 0003.
- **Escalonamento por departamento ou com a IA escolhendo o destino.** O destino é um atendente fixo (`userId`) por empresa. Departamento (`transfer` type=DEPARTMENT) e roteamento dinâmico ficam para depois, se surgir a necessidade. Ver ADR 0004.
- **Labels/tags no Helena.** O Helena aplica tags a contato, não a sessão; não replicamos o swap de labels do Chatwoot.
- **Follow-up agendado** no Helena (segue fora, como no MVP).
- **Redistribuição quando o atendente está offline.** Atribuição a `userId` fixo não redistribui; se virar problema, migrar para departamento.
- **Parsing definitivo do payload de anexo do Helena.** O schema entra tolerante e é confirmado com um payload real (marcado `ponytail:`).

## Further Notes

- Dois pontos a confirmar no primeiro uso real, marcar com `ponytail:` no código:
  1. Que `content.sessionId` serve como `{id}` na API de sessão do Helena (assignee). Fallback: resolver por telefone.
  2. Que o body de `assignee` quer o `userId` do agente (a doc lista `id` e `userId` no objeto do agente; usar `userId`).
- O `helena_assignee_id` é o `userId` do atendente, obtido em `GET /core/v1/agent` da conta do cliente. Vale documentar para quem configura a empresa.
- A cadeia que carrega o `channel` pelo núcleo toca `RequestManager.on_new_message`, `process_chat_in_memory` e `ChatHandler.__init__`/`ToolExecutionContext` — é a mudança de maior alcance da spec, mas é só repasse de um valor opaco, sem lógica. Manter assim.
