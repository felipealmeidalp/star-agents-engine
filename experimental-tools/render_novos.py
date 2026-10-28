"""Renderiza (Playwright) as páginas dos produtos NOVOS e extrai os campos que a REST não dá:
descrição longa e destaques. Junta com Tipo/Linha/Categoria (derivados dos termos que a própria
página exibe, classificados pelas listas de Tipos vs Linhas de cada marca) e monta a ficha
Markdown no formato do blob (spec.md §3.1).

Entrada:  catalogo_7ball.json (slugs do site) + _db/{sinuca,fitness}.txt (slugs já no blob).
Saída:    fichas_novos.json  -> {marca: [ficha_md, ...]} + metadados por produto.

Uso: render_novos.py            (renderiza todos os novos das 2 marcas)
     render_novos.py 7ball      (só uma marca)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
CHROMIUM = str(next(Path.home().glob(
    "Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/*.app/Contents/MacOS/*"
)))
BLOB = {"7ball": HERE / "_db/sinuca.txt", "reebok": HERE / "_db/fitness.txt"}

# JS de extração: descrição (1º text-editor com texto real), destaques (1º repeater),
# e os termos de categoria mostrados no topo da página (linkados p/ /_categoria...).
EXTRACT = r"""() => {
  const desc = [...document.querySelectorAll('.elementor-widget-text-editor')]
    .map(e => e.textContent.trim()).filter(t => t.length > 60)[0] || null;
  const rep = document.querySelector('.jet-listing-dynamic-repeater__items');
  const destaques = rep
    ? [...rep.querySelectorAll('.jet-listing-dynamic-repeater__item')].map(e => e.textContent.trim())
    : [];
  return { desc, destaques };
}"""


def blob_tipos_linhas(marca: str) -> tuple[set[str], set[str]]:
    """Lê as seções 2.1 (Tipos) e 2.2 (Linhas) do blob → dois conjuntos de nomes."""
    txt = BLOB[marca].read_text(encoding="utf-8")
    tipos = re.search(r"### 2\.1[^\n]*\n(.*?)### 2\.2", txt, re.S)
    linhas = re.search(r"### 2\.2[^\n]*\n(.*?)(?:\n## |\Z)", txt, re.S)
    grab = lambda m: {n.strip() for n in re.findall(r"^\- \*\*([^*]+)\*\*", m.group(1), re.M)} if m else set()
    return grab(tipos), grab(linhas)


def linha_descricoes(marca: str) -> dict[str, str]:
    """No Reebok a descrição do produto = descrição da Linha (seção 2.2). Mapa nome->texto."""
    txt = BLOB[marca].read_text(encoding="utf-8")
    bloco = re.search(r"### 2\.2[^\n]*\n(.*?)(?:\n## |\Z)", txt, re.S)
    out: dict[str, str] = {}
    if bloco:
        for nome, desc in re.findall(r"^\- \*\*([^*]+)\*\* \(\d+\) — (.+)$", bloco.group(1), re.M):
            out[nome.strip()] = desc.strip().rstrip(".")  # blob salva sem ponto final
    return out


def blob_slugs(marca: str) -> set[str]:
    return set(re.findall(r"\*Slug:\* `([^`]+)`", BLOB[marca].read_text(encoding="utf-8")))


def classify(nomes_cat: list[str], tipos: set[str], linhas: set[str]) -> tuple[list[str], str | None]:
    """Separa os NOMES de categoria (da REST) em Tipo(s) e Linha, pelas listas do blob."""
    t = [x for x in nomes_cat if x in tipos]
    ln = next((x for x in nomes_cat if x in linhas), None)
    return t, ln


def ficha_md(nome: str, tipos: list[str], linha: str | None, desc: str,
             destaques: list[str], slug: str, url: str) -> str:
    cab = f"**Tipo:** {', '.join(tipos) or '—'}"
    if linha:
        cab += f" · **Linha:** {linha}"
    # Categoria: rótulo derivado dos Tipos (no blob é texto livre; usamos os Tipos, unidos por " e ")
    cab += f" · **Categoria:** {' e '.join(tipos) or '—'}"
    dest = ", ".join(destaques[:3]) if destaques else "—"  # blob usa 3; ver spec
    return (
        f"#### {nome}\n{cab}\n{desc}\n"
        f"*Destaques:* {dest}\n*Slug:* `{slug}`\n*Link:* {url}\n"
    )


def main() -> int:
    catalog = json.loads((HERE / "catalogo_7ball.json").read_text(encoding="utf-8"))
    marcas = [sys.argv[1]] if len(sys.argv) > 1 else ["7ball", "reebok"]
    out: dict[str, list] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROMIUM, headless=True)
        for marca in marcas:
            tipos_set, linhas_set = blob_tipos_linhas(marca)
            desc_linha = linha_descricoes(marca)  # Reebok: descrição vem da linha
            done = blob_slugs(marca)
            cat_nome = {c["slug"]: c["name"] for c in catalog["marcas"][marca]["categorias"]}
            novos = [p for p in catalog["marcas"][marca]["produtos"] if p["slug"] not in done]
            print(f"\n== {marca}: {len(novos)} produtos novos ==", file=sys.stderr)
            fichas = []
            for p in novos:
                nomes_cat = [cat_nome.get(s, s) for s in p["categorias"]]
                tipos, linha = classify(nomes_cat, tipos_set, linhas_set)
                nome = p["title"]
                if marca == "reebok":
                    # descrição = da linha; destaques seguem o padrão do blob Reebok
                    desc = desc_linha.get(linha, "(sem descrição de linha)")
                    subtipo = tipos[0] if tipos else nome
                    destaques = [subtipo, "PEÇAS EM AÇO INOXIDÁVEL", f"Linha {linha}" if linha else "—"]
                else:
                    # 7ball: descrição + destaques únicos, do DOM
                    page = browser.new_page(user_agent="Mozilla/5.0 (7ball-sync/1.0)")
                    try:
                        page.goto(p["url"], wait_until="domcontentloaded", timeout=45_000)
                        page.wait_for_timeout(1500)
                        data = page.evaluate(EXTRACT)
                    except PWTimeout:
                        print(f"  ! timeout {p['slug']}", file=sys.stderr)
                        page.close()
                        continue
                    page.close()
                    desc = data["desc"] or "(sem descrição)"
                    destaques = data["destaques"][:3]
                md = ficha_md(nome, tipos, linha, desc, destaques, p["slug"], p["url"])
                fichas.append({
                    "slug": p["slug"], "nome": nome, "tipos": tipos, "linha": linha,
                    "desc": desc, "destaques": destaques, "url": p["url"], "ficha_md": md,
                })
                print(f"  ok {p['slug']:35s} tipo={tipos} linha={linha} dest={len(destaques)}",
                      file=sys.stderr)
            out[marca] = fichas
        browser.close()

    (HERE / "fichas_novos.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    total = sum(len(v) for v in out.values())
    print(f"\n{total} fichas -> fichas_novos.json", file=sys.stderr)
    # ponytail: avisa se alguma ficha ficou com descrição de fallback
    faltando = [f["slug"] for v in out.values() for f in v if "(sem descrição" in f["desc"]]
    if faltando:
        print(f"AVISO: sem descrição em {faltando}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
