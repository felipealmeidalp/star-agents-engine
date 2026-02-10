"""FastAPI application entry point."""

import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.chatwoot.buffer import close_redis_pool
from app.config import settings
from app.db.database import engine
from app.followUp import start_follow_up_consumer
from app.rabbitmq import (
    close_rabbitmq_connection,
    get_rabbitmq_connection,
    init_follow_up_queues,
)

# Configure logging to show in terminal
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Set specific loggers
logging.getLogger("app").setLevel(logging.DEBUG)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.getLogger("openai._base_client").setLevel(logging.WARNING)  # Evita payload duplicado
logging.getLogger("aiormq").setLevel(logging.WARNING)  # Remove heartbeat logs
logging.getLogger("aio_pika").setLevel(logging.WARNING)  # Remove connection debug logs


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan events.

    Handles startup and shutdown operations.
    """
    # Startup
    print(f"🚀 Starting {settings.app_name}...")

    # Test database connection
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        print("✅ Database connection established")
    except Exception as e:
        print(f"❌ Database connection failed: {e}")

    # Connect to RabbitMQ
    try:
        await get_rabbitmq_connection()
        print("✅ RabbitMQ connection established")

        # Initialize follow-up queues
        await init_follow_up_queues()
        print("✅ FollowUp queues initialized")

        # Start follow-up consumer
        await start_follow_up_consumer()
        print(f"✅ FollowUp consumer started (queue: {settings.rabbit_follow_up_queue})")
    except Exception as e:
        print(f"⚠️ RabbitMQ connection failed: {e}")
        # Note: We don't fail startup, follow-ups will just not work

    yield

    # Shutdown
    print("🛑 Shutting down...")
    await close_redis_pool()
    await close_rabbitmq_connection()
    await engine.dispose()


# Create FastAPI application
app = FastAPI(
    title="Star Agents Orchestrator",
    description="AI Agent Orchestration Platform - Python Migration from N8N",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import and include routers
from app.routes import chat, chatwoot, health

app.include_router(health.router, prefix="/api", tags=["Health"])
app.include_router(chat.router, prefix="/api", tags=["Chat"])
app.include_router(chatwoot.router, prefix="/api", tags=["Chatwoot"])


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint - basic service info."""
    return {
        "service": settings.app_name,
        "version": "1.0.0",
        "status": "running",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
    )
