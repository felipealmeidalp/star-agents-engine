"""Health check endpoints."""

from fastapi import APIRouter, Depends, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db

router = APIRouter()


@router.get("/health")
async def health_check() -> dict[str, str]:
    """
    Basic health check endpoint.

    Returns:
        dict: Service status information.
    """
    return {"status": "ok", "service": "star-agents"}


@router.get("/readiness")
async def readiness_check(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    """
    Readiness check including database connection test.

    Args:
        db: Database session from dependency injection.

    Returns:
        dict: Readiness status with database connection state.
    """
    try:
        result = await db.execute(text("SELECT 1"))
        result.scalar()
        return {"status": "ready", "database": "connected"}
    except Exception as e:
        return {
            "status": "not_ready",
            "database": "disconnected",
            "error": str(e),
        }
