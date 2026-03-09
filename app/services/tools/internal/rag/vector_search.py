"""Service for vector search using pgvector via match_chunks() function."""

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class VectorSearchService:
    """Service for vector similarity search using pgvector in Supabase."""

    def __init__(self, db: AsyncSession) -> None:
        """
        Initialize vector search service with database session.

        Args:
            db: Async SQLAlchemy session
        """
        self.db = db

    async def search(
        self,
        embedding: list[float],
        company_id: int,
        agent_id: int,
        sub_agent_id: int,
        match_count: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Search for similar chunks using the match_chunks() database function.

        Args:
            embedding: Query embedding vector
            company_id: Company ID for filtering
            agent_id: Agent ID for RAG base access control
            sub_agent_id: Sub-agent ID for RAG base access control
            match_count: Maximum number of results to return

        Returns:
            List of dicts with {id, base_id, question, answer, related_terms, similarity}
        """
        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

        result = await self.db.execute(
            text(
                "SELECT * FROM match_chunks("
                "CAST(:query_embedding AS vector), :company_id, "
                ":agent_id, :sub_agent_id, :match_count"
                ")"
            ),
            {
                "query_embedding": embedding_str,
                "company_id": company_id,
                "agent_id": agent_id,
                "sub_agent_id": sub_agent_id,
                "match_count": match_count,
            },
        )

        rows = result.mappings().all()
        results = [dict(row) for row in rows]

        logger.info(
            "match_chunks returned %d results for company=%s agent=%s sub_agent=%s",
            len(results), company_id, agent_id, sub_agent_id,
        )

        return results
