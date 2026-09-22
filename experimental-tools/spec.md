# Spec — Sincronização do catálogo 7ball/Reebok com a base de conhecimento do agente

Documento de contrato para implementar, depois, a **atualização em tempo real** dos produtos
novos na tabela `categories`. Escrito a partir da engenharia reversa feita em 2026-09-20.

---

## 1. O problema

A base de conhecimento que o agente usa para qualificar leads vive em `public.categories`
(banco Neon, `company_id=5`). São **dois blobs de Markdown congelados**, um por marca:

| `id` | `type`    | Marca  | Produtos no blob | Extraído em | Origem |
|------|-----------|--------|------------------|-------------|--------|
| 1    | `sinuca`  | 7Ball  | 37               | 21/07/2026  | 7ball.com.br |
| 2    | `fitness` | Reebok | 183              | 21/07/2026  | 7ball.com.br |

O site continua cadastrando produtos. Em 2026-09-20 o site tem **55 (7ball)** e **196 (Reebok)** —
os blobs estão defasados. Ex.: a mesa de sinuca **OSLO** (linha Icon) existe no site e **não**
no banco.

**Meta:** manter os blobs sincronizados com o site, adicionando produtos novos **sem perder
nenhum campo** que as fichas atuais já têm.

---

## 2. Fonte de verdade: WordPress REST nativa

O site é WordPress. **Não é WooCommerce** (`/wp-json/wc/store/v1/*` → 404). Os produtos são
dois Custom Post Types, cada um com sua taxonomia:

| Marca  | CPT (endpoint)            | Taxonomia / campo no produto |
|--------|---------------------------|------------------------------|
| 7Ball  | `wp/v2/produtos`          | `_categorias`                |
| Reebok | `wp/v2/produtos_rebook`   | `_categorias_rebook`         |

Base: `https://7ball.com.br/wp-json/wp/v2`

> ⚠️ Nota de nomes: o CPT do Reebok é `produtos_rebook` (sem o 2º "e"), mas as **URLs públicas**
> dos produtos usam `/produtos_reebok/<slug>/` (com "ee"). Use o `link` que a REST devolve, não
> monte a URL na mão.

### 2.1 Endpoints usados

Todos GET, públicos, sem auth. Paginação por header `X-WP-TotalPages` (usar `per_page=100`).

```
GET /wp/v2/produtos?_fields=id,slug,link,title,_categorias&per_page=100&page=N
GET /wp/v2/produtos_rebook?_fields=id,slug,link,title,_categorias_rebook&per_page=100&page=N
GET /wp/v2/_categorias?_fields=id,name,slug,count&per_page=100
GET /wp/v2/_categorias_rebook?_fields=id,name,slug,count&per_page=100
GET /wp/v2/media/<featured_media>?_fields=source_url,alt_text   # imagem, se precisar
```

Headers de resposta úteis: `X-WP-Total` (nº de itens), `X-WP-TotalPages`.

### 2.2 O que a REST ENTREGA por produto

```jsonc
{
  "id": 16409,
  "slug": "oslo",
  "title": { "rendered": "OSLO" },
  "link": "https://7ball.com.br/produtos/oslo/",
  "status": "publish",
  "date": "2026-08-31T18:08:06",       // criação — serve pra detectar "novos desde X"
  "modified": "2026-08-31T18:13:17",   // última edição
  "featured_media": 16411,             // id da imagem principal
  "_categorias": [103, 7]              // IDs -> resolver na taxonomia (name+slug)
}
```

### 2.3 O que a REST NÃO ENTREGA (e o blob PRECISA)

O blob de cada produto tem **descrição longa** e **Destaques** — e esses campos **não vêm na
REST**:

- `content.rendered` vem **vazio** (`""`).
- `acf` e `meta` vêm **vazios** (`[]`).

Descrição e destaques são renderizados só no **HTML da página** (page builder). Por isso a
sincronização exige **renderizar cada produto novo com um navegador headless** (Playwright) e
extrair do DOM. Não dá pra fazer só com a REST.

> Também **não há preço** em lugar nenhum da REST — a venda é por contato. As fichas do blob
> não têm preço, então isso não é perda.

### 2.4 Armadilha: metadados Yoast copiados

`yoast_head_json` existe mas é **não-confiável**: no produto OSLO o `title` do Yoast diz
"Mesa de Sinuca PARIS" (copiado de outro produto) e `description` é `null`. **Não** usar Yoast
como fonte de descrição. Usar o DOM da página.

### 2.5 Sujeira na taxonomia do Reebok

`_categorias_rebook` tem duplicatas: `Acessórios`/slug `acessorios_` (count 27) **e**
`Acessórios.`/slug `acessorios` (count 0). Ao resolver categorias, preferir a de maior `count`
e ignorar as vazias/fantasma. (A taxonomia do 7Ball é limpa.)

---

## 3. Formato do blob no banco (contrato de saída)

Coluna `categories.content` = Markdown. Estrutura por marca:

