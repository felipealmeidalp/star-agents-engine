# Categoria do comentário: resposta e DM por tipo de comentário

## Problem Statement

A boas-vindas por comentário do Instagram trata todo comentário do mesmo jeito: quem comenta recebe a DM de vendas e a Resposta pública fixa ("Enviei um direct com mais informações"). Isso funciona para quem pergunta preço, mas erra no resto:

- quem só reage com emoji ("👏👏👏") recebe uma DM de vendas que não pediu;
- quem marca um amigo ("@fulano olha essa") também;
- quem elogia ("Esta mesa é simplesmente maravilhosa") recebe uma abordagem de vendas pesada, quando um "que bom que gostou" seria o tom certo;
- quem reclama ("14 dias para obter uma resposta!! Um absurdo!", "Pena que ninguém responde ao orçamento") recebe, em público, um "te enviei direct" e, no direct, uma DM de vendas. Quando responde, cai no agente de vendas. Os dois exemplos são reais: estão no registro de boas-vindas da 7ball.

## Solution

Antes de responder, o engine decide de que tipo é o comentário, a [[Categoria do comentário]], e age conforme a categoria:

| Categoria | Resposta pública | DM de boas-vindas |
|---|---|---|
| só emoji | — | — |
| comercial (preço, valor, tamanho, nome de produto) | texto fixo da Company, como hoje | a DM de vendas de hoje |
| elogio | gerada pela IA ("Incrível, né? …") | gerada pela IA, leve ("Gostaria de conhecer mais dos nossos produtos?") |
| reclamação | — | gerada pela IA, tentando entender o problema |
| outro (marcar amigo, spam, assunto alheio) | — | — |

Na reclamação, quando a pessoa responde a DM, a IA não fala nada: a conversa vai direto para um atendente humano, sem depender de a IA tomar essa decisão.

Quem já recebeu uma DM de boas-vindas num post não recebe nada pelos comentários seguintes naquele post. Um comentário que não recebeu DM (emoji, outro) não gasta a vez: se a pessoa comentar "🔥🔥" e depois "qual o valor?", o segundo comentário é atendido.

A classificação é feita pela Decisions API da OpenAI. A lista de categorias é fixa no código. A descrição de cada categoria, que é o texto usado para escolher, e os prompts que escrevem as respostas vivem no banco, por Company: dá para afinar a classificação e o tom sem deploy.

## Depends On

- [`specs/instagram-comment-welcome/spec.md`](../instagram-comment-welcome/spec.md) — a boas-vindas por comentário inteira: o webhook, a reserva da vez, a DM, a Resposta pública fixa, o registro de boas-vindas, o casamento no canal Helena e a conversa anterior de quem já é Customer. Já está no ar. Esta spec muda o que acontece entre receber o comentário e enviar a DM, e o que o casamento faz quando a boas-vindas é de reclamação.
- [`specs/helena-channel-parity/spec.md`](../helena-channel-parity/spec.md) — o Gate de IA e o escalonamento humano no Helena (atribuir a sessão ao atendente fixo da Company). Já está no ar. A reclamação reusa esse escalonamento.

## User Stories

### Classificar

