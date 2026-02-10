"""Content formatting utilities for chat messages."""

import json
from typing import Any


def format_content_for_storage(content: Any) -> str:
    """
    Formata o content para persistência no banco de dados.

    Regras:
    1. Se null/None/vazio → string vazia
    2. Se não for string → converter para string
    3. Se for JSON válido com chave "resposta":
       - Se "resposta" for array → juntar com '\n'
    4. Se não for JSON válido → retornar texto original

    Args:
        content: Conteúdo raw da resposta da OpenAI

    Returns:
        String formatada pronta para persistência
    """
    # Regra 1: null, None ou vazio
    if content is None or content == "":
        return ""

    # Regra 2: converter para string se necessário
    if not isinstance(content, str):
        content = str(content)

    # Regra 3: tentar parsear como JSON
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict) and "resposta" in parsed:
            resposta = parsed["resposta"]
            if isinstance(resposta, list):
                return "\n".join(str(item) for item in resposta)
            return str(resposta)
        # JSON válido mas sem chave "resposta" - retornar original
        return content
    except (json.JSONDecodeError, TypeError):
        # Regra 4: não é JSON válido, retornar original
        return content
