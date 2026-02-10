"""Repository for agent-related database operations."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class AgentRepository:
    """Data access layer for agent context retrieval."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_full_context(self, session_id: str, company_id: int) -> dict[str, Any]:
        """
        Fetch complete agent context in a single optimized query.

        Returns JSON with: customer, agent, sub_agent, steps, decision_rules, tools

        Args:
            session_id: The session identifier
            company_id: Company ID for multi-tenancy

        Returns:
            dict with full agent context

        Raises:
            ValueError: If session not found for the given company
        """
        query = text("""
            SELECT jsonb_build_object(
                'customer', to_jsonb(c),
                'agent', to_jsonb(a),
                'sub_agent', to_jsonb(sa),
                'steps', (
                    SELECT COALESCE(jsonb_agg(s ORDER BY s.relative_id), '[]'::jsonb)
                    FROM steps s
                    WHERE s.sub_agent_id = c.sub_agent_id
                      AND s.deleted_at IS NULL
                ),
                'decision_rules', (
                    SELECT COALESCE(
                        jsonb_agg(
                            jsonb_build_object(
                                'decision_rule', to_jsonb(dr),
                                'sub_agent_connections', (
                                    SELECT COALESCE(jsonb_agg(sac), '[]'::jsonb)
                                    FROM sub_agent_connections sac
                                    WHERE sac.decision_rule_id = dr.id
                                )
                            )
                            ORDER BY dr.relative_id
                        ),
                        '[]'::jsonb
                    )
                    FROM decision_rules dr
                    WHERE dr.sub_agent_id = c.sub_agent_id
                      AND dr.deleted_at IS NULL
                ),
                'tools', (
                    SELECT COALESCE(
                        jsonb_agg(
                            jsonb_build_object(
                                'tool', to_jsonb(t),
                                'parameters', (
                                    SELECT COALESCE(jsonb_agg(tp), '[]'::jsonb)
                                    FROM tool_parameters tp
                                    WHERE tp.tool_id = t.id
                                )
                            )
                        ),
                        '[]'::jsonb
                    )
                    FROM tools t
                    WHERE t.title = ANY(sa.tools)
                )
            ) AS full_context
            FROM customers c
            JOIN agents a ON a.id = c.agent_id
            JOIN sub_agents sa ON sa.id = c.sub_agent_id
            WHERE c."sessionId" = :session_id
              AND c.company_id = :company_id
              AND c.deleted_at IS NULL
        """)

        result = await self.db.execute(
            query, {"session_id": session_id, "company_id": company_id}
        )
        row = result.fetchone()

        if not row or not row.full_context:
            raise ValueError(
                f"Session '{session_id}' not found for company {company_id}"
            )

        return row.full_context

    async def list_agents_by_company(self, company_id: int) -> list[dict[str, Any]]:
        """
        List active agents for a company, ordered by name.

        Args:
            company_id: Company ID for multi-tenancy

        Returns:
            List of dicts with id and name for each agent
        """
        # Note: DB uses 'title' column, ORM maps it to 'name'
        query = text("""
            SELECT id, title
            FROM agents
            WHERE company_id = :company_id
              AND deleted_at IS NULL
            ORDER BY title ASC
        """)
        result = await self.db.execute(query, {"company_id": company_id})
        return [{"id": r.id, "name": r.title} for r in result.fetchall()]