1. Como dono da Company, quero que cada comentário seja classificado antes de qualquer resposta, para a marca não responder do mesmo jeito a um preço, um elogio e uma reclamação.
2. Como dono da Company, quero que a classificação escolha sempre uma de cinco categorias fixas (só emoji, comercial, elogio, reclamação, outro), para o comportamento ser previsível.
3. Como dono da Company, quero escrever no banco a descrição de cada categoria, para ajustar o que conta como comercial ou reclamação sem deploy.
4. Como dono da Company, quero que a legenda do post entre na classificação junto com o comentário, para um comentário com só o nome do produto ("Capri") ser entendido como interesse de compra.
5. Como mantenedor, quero que adicionar uma categoria exija mudança de código, para nenhuma categoria existir sem uma ação definida.
6. Como mantenedor, quero que a pergunta feita à Decisions e o modelo fiquem fixos no código, para o banco conter só o que a Company precisa afinar.
7. Como mantenedor, quero que comentário e legenda entrem na classificação como dado, nunca como instrução, para ninguém conseguir escolher a categoria escrevendo no comentário.
8. Como mantenedor, quero que a confiança da classificação vá para o log junto com a categoria, para entender depois por que um comentário caiu onde caiu.
9. Como mantenedor, quero que a confiança não corte nada, para comentários ambíguos ("🔥😍 quero") seguirem a categoria escolhida.
10. Como mantenedor, quero que a Company sem as cinco descrições no banco não tenha comentário nenhum processado, só um registro no log, para nunca classificar entre categorias sem descrição.

### Só emoji e outro

11. Como lead que só reagiu com emoji, quero não receber DM nem resposta, para não ser abordado por uma reação.
12. Como lead que marcou um amigo, quero não receber DM nem resposta, para a marca não entrar numa conversa que não é com ela.
13. Como dono da Company, quero que spam e assuntos alheios não recebam nada, para a marca não responder a qualquer coisa.
14. Como lead que comentou um emoji e depois perguntou o preço no mesmo post, quero receber a DM pela pergunta, para o primeiro comentário não gastar a minha vez.
15. Como mantenedor, quero que comentários sem resposta não paguem geração de texto, para o custo ficar só onde algo é enviado.
16. Como mantenedor, quero que comentários sem resposta não sejam gravados no registro de boas-vindas, para o registro guardar só as boas-vindas de verdade.

### Comercial

17. Como lead que perguntou preço, valor, tamanho ou citou um produto, quero receber a mesma DM de vendas de hoje, para a parte que já funciona não mudar.
18. Como dono da Company, quero que a Resposta pública do comercial continue sendo o meu texto fixo ("te enviei direct"), para a resposta que promete o direct ser sempre a mesma.
19. Como dono da Company, quero continuar editando o prompt da DM de vendas no mesmo lugar do banco, para nada mudar no que já ajustei.

### Elogio

20. Como lead que elogiou, quero uma resposta pública que agradeça no tom do elogio ("Incrível, né? …"), para me sentir ouvido.
21. Como lead que elogiou, quero uma DM leve, que me convide a conhecer os produtos, para não receber uma abordagem de vendas que não pedi.
22. Como dono da Company, quero um prompt no banco para a resposta pública do elogio e outro para a DM do elogio, para ajustar cada texto sozinho.
23. Como lead, quero que a resposta pública do elogio só apareça se a DM chegou, para a regra da Resposta pública ser a mesma em todas as categorias.
24. Como mantenedor, quero que a resposta pública do elogio só seja gerada depois que a DM saiu, para não pagar geração de uma resposta que não vai ser publicada.
25. Como mantenedor, quero que uma falha ao gerar ou publicar a resposta do elogio só dispare alerta, para a boas-vindas que já saiu continuar valendo.

### Reclamação

