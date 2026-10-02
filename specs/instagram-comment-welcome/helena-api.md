# Pesquisa — o que a API do Helena oferece para o casamento

Levantamento feito em 2026-10-01, antes do ticket 01, com duas fontes: a documentação pública (`helena.readme.io`, `docs.helena.app`) e três leituras reais na API, a partir de um `MESSAGE_RECEIVED` capturado de uma sessão de Instagram da 7ball (Company 5).

Este arquivo **não substitui o `validation.md`** do ticket 01. Ele adianta os itens 3 e 4 daquela lista com fatos e deixa claro o que ainda só um comentário real responde. O ticket 04 continua esperando o `validation.md`.

## Resumo

- A DM de boas-vindas **não pode sair pelo Helena**. Continua saindo pela Meta (ADR 0001 confirmado).
- O username de quem escreveu **está no Helena**, mas no contato, não na sessão. O casamento por username precisa de **duas leituras** encadeadas, não de uma.
- O `details.from` do webhook é o **id do Instagram da pessoa, cortado** como se fosse telefone. É o candidato ao casamento sem chamada extra, se o id que a Meta nos dá for o mesmo.

## Por que a DM não sai pelo Helena

- A documentação diz: "Não é possível iniciar conversa com contato de Intagram e Messenger, apenas responder."
- `POST /chat/v1/session/{id}/message` exige sessão existente, e ela só existe depois que a pessoa responde.
- `POST /chat/v1/message/send` identifica o destinatário por telefone e só inicia conversa no WhatsApp, por template.
- Não existe endpoint para criar sessão, importar mensagem, nem responder comentário.
- `POST /chat/v1/chatbot/send` aceita `to` como `@usuarioinstagram` e leva `sessionMetadata`/`contactMetadata`. Não foi testado; a regra da Meta (não se escreve a quem nunca escreveu) indica que falha. Vale uma chamada no ticket 01: se funcionasse, o Helena abriria a sessão e o casamento deixaria de existir.

## O payload que o Helena manda

`MESSAGE_RECEIVED` real, primeira mensagem do lead numa sessão de Instagram:

```json
{
  "eventType": "MESSAGE_RECEIVED",
  "date": "2026-09-30T16:19:48.4843886Z",
  "content": {
    "id": "cde27561-9032-4727-9007-5d9c2648b940",
    "companyId": "c8ed372c-445f-406f-af12-f02f197f338f",
    "senderId": null,
    "type": "TEXT",
    "sessionId": "a98e2aa1-1ed4-4941-8bde-a35cbca1cac6",
    "userId": null,
    "timestamp": "2026-09-30T16:19:43.371Z",
    "text": "Oi",
    "direction": "FROM_HUB",
    "status": "DELIVERED",
    "origin": "GATEWAY",
    "refId": null,
    "details": { "to": null, "from": "+0324193345627" }
  },
  "changeMetadata": null
}
```

O evento **não traz** username, id de contato nem canal. Para o casamento só servem `sessionId` e `details.from`.

- `direction: "FROM_HUB"` é mensagem de entrada (do lead).
- `companyId` é o id da conta no Helena, não a nossa Company.

## Leituras reais

As três responderam 200 em `api.chat.fabriks.com.br`, o host que o engine já usa, com `Authorization: Bearer {helena_apikey}`. A documentação mostra `api.helena.run`; os prefixos `/chat` e `/core` são os mesmos.

### 1. Sessão — `GET /chat/v2/session/{sessionId}?includeDetails=ContactDetails&includeDetails=ChannelDetails`

| Campo | Valor |
|---|---|
| `channelType` | `INSTAGRAM` |
| `contactId` | `ae8c5c42-d767-4140-a10e-dc66724bd791` |
| `contactDetails.name` | nome de exibição da pessoa |
| `contactDetails.instagram` | **`null`** |
| `contactDetails.phonenumber` | `null` |
| `contactDetails.pictureUrl` | nome do arquivo termina em `_1090324193345627.jpg` |
| `channelDetails.humanId` | `@7ball` |
| `channelDetails.pictureUrl` | nome do arquivo termina em `_17841401326002849.jpg` |
| `metadata` | `null` |

`channelType` separa Instagram de WhatsApp sem depender do `content.channel` que o n8n injeta. O username **não vem** aqui, mesmo pedindo `ContactDetails`.

### 2. Contato — `GET /core/v1/contact/{contactId}`

| Campo | Valor |
|---|---|
| `instagram` | **`giovanadalmasoteixeira`** (username, sem `@`) |
| `nameInstagram` | nome de exibição |
| `origin` | `CREATED_FROM_HUB` |
| `phoneNumber`, `messengerId`, `metadata` | `null` |

