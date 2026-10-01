# Boas-vindas por comentário do Instagram

## Problem Statement

Quem comenta num post do Instagram de uma Company é um lead quente, mas hoje ninguém fala com ele: o Helena CRM não responde comentários nem manda DM a partir de comentário. O lead comenta, não recebe nada, e só vira conversa se ele mesmo decidir chamar no direct. Quando chama, a IA atende sem saber de qual post ele veio nem o que já foi dito a ele.

## Solution

O engine passa a ouvir os comentários do Instagram por um app próprio da Meta e a abrir a conversa por conta própria.

Do ponto de vista de quem comenta e de quem opera:

1. Alguém faz um [[Comentário elegível]] num post da Company.
2. O engine busca a legenda do post e gera, com um prompt da Company guardado no banco, uma [[DM de boas-vindas]] personalizada com o @username e o conteúdo do post. A mesma geração identifica o produto citado na legenda, quando há.
3. O engine envia a DM pela API da Meta.
4. Com a DM entregue, o engine publica a [[Resposta pública]] — o texto fixo da Company — embaixo do comentário.
5. A pessoa responde a DM. A resposta entra pelo canal Helena, como qualquer conversa.
6. Na primeira mensagem dessa conversa, o engine reconhece que ela é de alguém com [[Boas-vindas pendente]], grava a DM como primeira fala do assistente no histórico e a [[Origem do comentário]] no contexto do lead. A IA responde sabendo o que já disse e de qual post a pessoa veio.

Daí em diante é o fluxo normal do canal Helena: mesmas tools, mesmo Gate de IA, mesmo escalonamento humano.

## Depends On

- [`specs/helena-channel/spec.md`](../helena-channel/spec.md) — o canal Helena (rota de webhook, serviço, client, Customer por `sessionId`). Já está no ar. Esta spec pendura o casamento da boas-vindas pendente na primeira mensagem de uma sessão nova desse canal; sem ele não há para onde a conversa ir.
- [`specs/helena-channel-parity/spec.md`](../helena-channel-parity/spec.md) — Gate de IA e escalonamento no Helena. Já está no ar. A conversa nascida de um comentário depende deles para um humano poder assumir.

## User Stories

### Receber o comentário

1. Como dono da Company, quero que todo comentário de primeiro nível feito por outra pessoa nos meus posts dispare o fluxo, para não precisar configurar posts nem palavras-chave.
2. Como dono da Company, quero que comentários feitos pela minha própria conta sejam ignorados, para a Resposta pública não disparar o fluxo de novo.
3. Como dono da Company, quero que respostas a outros comentários sejam ignoradas, para o fluxo não entrar em discussões entre seguidores.
4. Como lead, quero receber uma única DM por post em que comento, para não ser incomodado se comentar várias vezes.
5. Como lead que comenta num post novo semanas depois, quero ser abordado de novo, para a regra de uma vez não me excluir para sempre.
6. Como mantenedor, quero que a reentrega do mesmo evento pela Meta não gere segunda DM nem segunda resposta, para o fluxo ser idempotente.
7. Como mantenedor, quero que um lote com vários comentários na mesma chamada seja processado item a item, para nenhum comentário se perder.
8. Como mantenedor, quero que o webhook responda à Meta imediatamente e processe em segundo plano, para a Meta não reenviar nem desassinar o webhook por lentidão.
9. Como mantenedor, quero que eventos que não são de comentário sejam ignorados sem erro, para o endpoint não quebrar se outro campo for assinado.

### Autenticar

10. Como mantenedor, quero que o endpoint responda ao handshake de verificação da Meta, para o webhook poder ser cadastrado no painel do app.
11. Como mantenedor, quero que toda chamada sem assinatura válida da Meta seja rejeitada antes de qualquer processamento, para ninguém conseguir forjar um comentário e fazer a Company mandar DM.
12. Como mantenedor, quero que a assinatura seja obrigatória mesmo se o segredo não estiver configurado, para um erro de configuração fechar a porta em vez de abri-la.

### Multi-tenancy

13. Como dono de uma Company, quero que os comentários da minha conta usem meu token, meu texto e meu prompt, para ficar isolado das outras Companies.
14. Como mantenedor, quero que a Company seja resolvida pelo id da conta do Instagram que vem no evento, para um único app da Meta servir todas as Companies.
15. Como mantenedor, quero que um evento de uma conta que nenhuma Company tem cadastrada seja descartado só com log, para uma conta desconhecida não gerar erro nem alerta.
16. Como dono de uma Company sem Instagram configurado, quero que nada mude para mim, para a feature ser opt-in por configuração.