26. Como lead que reclamou, quero não receber resposta pública, para a minha reclamação não ganhar um "te enviei direct" na frente de todo mundo.
27. Como lead que reclamou, quero receber uma DM que tente entender o problema, para ter um canal de verdade para resolver.
28. Como dono da Company, quero um prompt no banco para a DM de reclamação, para controlar o tom com quem está insatisfeito.
29. Como lead que reclamou e respondeu a DM, quero falar com uma pessoa e não com a IA de vendas, para a minha reclamação ser tratada por quem resolve.
30. Como dono da Company, quero que a passagem para o humano na reclamação seja automática, sem depender de a IA decidir escalar, para nunca uma reclamação cair numa conversa de vendas.
31. Como atendente, quero receber no Helena a sessão de quem reclamou já atribuída a mim, como no escalonamento humano de hoje, para saber que tenho que assumir.
32. Como atendente, quero ver no histórico a DM que o engine mandou e a mensagem da pessoa, para assumir com o contexto.
33. Como mantenedor, quero que a primeira mensagem de quem reclamou seja gravada no histórico mas não chegue à IA, como acontece com qualquer conversa com o Gate de IA desligado.
34. Como cliente antigo que reclamou num post, quero ir para o humano mesmo se eu responder numa conversa que já estava aberta, para o meu histórico com a marca não me colocar de volta na IA.
35. Como mantenedor, quero que, para quem já é Customer, o escalonamento aconteça logo depois do envio da DM, para cobrir a resposta que chega numa sessão já existente do Helena, onde o casamento não roda.
36. Como mantenedor, quero que a resposta numa sessão nova seja escalada no casamento, para cobrir quem ainda não era Customer e o Customer cuja resposta abre uma sessão nova.
37. Como mantenedor, quero que uma falha ao atribuir a sessão no Helena dispare alerta e mantenha a IA desligada, como no escalonamento humano de hoje, para a IA nunca voltar a falar com quem reclamou por causa de uma falha do Helena.
38. Como mantenedor, quero que uma Company sem atendente do Helena configurado ainda desligue a IA de quem reclamou, só sem atribuir, como no escalonamento humano de hoje.
39. Como mantenedor, quero que a proteção contra eco continue valendo para a DM de reclamação, para a nossa própria DM nunca ser tratada como resposta da pessoa.

### A vez da pessoa no post

40. Como lead que já recebeu uma DM de boas-vindas num post, quero não receber nada pelos meus comentários seguintes naquele post, para não ser abordado duas vezes.
41. Como mantenedor, quero que o comentário de quem já recebeu DM no post seja descartado antes da classificação, para não pagar a Decisions à toa.
42. Como mantenedor, quero que só gaste a vez o comentário que gerou uma DM, para emoji e outro não tirarem a chance de um comentário que vem depois.
43. Como mantenedor, quero que a reentrega do mesmo evento pela Meta continue gerando uma DM só, para a idempotência de hoje não se perder com a classificação antes da reserva.
44. Como mantenedor, quero que dois comentários da mesma pessoa no mesmo post, ao mesmo tempo, gerem no máximo uma DM, para a corrida continuar resolvida pela reserva.

### Falhas e configuração

45. Como mantenedor, quero que uma falha da Decisions dispare o alerta crítico da boas-vindas e não envie nada, para eu saber quando a API em beta falhar.
46. Como lead cujo comentário caiu numa falha da Decisions, quero ser atendido se comentar de novo no post, para a falha técnica não gastar a minha vez.
47. Como mantenedor, quero que a Decisions nunca caia para comercial quando falhar, para a falha não voltar a mandar DM de vendas para reclamação e emoji.
48. Como mantenedor, quero que uma resposta da Decisions com uma categoria fora da lista ou com recusa seja tratada como falha, para o código nunca agir sobre uma categoria que não conhece.
49. Como mantenedor, quero que uma falha ao buscar a legenda dispare alerta e não envie nada, como hoje, para a classificação nunca rodar sem o contexto do post por erro silencioso.
50. Como dono da Company, quero que uma categoria sem o prompt dela no banco (ou o comercial sem o texto fixo) não envie nada e não gaste a vez, só deixe um registro no log, para eu poder ligar as categorias aos poucos sem erro.
51. Como mantenedor, quero que a categoria fique gravada no registro de cada boas-vindas, para o casamento saber quando é reclamação e para eu ver no banco o que cada DM foi.
52. Como mantenedor, quero que as boas-vindas pendentes antigas, sem categoria, continuem sendo casadas como hoje, para a migração não mudar nada do que já está no ar.

### O que não muda

