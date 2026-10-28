"""Core seam of the product sync: pulls the catalog through a REST source, renders
what's missing (or everything, in full mode), and persists via a repository.

Trigger-agnostic — cron (ticket 04), the manual endpoint (ticket 03) and a local
`python -m` run all call run_sync with the same source/render/repo. Mode is a
parameter; there is no duplicated code between additive and full re-render.

Lock contract for 03/04: call run_sync and catch SyncInProgressError — 03 maps it to
HTTP 409, 04 skips the round. run_sync acquires the lock itself and releases it in
finally (even on exception). try_acquire()/release() are the same primitive exposed
raw for a caller that wants to gate *before* entering run_sync; use one path or the
other, never both (that would double-acquire and deadlock).
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from app.utils.alerter import send_critical_alert

logger = logging.getLogger(__name__)

Taxonomy = dict[int, dict[str, Any]]  # term_id -> {name, slug, count}
Row = dict[str, Any]
RenderResult = dict[str, Any]  # {"description": str, "highlights": list}
RenderFn = Callable[[str, str], Awaitable[RenderResult]]  # (url, slug) -> result
# () -> list of (products.type, [PulledProduct], taxonomy_map) per brand
SourceFn = Callable[[], Awaitable[list[tuple[str, "list[PulledProduct]", Taxonomy]]]]


class ProductStore(Protocol):
    """The persistence contract run_sync needs — ProductRepository, or a test fake."""

    async def list_existing_slugs(self, type_: str, company_id: int) -> set[str]: ...
    async def upsert(self, rows: list[Row]) -> None: ...

COMPANY_ID = 5  # 7ball; the sync is not multi-tenant this phase
NO_DESCRIPTION_FALLBACK = "(descrição pendente — produto sem texto renderizado)"

# A URL pública de uma categoria não é 7ball.com.br/{slug} (dá 404) — leva prefixo
# por marca. O slug gravado já vem com o prefixo pra o CRM montar 7ball.com.br/{slug}.
CATEGORY_URL_PREFIX = {"sinuca": "_categoria", "fitness": "categorias-reebok"}

# ponytail: global in-memory lock — 1 worker/1 replica; Redis lock only if it scales.
# A plain bool is race-free on a single event loop: check-and-set never awaits between them.
_running = False


def try_acquire() -> bool:
    """Non-blocking acquire. False when a sync is already running (03 → 409, 04 → skip)."""
    global _running
    if _running:
        return False
    _running = True
    return True


def release() -> None:
    """Release the lock. Paired with a manual try_acquire(); run_sync releases itself."""
    global _running
    _running = False


class SyncMode(str, Enum):
    ADDITIVE = "additive"  # weekly/manual default: render+insert only missing slugs
    FULL = "full"  # local only: render everything and UPSERT


@dataclass
class PulledProduct:
    """One product as the REST source yields it (taxonomy still unresolved term IDs)."""

    wp_id: int
    slug: str
    title: str
    url: str
    type: str
    term_ids: list[int] = field(default_factory=list)


def resolve_mother_category(
    term_ids: list[int], taxonomy: Taxonomy
) -> tuple[str | None, str | None]:
    """Pick the taxonomy term with the highest count, ignoring count-0 phantoms.

    Reebok has duplicates (acessorios_/27 vs acessorios/0); the highest-count rule
    handles it and is a no-op on the clean 7ball taxonomy.
    """
    terms = [taxonomy[i] for i in term_ids if i in taxonomy and taxonomy[i].get("count", 0) > 0]
    if not terms:
        return None, None
    best = max(terms, key=lambda t: t["count"])
    return best["name"], best["slug"]


async def _row_for(product: PulledProduct, render: RenderFn, taxonomy: Taxonomy) -> Row | None:
    """Render one product and build its row. None → skip (render failed)."""
    try:
        rendered = await render(product.url, product.slug)
    except Exception as err:
        logger.warning("[ProductSync] render failed for %s: %s", product.slug, err)
        send_critical_alert(
            "PRODUCT_RENDER_FAILED",
            "services/product_sync/sync.py:_row_for",
            err,
            company_id=COMPANY_ID,
            extra=product.slug,
        )
        return None

    description = (rendered.get("description") or "").strip()
    if not description:
        description = NO_DESCRIPTION_FALLBACK
        send_critical_alert(
            "PRODUCT_NO_DESCRIPTION",
            "services/product_sync/sync.py:_row_for",
            "rendered with empty description",
            company_id=COMPANY_ID,
            extra=product.slug,
        )

    mother_name, mother_slug = resolve_mother_category(product.term_ids, taxonomy)
    if mother_slug:
        mother_slug = f"{CATEGORY_URL_PREFIX[product.type]}/{mother_slug}"
    return {
        "company_id": COMPANY_ID,
        "wp_id": product.wp_id,
        "title": product.title,
        "slug": product.slug,
        "description": description,
        "image_url": None,  # out of scope this ticket; column tolerates NULL
        "url": product.url,
        "mother_category": mother_name,
        "mother_category_slug": mother_slug,
        "is_mother_category": False,
        "type": product.type,
    }


async def run_sync(
    *,
    source: SourceFn,
    render: RenderFn,
    repo: ProductStore,
    mode: SyncMode = SyncMode.ADDITIVE,
) -> int:
    """Reconcile the site catalog into `products`; returns how many rows were written."""
    if not try_acquire():
        raise SyncInProgressError()
    try:
        brands = await source()
        written = 0
        for type_, products, taxonomy in brands:
            if mode is SyncMode.ADDITIVE:
                existing = await repo.list_existing_slugs(type_, COMPANY_ID)
                targets = [p for p in products if p.slug not in existing]
            else:
                targets = list(products)

            rows = []
            for product in targets:
                row = await _row_for(product, render, taxonomy)
                if row is not None:
                    rows.append(row)

            if rows:
                await repo.upsert(rows)
                written += len(rows)
        return written
    finally:
        release()


class SyncInProgressError(RuntimeError):
    """Raised by run_sync when another sync already holds the lock."""

    def __init__(self) -> None:
        super().__init__("a product sync is already running")
