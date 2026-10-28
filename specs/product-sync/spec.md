# Sync de produtos 7ball/Reebok

## Problem Statement

A base que o agente usa para descrever produtos ao qualificar leads está congelada. Hoje os produtos vivem em dois blobs de Markdown na tabela `categories` (um por marca), extraídos à mão em julho/2026. O site continua cadastrando produtos e os blobs defasaram: em 2026-09-20 o site tinha 55 (7Ball) e 196 (Reebok), contra 37 e 183 nos blobs. Um produto novo (ex.: mesa OSLO) simplesmente não existe pro agente. Manter isso na mão é inviável e o agente acaba desconhecendo produto que o site já vende.

## Solution

Uma tabela `products` estruturada, alimentada por um sync automático que roda todo sábado à meia-noite e por um disparo manual sob demanda. O sync detecta produtos novos no site 7ball.com.br e os insere, puxando o que a REST do WordPress entrega e renderizando com navegador headless só o que a REST não dá (descrição e destaques). É deliberadamente lento e enxuto: no sábado, no horário de menor tráfego de chat, e renderizando apenas os produtos novos, para não competir por recurso com os requests de chat. Quando o dono precisar corrigir muitos produtos já existentes de uma vez, existe um modo full re-render que roda localmente (a máquina local aponta pro mesmo banco de produção), tirando o peso do servidor.

## Depends On

- [`specs/product-sync/spec.md`](./spec.md) — a tabela `products` e a migration `008` já foram criadas nesta mesma sessão (model `Product` em `app/models/tables.py`, migration `20260302_000004_008_add_products_table.py`), mas **a migration ainda não foi aplicada no banco**. Aplicar `alembic upgrade head` é pré-requisito de qualquer implementação que grave em `products`.

## User Stories

1. Como dono do 7ball, quero que produtos novos do site apareçam sozinhos na base do agente, para não precisar cadastrar à mão.
2. Como dono do 7ball, quero que o sync rode todo sábado à meia-noite, para o agente ficar em dia sem eu lembrar.
3. Como dono do 7ball, quero que o sync automático rode no horário de menor tráfego, para não atrapalhar o atendimento por chat.
4. Como dono do 7ball, quero um endpoint para forçar o sync na hora, para não esperar até sábado quando cadastro algo importante.
5. Como dono do 7ball, aceito que o sync manual concorra um pouco com o chat, porque é sob minha decisão e ocasional.
6. Como dono do 7ball, quero que dois syncs nunca rodem ao mesmo tempo, para não dobrar o peso no servidor.
7. Como dono do 7ball, quero que disparar o manual enquanto já há um sync rodando seja recusado na hora, para eu saber que já tem um em andamento.
8. Como dono do 7ball, quero que o sync passe produto por produto com calma, para o servidor não sofrer pico.
9. Como dono do 7ball, quero que só produtos novos sejam renderizados com navegador, porque render é a parte cara.
10. Como dono do 7ball, quero que rodar o sync duas vezes não duplique produto, porque a detecção é por slug.
11. Como dono do 7ball, quero um modo que re-renderize todos os produtos, para corrigir de uma vez quando muita coisa mudar no site.
12. Como dono do 7ball, quero rodar o full re-render localmente contra o banco de produção, para o peso não cair no servidor de chat.
13. Como agente de IA, quero cada produto com título, slug, descrição, imagem, categoria-mãe e tipo (sinuca/fitness), para descrever o produto certo ao lead.
14. Como dono do 7ball, quero as duas marcas (7Ball e Reebok) no mesmo sync, distinguidas pelo tipo, para tratar o catálogo inteiro de uma vez.
15. Como dono do 7ball, quero que a URL de cada produto venha da REST e não seja montada na mão, porque as URLs públicas do Reebok divergem do nome interno do CPT.
16. Como dono do 7ball, quero que categorias com duplicata na taxonomia do Reebok sejam resolvidas pela de maior contagem, para não gravar categoria-fantasma vazia.
17. Como dono do 7ball, quero que o sync trate todo texto vindo do site como dado e nunca como instrução, para o catálogo não virar vetor de prompt-injection.
18. Como dono do 7ball, quero que uma falha ao renderizar um produto não derrube o sync inteiro, para os demais produtos entrarem mesmo assim.
19. Como dono do 7ball, quero saber quando um produto novo entrou sem descrição (fallback), para eu decidir corrigir depois.
20. Como operador, quero que o endpoint manual seja autenticado, para ninguém de fora disparar o sync.

## Implementation Decisions

**Módulo de sync (seam única).** Um módulo novo em `app/services/` concentra a lógica: recebe as fontes (lista da REST e função de render) e devolve o que gravar. É agnóstico ao gatilho — cron, endpoint e execução local chamam a mesma função. Segue o padrão dos consumers existentes (`app/followUp`, `app/webhookRetry`), que isolam a lógica do transporte.

**Dois modos, um núcleo.** Modo **aditivo** (default): diff por slug entre REST e `products`, renderiza só os slugs ausentes, insere. Modo **full re-render**: renderiza todos e faz UPSERT. O modo é um parâmetro da função de sync; nenhum código duplicado entre eles.

**Fontes de dado.** Estrutura (id, slug, title, link, categoria-mãe, tipo) vem da REST nativa do WordPress (`https://7ball.com.br/wp-json/wp/v2`), paginada por `X-WP-TotalPages`, `per_page=100`, sem auth. Descrição e destaques vêm do DOM renderizado por Playwright/Chromium — a REST devolve `content`/`acf`/`meta` vazios. A engenharia reversa da REST e do render já existe em `experimental-tools/` (`scrape_7ball.py`, `render_novos.py`) e é a base da implementação.