53. Como mantenedor, quero que o dev mode continue restringindo tudo ao autor de teste, antes da classificação, para testar em produção sem atingir clientes.
54. Como IA, quero continuar recebendo a DM como minha primeira fala e a Origem do comentário no contexto, em comercial e elogio, para responder em continuidade.
55. Como mantenedor, quero que as DMs de elogio e reclamação também sejam geradas conhecendo a conversa anterior de quem já é Customer, como a de vendas hoje, para a IA não se apresentar de novo a um cliente antigo.
56. Como mantenedor, quero que as DMs de elogio e reclamação também extraiam o produto da legenda, para a Origem do comentário ter o mesmo formato em todas as categorias.

## Implementation Decisions

### Forma geral

Nada de rota nova, canal novo ou tabela nova. As mudanças:

- **Serviço de boas-vindas** — ganha a classificação e passa a agir por categoria.
- **Serviço de OpenAI** — ganha a chamada à Decisions.
- **Registro de boas-vindas** — ganha uma coluna de categoria.
- **Casamento no canal Helena** — passa a escalar quando a boas-vindas é de reclamação.
- **Escalonamento humano no Helena** — o passo que hoje vive dentro da tool `transfer_to_human` (desligar o Gate de IA e atribuir a sessão ao atendente fixo, com alerta se a atribuição falhar) passa a ser reusável, e é chamado pela tool, pelo casamento e pelo envio da DM.
- **Dados no banco da 7ball** — as cinco descrições e os três prompts novos.

Ver ADR 0001 (Decisions API) e ADR 0002 (sem curtida) desta pasta.

### Categorias (no código)

As cinco categorias e a ação de cada uma são fixas no código. É a decisão central da spec:

```
categoria    resposta pública         DM
so_emoji     —                        —
comercial    texto fixo da Company    prompt instagram_welcome_dm (o de hoje)
elogio       prompt de resposta       prompt de DM do elogio
reclamacao   —                        prompt de DM da reclamação + escalonamento humano
outro        —                        —
```

### Classificação (Decisions API)

- Uma chamada ao endpoint de decisões da OpenAI, com a chave da OpenAI da Company, por um método novo no serviço de OpenAI. O SDK instalado não tem esse recurso; a chamada é um POST genérico pelo mesmo client.
- Modelo fixo no código: `gpt-6-luna`, o único que a Decisions aceita hoje.
- Uma pergunta só, do tipo escolha, com instrução fixa no código (na linha de "que tipo de comentário uma pessoa deixou num post de uma marca no Instagram?").
- As opções são as cinco categorias do código. A descrição de cada uma vem do banco.
- A entrada é o texto do comentário e a legenda do post, marcados como dado de terceiros.
- Da resposta, o engine lê a categoria escolhida e a confiança. A confiança vai só para o log. Categoria fora da lista, resposta sem resposta para a pergunta ou recusa = falha.
- Formato testado em 08/10/2026: a resposta traz `answers`, e cada item tem `choice`, `probabilities` e `confidence`. A API está em beta e o formato pode mudar até o lançamento final.

### Textos no banco (tabela `prompts`, por Company)

Nenhuma mudança de schema na tabela.

- **Descrições das categorias**: cinco linhas com o `reason` próprio da classificação de comentários, `name` igual ao valor da categoria no código e a descrição no `prompt`. As colunas de modelo ficam sem uso. Faltando qualquer uma das cinco, nenhum comentário da Company é processado: só um log, conferido antes de qualquer chamada à Meta.
- **Prompts de geração**: no mesmo `reason` do prompt de boas-vindas de hoje. São o `instagram_welcome_dm` de hoje (DM comercial, sem mudança) e três novos: DM do elogio, Resposta pública do elogio e DM da reclamação. Cada um tem modelo e reasoning effort próprios, como os outros prompts, e usa os mesmos marcadores de hoje (nome, comentário, legenda), substituídos do mesmo jeito.
- Os três prompts de DM devolvem o mesmo JSON estrito de hoje: o texto da DM e o produto (ou nulo). O prompt de resposta do elogio devolve um JSON estrito com um único campo de texto.
- Os prompts de DM recebem a conversa anterior de quem já é Customer, como hoje. O prompt da resposta pública não recebe, porque o texto é público.
- Categoria escolhida sem o prompt dela (ou comercial sem o texto fixo da Company): só log, nada enviado, nada gravado, a vez não é gasta.

