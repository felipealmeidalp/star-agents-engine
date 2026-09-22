#!/usr/bin/env python3
"""Le, remonta e edita no banco de producao o prompt de um agente/sub-agente do Star Agents.

Reaproveita o proprio ContextBuilder._build_system_prompt do projeto, entao o prompt
mostrado aqui e IDENTICO ao que o sistema envia pra OpenAI. Edicoes sao aplicadas por
match exato de ancora, sempre com backup e dry-run.

Uso:
  # ver o prompt montado e de onde vem cada secao
  python3 prompt_db.py show --agent 39 --sub 137

  # aplicar edicoes de um arquivo de patch JSON (dry-run por padrao)
  python3 prompt_db.py apply --agent 39 --sub 137 --patch patch.json
  python3 prompt_db.py apply --agent 39 --sub 137 --patch patch.json --write   # grava de fato

  # restaurar de um backup gerado por um apply anterior
  python3 prompt_db.py restore --backup /caminho/backup.json --write

Formato do patch.json — lista de edicoes. Cada uma:
  {
    "target": "golden_rules" | "identity" | "voice_tone" | "master_goal"
              | "negative_rules" | "output_instructions"   (colunas de agents)
            | "mission"                                     (coluna de sub_agents)
            | "step:<relative_id>"                          (coluna step do step com aquele relative_id)
            | "sub_agent.<coluna>" | "agent.<coluna>",      (qualquer outra coluna, explicita)
    "anchor": "<trecho exato ja existente no texto>",
    "text":   "<texto novo>",
    "mode":   "after" | "before" | "replace"   (default "after")
  }
`anchor` precisa casar EXATAMENTE 1x no campo, senao a edicao inteira aborta (nada e gravado).
Para `replace`, o `anchor` e o trecho substituido por `text`.
"""
import argparse
import json
import os
import sys
from datetime import datetime

import asyncpg

PROJECT_ROOT = os.environ.get("STAR_AGENTS_ROOT", "/Users/felipealmeida/Documents/GitHub/star-agents-engine")
sys.path.insert(0, PROJECT_ROOT)

# Colunas de texto editaveis, mapeadas para a tabela real.
AGENT_COLS = {
    "identity", "voice_tone", "master_goal", "golden_rules",
    "negative_rules", "output_instructions",
}
SUB_AGENT_COLS = {"mission"}


def load_db_url() -> str:
    env = os.path.join(PROJECT_ROOT, ".env")
    with open(env) as f:
        for line in f:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].replace("postgresql+asyncpg://", "postgresql://")
                return url.split("?")[0] if "?" in url else url
    raise SystemExit(f"DATABASE_URL nao encontrado em {env}")


# ---- Resolucao de target -> (tabela, coluna, filtro) -------------------------

def resolve_target(target: str, agent_id: int, sub_agent_id: int):
    """Devolve (kind, column, key) onde kind in {'agent','sub_agent','step'}."""
    if target.startswith("step:"):
        rel = int(target.split(":", 1)[1])
        return ("step", "step", rel)
    if target.startswith("agent."):
        return ("agent", target.split(".", 1)[1], agent_id)
    if target.startswith("sub_agent."):
        return ("sub_agent", target.split(".", 1)[1], sub_agent_id)
    if target in AGENT_COLS:
        return ("agent", target, agent_id)
    if target in SUB_AGENT_COLS:
        return ("sub_agent", target, sub_agent_id)
    raise SystemExit(
        f"target '{target}' desconhecido. Use um de: {sorted(AGENT_COLS)}, "
        f"{sorted(SUB_AGENT_COLS)}, 'step:<relative_id>', ou 'agent.<col>'/'sub_agent.<col>'."
    )


async def fetch_field(conn, kind, column, key, sub_agent_id):
    if kind == "agent":
        return await conn.fetchval(f'SELECT "{column}" FROM agents WHERE id=$1', key)
    if kind == "sub_agent":
        return await conn.fetchval(f'SELECT "{column}" FROM sub_agents WHERE id=$1', key)
    if kind == "step":  # key = relative_id
        return await conn.fetchval(
            "SELECT step FROM steps WHERE sub_agent_id=$1 AND relative_id=$2 AND deleted_at IS NULL",
            sub_agent_id, key,
        )
    raise AssertionError(kind)