Não há campo com o id do Instagram no contato.

### 3. Mensagens da sessão — `GET /chat/v1/session/{sessionId}/message`

- Mensagem do lead: `direction: FROM_HUB`, `origin: GATEWAY`.
- Resposta enviada pelo engine: `direction: TO_HUB`, `origin: GATEWAY`. O `origin` não distingue quem enviou; só a `direction`.
- `details.from` vem `null` na listagem — ele só existe no webhook.
- Um aviso gerado pelo próprio Helena ("*Atenção:* o tipo da mensagem enviada pe…", logo depois de um áudio do lead) aparece como `FROM_HUB`, igual a uma mensagem do lead. Fora do escopo desta spec.

## O que isso muda no casamento

A regra de decisão da spec continua a mesma; o que muda é o detalhe de cada caminho.

**Caminho 1 — por id, sem chamada extra.** O `details.from` (`+0324193345627`) são os 13 últimos dígitos do id que aparece na foto do contato (`1090324193345627`): o Helena formata o id como telefone e corta o começo. Se o `from.id` do evento de comentário ou o `recipient_id` devolvido pela Meta no envio da DM for `1090324193345627`, o casamento é por sufixo — o id da Meta termina com os dígitos do `details.from` sem o `+`. Risco conhecido: o Helena conecta a conta por Facebook Login e o nosso app por Instagram Login (ADR 0002), e a Meta pode dar ids diferentes para a mesma pessoa em cada um.

**Caminho 2 — por username, duas leituras.** A spec previa uma consulta de sessão que devolvesse o username; ela devolve `null`. O caminho que funciona é:

1. `GET /chat/v2/session/{sessionId}` → conferir `channelType == "INSTAGRAM"` e pegar `contactId`;
2. `GET /core/v1/contact/{contactId}` → comparar `instagram` com o `commenter_username` da Boas-vindas pendente (sem `@`, sem diferenciar maiúsculas).

O `HelenaClient` hoje só conhece a base `/chat` (`settings.helena_base_url`); a leitura do contato fica em `/core`, no mesmo host.

## O que o ticket 01 ainda precisa responder

1. O `from.id` do evento de comentário e o `recipient_id` da DM são iguais a `1090324193345627` para essa mesma pessoa? (decide o caminho 1)
2. O username do evento de comentário é igual ao `instagram` do contato no Helena? (confirma o caminho 2)
3. O Helena preenche o `instagram` do contato já na primeira mensagem de uma pessoa nova? O contato consultado tinha dois dias.
4. O `details.from` é o mesmo em duas sessões da mesma pessoa?
5. O Helena enxerga a DM enviada por fora? Se sim, com qual evento e qual `direction` — `MESSAGE_SENT` é um evento separado de `MESSAGE_RECEIVED`, e a listagem de mensagens da sessão mostra o que o Helena guardou.
6. `POST /chat/v1/chatbot/send` com `to: "@username"` alcança quem só comentou?

## Outros recursos da API que podem servir

Não estão na spec; ficam registrados para decisão.

- **Nota interna na sessão** — `POST /chat/v1/session/{id}/note` grava texto livre. Ao casar, o engine poderia deixar a Origem do comentário na sessão, para o atendente humano que assumir ver de qual post o lead veio, caso o Helena não mostre a DM enviada por fora.
- **Metadados da sessão** — `PUT /chat/v2/session/{id}/partial` aceita `metadata` livre, filtrável em `GET /chat/v2/session`.
- **Etiqueta no contato** — `POST /core/v1/contact/{id}/tags`, para marcar e segmentar quem veio de comentário.
- **Busca de contato por username** — `POST /core/v1/contact/filter` aceita `instagram`; daí `GET /chat/v2/session?ContactId=` lista as sessões da pessoa.
- **Evento `SESSION_NEW`** — o Helena avisa a criação da sessão. Alternativa para rodar o casamento antes da primeira mensagem, se for preciso.
- **Pré-criar o contato** (`POST /core/v1/contact` com `instagram`) continua descartado: o ADR 0001 registra que isso duplicou contato e atendimento.

## Fontes

- Referência da API: <https://helena.readme.io/reference>
- Sessão: <https://helena.readme.io/reference/get_v2-session-id>
- Contato: <https://helena.readme.io/reference/get_v1-contact-id>
- Mensagens da sessão: <https://helena.readme.io/reference/get_v1-session-id-message>
- Envio de chatbot: <https://helena.readme.io/reference/post_v1-chatbot-send>
- Eventos de webhook: <https://helena.readme.io/reference/get_v1-webhook-event>
- Instagram no Helena: <https://docs.helena.app/configurando-sua-plataforma/ajustes/conta/cadastrar-instagram-facebook-messenger>
