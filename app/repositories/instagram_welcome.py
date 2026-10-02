"""Repository for the Instagram welcome records (instagram_welcomes)."""

from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import InstagramWelcome


class InstagramWelcomeRepository:
    """Data access layer for the instagram_welcomes table."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def reserve(
        self,
        company_id: int,
        comment_id: str,
        media_id: str,
        commenter_id: str,
        commenter_username: str | None,
        comment_text: str | None,
    ) -> int | None:
        """Take the turn for (Company, post, commenter); None when it is already taken."""
        stmt = (
            insert(InstagramWelcome)
            .values(
                company_id=company_id,
                comment_id=comment_id,
                media_id=media_id,
                commenter_id=commenter_id,
                commenter_username=commenter_username,
                comment_text=comment_text,
                status="em_processamento",
            )
            .on_conflict_do_nothing(
                index_elements=["company_id", "media_id", "commenter_id"],
                index_where=text("status <> 'falhou'"),
            )
            .returning(InstagramWelcome.id)
        )
        welcome_id = (await self.db.execute(stmt)).scalar_one_or_none()
        await self.db.commit()
        return welcome_id

    async def mark_pending(
        self,
        welcome_id: int,
        caption: str,
        product: str | None,
        dm_text: str,
        recipient_id: str | None,
    ) -> None:
        await self.db.execute(
            update(InstagramWelcome)
            .where(InstagramWelcome.id == welcome_id)
            .values(
                caption=caption,
                product=product,
                dm_text=dm_text,
                recipient_id=recipient_id,
                status="pendente",
            )
        )
        await self.db.commit()

    async def mark_failed(self, welcome_id: int) -> None:
        await self.db.execute(
            update(InstagramWelcome)
            .where(InstagramWelcome.id == welcome_id)
            .values(status="falhou")
        )
        await self.db.commit()