### Configuração por Company

- O texto fixo da Resposta pública continua no mesmo lugar e passa a ser exigido só para o comercial.
- A feature fica ligada para a Company quando ela tem a conta do Instagram, o token e as cinco descrições. Cada categoria que envia algo também precisa dos seus textos.

### Ordem das chamadas

```
elegibilidade (primeiro nível, não é a própria conta, dev mode)
→ já recebeu DM neste post?            sim → fim, nada enviado
→ busca legenda
→ classifica (comentário + legenda)
→ so_emoji / outro                     → log, fim
→ categoria sem os textos dela         → log, fim
→ reserva a vez (grava a categoria)    perdeu a corrida → fim
→ busca nome → gera DM (prompt da categoria) → envia DM → libera a conversa → grava pendente
→ quem comentou já é Customer          → grava DM e Origem na sessão; se reclamação, escala
→ comercial: publica o texto fixo | elogio: gera e publica a resposta | reclamação: nada
```

- **Antes da reserva** ficam a leitura de quem já recebeu DM, a busca da legenda e a classificação. A reentrega do mesmo evento pela Meta classifica de novo e é barrada pela leitura ou pela reserva. A unicidade por Company + post + autor e a exclusão dos registros falhos não mudam.
- **"Já recebeu DM neste post"** é ter qualquer registro não falho para Company + post + autor. Como emoji e outro não gravam nada, um registro sempre quer dizer que uma DM foi tentada ou enviada.
- **Falha na legenda ou na classificação**: alerta crítico da boas-vindas, com o passo, e nada enviado. Não há registro para marcar como falho, então a vez não é gasta.
- **Falhas a partir da reserva**: tudo como hoje (marca falho, alerta, nada publicado).
- **Elogio**: a resposta pública só é gerada depois que a DM saiu e a boas-vindas foi gravada como pendente. Falha ao gerar ou publicar: só alerta, a boas-vindas segue pendente.
- **Escalonamento no envio** (reclamação de quem já é Customer): roda depois de a DM ser gravada na sessão do Customer. Falha ao atribuir: alerta, IA continua desligada. Qualquer outra falha aqui não desfaz a DM: vai para o log com alerta, e o casamento ainda escala se a resposta abrir uma sessão nova.

### Registro de boas-vindas

- Coluna nova de categoria, texto simples, nula nas linhas antigas. É gravada na reserva.
- Os estados não mudam (em processamento → pendente → casada, ou falhou). Reclamação não é um estado: é a categoria.

### Casamento no canal Helena

- Depois de casar a boas-vindas pendente com a sessão nova (como hoje), se a categoria for reclamação, o casamento chama o escalonamento humano no Helena para aquela sessão: desliga o Gate de IA e atribui a sessão ao atendente fixo da Company.
- A mensagem segue o caminho que já existe para Gate desligado: é gravada no histórico e a IA não é chamada.
- A ordem é: casar → escalar, se for reclamação → proteção contra eco → Gate de IA. Assim, mesmo uma mensagem que é eco da nossa DM deixa a sessão escalada.
- Boas-vindas sem categoria (antigas), comercial e elogio: tudo como hoje.
- Falha ao atribuir no Helena: alerta, IA continua desligada. Falha antes disso cai na regra de hoje do casamento: log, e a mensagem segue o fluxo normal.

### Escalonamento humano reusável

O trecho da tool `transfer_to_human` que, no canal Helena, desliga o Gate de IA e atribui a sessão sai da tool para um ponto reusável, com a mesma semântica de hoje:

