# Execution plan — Canal Helena, paridade parcial com o Chatwoot

3 tickets em 1 wave. Os três são independentes (nenhum bloqueia outro) e rodam em paralelo, um agente cada, um branch cada.

> **Depends on** [`specs/helena-channel/spec.md`](../helena-channel/spec.md) — implementar aquela spec primeiro. Este plano assume que o MVP do canal Helena já subiu (rota `POST /api/helena/{token}`, `HelenaService`, `HelenaClient`, colunas `helena_token`/`helena_apikey`, `upsert_api_customer`).

## Wave 1 — 3 tickets em paralelo

| Ticket | Delivers | Touches |
|---|---|---|
| [01 — Gate de IA no HelenaService](./ticket_01.md) | `status is False` → grava a mensagem no histórico, não chama a IA (paridade com o passo 1.5 do Chatwoot) | `app/helena/service.py` (`__init__`, `process_webhook`), `tests/test_helena_route.py` |
| [02 — Anexos no HelenaService](./ticket_02.md) | áudio transcrito vira a `message`; imagem/vídeo/arquivo → frase descritiva; texto tem precedência; falha de transcrição avisa o lead + alerta | `app/helena/service.py` (`__init__`, `process_webhook`), `app/helena/schemas.py`, `app/utils/attachments.py` (novo), `app/chatwoot/service.py` (refactor de `_handle_attachments`), `tests/test_helena_route.py` |
| [03 — `transfer_to_human` multi-canal](./ticket_03.md) | escalonamento Helena: `status=False` + atribui a sessão ao atendente fixo (`helena_assignee_id`) com `stopBotInExecution:true`; falha mantém IA parada + alerta. Inclui a cadeia `channel` pelo núcleo, a coluna nova (+ migração `007`) e `HelenaClient.assign_session` | `app/models/tables.py`, `app/models/schemas.py`, `app/services/chat_handler.py`, `app/services/chat_processor.py`, `app/services/request_manager.py`, `app/helena/client.py`, `app/helena/service.py` (`on_new_message`), `app/chatwoot/service.py` (`on_new_message`), `migrations/versions/`, `tests/` |

A coluna "Touches" é um aviso para o merge, não motivo para quebrar a wave.

O ticket 01 roda no branch já em uso, em cima do lugar. Os outros dois ganham um worktree cada:

```sh
# 01 — sem worktree: roda no branch já checked out
git worktree add ../helena-channel-parity-02 -b helena-channel-parity/02-anexos
git worktree add ../helena-channel-parity-03 -b helena-channel-parity/03-transfer-multicanal
```

## Dependency graph

```
01   (sem blocker)
02   (sem blocker)
03   (sem blocker)
```

Três raízes independentes — nenhuma aresta. Wave única.

## Expected merge conflicts

Todos na mesma wave; rodam em paralelo mesmo assim. Onde o orquestrador deve esperar resolver conflito ao mergear:

- **01, 02 e 03 — todos em `app/helena/service.py`.** Os três editam `HelenaService.process_webhook` (01 insere o gate após `upsert_api_customer`; 02 reescreve o early-return `no_text`; 03 adiciona `channel="helena"` no `on_new_message`) e 01+02 editam `HelenaService.__init__` (01 adiciona `chat_history_repo`, 02 adiciona `company_repo`). Conflito garantido, mas trivial — inserções em pontos distintos do mesmo método/`__init__`.
- **02 e 03 — em `app/chatwoot/service.py`.** 02 refatora `_handle_attachments`/`_transcribe_audio_attachment` para o helper compartilhado; 03 adiciona `channel="chatwoot"` no `on_new_message`. Blocos diferentes do arquivo.
- **01, 02 e 03 — em `tests/test_helena_route.py`.** Cada um adiciona seus próprios casos no `main()`.

Sugestão de ordem de merge (do menor blast radius ao maior): **01 → 02 → 03**. Assim o gate entra primeiro, os anexos reescrevem o `no_text` sobre ele, e o `channel=` do 03 (a mudança de maior alcance) entra por último sobre um `process_webhook` já estável.

## Critical path

Não há cadeia de blockers — a wall-clock é a do ticket mais longo isolado, o **03** (cadeia `channel` pelo núcleo + coluna + migração + client + branch + testes). 01 e 02 terminam mais cedo e esperam o merge da wave.
