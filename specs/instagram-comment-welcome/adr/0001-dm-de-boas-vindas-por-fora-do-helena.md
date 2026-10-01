# A DM de boas-vindas sai pela Meta e a conversa continua no Helena

O Helena não responde comentários do Instagram nem envia DM a partir de comentário, e a Meta só aceita essa DM com o id do comentário, numa chamada que o Helena não expõe. Decidimos que o engine recebe o webhook de comentários num app próprio da Meta, envia a DM de boas-vindas e publica a resposta pública direto pela API da Meta — e para aí. Quando a pessoa responde a DM, a conversa entra pelo canal Helena como qualquer outra.

A consequência estranha: o Customer não pode ser criado no momento da DM. Ele é identificado pelo `sessionId` do Helena, que só existe quando o Helena abre a sessão, e o Helena não tem como criar sessão nem importar mensagem. Por isso a DM fica guardada como boas-vindas pendente e só vira histórico na primeira mensagem da sessão nova, quando casamos a sessão com a pessoa.

## Considered Options

- **Atender a resposta da DM também pelo nosso app da Meta** — dispensaria o casamento, mas criaria um canal Instagram inteiro e tiraria a conversa do painel do Helena, onde o atendente humano assume.
- **Pré-criar o contato no Helena pelo username** — não resolve, porque o histórico é por sessão e não por contato, e o Helena já duplicou contato e atendimento nesse caminho.

## Consequences

A chave do casamento depende de um teste real ainda não feito: se o `details.from` que o Helena manda em sessão de Instagram for igual ao id que a Meta devolve no envio da DM, casamos por ele; senão, por username, consultando a sessão no Helena. Se nenhum dos dois servir, esta decisão precisa ser revista.