- desliga primeiro;
- sem atendente ou sem chave do Helena configurados, só desliga, com log;
- falha ao atribuir = alerta, e a IA continua desligada.

A tool passa a chamar esse ponto, e o comportamento dela não muda.

### Migração e dados

- Uma migration adiciona a coluna de categoria ao registro de boas-vindas. O `.env` local aponta para o banco de produção: rodar a migration localmente aplica em produção.
- As cinco descrições e os três prompts novos da 7ball (Company 5) são inseridos direto no banco, como o prompt de boas-vindas foi. Não entram na migration. Ponto de partida das descrições, já testado:
  - **so_emoji**: Só emojis, sem texto.
  - **comercial**: Interesse de compra: preço, valor, tamanho, medidas, prazo, pagamento, ou cita o nome de um produto ou modelo.
  - **elogio**: Elogio ou reação positiva ao produto, ao post ou à marca.
  - **reclamacao**: Reclamação, insatisfação ou crítica à marca, ao atendimento ou ao produto.
  - **outro**: Qualquer outra coisa: marcar amigos, spam, assunto alheio.

## Testing Decisions

Um bom teste aqui observa o que entra por uma rota HTTP e o que sai pelos clients simulados: Meta, OpenAI e Helena. Não testa o formato do registro, a ordem interna das funções nem o texto dos prompts.

**Seam 1 — a rota existente do webhook do Instagram.** Simulados: o client da API do Instagram, a chamada à Decisions, a geração da OpenAI e o client do Helena. Casos:

- só emoji → nenhuma geração, nenhuma DM, nenhuma resposta, nada gravado;
- outro → idem;
- só emoji e depois comercial, mesma pessoa, mesmo post → a segunda gera DM;
- comercial → DM gerada pelo prompt de hoje, depois o texto fixo da Company como Resposta pública;
- elogio → DM pelo prompt de DM do elogio, depois a resposta gerada pelo prompt de resposta e publicada, nessa ordem;
- elogio com DM falhando → resposta nem gerada nem publicada, alerta;
- elogio com falha na geração da resposta → alerta, boas-vindas pendente;
- reclamação → DM pelo prompt de reclamação, nenhuma Resposta pública;
- reclamação de quem já é Customer → Gate de IA desligado e sessão atribuída ao atendente da Company;
- reclamação de quem já é Customer, com falha ao atribuir → alerta, Gate desligado;
- segundo comentário de quem já recebeu DM no post → nenhuma chamada à Decisions, nada enviado;
- mesmo evento entregue duas vezes → uma DM só;
- Decisions falhando → alerta, nada enviado; novo comentário da pessoa no post → atendido;
- Decisions devolvendo categoria fora da lista → tratado como falha;
- Company sem alguma das cinco descrições → nenhuma chamada à Meta nem à OpenAI;
- categoria sem o prompt dela → nada enviado, e um comentário seguinte da pessoa ainda é atendido;
- a categoria classificada fica gravada na boas-vindas;
- a legenda do post chega à Decisions junto com o comentário.

**Seam 2 — a rota existente do webhook do Helena.** Com uma boas-vindas pendente gravada para a Company de teste, simulando o client do Helena e a OpenAI:

- reclamação, primeira mensagem de sessão nova que casa → IA não chamada, mensagem do lead no histórico, Gate desligado, sessão atribuída ao atendente;
- reclamação com eco da própria DM → sessão escalada, IA não chamada;
- comercial e elogio → comportamento de hoje (DM no histórico, IA responde);
- boas-vindas antiga sem categoria → comportamento de hoje.

**Escalonamento reusável** — coberto pelos casos acima e pelos testes que já existem da tool `transfer_to_human` no Helena, que precisam continuar passando sem mudança.

