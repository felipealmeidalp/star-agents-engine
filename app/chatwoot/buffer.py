"""Redis-based message buffer service for Chatwoot integration.

Batches rapid incoming messages, ensuring only the LAST message
in a sequence gets processed. This prevents duplicate agent responses
when users send multiple messages quickly.
"""

import asyncio
import logging

import redis.asyncio as aioredis
from redis.asyncio.connection import ConnectionPool

from app.config import settings

logger = logging.getLogger(__name__)

# Module-level connection pool (initialized lazily)
_redis_pool: ConnectionPool | None = None


async def get_redis_pool() -> ConnectionPool:
    """
    Get or create the Redis connection pool.

    Uses lazy initialization to avoid connection on import.
    Pool is shared across all buffer operations.

    Returns:
        ConnectionPool: Shared async Redis connection pool
    """
    global _redis_pool
    if _redis_pool is None:
        logger.info(f"[MessageBuffer] Creating Redis pool: {settings.redis_url}")
        _redis_pool = aioredis.ConnectionPool.from_url(
            settings.redis_url,
            max_connections=20,
            decode_responses=True,
        )
    return _redis_pool


async def close_redis_pool() -> None:
    """
    Close the Redis connection pool.

    Should be called during application shutdown.
    """
    global _redis_pool
    if _redis_pool is not None:
        logger.info("[MessageBuffer] Closing Redis pool")
        await _redis_pool.disconnect()
        _redis_pool = None


class MessageBuffer:
    """
    Redis-based buffer for batching incoming messages.

    When multiple messages arrive quickly from the same contact,
    only the LAST message gets processed. Earlier messages are
    discarded to prevent duplicate/fragmented responses.

    Algorithm:
    1. LPUSH message to Redis list (key = buffer:{contact_id})
    2. Wait configured delay
    3. Check if this message is still the LAST in the list
    4. If yes: delete list and return True (proceed)
    5. If no: return False (discard, newer message will handle)
    """

    def __init__(self, delay_seconds: int | None = None) -> None:
        """
        Initialize buffer with configurable delay.

        Args:
            delay_seconds: Override for buffer delay (defaults to config)
        """
        self.delay_seconds = delay_seconds or settings.message_buffer_delay
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        """Get Redis client with connection pool."""
        if self._redis is None:
            pool = await get_redis_pool()
            self._redis = aioredis.Redis(connection_pool=pool)
        return self._redis

    def _get_buffer_key(self, contact_id: int) -> str:
        """Generate Redis key for contact's message buffer."""
        return f"buffer:{contact_id}"

    async def should_process_message(
        self,
        message: str,
        contact_id: int,
    ) -> bool:
        """
        Buffer a message and determine if it should be processed.

        In DEV_MODE, bypasses Redis and returns True immediately.

        Args:
            message: The incoming message content
            contact_id: Chatwoot sender/contact ID for grouping

        Returns:
            True if this message should be processed (it's the last one),
            False if it should be discarded (newer message arrived)
        """
        # DEV_MODE bypass - process immediately without buffering
        if settings.dev_mode:
            logger.info(
                f"[MessageBuffer] DEV_MODE active, bypassing buffer for contact {contact_id}"
            )
            return True

        buffer_key = self._get_buffer_key(contact_id)

        try:
            redis = await self._get_redis()

            # 1. Push message to the list (LPUSH = newest at index 0)
            await redis.lpush(buffer_key, message)

            # Set TTL on the key to prevent orphaned buffers (2x delay as safety margin)
            await redis.expire(buffer_key, self.delay_seconds * 2 + 10)

            logger.info(
                f"[MessageBuffer] Buffered message for contact {contact_id}, "
                f"waiting {self.delay_seconds}s"
            )

            # 2. Wait for the buffer delay
            await asyncio.sleep(self.delay_seconds)

            # 3. Get all messages in the buffer
            messages = await redis.lrange(buffer_key, 0, -1)

            if not messages:
                # List was deleted by another process or expired
                logger.warning(
                    f"[MessageBuffer] Buffer empty for contact {contact_id}, "
                    "another process may have handled it"
                )
                return False

            # 4. Check if OUR message is the LAST one (at index 0, most recent)
            last_message = messages[0]
            is_last = last_message == message

            logger.info(
                f"[MessageBuffer] Contact {contact_id}: "
                f"is_last={is_last}, buffer_size={len(messages)}"
            )

            if is_last:
                # 5a. This is the last message - delete buffer and proceed
                await redis.delete(buffer_key)
                logger.info(
                    f"[MessageBuffer] Processing message for contact {contact_id} "
                    f"(batched {len(messages)} messages)"
                )
                return True
            else:
                # 5b. A newer message arrived - discard this one
                logger.info(
                    f"[MessageBuffer] Discarding message for contact {contact_id} "
                    "(newer message will process)"
                )
                return False

        except aioredis.RedisError as e:
            # On Redis failure, log error and process anyway (graceful degradation)
            logger.error(
                f"[MessageBuffer] Redis error for contact {contact_id}: {e}. "
                "Processing message anyway."
            )
            return True
