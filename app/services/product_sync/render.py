"""Playwright (async) render of a product page → the fields the REST doesn't give:
long description and highlights, extracted from the DOM.

Selectors and extraction JS are verbatim from experimental-tools/render_novos.py.
Both brands render the same way (own per-product DOM description) — no Linha-derived
text, no per-brand branch.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from playwright.async_api import async_playwright

from app.services.product_sync.sync import RenderFn, RenderResult

logger = logging.getLogger(__name__)

# Description = first .elementor-widget-text-editor with real text (>60 chars).
# Highlights = items of the first .jet-listing-dynamic-repeater__items.
_EXTRACT = r"""() => {
  const desc = [...document.querySelectorAll('.elementor-widget-text-editor')]
    .map(e => e.textContent.trim()).filter(t => t.length > 60)[0] || null;
  const rep = document.querySelector('.jet-listing-dynamic-repeater__items');
  const highlights = rep
    ? [...rep.querySelectorAll('.jet-listing-dynamic-repeater__item')]
        .map(e => e.textContent.trim())
    : [];
  return { description: desc, highlights };
}"""


@asynccontextmanager
async def product_renderer() -> AsyncIterator[RenderFn]:
    """Launch Chromium once, yield an async render(url, slug) bound to it.

    run_sync takes a plain render callable; this keeps the browser alive across the
    whole sync instead of relaunching per product.
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        async def render(url: str, slug: str) -> RenderResult:
            page = await browser.new_page(user_agent="Mozilla/5.0 (7ball-sync/1.0)")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                await page.wait_for_timeout(1500)
                data = await page.evaluate(_EXTRACT)
            finally:
                await page.close()
            return {
                "description": data.get("description") or "",
                "highlights": data.get("highlights") or [],
            }

        try:
            yield render
        finally:
            await browser.close()
