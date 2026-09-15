# sessionId do Helena vira o sessionId do Customer

No canal Helena, usamos o `sessionId` que vem no payload do webhook (`content.sessionId`, um UUID nativo do Helena) diretamente como `Customer.sessionId`, em vez de derivar a chave do telefone do cliente.

Isso alinha nosso modelo de conversa com o do Helena: quando o Helena considera um atendimento como uma sessão, nós tratamos como uma conversa; quando ele abre uma sessão nova (por exemplo, atendimento concluído e reaberto), para nós é uma conversa nova e o histórico daquele contato recomeça. O envio da resposta também usa esse `sessionId` (`send/text` aceita `sessionId`), então o identificador é o mesmo nas duas pontas.

O trade-off aceito: não há histórico perpétuo por pessoa — o histórico é por sessão do Helena. Se no futuro for preciso continuidade por telefone independente de sessão, a chave do Customer teria que mudar, o que é uma alteração cara. Reusa o `upsert_api_customer` existente (customer por `sessionId` puro, sem campos Chatwoot).
