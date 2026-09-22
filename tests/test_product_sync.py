"""Behavioral tests for the product sync seam (app.services.product_sync.run_sync).

One seam, driven with a fake REST source and a fake render — no browser, no live
DB. Assertions are on what got persisted / what was refused, never on internal
calls. House style of tests/test_categories_tool.py: self-contained
asyncio.run(main()) with plain asserts, no pytest/fixtures/new deps.

The five cases (ticket_02 §Tests):
  (a) additive inserts ONLY slugs absent from products
  (b) running twice does NOT duplicate (idempotent by slug)
  (c) the lock refuses a second concurrent sync
  (d) Reebok category resolution picks the highest-count term (27 beats 0)
  (e) a render failure for ONE product doesn't interrupt the others
"""

import asyncio
import os
import sys

sys.path.insert(0, ".")

# app.config.Settings needs these at import time; placeholders, no DB connection.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres",
)
os.environ.setdefault("API_KEY", "test-key")

from app.services.product_sync import (  # noqa: E402
    PulledProduct,
    SyncInProgressError,
    SyncMode,
    run_sync,
)


class FakeRepo:
    """In-memory stand-in for ProductRepository — the DB system boundary.

    Keyed by (type, slug), so a re-run of the same slug overwrites instead of
    appending — exactly the DB unique-constraint behaviour we assert against.
    """

    def __init__(self, existing: dict[tuple[str, str], dict] | None = None) -> None:
        self.rows: dict[tuple[str, str], dict] = dict(existing or {})

    async def list_existing_slugs(self, type_: str, company_id: int = 5) -> set[str]:
        return {slug for (t, slug) in self.rows if t == type_}

    async def upsert(self, rows: list[dict]) -> None:
        for r in rows:
            self.rows[(r["type"], r["slug"])] = r


def pp(
    wp_id: int, slug: str, type_: str = "sinuca", term_ids: list[int] | None = None
) -> PulledProduct:
    """Compact PulledProduct for the tests (title/url derived from slug)."""
    return PulledProduct(
        wp_id=wp_id, slug=slug, title=slug.upper(), url=f"u/{slug}",
        type=type_, term_ids=term_ids or [],
    )


def brand(type_: str, products: list[PulledProduct], taxonomy: dict[int, dict]):
    """One brand tuple as run_sync's source yields it."""
    return (type_, products, taxonomy)


def fake_source(brands: list):
    async def _source():
        return brands

    return _source


def fake_render(descriptions: dict[str, str], fail_slugs: set[str] | None = None):
    """Render stub keyed by slug. Raises for slugs in fail_slugs."""
    fail = fail_slugs or set()

    async def _render(url: str, slug: str):
        if slug in fail:
            raise RuntimeError(f"render boom {slug}")
        return {"description": descriptions.get(slug, ""), "highlights": []}

    return _render


async def case_a_additive_inserts_only_missing() -> None:
    repo = FakeRepo(existing={("sinuca", "paris"): {"type": "sinuca", "slug": "paris"}})
    products = [pp(1, "paris"), pp(2, "oslo")]
    source = fake_source([brand("sinuca", products, {})])
    render = fake_render({"oslo": "mesa oslo linda", "paris": "nao deveria renderizar"})

    await run_sync(source=source, render=render, repo=repo, mode=SyncMode.ADDITIVE)

    assert ("sinuca", "oslo") in repo.rows, "oslo (missing) should be inserted"
    # paris pre-existed and additive must not have re-rendered/overwritten it
    assert repo.rows[("sinuca", "paris")]["type"] == "sinuca"
    assert "description" not in repo.rows[("sinuca", "paris")], "additive touched an existing row"
    print("  (a) additive inserts only missing slugs: OK")


