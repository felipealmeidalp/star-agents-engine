"""Database engine and session management for async PostgreSQL."""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.models.tables import Base  # noqa: F401 - needed for Alembic


# Connection args for asyncpg
CONNECT_ARGS = {
    "server_settings": {"application_name": settings.app_name},
    # PgBouncer (transaction mode) nao suporta prepared statements do asyncpg
    "statement_cache_size": 0,
}


# Create async engine
# Pool local mesmo atrás do pooler do Neon: sem ele (NullPool) cada query paga um
# handshake TCP+TLS novo até us-east-1 — ~2.4s vs ~1.15s por query a partir do BR.
# pool_pre_ping descarta conexões que o pooler derrubou por idle.
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_size=5,
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=300,
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
