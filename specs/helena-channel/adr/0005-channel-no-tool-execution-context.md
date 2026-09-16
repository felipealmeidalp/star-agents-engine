# Tools sabem o canal por um campo no ToolExecutionContext

Para `transfer_to_human` rodar diferente em cada canal (labels + time no Chatwoot; assignee de sessão no Helena), a tool precisa saber de qual canal a execução veio. Adicionamos um campo explícito `channel: str` ao `ToolExecutionContext` (`"chatwoot"` / `"helena"`), preenchido pelo serviço do canal ao montar o contexto, e a tool ramifica por ele.

O contexto era propositalmente agnóstico ao canal (session_id, company_id, agentes, db) — essa é a primeira coisa específica de canal que entra nele. Escolhemos um campo explícito em vez de inferir o canal pela config da company (ex. "tem `helena_apikey` e não tem Chatwoot") porque a inferência quebra assim que uma empresa usar os dois canais ao mesmo tempo, enquanto o campo é sempre correto e custa uma linha em cada serviço.

Consequência: `transfer_to_human` deixa de ser Chatwoot-only e passa a ter um branch por canal (a alternativa — extrair um handler de escalonamento por canal — é mais limpa, mas é refactor maior sem problema concreto que a justifique agora). E o campo fica disponível para qualquer tool futura que precise falar com o CRM certo.