async def case_b_twice_no_duplicate() -> None:
    repo = FakeRepo()
    products = [pp(2, "oslo")]
    source = fake_source([brand("sinuca", products, {})])
    render = fake_render({"oslo": "mesa oslo linda"})

    await run_sync(source=source, render=render, repo=repo, mode=SyncMode.ADDITIVE)
    first = dict(repo.rows)
    await run_sync(source=source, render=render, repo=repo, mode=SyncMode.ADDITIVE)

    assert len(repo.rows) == 1, f"second run duplicated: {repo.rows}"
    assert repo.rows.keys() == first.keys()
    print("  (b) running twice does not duplicate: OK")


async def case_c_lock_refuses_second() -> None:
    repo = FakeRepo()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_render(url: str, slug: str):
        started.set()
        await release.wait()
        return {"description": "x" * 70, "highlights": []}

    products = [pp(2, "oslo")]
    source = fake_source([brand("sinuca", products, {})])

    first = asyncio.create_task(
        run_sync(source=source, render=slow_render, repo=repo, mode=SyncMode.ADDITIVE)
    )
    await started.wait()  # first sync is mid-flight, holding the lock

    refused = False
    try:
        await run_sync(source=source, render=slow_render, repo=repo, mode=SyncMode.ADDITIVE)
    except SyncInProgressError:
        refused = True
    assert refused, "a second sync must be refused while one is running"

    release.set()
    await first
    # lock released on completion: a fresh sync now runs without refusal
    await run_sync(source=fake_source([]), render=slow_render, repo=repo, mode=SyncMode.ADDITIVE)
    print("  (c) lock refuses a concurrent second sync: OK")


async def case_d_reebok_highest_count_wins() -> None:
    repo = FakeRepo()
    taxonomy = {
        # duplicate: acessorios_ has the real 27 items, acessorios is the phantom 0
        10: {"name": "Acessórios", "slug": "acessorios_", "count": 27},
        11: {"name": "Acessórios.", "slug": "acessorios", "count": 0},
    }
    # phantom (11) listed first on purpose; the count-27 term (10) must still win
    products = [pp(3, "halter", type_="fitness", term_ids=[11, 10])]
    source = fake_source([brand("fitness", products, taxonomy)])
    render = fake_render({"halter": "halter de aço" * 10})

    await run_sync(source=source, render=render, repo=repo, mode=SyncMode.ADDITIVE)

    row = repo.rows[("fitness", "halter")]
    assert row["mother_category"] == "Acessórios", row
    assert row["mother_category_slug"] == "acessorios_", row
    print("  (d) reebok category picks highest-count term: OK")


async def case_e_one_render_failure_isolated() -> None:
    repo = FakeRepo()
    products = [pp(1, "oslo"), pp(2, "paris"), pp(3, "tokyo")]
    source = fake_source([brand("sinuca", products, {})])
    render = fake_render(
        {"oslo": "mesa oslo" * 10, "tokyo": "mesa tokyo" * 10},
        fail_slugs={"paris"},
    )

    await run_sync(source=source, render=render, repo=repo, mode=SyncMode.ADDITIVE)

    assert ("sinuca", "oslo") in repo.rows
    assert ("sinuca", "tokyo") in repo.rows
    assert ("sinuca", "paris") not in repo.rows, "failed render must not be written"
    print("  (e) one render failure does not abort the rest: OK")


async def case_f_empty_description_fallback() -> None:
    repo = FakeRepo()
    source = fake_source([brand("sinuca", [pp(9, "novo")], {})])
    render = fake_render({"novo": ""})  # rendered but no description

    await run_sync(source=source, render=render, repo=repo, mode=SyncMode.ADDITIVE)

    row = repo.rows[("sinuca", "novo")]
    assert row["description"], "empty render must get a fallback, product still written"
    print("  (f) empty description → fallback, product still written: OK")


async def main() -> None:
    print("Product sync behavioral tests (seam: run_sync)")
    await case_a_additive_inserts_only_missing()
    await case_b_twice_no_duplicate()
    await case_c_lock_refuses_second()
    await case_d_reebok_highest_count_wins()
    await case_e_one_render_failure_isolated()
    await case_f_empty_description_fallback()
    print("\nOK - all product sync cases passed")


if __name__ == "__main__":
    asyncio.run(main())
