"""Repository for error log operations."""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import ErrorLog

logger = logging.getLogger(__name__)


class ErrorLogRepository:
    """Data access layer for error_logs table."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        error_type: str,
        location: str,
        error_message: str,
        severity: str = "error",
        traceback: str | None = None,
        company_id: int | None = None,
        session_id: str | None = None,
        contact_id: str | None = None,
        agent_id: int | None = None,
        sub_agent_id: int | None = None,
        tool_name: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Persist an error log entry. Never raises."""
        try:
            log = ErrorLog(
                error_type=error_type,
                severity=severity,
                location=location,
                error_message=str(error_message)[:500],
                traceback=traceback,
                company_id=int(company_id) if company_id is not None else None,
                session_id=str(session_id) if session_id is not None else None,
                contact_id=str(contact_id) if contact_id is not None else None,
                agent_id=int(agent_id) if agent_id is not None else None,
                sub_agent_id=int(sub_agent_id) if sub_agent_id is not None else None,
                tool_name=tool_name,
                extra=extra,
            )
            self.db.add(log)
            await self.db.commit()
        except Exception as exc:
            logger.warning("[ErrorLogRepo] Failed to persist error log: %s", exc)
            try:
                await self.db.rollback()
            except Exception:
                pass