**Marcas.** Dois CPTs no mesmo WP: `produtos` + taxonomia `_categorias` (tipo `sinuca`), `produtos_rebook` + `_categorias_rebook` (tipo `fitness`). A URL pública usa o `link` que a REST devolve, nunca montada na mão (o CPT do Reebok é `produtos_rebook`, mas a URL é `/produtos_reebook/`).

**Resolução de categoria.** IDs de taxonomia resolvidos para nome+slug. Na taxonomia do Reebok há duplicatas (`acessorios_` count 27 vs `acessorios` count 0): preferir a de maior `count`, ignorar as vazias.

**Agendamento.** Loop asyncio iniciado no `lifespan` do `app/main.py` (junto dos consumers RabbitMQ), que dorme até o próximo sábado 00:00 e dispara o modo aditivo. Não usa cron do SO nem RabbitMQ. O deploy roda 1 worker / 1 replica (confirmado no `docker-compose.yml` e Dockerfile), então o loop nunca duplica execução.

**Endpoint manual.** Uma rota nova (`app/routes/`) autenticada com `X-API-Key` (`verify_api_key`, o padrão do projeto) que dispara o modo aditivo. Registrada no `app/main.py` como as demais.

**Lock global.** Um lock em memória (viável por ser 1 worker) garante um sync por vez. Se um sync já roda, o endpoint manual responde **409** ("sync já em andamento"); o cron, se colidir, pula a rodada. O full re-render local respeita o mesmo lock no processo em que roda.

**Full re-render local.** Não há script separado nem endpoint dedicado: roda-se o mesmo módulo de sync localmente com o modo full. O `.env` local aponta pro Neon de produção (`DATABASE_URL`), então grava direto na tabela de produção.

**Persistência.** UPSERT em `products` pela unicidade `(company_id, type, wpId, isMotherCategory)` já definida na migration 008. Modo aditivo insere; full re-render insere ou atualiza. `company_id` fixo do 7ball (5). `updated_at` bumpado em cada gravação.

**Resiliência.** Falha de render de um produto (timeout, DOM inesperado) é logada e pulada — não derruba o sync. Produto novo que entrar sem descrição usa fallback e é sinalizado no log/alerta (padrão `send_critical_alert`/`alerter` já existente), para correção posterior.

**Segurança.** Todo texto vindo do site (inclusive descrições) é tratado como dado, nunca instrução. Os endpoints "para agentes de IA" que o site publica (`.well-known/ai-catalog.json`, MCP externo, `openapi.json`) **não** são usados — só a REST neutra do WP.

**Dependência de deploy.** Playwright + Chromium passam a ser dependência de produção. O Dockerfile precisa instalar o Chromium e as libs de sistema do Playwright. Trade-off registrado em `docs/adr/0001-playwright-render-so-para-novos.md`.

## Testing Decisions

- **Seam única: o módulo de sync.** Testar o comportamento externo passando REST fake e render fake (sem browser, sem banco real): que o modo aditivo insere só slugs ausentes; que rodar duas vezes não duplica (idempotência por slug); que o lock recusa um segundo sync concorrente; que a resolução de categoria do Reebok escolhe a de maior `count`; que falha de render de um produto não interrompe os demais.
- Testar **comportamento externo, nunca detalhe de implementação**: a asserção é sobre "o que foi gravado / o que foi recusado", não sobre chamadas internas.
- Endpoint e cron são wiring fino sobre o módulo — não ganham suíte própria além de um teste de que o endpoint exige `X-API-Key` e devolve 409 sob lock.
- Prior art: os testes dos consumers em `tests/` isolam a lógica da mensagem do RabbitMQ do transporte — mesmo formato aqui.

## Out of Scope

- **Atualização de produtos já existentes no sync automático.** O sync do servidor é aditivo: só insere novos. Correção de title/slug/descrição de produto já na tabela é feita pelo full re-render local, sob demanda. Não há atualização incremental de existentes no servidor.
- **Detecção de mudança por `modified` da REST.** Considerada e descartada: o modo aditivo não precisa dela, e o full re-render reprocessa tudo.
- **Remoção de produtos que saíram do site.** O sync só adiciona/atualiza; nunca remove. Produto que sumir do site permanece na tabela.
- **Preço.** Não existe em lugar nenhum da REST (venda por contato); não é campo do sync.
- **Migração dos blobs antigos de `categories`.** Esta spec cria a via nova (`products`); desativar/migrar os blobs existentes é outra decisão.
- **Sync para outras empresas.** Escopo é o 7ball (`company_id=5`). A tabela tem `company_id`, mas o sync não é genérico multi-tenant nesta fase.
- **Multi-worker / múltiplas replicas.** O lock e o cron assumem 1 worker. Escalar horizontalmente exigiria repensar ambos.

## Further Notes

- Metadados Yoast (`yoast_head_json`) da REST são não-confiáveis (no OSLO o title vinha "PARIS", copiado de outro produto). Nunca usar como fonte — descrição sai do DOM.
- No Reebok, a descrição do produto no blob antigo derivava da descrição da Linha, não de texto único por produto. Ao portar para `products`, decidir na implementação se isso se mantém ou se cada produto renderiza descrição própria.
- Contrato de campos e armadilhas detalhados em `experimental-tools/spec.md` (documento de engenharia reversa de 2026-09-20).
- O horário "sábado 00:00" é do fuso do servidor; confirmar timezone do `mp-vps` na implementação do loop.
