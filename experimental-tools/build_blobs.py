"""Insere as fichas novas (fichas_novos.json) nos blobs atuais (_db/*.txt), em ordem alfabética
na seção '## 3. ... fichas de produto', e atualiza o contador de produtos no cabeçalho.

Gera _db/{sinuca,fitness}.novo.txt SEM tocar no banco. A gravação é o build_blobs.py --commit,
num passo separado, para o dono revisar o diff antes.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
BLOB = {"7ball": HERE / "_db/sinuca.txt", "reebok": HERE / "_db/fitness.txt"}
NOVO = {"7ball": HERE / "_db/sinuca.novo.txt", "reebok": HERE / "_db/fitness.novo.txt"}
FICHA_NOME = re.compile(r"^#### (.+)$", re.M)
FICHA_SLUG = re.compile(r"\*Slug:\* `([^`]+)`")


def parse_fichas(txt: str) -> list[tuple[str, str, str]]:
    """Divide a seção 3 em fichas: [(nome_ordenacao, slug, bloco_md), ...]. Identidade = slug."""
    out = []
    for blk in re.split(r"(?=^#### )", txt, flags=re.M):
        mn, ms = FICHA_NOME.match(blk), FICHA_SLUG.search(blk)
        if mn and ms:
            out.append((mn.group(1).strip().upper(), ms.group(1), blk.rstrip() + "\n"))
    return out


def build(marca: str, novas: list[dict]) -> str:
    full = BLOB[marca].read_text(encoding="utf-8")
    sec = re.search(r"(## 3\..*?fichas de produto.*?\n\n)(.*?)(\n## \d)", full, re.S)
    if not sec:
        raise SystemExit(f"{marca}: não achei a seção 3 de fichas")
    head, corpo, tail = sec.group(1), sec.group(2), sec.group(3)

    fichas = parse_fichas(corpo)
    slugs_existentes = {s for _, s, _ in fichas}
    nomes_ocupados = {n for n, _, _ in fichas}  # p/ desambiguar header colidido
    for f in novas:
        if f["slug"] in slugs_existentes:  # idempotente por slug
            continue
        nome = f["nome"].strip().upper()
        bloco = f["ficha_md"].rstrip() + "\n"
        # header duplicado -> desambigua com o Tipo: '#### DOHA (Jantar & Sinuca)'
        if nome in nomes_ocupados and f.get("tipos"):
            novo_header = f"{f['nome']} ({f['tipos'][0]})"
            bloco = re.sub(r"^#### .+$", f"#### {novo_header}", bloco, count=1, flags=re.M)
            nome = novo_header.upper()
        fichas.append((nome, f["slug"], bloco))
        slugs_existentes.add(f["slug"])
        nomes_ocupados.add(nome)
    fichas.sort(key=lambda x: (x[0], x[1]))
    novo_corpo = "\n".join(blk for _, _, blk in fichas)

    novo_full = full[: sec.start()] + head + novo_corpo + tail + full[sec.end():]
    n = len(fichas)
    novo_full = re.sub(r"\b\d+ produtos\b", f"{n} produtos", novo_full, count=2)
    return novo_full


def main() -> int:
    fichas = json.loads((HERE / "fichas_novos.json").read_text(encoding="utf-8"))
    commit = "--commit" in sys.argv
    for marca in ("7ball", "reebok"):
        novo = build(marca, fichas.get(marca, []))
        NOVO[marca].write_text(novo, encoding="utf-8")
        antes = len(parse_fichas(BLOB[marca].read_text(encoding="utf-8")))
        depois = len(parse_fichas(novo))
        print(f"{marca:7s}: {antes} -> {depois} fichas  ({NOVO[marca].name})")
    if not commit:
        print("\n(preview only — nada gravado no banco. Rode build_update_sql.py p/ gerar o UPDATE)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
