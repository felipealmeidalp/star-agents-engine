"""Redis-based message buffer service for Chatwoot integration.

Batches rapid incoming messages, ensuring only the LAST message
in a sequence gets processed. Uses UUID-based comparison to correctly
handle identical messages. Supports atomic move-to-processing for
request cancellation flow.
"""

import asyncio
import json
import logging
import uuid

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


# Lua script: atomically move all messages from buffer to processing key.
# Returns the messages as a JSON array of {uuid, content} objects.
_LUA_MOVE_TO_PROCESSING = """
local buffer_key = KEYS[1]
local processing_key = KEYS[2]
local ttl = tonumber(ARGV[1])

local msgs = redis.call('LRANGE', buffer_key, 0, -1)
if #msgs == 0 then
    return '[]'
end

redis.call('DEL', buffer_key)
redis.call('DEL', processing_key)

for i, msg in ipairs(msgs) do
    redis.call('RPUSH', processing_key, msg)
end

redis.call('EXPIRE', processing_key, ttl)
return cjson.encode(msgs)
"""

# Lua script: recover messages from processing back to buffer (prepend).
# Used when cancelling an active request to re-buffer the messages.
_LUA_RECOVER_PROCESSING_TO_BUFFER = """
local processing_key = KEYS[1]
local buffer_key = KEYS[2]
local buffer_ttl = tonumber(ARGV[1])

local msgs = redis.call('LRANGE', processing_key, 0, -1)
if #msgs == 0 then
    return 0
end

redis.call('DEL', processing_key)

-- Prepend to buffer in reverse order so oldest ends up at the right (tail)
-- Buffer uses LPUSH (newest at index 0), so we RPUSH recovered msgs
-- to put them AFTER any new messages already in buffer
for i, msg in ipairs(msgs) do
    redis.call('RPUSH', buffer_key, msg)
end

redis.call('EXPIRE', buffer_key, buffer_ttl)
return #msgs
"""


