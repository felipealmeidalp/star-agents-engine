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
        # excluded é indexado por NOME DE COLUNA (camelCase no banco), não pelo atributo
        # ORM; imageUrl/motherCategory divergem do snake_case, daí o acesso por coluna.
        excluded = stmt.excluded
        cols = Product.__table__.c
        stmt = stmt.on_conflict_do_update(
            constraint="uq_products_company_type_wp",
            set_={
                cols.title: excluded[cols.title.name],
                cols.description: excluded[cols.description.name],
                cols.url: excluded[cols.url.name],
                cols.imageUrl: excluded[cols.imageUrl.name],
                cols.motherCategory: excluded[cols.motherCategory.name],
                cols.motherCategorySlug: excluded[cols.motherCategorySlug.name],
                cols.updated_at: now,
            },
        )
        await self.db.execute(stmt)
        await self.db.commit()
