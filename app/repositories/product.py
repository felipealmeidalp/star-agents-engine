"""Repository for the products catalog (7ball/Reebok sync)."""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import Product

logger = logging.getLogger(__name__)


class ProductRepository:
    """Data access layer for the products table."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_existing_slugs(self, type_: str, company_id: int) -> set[str]:
        """Slugs already stored for a (company_id, type) — the additive diff input."""
        result = await self.db.execute(
            select(Product.slug).where(
                Product.company_id == company_id,
                Product.type == type_,
                # exclui linhas isMotherCategory=true (outro ticket) que possam repetir slug
                Product.is_mother_category.is_(False),
            )
        )
        return set(result.scalars().all())

    async def upsert(self, rows: list[dict[str, Any]]) -> None:
        """UPSERT product rows on uq_products_company_type_wp, bumping updated_at.

        Additive mode only ever hits new rows; full re-render updates existing ones.
        One write path either way.
        """
        if not rows:
            return
        now = datetime.now(UTC)
        values = [{**r, "updated_at": now} for r in rows]
        stmt = insert(Product).values(values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_products_company_type_wp",
            set_={
                "title": stmt.excluded.title,
                "description": stmt.excluded.description,
                "url": stmt.excluded.url,
                "image_url": stmt.excluded.image_url,
                "mother_category": stmt.excluded.mother_category,
                "mother_category_slug": stmt.excluded.mother_category_slug,
                "updated_at": now,
            },
        )
        await self.db.execute(stmt)
        await self.db.commit()
