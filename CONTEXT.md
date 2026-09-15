# Star Agents Engine

Orquestrador de agentes de IA que atende clientes via chat, multi-tenant. Recebe mensagens por webhooks de canais externos, processa com LLM (tool calling recursivo) e devolve a resposta pelo mesmo canal.

## Language

**Canal**:
Uma origem de mensagens externa que entrega mensagens ao engine por webhook e recebe as respostas da IA por uma API própria. Cada canal tem seu endpoint de entrada, seu formato de payload e seu client de saída, mas compartilha o núcleo agnóstico (RequestManager, MessageBuffer, pipeline de IA). Ex: Chatwoot, Helena.
_Avoid_: Integração, provider, conector

**Helena**:
Canal do Helena CRM. Entra por `POST /helena/{token}` (evento `MESSAGE_RECEIVED`), resolve a company pelo `helena_token`, e devolve a resposta por `POST api.helena.run/chat/v1/send/text` autenticado com Bearer `helena_apikey`. O `sessionId` da conversa vem do próprio payload do Helena.
_Avoid_: helenacrm, helena.app

**Company**:
Raiz da multi-tenancy. Cada empresa tem suas credenciais de canal, agentes e clientes. Toda operação é escopada por `company_id`.
_Avoid_: Tenant, cliente (que aqui é o lead), account

**Customer**:
O lead/contato que conversa com a IA de uma company. Identificado internamente por `sessionId` + `company_id`. Uma linha em `customers`.
_Avoid_: Lead, contato, usuário

**sessionId**:
Identificador da conversa de um Customer. É a chave que amarra `customers` e `chat_history`. No Chatwoot vem do `conversation.id`; no Helena vem do `sessionId` nativo do payload.
_Avoid_: session_id (na fronteira externa), conversation_id

**RequestManager**:
Singleton que coordena o ciclo de vida do processamento por cliente: um lock por cliente, cancela a task ativa quando chega mensagem nova, e move mensagens entre buffer e processing. Agnóstico ao canal — o envio da resposta é sempre por callback (`on_send_messages`).

**MessageBuffer**:
Buffer no Redis que agrupa mensagens rápidas do mesmo cliente e só processa a última (comparação por UUID). Chaveado por um inteiro estável por cliente (`buffer:{id}`, `processing:{id}`).

**Delay humanizado**:
Regra que espaça as várias mensagens de uma resposta da IA para simular digitação. Fórmula por tamanho: `chars * 0.05 - 3.5`, limitada entre 2s e 15s. Hoje vive no client de saída do canal.

**ChatHistory**:
Registro das mensagens da conversa (`chat_history`), chaveado por `sessionId`. Guarda role, content, tool_calls e métricas de token.
