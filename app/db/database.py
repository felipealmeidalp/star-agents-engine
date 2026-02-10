"""Database engine and session management for async PostgreSQL."""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models.tables import Base  # noqa: F401 - needed for Alembic


# Connection args for asyncpg
CONNECT_ARGS = {
    "server_settings": {"application_name": settings.app_name},
}


# Create async engine
# Using NullPool because Supabase Session Pooler manages connection pooling externally.
# This avoids double-pooling and lets Supavisor handle connection management efficiently.
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    poolclass=NullPool,
    connect_args=CONNECT_ARGS,
)

# Session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency for database sessions.

    Yields:
        AsyncSession: Database session for the request lifecycle.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
