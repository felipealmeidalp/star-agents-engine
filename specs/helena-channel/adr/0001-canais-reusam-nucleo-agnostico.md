# Canais externos reusam o núcleo agnóstico de processamento

Ao adicionar o Helena como segundo canal, decidimos que um canal novo traz apenas quatro peças próprias — rota de webhook, schema do payload, client de saída e resolução dos seus identificadores — e reusa todo o núcleo já existente: `RequestManager` (lock + cancelamento por cliente), `MessageBuffer` (Redis), `process_chat_in_memory` e o pipeline de IA (`ChatHandler`/`ContextBuilder`).

Isso é possível porque o núcleo já é agnóstico ao canal: o `RequestManager` recebe uma chave inteira e callbacks de envio (`on_send_messages`), sem saber de onde a mensagem veio nem para onde a resposta vai. Um canal se conecta fornecendo (1) uma chave inteira estável por cliente para o buffer/lock, (2) um `sessionId` string para amarrar `customers`/`chat_history`, e (3) um callback que despacha as mensagens pela API do canal.

A consequência é que o `HelenaService` é propositalmente enxuto e delega ao mesmo lugar que o `ChatwootService`. Quem chega ao código e vê pouca lógica no serviço do canal deve procurar o comportamento no núcleo compartilhado, não assumir que falta implementação.
