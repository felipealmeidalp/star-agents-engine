"""Repository for objection operations."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import Objection


class ObjectionRepository:
    """Data access layer for objections table."""

    def __init__(self, db: AsyncSession) -> None:
        """Initialize repository with database session."""
        self.db = db

    async def get_script(
        self,
        company_id: int,
        agent_id: int,
    ) -> str | None:
        """
        Fetch objection script by company and agent.

        Args:
            company_id: Company ID for multi-tenancy
            agent_id: Agent ID

        Returns:
            Script content or None if not found
        """
        result = await self.db.execute(
            select(Objection.script).where(
                Objection.company_id == company_id,
                Objection.agent_id == agent_id,
            )
        )
        return result.scalar_one_or_none()
