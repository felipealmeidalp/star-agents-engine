---
name: editar-prompt-agente
description: Edita o system prompt de um agente/sub-agente do Star Agents direto no banco de producao. Use quando o usuario quiser ajustar identidade, tom de voz, regras (de ouro/negativas), missao, passos do fluxo, formatacao de saida ou objetivo de um agente — passando os ids do agente e do sub-agente e o que quer mudar. Gatilhos: "editar prompt do agente", "ajustar as regras do sub-agente X", "mudar o passo N do fluxo", "subir isso no prompt em producao".
---

# Editar prompt de agente no banco

O prompt do Star Agents nao vive em arquivo: e montado em runtime pelo `ContextBuilder`
a partir de colunas de texto nas tabelas `agents`, `sub_agents` e `steps`. Editar o prompt
= editar essas colunas no Postgres (Neon, via `DATABASE_URL` do `.env`). Como o builder le
o banco a cada request, a mudanca vale na proxima conversa — sem redeploy.

Esta skill usa `scripts/prompt_db.py`, que reaproveita o proprio
`ContextBuilder._build_system_prompt` do projeto, entao o que voce ve e exatamente o que a
OpenAI recebe.

## De onde vem cada secao do prompt

| Secao no prompt | Origem (`target` no patch) | Tabela.coluna |
|---|---|---|
| `## Identidade` | `identity` | agents.identity |
| `## Tom de Voz` | `voice_tone` | agents.voice_tone |
| `## Objetivo Geral` | `master_goal` | agents.master_goal |
| `## Regras de Ouro` | `golden_rules` | agents.golden_rules |
| `## Regras Negativas` | `negative_rules` | agents.negative_rules |
| `## Missao Principal` | `mission` | sub_agents.mission |
| `## Fluxo de Passos` (passo N) | `step:N` | steps.step (relative_id=N) |
| `## Formatacao de Saida` | `output_instructions` | agents.output_instructions |
| `## Ferramentas Disponiveis` | — (vem de tools.instructions; nao editar por aqui) |

`identity`, `tom`, `objetivo`, regras e `output` sao do **agente** (afetam todos os
sub-agentes dele). `mission` e os `step:N` sao do **sub-agente** especifico.

## Fluxo (sempre nesta ordem)

1. **Pegue os ids.** Peca ao usuario `agent_id` e `sub_agent_id` se ele nao deu. Toda edicao
   precisa dos dois.

2. **Leia o prompt atual** antes de propor mudanca:
   ```
   python3 .claude/skills/editar-prompt-agente/scripts/prompt_db.py show --agent <A> --sub <S>
   ```
   Entenda em qual secao a mudanca cai e leia o texto cru daquele campo. **Nunca edite sem
   ter lido o texto atual** — a edicao e por ancora exata.

3. **Monte o patch.** Crie um JSON no scratchpad (lista de edicoes). Cada edicao ancora num
   trecho **unico** ja existente no campo e insere/substitui a partir dele:
   ```json
   [
     {
       "target": "golden_rules",
       "anchor": "<trecho exato e unico ja presente no campo>",
       "text": "\n\n16. **NUNCA** ...",
       "mode": "after"
     },
     { "target": "step:2", "anchor": "...", "text": "...", "mode": "after" }
   ]
   ```
   - `mode`: `after` (default), `before` ou `replace` (troca o `anchor` pelo `text`).
   - A ancora precisa casar **exatamente 1x**; se casar 0 ou 2+, o script aborta sem gravar.
     Escolha um trecho longo o bastante pra ser unico.
   - Escreva o texto no MESMO estilo do campo (numeracao das regras, markdown dos passos,
     acentuacao correta em portugues).

4. **Dry-run obrigatorio.** Rode sem `--write` e mostre o diff ao usuario:
   ```
   python3 .../prompt_db.py apply --agent <A> --sub <S> --patch <patch.json>
   ```

5. **Confirme e grave.** So depois do OK do usuario, rode com `--write`. Isso gera um backup
   automatico (caminho impresso) e aplica tudo numa transacao:
   ```
   python3 .../prompt_db.py apply --agent <A> --sub <S> --patch <patch.json> --write
   ```

6. **Verifique.** Rode `show` de novo e confirme que o trecho novo aparece na secao certa.

## Reverter

O `--write` imprime o caminho de um backup JSON. Pra desfazer:
```
python3 .../prompt_db.py restore --backup <backup.json> --write
```

## Regras

- **Producao de verdade.** Isso altera o comportamento do agente ao vivo. Sempre dry-run +
  confirmacao antes de `--write`. Nunca pule pro `--write` direto.
- **Uma ancora, um match.** Se o script reclamar de match ambiguo, refine a ancora — nao
  force.
- **Nao invente ids nem colunas.** Se nao souber o id, rode `show` ou pergunte. Colunas fora
  da tabela acima nao sao editaveis por aqui.
- **Coerencia do prompt.** Ao adicionar regra, cheque se nao contradiz outra secao (ex: uma
  regra nova de "avance na duvida" vs. uma proibicao de "nunca invente"). Aponte a tensao ao
  usuario.
