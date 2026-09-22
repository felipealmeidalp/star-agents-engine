"""Renderiza o 7ball com Chromium real (JS executado) e compara os produtos visíveis
com os 55 que a REST devolve. Objetivo: descobrir se algum produto aparece no site mas
não vem pela REST (ou vice-versa).

Fonte da verdade REST: catalogo_7ball.json (gerado por scrape_7ball.py).
Estratégia de render: abre /produtos/, rola até o fim (lazy-load), coleta todo href que
casa /produtos/<slug>/.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

CHROMIUM = str(next(Path.home().glob(
    "Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/*.app/Contents/MacOS/*"
)))
SLUG_RE = re.compile(r"^https?://7ball\.com\.br/produtos/([a-z0-9][a-z0-9-]*)/?$")
# segmentos de URL que são paginação, não produto (capas É produto real, id 3026):
NOT_PRODUCT = {"", "page"}


def rest_slugs(catalog: Path) -> set[str]:
    data = json.loads(catalog.read_text(encoding="utf-8"))
    return {p["slug"] for p in data["produtos"]}


def rendered_slugs(base: str) -> set[str]:
    """Segue a paginação /produtos/page/N/ até uma página não trazer produto novo."""
    found: set[str] = set()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROMIUM, headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (7ball-render/1.0)")
        for n in range(1, 30):  # ponytail: teto de 30 páginas; 55 produtos / ~9 por página cabe folgado
            url = base if n == 1 else f"{base.rstrip('/')}/page/{n}/"
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            except PWTimeout:
                print(f"  (timeout em {url}, parando)", file=sys.stderr)
                break
            if resp and resp.status == 404:
                break
            page.wait_for_timeout(800)  # deixa o grid pintar
            before = len(found)
            for h in page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)"):
                m = SLUG_RE.match(h)
                if m and m.group(1) not in NOT_PRODUCT:
                    found.add(m.group(1))
            if len(found) == before:  # página sem produto novo = fim
                break
        browser.close()
    return found


def main() -> int:
    here = Path(__file__).parent
    catalog = here / "catalogo_7ball.json"
    rest = rest_slugs(catalog)
    url = sys.argv[1] if len(sys.argv) > 1 else "https://7ball.com.br/produtos/"
    rendered = rendered_slugs(url)

    only_rest = sorted(rest - rendered)
    only_rendered = sorted(rendered - rest)
    both = rest & rendered

    print(f"REST: {len(rest)}  |  Renderizado: {len(rendered)}  |  Em ambos: {len(both)}")
    print(f"\nSó na REST (não apareceram no site renderizado) [{len(only_rest)}]:")
    print("  " + ("\n  ".join(only_rest) or "— nenhum —"))
    print(f"\nSó no site renderizado (não vêm pela REST) [{len(only_rendered)}]:")
    print("  " + ("\n  ".join(only_rendered) or "— nenhum —"))

    json.dump(
        {"rest": sorted(rest), "rendered": sorted(rendered),
         "only_rest": only_rest, "only_rendered": only_rendered},
        (here / "compare_result.json").open("w", encoding="utf-8"),
        ensure_ascii=False, indent=2,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
