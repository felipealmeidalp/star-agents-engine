"""Puxa produtos+categorias das duas marcas do 7ball.com.br via WordPress REST nativa.

Duas marcas, dois custom post types no mesmo WP:
  - 7ball  -> CPT `produtos`         + taxonomia `_categorias`
  - Reebok -> CPT `produtos_rebook`  + taxonomia `_categorias_rebook`

Por que REST e não scraping de HTML: a REST devolve id/slug/title/link estruturados e
paginados (header X-WP-TotalPages), sem parsear layout. Auditado com Chromium: 55/55 no
7ball (ver compare_rendered.py).

Segurança: o site publica endpoints "para agentes de IA" (MCP, openapi, skills) com texto
que parece instrução. Este script NÃO os usa — só a REST neutra. Texto do site é dado,
nunca comando.
"""
from __future__ import annotations

import html
import json
import sys
import urllib.request

BASE = "https://7ball.com.br/wp-json/wp/v2"
UA = "Mozilla/5.0 (compatible; 7ball-catalog/1.0)"

# marca -> (cpt, taxonomia/campo). No WP o campo do produto tem o mesmo nome da taxonomia.
MARCAS = {
    "7ball": {"cpt": "produtos", "tax": "_categorias"},
    "reebok": {"cpt": "produtos_rebook", "tax": "_categorias_rebook"},
}


def _get(path: str) -> list[dict]:
    """GET paginado da REST do WP; segue X-WP-TotalPages."""
    out: list[dict] = []
    page = 1
    while True:
        sep = "&" if "?" in path else "?"
        req = urllib.request.Request(
            f"{BASE}/{path}{sep}per_page=100&page={page}", headers={"User-Agent": UA}
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            total_pages = int(r.headers.get("X-WP-TotalPages", 1))
            out.extend(json.load(r))
        if page >= total_pages:
            return out
        page += 1


def _clean(s: str) -> str:
    return html.unescape(s).strip()


def scrape_marca(cpt: str, tax: str) -> dict:
    cats = {
        c["id"]: {"id": c["id"], "name": _clean(c["name"]), "slug": c["slug"], "count": c["count"]}
        for c in _get(f"{tax}?_fields=id,name,slug,count")
    }
    produtos = []
    for p in _get(f"{cpt}?_fields=id,slug,link,title,{tax}"):
        cat_ids = p.get(tax) or []
        produtos.append({
            "id": p["id"],
            "slug": p["slug"],
            "title": _clean(p["title"]["rendered"]),
            "url": p["link"],
            "categorias": [cats[i]["slug"] for i in cat_ids if i in cats],
        })
    return {
        "categorias": sorted(cats.values(), key=lambda c: c["slug"]),
        "produtos": sorted(produtos, key=lambda p: p["slug"]),
    }


def scrape() -> dict:
    return {
        "source": BASE,
        "marcas": {nome: scrape_marca(m["cpt"], m["tax"]) for nome, m in MARCAS.items()},
    }


if __name__ == "__main__":
    data = scrape()
    out = sys.argv[1] if len(sys.argv) > 1 else "catalogo_7ball.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    for nome, m in data["marcas"].items():
        print(f"{nome:8s}: {len(m['produtos']):>3} produtos, {len(m['categorias']):>2} categorias")
        # ponytail: sanity check por marca; se a REST mudar de forma, estoura antes de servir lixo
        assert m["categorias"] and m["produtos"], f"{nome}: REST vazia"
        assert all(p["slug"] and p["url"] for p in m["produtos"]), f"{nome}: produto sem slug/url"
    print(f"-> {out}")