async def write_field(conn, kind, column, key, sub_agent_id, value):
    if kind == "agent":
        await conn.execute(f'UPDATE agents SET "{column}"=$1 WHERE id=$2', value, key)
    elif kind == "sub_agent":
        await conn.execute(f'UPDATE sub_agents SET "{column}"=$1 WHERE id=$2', value, key)
    elif kind == "step":
        await conn.execute(
            "UPDATE steps SET step=$1 WHERE sub_agent_id=$2 AND relative_id=$3 AND deleted_at IS NULL",
            value, sub_agent_id, key,
        )
    else:
        raise AssertionError(kind)


def apply_patch(original: str, anchor: str, text: str, mode: str, label: str) -> str:
    if original is None:
        raise SystemExit(f"[{label}] campo esta vazio no banco; nao ha ancora onde inserir.")
    n = original.count(anchor)
    if n != 1:
        raise SystemExit(
            f"[{label}] ancora casou {n}x (esperado exatamente 1). "
            f"Refine a ancora para um trecho unico. Nada foi gravado.\n  ancora: {anchor[:120]!r}"
        )
    if mode == "after":
        return original.replace(anchor, anchor + text)
    if mode == "before":
        return original.replace(anchor, text + anchor)
    if mode == "replace":
        return original.replace(anchor, text)
    raise SystemExit(f"[{label}] mode '{mode}' invalido (use after|before|replace).")


# ---- Comandos ---------------------------------------------------------------

async def cmd_show(args):
    from app.services.context_builder import ContextBuilder
    from app.repositories.agent import AgentRepository  # noqa: F401 (garante import do projeto ok)

    conn = await asyncpg.connect(load_db_url())
    try:
        raw = await _fetch_full_context(conn, args.agent, args.sub)
    finally:
        await conn.close()

    cb = ContextBuilder.__new__(ContextBuilder)
    context = cb._parse_context(raw)
    prompt = cb._build_system_prompt(context)
    tools = cb._format_tools_for_openai(context.tools)
    print(f"AGENT {context.agent.id} — {context.agent.name}")
    print(f"SUB   {context.sub_agent.id} — {context.sub_agent.name} (model={context.sub_agent.model})")
    print(f"tools={[t['function']['name'] for t in tools]} steps={len(context.steps)} rules={len(context.decision_rules)}")
    print("=" * 80)
    print(prompt)


async def cmd_apply(args):
    patches = json.load(open(args.patch))
    if not isinstance(patches, list):
        raise SystemExit("patch.json deve ser uma LISTA de edicoes.")

    conn = await asyncpg.connect(load_db_url())
    try:
        planned = []  # (kind, column, key, old, new, label)
        backup = {"_meta": {"agent": args.agent, "sub": args.sub, "at": datetime.now().isoformat()}, "fields": []}
        for i, p in enumerate(patches, 1):
            kind, column, key = resolve_target(p["target"], args.agent, args.sub)
            label = f'#{i} {p["target"]}'
            old = await fetch_field(conn, kind, column, key, args.sub)
            new = apply_patch(old, p["anchor"], p["text"], p.get("mode", "after"), label)
            planned.append((kind, column, key, old, new, label))
            backup["fields"].append({"kind": kind, "column": column, "key": key, "sub_agent_id": args.sub, "value": old})
            print(f"[OK] {label}: {p.get('mode','after')} +{len(p['text'])} chars")

        if not args.write:
            print("\n[DRY-RUN] nada gravado. Reveja acima e rode de novo com --write.")
            return

        bpath = args.backup or os.path.join(
            "/private/tmp", f"prompt_backup_a{args.agent}_s{args.sub}_{datetime.now():%Y%m%d_%H%M%S}.json"
        )
        json.dump(backup, open(bpath, "w"), ensure_ascii=False, indent=2)
        print(f"\nbackup: {bpath}")

        async with conn.transaction():
            for kind, column, key, _old, new, _label in planned:
                await write_field(conn, kind, column, key, args.sub, new)
        print(f"[GRAVADO] {len(planned)} campo(s) atualizado(s) em producao.")
    finally:
        await conn.close()


