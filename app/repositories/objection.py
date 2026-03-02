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

    async def get_titles_by_agent(
        self,
        company_id: int,
        agent_id: int,
    ) -> list[dict[str, int | str]]:
        """
        Fetch all objection titles for a given agent.

        Args:
            company_id: Company ID for multi-tenancy
            agent_id: Agent ID

        Returns:
            List of dicts with id and title for each objection
        """
        result = await self.db.execute(
            select(Objection.id, Objection.title).where(
                Objection.company_id == company_id,
                Objection.agent_id == agent_id,
            )
        )
        return [{"id": row.id, "title": row.title or ""} for row in result.fetchall()]

    async def get_objection_by_id(self, objection_id: int) -> dict[str, str] | None:
        """
        Fetch objection title and script by its ID.

        Args:
            objection_id: Objection primary key

        Returns:
            Dict with title and script, or None if not found
        """
        result = await self.db.execute(
            select(Objection.title, Objection.script).where(
                Objection.id == objection_id
            )
        )
        row = result.first()
        if not row:
            return None
        return {"title": row.title or "", "script": row.script or ""}

    async def get_objections_by_ids(self, objection_ids: list[int]) -> list[dict[str, str]]:
        """
        Fetch objection titles and scripts by multiple IDs, preserving input order.

        Args:
            objection_ids: List of objection primary keys (ordered by relevance)

        Returns:
            List of dicts with title and script, in the same order as input IDs.
            IDs not found in the database are silently skipped.
        """
        if not objection_ids:
            return []
        result = await self.db.execute(
            select(Objection.id, Objection.title, Objection.script).where(
                Objection.id.in_(objection_ids)
            )
        )
        rows_by_id = {row.id: row for row in result.fetchall()}
        return [
            {"title": rows_by_id[oid].title or "", "script": rows_by_id[oid].script or ""}
            for oid in objection_ids
            if oid in rows_by_id
        ]