### DM de boas-vindas

17. Como dono da Company, quero escrever no banco o prompt que gera a DM, para controlar tom e conteúdo sem deploy.
18. Como dono da Company, quero que o prompt receba o @username de quem comentou e a legenda do post, para a mensagem ser pessoal e sobre o post certo.
19. Como dono da Company, quero escolher modelo e reasoning effort da geração pelo banco, como nos outros prompts, para ajustar custo e qualidade.
20. Como lead, quero que a DM fale do post em que comentei, para entender por que a marca me chamou.
21. Como lead que comentou num post sem legenda, quero ainda assim receber a DM, para a falta de legenda não me excluir.
22. Como mantenedor, quero que a DM saia pela chamada da Meta que responde em privado a um comentário, para funcionar com quem nunca falou com a conta.
23. Como mantenedor, quero guardar o id do destinatário que a Meta devolve no envio, para ter a chave de casamento mais direta possível.

### Resposta pública

24. Como dono da Company, quero definir um texto fixo de resposta pública, para quem lê os comentários ver que a marca respondeu.
25. Como lead, quero que a resposta pública só apareça se a DM realmente chegou, para a marca não prometer em público um direct que não mandou.
26. Como mantenedor, quero que uma falha ao publicar a resposta pública não desfaça nada, para o lead que já recebeu a DM continuar no fluxo.

### Falhas

27. Como mantenedor, quero que falha na geração ou no envio da DM dispare o alerta crítico existente, para eu saber quando um lead ficou sem abordagem.
28. Como mantenedor, quero que, se a DM falhar, nada seja publicado e nada fique pendente, para não existir boas-vindas pendente de uma DM que nunca saiu.
29. Como lead cuja DM falhou, quero ser abordado se comentar de novo no mesmo post, para uma falha técnica não gastar a minha vez.
30. Como mantenedor, quero que falha ao buscar a legenda seja tratada como falha do fluxo, para a DM nunca sair sem o contexto do post por erro silencioso.

### Entrar no contexto

31. Como IA, quero ver a DM de boas-vindas como minha primeira fala no histórico, para responder em continuidade ao que já foi dito.
32. Como IA, quero ter a legenda do post e o texto do comentário no contexto do lead, para saber de onde ele veio.
33. Como IA, quero ter o produto do post no contexto quando a legenda cita um, para falar do produto certo sem perguntar.
34. Como IA, quero que o produto simplesmente não apareça quando a legenda não cita nenhum, para não trabalhar com um produto inventado.
35. Como lead, quero que a IA não se apresente de novo nem repita a boas-vindas, para a conversa parecer uma só.
36. Como mantenedor, quero que o casamento aconteça só na primeira mensagem de uma sessão nova, para conversas em andamento não pagarem custo nenhum.
37. Como mantenedor, quero que o casamento só seja tentado para Companies com Instagram configurado e com boas-vindas pendentes, para o canal Helena das outras Companies não mudar.
38. Como mantenedor, quero que uma boas-vindas pendente seja consumida uma única vez, para a mesma DM não ser semeada em duas conversas.
39. Como lead com mais de uma boas-vindas pendente, quero que a conversa use a mais recente, para o contexto refletir o último post em que comentei.
40. Como lead de WhatsApp da mesma Company, quero que minha conversa nunca seja casada com uma boas-vindas do Instagram, para os canais não se misturarem.
41. Como mantenedor, quero que uma falha no casamento não impeça a IA de responder, para o pior caso ser uma conversa sem o contexto do comentário, nunca um lead sem resposta.
42. Como mantenedor, quero que, se o Helena entregar a nossa própria DM como mensagem recebida, a IA não responda a ela, para a IA não conversar consigo mesma.

### Token

43. Como dono da Company, quero que o token do Instagram se renove sozinho, para a feature não parar a cada 60 dias.
44. Como mantenedor, quero um alerta crítico quando a renovação falhar, para agir antes de o token expirar.

### Validação inicial

45. Como mantenedor, quero rodar um teste real antes de implementar o casamento, para escolher a chave com fato e não com suposição.

## Implementation Decisions

### Forma geral

A feature não é um Canal novo. O engine fala com a Meta só para três chamadas de saída (buscar legenda, enviar DM, publicar resposta) e uma de manutenção (renovar token); a conversa continua sendo do canal Helena. Ver ADR 0001.

Peças novas:

