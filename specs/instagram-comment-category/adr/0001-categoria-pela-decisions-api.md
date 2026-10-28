# A Categoria do comentário sai da Decisions API, numa chamada separada da geração

Para decidir o que fazer com cada Comentário elegível, o engine classifica o comentário, junto com a legenda do post, pela Decisions API da OpenAI (`POST /v1/decisions`, `gpt-6-luna`). Só depois chama os prompts de geração, e só nas categorias que enviam algo. A alternativa óbvia era pôr a categoria como mais um campo do JSON que a chamada da DM já devolve: uma chamada só, sem dependência nova. Escolhemos a Decisions porque a resposta dela é sempre uma das opções enviadas e vem com uma confiança. Ela também separa classificar de escrever: só emoji e outro não pagam geração, e cada categoria tem seus próprios prompts.

A lista de categorias é fixa no código, porque cada uma tem uma ação diferente e categoria nova é código novo. Também ficam no código a pergunta e o modelo. A descrição de cada opção, que a Decisions usa para escolher, vive na tabela `prompts`, por Company, para afinar a classificação sem deploy.

## Considered Options

- **Categoria como campo do JSON da geração** — uma chamada só e nenhuma dependência nova. Foi descartada porque mistura classificar com escrever, e porque os comentários sem resposta pagariam a geração inteira.

## Consequences

- A Decisions API está em beta público desde 06/10/2026 e só aceita o `gpt-6-luna`. O SDK instalado (`openai` 2.31.0) não tem esse recurso, então a chamada é um POST genérico pelo client da OpenAI. O formato da requisição e da resposta pode mudar até o lançamento final; o testado em 08/10/2026 foi `answers[0].choice` + `confidence`.
- Se a Decisions falhar, o fluxo falha: alerta crítico, nada é enviado e a vez da pessoa não é gasta. Não há fallback para comercial, porque ele voltaria a mandar DM de vendas para reclamação e emoji.
- A confiança só vai para o log; não há corte mínimo. O primeiro ajuste de classificação é a descrição da categoria no banco.
- A legenda entra na classificação porque, sem ela, um comentário com só o nome do produto ("Capri") cai em outro. Em compensação, todo comentário paga uma leitura do post na Meta, inclusive os emojis.