class MessageBuffer:
    """
    Redis-based buffer for batching incoming messages.

    When multiple messages arrive quickly from the same contact,
    only the LAST message gets processed. Uses UUID-based identification
    to correctly handle identical message content.

    Storage format: Each entry is "{uuid}||{content}"

    Algorithm:
    1. LPUSH "{uuid}||{message}" to Redis list (key = buffer:{contact_id})
    2. Wait configured delay
    3. Check if this UUID is still the LAST in the list
    4. If yes: proceed (caller handles move to processing)
    5. If no: return False (discard, newer message will handle)
    """

    SEPARATOR = "||"
    PROCESSING_TTL_EXTRA = 125  # Extra seconds for processing key TTL

    def __init__(self, delay_seconds: int | None = None) -> None:
        """
        Initialize buffer with configurable delay.

        Args:
            delay_seconds: Override for buffer delay (defaults to config)
        """
        self.delay_seconds = delay_seconds or settings.message_buffer_delay
        self._redis: aioredis.Redis | None = None
        self._move_script: aioredis.client.Script | None = None
        self._recover_script: aioredis.client.Script | None = None

    async def _get_redis(self) -> aioredis.Redis:
        """Get Redis client with connection pool."""
        if self._redis is None:
            pool = await get_redis_pool()
            self._redis = aioredis.Redis(connection_pool=pool)
        return self._redis

    def _get_buffer_key(self, contact_id: int) -> str:
        """Generate Redis key for contact's message buffer."""
        return f"buffer:{contact_id}"

    def _get_processing_key(self, contact_id: int) -> str:
        """Generate Redis key for contact's processing list."""
        return f"processing:{contact_id}"

    def _encode_entry(self, message: str) -> tuple[str, str]:
        """Encode a message with a UUID for unique identification.

        Returns:
            Tuple of (msg_uuid, encoded_entry)
        """
        msg_uuid = str(uuid.uuid4())
        return msg_uuid, f"{msg_uuid}{self.SEPARATOR}{message}"

    @staticmethod
    def _decode_entry(entry: str) -> tuple[str, str]:
        """Decode a buffer entry into (uuid, content).

        Args:
            entry: Encoded entry in format "uuid||content"

        Returns:
            Tuple of (uuid, content)
        """
        separator = "||"
        idx = entry.find(separator)
        if idx == -1:
            # Fallback for legacy entries without UUID
            return "", entry
        return entry[:idx], entry[idx + len(separator):]

    @staticmethod
    def _extract_uuid(entry: str) -> str:
        """Extract UUID from a buffer entry."""
        separator = "||"
        idx = entry.find(separator)
        if idx == -1:
            return ""
        return entry[:idx]

    async def add_to_buffer(self, message: str, contact_id: int) -> str:
        """Add a message to the buffer and return its UUID.

        Args:
            message: The message content
            contact_id: Contact ID for grouping

        Returns:
            The UUID assigned to this message
        """
        redis = await self._get_redis()
        buffer_key = self._get_buffer_key(contact_id)

        msg_uuid, encoded = self._encode_entry(message)
        await redis.lpush(buffer_key, encoded)
        await redis.expire(buffer_key, self.delay_seconds * 2 + 10)

        logger.info(
            "[MessageBuffer] Buffered message for contact %d, uuid=%s",
            contact_id,
            msg_uuid,
        )

        return msg_uuid

    async def wait_and_check_is_last(self, msg_uuid: str, contact_id: int) -> bool:
        """Wait for the buffer delay and check if this UUID is still the most recent.

        Args:
            msg_uuid: UUID of the message to check
            contact_id: Contact ID

        Returns:
            True if this message is the most recent in the buffer
        """
        await asyncio.sleep(self.delay_seconds)

        redis = await self._get_redis()
        buffer_key = self._get_buffer_key(contact_id)

        entries = await redis.lrange(buffer_key, 0, 0)  # Only get the first (newest)
        if not entries:
            logger.warning(
                "[MessageBuffer] Buffer empty for contact %d (uuid=%s)",
                contact_id,
                msg_uuid,
            )
            return False

        newest_uuid = self._extract_uuid(entries[0])
        is_last = newest_uuid == msg_uuid

        logger.info(
            "[MessageBuffer] Contact %d: uuid=%s, is_last=%s",
            contact_id,
            msg_uuid,
            is_last,
        )

        return is_last

    async def move_to_processing(self, contact_id: int) -> list[str]:
        """Atomically move all buffer messages to processing key.

        Uses a Lua script to ensure atomicity: reads buffer, deletes it,
        and writes to processing key in a single Redis operation.

        Args:
            contact_id: Contact ID

        Returns:
            List of message contents (without UUIDs), ordered oldest-first
        """
        redis = await self._get_redis()
        buffer_key = self._get_buffer_key(contact_id)
        processing_key = self._get_processing_key(contact_id)
        ttl = self.delay_seconds + self.PROCESSING_TTL_EXTRA

        if self._move_script is None:
            self._move_script = redis.register_script(_LUA_MOVE_TO_PROCESSING)

        raw_result = await self._move_script(
            keys=[buffer_key, processing_key],
            args=[ttl],
        )

        entries = json.loads(raw_result)

        # Decode and reverse: Redis LRANGE returns newest-first, we want oldest-first
        messages = [self._decode_entry(e)[1] for e in reversed(entries)]

        logger.info(
            "[MessageBuffer] Moved %d messages to processing for contact %d",
            len(messages),
            contact_id,
        )

        return messages

    async def recover_processing_to_buffer(self, contact_id: int) -> int:
        """Recover messages from processing back to buffer.

        Used when cancelling an active request. Messages are appended
        to the buffer tail so any new messages remain at the head.

        Args:
            contact_id: Contact ID

        Returns:
            Number of messages recovered
        """
        redis = await self._get_redis()
        processing_key = self._get_processing_key(contact_id)
        buffer_key = self._get_buffer_key(contact_id)
        buffer_ttl = self.delay_seconds * 2 + 10

        if self._recover_script is None:
            self._recover_script = redis.register_script(
                _LUA_RECOVER_PROCESSING_TO_BUFFER
            )

        count = await self._recover_script(
            keys=[processing_key, buffer_key],
            args=[buffer_ttl],
        )

        logger.info(
            "[MessageBuffer] Recovered %d messages from processing to buffer "
            "for contact %d",
            count,
            contact_id,
        )

        return count

    async def clear_processing(self, contact_id: int) -> None:
        """Clear the processing key after successful completion.

        Args:
            contact_id: Contact ID
        """
        redis = await self._get_redis()
        processing_key = self._get_processing_key(contact_id)
        await redis.delete(processing_key)

    async def should_process_message(
        self,
        message: str,
        contact_id: int,
    ) -> bool:
        """
        Buffer a message and determine if it should be processed.

        LEGACY method - kept for backward compatibility with ChatwootService
        until RequestManager is integrated. Uses UUID-based comparison
        to correctly handle identical messages.

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

        try:
            msg_uuid = await self.add_to_buffer(message, contact_id)
            is_last = await self.wait_and_check_is_last(msg_uuid, contact_id)

            if is_last:
                # Delete the buffer key (legacy behavior)
                redis = await self._get_redis()
                buffer_key = self._get_buffer_key(contact_id)
                await redis.delete(buffer_key)
                logger.info(
                    f"[MessageBuffer] Processing message for contact {contact_id}"
                )

            return is_last

        except aioredis.RedisError as e:
            # On Redis failure, log error and process anyway (graceful degradation)
            logger.error(
                f"[MessageBuffer] Redis error for contact {contact_id}: {e}. "
                "Processing message anyway."
            )
            return True