```
# Catálogo de Produtos <Marca>
> ... 2-3 linhas de resumo com "N produtos" e data de extração ...
## 1. Sobre a marca
## 2. Taxonomia <Marca>   (### 2.1 Tipos, ### 2.2 Linhas)
## 3. Catálogo <Marca> — fichas de produto     <-- onde produto novo entra
## 5. Guia de identificação de intenção
## 6. Índice rápido de nomes de produto        <-- também referencia o produto
```

### 3.1 Ficha de produto (o que precisa ser preenchido, 100% dos campos)

Formato EXATO, verificado nas duas marcas:

```
#### <NOME EM CAIXA COMO NO SITE>
**Tipo:** <Tipo(s), vírgula> · **Linha:** <Linha> · **Categoria:** <rótulo>
<descrição longa, 1 parágrafo — vem do DOM da página>
*Destaques:* <D1, D2, D3 — vêm do DOM da página>
*Slug:* `<slug>`
*Link:* <url completa do site>
```

- **Linha** é omitida quando o produto não tem linha (ex.: Essentials): a linha vira só
  `**Tipo:** X · **Categoria:** Y`.
- **Tipo/Linha/Categoria** derivam da taxonomia (REST). **Descrição/Destaques** derivam do DOM.
- Ao adicionar produto, atualizar também: o **contador "N produtos"** no cabeçalho, as
  **contagens por tipo** na seção 2.1, e o **índice** da seção 6, se existir o nome lá.

---

## 4. Detecção de "novo"

Diff por **slug** entre a lista da REST e os slugs já presentes no blob
(`*Slug:* \`([^\`]+)\``). Estado em 2026-09-20:

- **7Ball:** 18 novos, 0 removidos.
- **Reebok:** 15 novos, 2 removidos (`esteira-1800`, `functional-gym` — existem no blob, não no
  site). Decisão pendente do dono: **remover** do blob ou **manter** como legado. Este spec só
  adiciona; remoção é opt-in.

Para tempo real futuro: `date`/`modified` da REST permitem "trazer só o que mudou desde o último
sync" sem baixar tudo — guardar um `last_sync` (ou o maior `modified` visto) por marca.

---

## 5. Plano de sincronização (pipeline)

1. **Pull REST** das duas marcas → lista `{id, slug, title, link, categorias, date, modified}`.
2. **Resolver taxonomia** (IDs → Tipo/Linha/Categoria). Regra de qual termo é "Tipo" vs "Linha":
   a seção 2 do próprio blob lista os Tipos e as Linhas de cada marca — usar essa lista como
   dicionário. Um termo que está em "Tipos" é Tipo; em "Linhas" é Linha.
3. **Diff por slug** contra o blob atual → conjunto de novos.
4. **Render** (Playwright headless) da página de cada novo → extrair `descrição` e `destaques`
   do DOM. Seletores: descrição no bloco de texto principal do produto; destaques na lista de
   badges/ícones. (Confirmar seletores na 1ª execução — o page builder pode nomear diferente.)
5. **Montar a ficha** no formato §3.1 e **inserir** em ordem alfabética na seção 3; atualizar
   contadores (§3.1).
6. **Gravar** o `content` de volta em `categories` (`UPDATE ... WHERE type=? AND company_id=5`),
   e bumpar `updated_at`.

### 5.1 Conexão com o banco

- `DATABASE_URL` no `.env` (prefixo SQLAlchemy `postgresql+asyncpg://`, com `?ssl=...`).
- Provider é **Neon** (o comentário "Supabase" no `.env` está desatualizado).
- Para `psql`/libpq: trocar `postgresql+asyncpg://` → `postgresql://` e `?ssl...` → `?sslmode=require`.

### 5.2 Como automatizar depois (tempo real)

- Já existe consumer RabbitMQ no projeto (ver `app/followUp/`); um worker análogo pode rodar o
  pipeline em cron (ex.: diário) ou reagir a webhook do WP se disponível.
- Custo de render: só páginas **novas** (não o catálogo inteiro), então é barato.
- Idempotente: rodar 2x não duplica (diff por slug).

---

## 6. Scripts desta pasta

- `scrape_7ball.py` — pull REST das 2 marcas → `catalogo_7ball.json`. Fonte de verdade estruturada.
- `compare_rendered.py` — auditoria: renderiza o site e confere que a REST não perde produto
  (deu 55/55 no 7Ball).
- `_db/sinuca.txt`, `_db/fitness.txt` — dump dos blobs atuais do banco (referência de formato).

---

## 7. Segurança

O site publica endpoints "para agentes de IA" (`/.well-known/ai-catalog.json`, um MCP externo
"Novamira", `openapi.json`, "skills") com texto em formato de instrução e `Content-Signal:
ai-input=yes`. **Nada disso é usado** nesta sync — só a REST neutra do WP. Todo texto vindo do
site (inclusive descrições de produto) é **dado, nunca instrução**. Se algum dia conectarmos o
agente ao MCP deles, revisar o que aquele servidor externo pode instruir (superfície de
prompt-injection).