- **Rota de webhook do Instagram**, separada da rota Meta existente (que é o proxy de WhatsApp para o Chatwoot e não é tocada). Aceita o handshake de verificação e os eventos.
- **Client da API do Instagram** — as quatro chamadas à Meta.
- **Serviço de boas-vindas** — decide elegibilidade, reserva a vez, gera a DM, envia, publica e registra.
- **Registro de boas-vindas** — uma tabela nova.
- **Passo de casamento no serviço do canal Helena** — o único ponto do código existente que muda de comportamento.
- **Loop de renovação de token** no ciclo de vida do app.

### App da Meta e autenticação

- Um único app da Meta para todas as Companies, com Instagram Login. Ver ADR 0002. Permissões: básica, gerenciar comentários e gerenciar mensagens (as três `instagram_business_*`). O webhook de comentários exige app em modo Live com Advanced Access — o App Review é pré-requisito de qualquer teste real.
- Segredo do app e token de verificação são configuração global do engine, próprios desta feature (não reusam os do proxy de WhatsApp, que pertencem a outro app).
- Handshake: confere o token de verificação e devolve o desafio.
- Eventos: a assinatura HMAC-SHA256 do corpo bruto, no cabeçalho de assinatura da Meta, é **obrigatória**. Assinatura ausente, inválida ou segredo não configurado → rejeita. A rota Meta existente trata a assinatura como opcional; aqui não, porque um evento forjado faz a Company enviar DM a um terceiro.
- Qual dos segredos do painel (o do app ou o do produto Instagram) assina os eventos não está confirmado na documentação; confirmar no ticket de validação.

### Resolução da Company

Pelo id da conta do Instagram que vem em cada entrada do evento, comparado a uma coluna nova e única na Company. Conta desconhecida → descarta com log. A resolução e todo o trabalho de banco acontecem em segundo plano, com sessão de banco própria, como nos outros webhooks.

### Elegibilidade

Um comentário é elegível quando: é de primeiro nível (sem comentário pai), o autor não é a própria conta da Company, e o autor ainda não tem boas-vindas enviada naquele post. O parser é tolerante: aceita os dois nomes que a Meta usa para o id do comentário e trata o comentário pai como opcional, porque o formato difere entre os dois logins e o exemplo do Instagram Login não mostra todos os campos.

### Reserva e idempotência

Antes de qualquer chamada externa, o serviço reserva a vez gravando o registro de boas-vindas. A unicidade por Company + post + autor do comentário cobre de uma vez a regra de uma boas-vindas por pessoa por post e a reentrega do mesmo evento pela Meta. Quem perde a corrida da reserva encerra em silêncio. Registros que falharam não contam para a unicidade, para um novo comentário da mesma pessoa poder tentar de novo.

### Registro de boas-vindas (tabela nova)

Uma linha por Comentário elegível processado. Guarda: Company, id do comentário, id do post, id e @username do autor, texto do comentário, legenda, produto (nulo quando a legenda não cita), texto da DM, id do destinatário devolvido pela Meta, estado, o `sessionId` casado e as datas de criação e de casamento.

Estados: em processamento → pendente (DM enviada, conversa ainda não apareceu) → casada; ou falhou. O estado é texto simples, não enum do Postgres.

Uma boas-vindas pendente não expira: a pessoa pode responder a DM dias depois e o contexto continua correto.

### Configuração por Company (colunas novas)

- id da conta do Instagram (único, indexado) — chave de resolução;
- token de acesso do Instagram;
- texto da Resposta pública.

A feature está ligada para a Company quando os três estão preenchidos e existe o prompt de boas-vindas.

### Geração da DM

- O prompt vive na tabela de prompts que já existe, numa linha própria por Company, com modelo e reasoning effort configuráveis como nos outros prompts.
- Recebe dois marcadores, substituídos no mesmo estilo dos prompts atuais: o @username e a legenda do post. Só o @username está disponível — a Meta só libera o nome de exibição depois que a pessoa manda mensagem.
- Uma única chamada ao LLM, sem tools e sem streaming, pelo serviço de OpenAI existente e com a chave da Company. A resposta é estruturada: o texto da DM e o produto (texto livre extraído da legenda, ou nulo).
- O produto não é validado contra o catálogo. A legenda é a fonte; a feature funciona igual para Companies sem tabela de produtos.
- Legenda e @username são dados de terceiros e entram no prompt como dado, nunca como instrução.
- Post sem legenda: a geração segue com legenda vazia.

### Ordem das chamadas

```
reserva → busca legenda → gera DM → envia DM → grava pendente → publica Resposta pública
```

- Falha em qualquer passo até o envio da DM: marca o registro como falho, dispara alerta crítico, nada é publicado.
- Falha ao publicar a Resposta pública: só alerta crítico. A boas-vindas continua pendente e válida.
- Não há retry automático. A Meta só reenvia quando não recebe a confirmação, e a confirmação é imediata.

