"""Repository for tool-related database operations."""

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class ToolRepository:
    """Data access layer for external tool configuration retrieval."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_external_tool_config(
        self,
        tool_name: str,
        company_id: int,
    ) -> dict[str, Any] | None:
        """
        Fetch external tool configuration with parameters.

        Args:
            tool_name: The tool title/name
            company_id: Company ID for multi-tenancy

        Returns:
            dict with tool config (id, title, method, endpoint, parameters)
            or None if not found
        """
        logger.info(f"[ToolRepository] Buscando tool: name={tool_name}, company_id={company_id}")
        query = text("""
            SELECT jsonb_build_object(
                'id', t.id,
                'title', t.title,
                'method', t.method,
                'endpoint', t.endpoint,
                'parameters', COALESCE(
                    jsonb_agg(
                        jsonb_build_object(
                            'name', tp.name,
                            'type', tp.type,
                            'array_type', tp.array_type,
                            'value', tp.value,
                            'source', tp.source,
                            'location', tp.location,
                            'mandatory', tp.mandatory
                        )
                    ) FILTER (WHERE tp.id IS NOT NULL),
                    '[]'::jsonb
                )
            ) AS tool_config
            FROM tools t
            LEFT JOIN tool_parameters tp ON tp.tool_id = t.id
            WHERE t.title = :tool_name
              AND t.company_id = :company_id
            GROUP BY t.id, t.title, t.method, t.endpoint
        """)

        try:
            result = await self.db.execute(
                query, {"tool_name": tool_name, "company_id": company_id}
            )
            row = result.fetchone()

            if not row or not row.tool_config:
                logger.warning(f"[ToolRepository] Tool não encontrada: {tool_name}")
                return None

            logger.info(f"[ToolRepository] Tool encontrada: id={row.tool_config.get('id')}")
            logger.debug(f"[ToolRepository] Config completo: {row.tool_config}")
            return row.tool_config
        except Exception as e:
            logger.error(f"[ToolRepository] Erro ao buscar tool: {str(e)}")
            raise
