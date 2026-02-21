"""Meta WhatsApp webhook helpers: signature verification, deduplication, and caching."""

import hashlib
import hmac
import json
import logging

import redis.asyncio as aioredis

from app.chatwoot.buffer import get_redis_pool
from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def verify_signature(payload: bytes, signature_header: str, app_secret: str) -> bool:
    """
    Validate X-Hub-Signature-256 from Meta webhook.

    Args:
        payload: Raw request body bytes
        signature_header: Value of X-Hub-Signature-256 header (e.g. "sha256=abc...")
        app_secret: Facebook App Secret

    Returns:
        True if signature matches, False otherwise
    """
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(
        app_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()

    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)


# ---------------------------------------------------------------------------
# Deduplicator (Redis SET NX EX)
# ---------------------------------------------------------------------------

class MetaDeduplicator:
    """Deduplicate Meta webhook events by message/status ID using Redis."""

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        """Get Redis client reusing the shared pool from buffer.py."""
        if self._redis is None:
            pool = await get_redis_pool()
            self._redis = aioredis.Redis(connection_pool=pool)
        return self._redis

    async def is_duplicate(self, event_id: str) -> bool:
        """
        Check if event_id was already seen.

        Uses SET NX EX so the key auto-expires after meta_dedup_ttl seconds.

        Args:
            event_id: Unique event identifier (Meta message ID or status ID)

        Returns:
            True if duplicate (already processed), False if new
        """
        key = f"meta:dedup:{event_id}"
        try:
            redis = await self._get_redis()
            # SET NX returns True only if key was newly created
            was_set = await redis.set(key, "1", nx=True, ex=settings.meta_dedup_ttl)
            if not was_set:
                logger.info(f"[MetaWebhook] Duplicate event: {event_id}")
                return True
            return False
        except aioredis.RedisError as e:
            # Graceful degradation: if Redis fails, process normally
            logger.error(f"[MetaWebhook] Redis dedup error: {e}. Processing anyway.")
            return False


# ---------------------------------------------------------------------------
# Webhook cache (whatsapp -> company_id, cw_base_url)
# ---------------------------------------------------------------------------

class MetaWebhookCache:
    """Cache whatsapp number -> (company_id, cw_base_url) mapping in Redis."""

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        """Get Redis client reusing the shared pool from buffer.py."""
        if self._redis is None:
            pool = await get_redis_pool()
            self._redis = aioredis.Redis(connection_pool=pool)
        return self._redis

    async def get(self, whatsapp: str) -> tuple[int, str] | None:
        """
        Lookup cached mapping for a whatsapp number.

        Args:
            whatsapp: WhatsApp phone number

        Returns:
            (company_id, cw_base_url) or None if not cached
        """
        key = f"meta:cache:{whatsapp}"
        try:
            redis = await self._get_redis()
            data = await redis.get(key)
            if data:
                parsed = json.loads(data)
                logger.debug(f"[MetaWebhook] Cache hit for whatsapp={whatsapp}")
                return (parsed["company_id"], parsed["cw_base_url"])
        except (aioredis.RedisError, json.JSONDecodeError, KeyError) as e:
            logger.error(f"[MetaWebhook] Redis cache get error: {e}")
        return None

    async def set(self, whatsapp: str, company_id: int, cw_base_url: str) -> None:
        """
        Store mapping in cache with TTL.

        Args:
            whatsapp: WhatsApp phone number
            company_id: Company ID
            cw_base_url: Chatwoot base URL
        """
        key = f"meta:cache:{whatsapp}"
        try:
            redis = await self._get_redis()
            data = json.dumps({"company_id": company_id, "cw_base_url": cw_base_url})
            await redis.set(key, data, ex=settings.meta_cache_ttl)
            logger.debug(f"[MetaWebhook] Cached mapping for whatsapp={whatsapp}")
        except aioredis.RedisError as e:
            logger.error(f"[MetaWebhook] Redis cache set error: {e}")
