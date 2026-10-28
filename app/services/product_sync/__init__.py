"""Product sync: WP REST pull + headless render → `products` table, one seam.

Tickets 03 (endpoint) and 04 (cron) import from here:
  - run_sync(source, render, repo, mode) — the single entry point
  - try_acquire() — non-blocking lock probe (False = a sync is running)
  - SyncInProgressError — run_sync raises this when the lock is held

The production source and render live in the submodules `rest.fetch_catalog` and
`render.product_renderer`; they aren't re-exported here so importing the seam (and
testing it) never drags in httpx/playwright.
"""

from app.services.product_sync.scheduler import start_weekly_sync
from app.services.product_sync.sync import (
    PulledProduct,
    SyncInProgressError,
    SyncMode,
    release,
    run_sync,
    try_acquire,
)

__all__ = [
    "run_sync",
    "try_acquire",
    "release",
    "SyncMode",
    "SyncInProgressError",
    "PulledProduct",
    "start_weekly_sync",
]
