# O app da Meta usa Instagram Login, não Facebook Login

Um app da Meta só pode usar um dos dois logins. Escolhemos Instagram Login para o app de comentários: três permissões para aprovar no App Review em vez de sete, nenhuma dependência de Página do Facebook e um token de 60 dias que se renova com uma chamada, que o engine faz sozinho.

O que abrimos mão: o webhook de comentário do Facebook Login traz dados de anúncio e o token de Página não expira. E o Helena conecta a mesma conta por Facebook Login, então os ids de usuário que cada app enxerga podem ser diferentes.

Trocar depois custa um novo App Review e a reconexão de cada conta. O único motivo previsto para trocar é o teste de casamento da DM com a sessão do Helena (ADR 0001) mostrar que o id só coincide sob Facebook Login e que o username não serve como chave.
