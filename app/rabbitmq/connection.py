"""RabbitMQ connection manager using aio-pika."""

import asyncio
import logging

import aio_pika
from aio_pika.abc import AbstractRobustChannel, AbstractRobustConnection

from app.config import settings

logger = logging.getLogger(__name__)

# Module-level connection (singleton pattern, like Redis in buffer.py)
_rabbitmq_connection: AbstractRobustConnection | None = None
_rabbitmq_channel: AbstractRobustChannel | None = None

# Locks to prevent TOCTOU race on connection/channel creation
_connection_lock = asyncio.Lock()
_channel_lock = asyncio.Lock()


async def get_rabbitmq_connection() -> AbstractRobustConnection:
    """
    Get or create RabbitMQ connection.

    Uses robust connection for automatic reconnection on failures.
    Connection is shared across the application (singleton).

    Returns:
        AbstractRobustConnection: Shared RabbitMQ connection
    """
    global _rabbitmq_connection

    async with _connection_lock:
        if _rabbitmq_connection is None or _rabbitmq_connection.is_closed:
            # Build URL with credentials
            url = settings.rabbit_url
            if settings.rabbit_user and settings.rabbit_pass:
                # Parse URL and inject credentials
                # amqp://localhost:5672/ -> amqp://user:pass@localhost:5672/
                url = url.replace("amqp://", f"amqp://{settings.rabbit_user}:{settings.rabbit_pass}@")

            logger.info(f"[RabbitMQ] Creating connection to {settings.rabbit_url}")
            _rabbitmq_connection = await aio_pika.connect_robust(
                url,
                timeout=settings.rabbit_connection_timeout,
                heartbeat=settings.rabbit_heartbeat,
            )
            logger.info("[RabbitMQ] Connection established")

    return _rabbitmq_connection


async def get_rabbitmq_channel() -> AbstractRobustChannel:
    """
    Get or create RabbitMQ channel.

    Channel is reused for publishing to avoid overhead.

    Returns:
        AbstractRobustChannel: Shared RabbitMQ channel
    """
    global _rabbitmq_channel

    async with _channel_lock:
        if _rabbitmq_channel is None or _rabbitmq_channel.is_closed:
            connection = await get_rabbitmq_connection()
            _rabbitmq_channel = await connection.channel()
            logger.info("[RabbitMQ] Channel created")

    return _rabbitmq_channel


async def close_rabbitmq_connection() -> None:
    """
    Close RabbitMQ connection gracefully.

    Should be called during application shutdown.
    """
    global _rabbitmq_connection, _rabbitmq_channel

    if _rabbitmq_channel is not None:
        await _rabbitmq_channel.close()
        _rabbitmq_channel = None
        logger.info("[RabbitMQ] Channel closed")

    if _rabbitmq_connection is not None:
        await _rabbitmq_connection.close()
        _rabbitmq_connection = None
        logger.info("[RabbitMQ] Connection closed")