### Chamadas à Meta (Instagram Login)

- Legenda: leitura do post pelo id, pedindo legenda e link.
- DM: envio de mensagem com o destinatário identificado pelo id do comentário. Só texto. A Meta aceita uma DM por comentário, até 7 dias depois dele; devolve o id do destinatário e o id da mensagem.
- Resposta pública: criação de resposta ao comentário pelo id.
- Renovação: troca do token de longa duração por outro de mais 60 dias.

### Casamento no canal Helena

Acontece no serviço do canal Helena, na primeira mensagem de uma sessão que acabou de virar Customer, e só para Company com Instagram configurado e com alguma boas-vindas pendente. Fora disso o caminho atual não muda e nenhuma chamada extra é feita.

A chave do casamento é decidida pelo ticket de validação, por esta regra:

1. Se o identificador do remetente que o Helena manda no evento, em sessão de Instagram, for igual ao id do destinatário que a Meta devolveu no envio da DM → casa por ele, sem chamada extra.
2. Senão → consulta a sessão no Helena pedindo os detalhes do contato; se o canal for Instagram e o username do contato for igual ao da boas-vindas pendente (sem `@`, sem diferenciar maiúsculas) → casa por username.
3. Se nenhum dos dois servir → o casamento volta para o desenho (ADR 0001) e o ADR 0002 é reavaliado.

Implementa-se só o caminho que o teste confirmar, não os dois.

Ao casar, na mesma transação e antes de a mensagem do lead seguir para a IA:

- a DM é gravada no histórico da sessão como mensagem do assistente;
- a Origem do comentário (legenda, texto do comentário e produto, quando houver) é gravada no contexto do lead que já entra no system prompt;
- o registro passa a casado, com o `sessionId`.

Com mais de uma pendente para a mesma pessoa, vale a mais recente; as demais ficam como estão.

Proteção contra eco: se a mensagem que chega do Helena tiver o mesmo texto da DM pendente da pessoa casada, ela é a nossa própria DM voltando. O casamento é feito, mas a mensagem não vai para a IA nem é duplicada no histórico.

Qualquer erro no casamento é registrado em log e a mensagem segue o fluxo normal sem o contexto do comentário.

### Renovação do token

Um loop assíncrono no ciclo de vida do app, no mesmo molde do Sync semanal, renova uma vez por semana o token de cada Company com Instagram configurado e grava o novo. Falha → alerta crítico com a Company. Como os outros loops e os locks em memória do engine, pressupõe uma única réplica.

### Contexto de tool

Nenhuma mudança. A conversa nasce no canal Helena, então o canal informado às tools continua sendo Helena.

## Testing Decisions

Um bom teste aqui observa o que entra por uma rota HTTP e o que sai pelos clients de saída, que são simulados. Não testa o formato do registro, a ordem interna das funções nem o conteúdo do prompt.

**Seam 1 — a rota nova do webhook do Instagram.** Simulados: o client da API do Instagram e a chamada à OpenAI. Casos:

- handshake com token certo devolve o desafio; com token errado é rejeitado;
- evento sem assinatura, com assinatura errada, ou com segredo não configurado → rejeitado, nenhuma chamada de saída;
- comentário elegível → DM enviada e depois Resposta pública, nessa ordem, com o texto gerado e o texto fixo da Company;
- comentário da própria conta → nenhuma chamada;
- resposta a outro comentário → nenhuma chamada;
- mesma pessoa, mesmo post, segundo comentário → nenhuma chamada;
- mesmo evento entregue duas vezes → uma DM só;
- mesma pessoa em outro post → nova DM;
- falha no envio da DM → Resposta pública não publicada, alerta disparado; novo comentário da pessoa no mesmo post → tenta de novo;
- falha na Resposta pública → alerta, boas-vindas segue pendente;
- conta de Instagram que nenhuma Company tem → nenhuma chamada, sem exceção;
- lote com dois comentários elegíveis → duas DMs.

**Seam 2 — a rota existente do webhook do Helena.** Com uma boas-vindas pendente gravada para a Company de teste, simulando o client do Helena e a OpenAI:

- primeira mensagem de sessão nova que casa → o que chega à OpenAI contém a DM como fala do assistente antes da mensagem do lead, e a Origem do comentário no contexto;
- boas-vindas com produto nulo → o contexto não menciona produto;
- segunda mensagem da mesma sessão → a DM não é gravada de novo;
- sessão nova que não casa → comportamento atual, sem contexto extra;
- Company sem Instagram configurado → nenhuma consulta extra, comportamento atual;
- mensagem com o mesmo texto da DM pendente → IA não é chamada;
- erro no casamento → a IA responde normalmente.

