# Não curtimos comentários: o endpoint só existe no Facebook Login

O pedido era curtir todo comentário, mesmo os que não recebem resposta nem DM. A Meta lançou a curtida por API em 22/04/2026 (`POST /<IG_USER_ID>/likes` com `comment_id`), mas só a documenta em `graph.facebook.com`, com `instagram_basic` + `instagram_manage_engagement`, que são permissões do Facebook Login. O nosso app usa Instagram Login ([ADR 0002 da boas-vindas](../../instagram-comment-welcome/adr/0002-instagram-login-no-app-da-meta.md)). Em 08/10/2026, um teste com o token da 7ball em `graph.instagram.com/v25.0/me/likes` voltou `#100/33 Unsupported post request`. Deixamos a curtida de fora para não pagar um novo App Review.

## Considered Options

- **Segundo app da Meta, com Facebook Login, só para curtir** — o app atual fica intacto, mas exige outro App Review, conectar a conta por Página e guardar mais um token por Company.
- **Migrar o app inteiro para Facebook Login** — reabre o ADR 0002 da boas-vindas: novo App Review, reconexão das contas e risco na chave de casamento com o Helena.
- **Esperar a Meta liberar no Instagram Login** — custo zero, mas sem prazo. Se isso acontecer, a curtida volta como uma feature pequena: uma chamada a mais no client do Instagram.