**Prior art.** O teste da rota do Instagram já simula os métodos do client do Instagram e a chamada de geração da OpenAI por patch, e é o molde direto: o método novo da Decisions entra simulado do mesmo jeito. O teste da rota do Helena é o molde do seam 2: banco real com Company de teste, dev mode síncrono e clients de saída simulados. Os testes do projeto são scripts com asserts, não pytest. Para rodá-los fora de produção, ver a memória da DM do Instagram (Postgres descartável e stamp da migration 010).

## Out of Scope

- **Curtir comentários.** O endpoint da Meta só existe no Facebook Login. Ver ADR 0002 desta pasta.
- **Corte por confiança.** A categoria escolhida vale sempre; a confiança só vai para o log.
- **Fallback quando a Decisions falha.** Não cai para comercial nem para outra categoria.
- **Gravar no registro os comentários sem resposta** (emoji, outro, sem configuração). Ficam só no log.
- **Responder ao segundo comentário da pessoa no post**, seja qual for o tipo, inclusive reclamação depois de uma DM de vendas.
- **Respostas entre seguidores** (comentários com comentário pai) e comentários da própria conta. Continuam ignorados.
- **Categorias por Company.** A lista é a mesma para todas; só as descrições e os prompts mudam por Company.
- **Mudar a DM comercial ou a Resposta pública fixa.** As duas continuam como estão.
- **Resposta pública para reclamação.**
- **Categoria na Origem do comentário.** A IA recebe o comentário, e na reclamação ela nem chega a ser chamada.
- **Religar a IA automaticamente depois do atendimento humano.** Continua manual, como em todo o escalonamento.
- **Classificar em outros pontos do sistema** (mensagens do chat, Chatwoot).
- **Investigar as 28 boas-vindas falhas** que estão no banco da 7ball, quase todas de comentário de preço. A causa não aparece nos logs do container de produção. Vale um ticket à parte, e ela afeta esta feature igual afeta a de hoje.
- **Tela ou endpoint de configuração.** Descrições e prompts são gravados direto no banco.

## Further Notes

**Teste real da classificação (08/10/2026, chave da 7ball, `gpt-6-luna`).** Com a legenda do post da Capri e as descrições acima:

| Comentário | Categoria | Confiança |
|---|---|---|
| Pena que ninguém responde ao orçamento | reclamação | 0,86 |
| 14 dias para obter uma resposta!! Um absurdo | reclamação | 0,95 |
| 👏👏👏 | só emoji | 0,82 |
| Olha o acabamento🤩 | elogio | 0,96 |
| @joao_silva olha essa | outro | 0,86 |
| Capri | comercial | 0,79 |
| Linda! Quanto custa? | comercial | 0,90 |
| Comprei e amei, chegou certinho | elogio | 0,69 |
| 🔥😍 quero | elogio | 0,51 |

Sem a legenda, "Capri" caiu em outro (0,41), e a confiança das outras despencou. Por isso a legenda entra na classificação.

**Os prompts novos da 7ball**, na direção que o dono deu:

- **Resposta pública do elogio**: curta, celebrando junto ("Incrível, né? …").
- **DM do elogio**: leve, sem abordagem de vendas ("Gostaria de conhecer mais dos nossos produtos?").
- **DM da reclamação**: acolher e perguntar o que aconteceu, sem vender e sem prometer prazo.

Os três seguem a forma do prompt de boas-vindas de hoje: persona Duda, dados de terceiros entre aspas triplas, não se apresentar de novo a quem já conversou. O agente da 7ball que recebe a conversa no Helena não muda.

**Riscos.**

- A Decisions API está em beta público desde 06/10/2026. O formato pode mudar até o lançamento final; se mudar, a classificação falha com alerta e nada é enviado.
- A chamada à Meta para a legenda passa a acontecer em todo comentário elegível, inclusive nos que não recebem nada. Num post viral, isso conta no limite de chamadas da conta.

**Documentação.** Ao implementar, atualizar a referência de arquivos (serviço de boas-vindas, serviço de OpenAI, casamento no Helena, tool de transferência, modelos) e a documentação do schema (coluna nova).