async def cmd_restore(args):
    data = json.load(open(args.backup))
    fields = data.get("fields")
    if not fields:
        raise SystemExit(
            "backup invalido: sem chave 'fields'. So restauro backups gerados por este "
            "script (apply --write). Backups de outros scripts precisam ser revertidos a mao."
        )
    conn = await asyncpg.connect(load_db_url())
    try:
        for fld in fields:
            print(f"restore {fld['kind']}.{fld['column']} key={fld['key']}")
        if not args.write:
            print("\n[DRY-RUN] rode com --write para restaurar.")
            return
        async with conn.transaction():
            for fld in fields:
                await write_field(conn, fld["kind"], fld["column"], fld["key"], fld["sub_agent_id"], fld["value"])
        print(f"[RESTAURADO] {len(fields)} campo(s).")
    finally:
        await conn.close()


async def _fetch_full_context(conn, agent_id, sub_agent_id):
    """Mesma forma de get_full_context, mas por ids diretos (sem customer)."""
    q = """
    SELECT jsonb_build_object(
      'agent', to_jsonb(a), 'sub_agent', to_jsonb(sa),
      'steps', (SELECT COALESCE(jsonb_agg(s ORDER BY s.relative_id),'[]'::jsonb)
                FROM steps s WHERE s.sub_agent_id=sa.id AND s.deleted_at IS NULL),
      'decision_rules', (SELECT COALESCE(jsonb_agg(jsonb_build_object(
              'decision_rule', to_jsonb(dr),
              'sub_agent_connections',(SELECT COALESCE(jsonb_agg(sac),'[]'::jsonb)
                   FROM sub_agent_connections sac WHERE sac.decision_rule_id=dr.id)
            ) ORDER BY dr.relative_id),'[]'::jsonb)
          FROM decision_rules dr WHERE dr.sub_agent_id=sa.id AND dr.deleted_at IS NULL),
      'tools', (SELECT COALESCE(jsonb_agg(jsonb_build_object(
              'tool', to_jsonb(t),
              'parameters',(SELECT COALESCE(jsonb_agg(tp),'[]'::jsonb)
                   FROM tool_parameters tp WHERE tp.tool_id=t.id)
            )),'[]'::jsonb)
          FROM tools t WHERE t.title = ANY(sa.tools) AND t.company_id=sa.company_id)
    ) AS ctx
    FROM agents a JOIN sub_agents sa ON sa.id=$2
    WHERE a.id=$1 AND a.deleted_at IS NULL
    """
    row = await conn.fetchrow(q, agent_id, sub_agent_id)
    if not row or not row["ctx"]:
        raise SystemExit(f"agent {agent_id} / sub_agent {sub_agent_id} nao encontrado.")
    raw = json.loads(row["ctx"])
    raw["customer"] = {
        "id": 0, "sessionId": "<sem-sessao>", "agent_id": agent_id, "sub_agent_id": sub_agent_id,
        "variable_prompt_status": False, "variable_prompt_id": None, "customer_context": None,
    }
    return raw


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("show", help="mostra o prompt montado")
    s.add_argument("--agent", type=int, required=True)
    s.add_argument("--sub", type=int, required=True)

    a = sub.add_parser("apply", help="aplica patches (dry-run sem --write)")
    a.add_argument("--agent", type=int, required=True)
    a.add_argument("--sub", type=int, required=True)
    a.add_argument("--patch", required=True)
    a.add_argument("--write", action="store_true")
    a.add_argument("--backup", help="caminho do backup (default: /private/tmp)")

    r = sub.add_parser("restore", help="restaura de um backup")
    r.add_argument("--backup", required=True)
    r.add_argument("--write", action="store_true")

    args = ap.parse_args()
    import asyncio
    asyncio.run({"show": cmd_show, "apply": cmd_apply, "restore": cmd_restore}[args.cmd](args))


if __name__ == "__main__":
    main()
