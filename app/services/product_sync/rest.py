"""WordPress REST pull for both 7ball brands (httpx async).

Structure only: id/slug/title/link + taxonomy term IDs, paginated by X-WP-TotalPages.
Description/highlights are NOT here — the REST returns content/acf/meta empty; those
come from the render. Neutral WP REST only; the site's "for-AI" endpoints are ignored
and every string it returns is treated as data, never instruction.
"""

import html
import logging
from typing import Any

import httpx

from app.services.product_sync.sync import PulledProduct, Taxonomy

logger = logging.getLogger(__name__)

BASE = "https://7ball.com.br/wp-json/wp/v2"
UA = "Mozilla/5.0 (compatible; 7ball-catalog/1.0)"

# (products.type, CPT endpoint, taxonomy field — same name as the product field in WP)
BRANDS = [
    ("sinuca", "produtos", "_categorias"),
    ("fitness", "produtos_rebook", "_categorias_rebook"),
]


def _clean(s: str) -> str:
    return html.unescape(s).strip()


async def _get_paginated(client: httpx.AsyncClient, path: str) -> list[dict[str, Any]]:
    """GET every page of a WP REST collection, following X-WP-TotalPages."""
    out: list[dict[str, Any]] = []
    page = 1
    while True:
        sep = "&" if "?" in path else "?"
        resp = await client.get(f"{BASE}/{path}{sep}per_page=100&page={page}")
        resp.raise_for_status()
        out.extend(resp.json())
        if page >= int(resp.headers.get("X-WP-TotalPages", 1)):
            return out
        page += 1


async def fetch_catalog() -> list[tuple[str, list[PulledProduct], Taxonomy]]:
    """Pull both brands. Returns [(type, [PulledProduct], taxonomy_map), ...].

    The tuple shape is exactly what run_sync's `source` parameter consumes.
    """
    brands: list[tuple[str, list[PulledProduct], Taxonomy]] = []
    async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": UA}) as client:
        for type_, cpt, tax in BRANDS:
            taxonomy: Taxonomy = {
                c["id"]: {"name": _clean(c["name"]), "slug": c["slug"], "count": c["count"]}
                for c in await _get_paginated(client, f"{tax}?_fields=id,name,slug,count")
            }
            raw = await _get_paginated(
                client, f"{cpt}?_fields=id,slug,link,title,{tax}"
            )
            products = [
                PulledProduct(
                    wp_id=p["id"],
                    slug=p["slug"],
                    title=_clean(p["title"]["rendered"]),
                    url=p["link"],  # public URL — never built by hand
                    type=type_,
                    term_ids=list(p.get(tax) or []),
                )
                for p in raw
            ]
            logger.info(
                "[ProductSync] REST %s: %d products, %d terms",
                type_, len(products), len(taxonomy),
            )
            brands.append((type_, products, taxonomy))
    return brands