**Renovação do token** — testada pela função que o loop chama, com o client simulado: token novo gravado no sucesso, alerta na falha.

**Prior art.** O teste da rota do Helena é o modelo direto: script assíncrono com asserts simples, chamando o app em processo, com o modo de desenvolvimento ligado para rodar síncrono e sem Redis, banco real com uma Company de teste preparada pelo próprio teste, e só os clients de saída e a OpenAI simulados. O teste da rota de sync de produtos é o modelo para trabalho em segundo plano. Os testes do projeto são scripts, não pytest.

## Out of Scope

- **Atender DMs do Instagram pelo nosso app.** O engine envia a boas-vindas e para. Toda a conversa é do canal Helena. Ver ADR 0001.
- **Filtro por palavra-chave ou por post.** Todo Comentário elegível dispara.
- **Validar o produto contra o catálogo.** O produto é texto livre vindo da legenda.
- **Nome de exibição na DM.** Só o @username; a Meta não libera o nome antes de a pessoa mandar mensagem.
- **Quem já tem conversa aberta com a IA.** Recebe a DM como qualquer um. O casamento só roda em sessão nova, então nessa situação a DM não entra no histórico da conversa em andamento.
- **Follow-up para quem não responde a DM.** A Meta permite uma única mensagem até a pessoa responder.
- **Comentários em Lives, Stories e menções.** Só comentários em posts e reels.
- **Tratamento especial para comentários em anúncios.** Chegam como comentário comum; os dados de anúncio não existem no Instagram Login.
- **Variações da Resposta pública.** Um texto fixo por Company.
- **Retry automático** de DM ou de Resposta pública que falhou.
- **Tela ou endpoint de configuração.** Token, id da conta, texto e prompt são gravados direto no banco. O fluxo de conexão da conta (obter o primeiro token) é manual.
- **Mídia na DM.** Só texto.
- **Canal Chatwoot.** O casamento existe só no canal Helena.
- **Corrigir o seed por gatilho do Chatwoot**, que é o precedente mais parecido e hoje está quebrado (nome de configuração errado e histórico gravado numa chave que o Customer não usa mais). Vale um ticket à parte.

## Further Notes

**Ticket de validação — primeiro, antes do casamento.** Não precisa de código do engine: o webhook da Meta pode apontar para o n8n e a DM sair por uma chamada manual. Capturar e anotar:

1. O evento real de comentário no Instagram Login: nome do campo do id, presença do comentário pai, formato da entrada.
2. O que a Meta devolve ao enviar a DM (id do destinatário) e se ele é igual ao id do autor que veio no evento de comentário.
3. O evento que o Helena manda quando a pessoa responde a DM: o identificador do remetente (num evento já capturado de mensagem recebida veio um valor em formato de telefone que não é telefone — `+0324193345627` — e que pode ser um id do Instagram) e se ele é o mesmo em duas sessões da mesma pessoa.
4. O que a consulta de sessão do Helena devolve para essa sessão: tipo de canal e username do contato. Confirmar que essa consulta responde no host que o engine usa hoje.
5. Se o Helena enxerga a DM enviada por fora e se isso dispara evento de mensagem recebida.
6. Se a nossa Resposta pública gera um novo evento de comentário, e com quais campos.
7. Se o evento de comentário chega sem Advanced Access para uma conta com papel no app.
8. Qual segredo assina os eventos.

Os itens 2 a 4 escolhem a chave do casamento; 5 e 6 confirmam as duas proteções já desenhadas; 1, 7 e 8 ajustam parser e configuração.

**Riscos conhecidos.**

- A DM cai em "Solicitações" quando a pessoa não segue a conta. É comportamento da Meta.
- A Meta limita a 750 DMs deste tipo por hora por conta. Um post viral pode bater no limite; as falhas viram alerta e a pessoa pode ser abordada num novo comentário.
- O texto da DM tem limite de tamanho na Meta. O prompt da Company deve pedir mensagem curta; texto longo demais falha no envio e vira alerta.
- Com Conversation Routing configurado na conta do cliente, o comportamento de dois apps ligados à mesma conta pode mudar. Verificar na conta do cliente durante o ticket de validação.
- O `.env` local aponta para o banco de produção: rodar a migration localmente aplica em produção.

**Documentação.** Ao implementar, atualizar a referência de arquivos (rotas, serviços, repositórios, modelos) e a documentação do schema. A referência atual já está defasada em vários pontos do canal Helena; a entrada **Helena** do glossário também descreve um endereço de envio diferente do que o código usa.
