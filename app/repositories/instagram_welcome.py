"""Repository for the Instagram welcome records (instagram_welcomes)."""

from datetime import UTC, datetime

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import ChatHistory, Customer, InstagramWelcome


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

    async def find_pending_by_commenter_suffix(
        self, company_id: int, commenter_id_suffix: str
    ) -> InstagramWelcome | None:
        """Most recent Boas-vindas pendente of the person; the row stays locked until commit."""
        stmt = (
            select(InstagramWelcome)
            .where(
                InstagramWelcome.company_id == company_id,
                InstagramWelcome.status == "pendente",
                InstagramWelcome.commenter_id.endswith(commenter_id_suffix),
            )
            .order_by(InstagramWelcome.created_at.desc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def match(
        self, welcome: InstagramWelcome, customer: Customer, *, write_dm: bool = True
    ) -> None:
        """Seed the Customer's conversation from the welcome and mark it casada, in one commit."""
        if write_dm:
            self.db.add(
                ChatHistory(
                    sessionId=customer.sessionId,
                    role="assistant",
                    content=welcome.dm_text,
                    agent_id=customer.agent_id,
                    sub_agent_id=customer.sub_agent_id,
                    company_id=customer.company_id,
                    isHuman=False,
                )
            )
        origem = {
            "legenda_do_post": welcome.caption or "",
            "comentario": welcome.comment_text or "",
        }
        if welcome.product is not None:
            origem["produto"] = welcome.product
        # Reassigned, not mutated: SQLAlchemy does not track in-place changes to a JSON column.
        customer.customer_context = {
            **(customer.customer_context or {}),
            "origem_comentario_instagram": origem,
        }
        welcome.status = "casada"
        welcome.session_id = customer.sessionId
        welcome.matched_at = datetime.now(UTC)
        await self.db.commit()
