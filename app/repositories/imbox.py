"""Repository for imbox operations."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import Imbox


class ImboxRepository:
    """Data access layer for imboxes table."""

    def __init__(self, db: AsyncSession) -> None:
        """Initialize repository with database session."""
        self.db = db

    async def get_by_whatsapp(self, whatsapp: str) -> Imbox | None:
        """
        Find imbox by WhatsApp number.

        Args:
            whatsapp: WhatsApp phone number

        Returns:
            Imbox or None if not found
        """
        result = await self.db.execute(
            select(Imbox).where(Imbox.whatsapp == whatsapp)
        )
        return result.scalar_one_or_none()
