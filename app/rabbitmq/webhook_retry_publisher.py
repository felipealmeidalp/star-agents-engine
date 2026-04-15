"""Publisher for webhook retry messages (pool exhaustion recovery)."""

import asyncio
import logging

from aio_pika import DeliveryMode, Message
from aio_pika.abc import AbstractRobustChannel

from app.config import settings
from app.rabbitmq.connection import get_rabbitmq_channel
from app.rabbitmq.schemas import WebhookRetryMessage
from app.utils.alerter import send_critical_alert

logger = logging.getLogger(__name__)


class WebhookRetryPublisher:
    """
    Publisher for re-enqueuing failed webhooks due to DB pool exhaustion.

    Uses a simple durable queue (no TTL/DLX) - backoff is handled by the consumer via sleep.
    """

    def __init__(self) -> None:
        self._channel: AbstractRobustChannel | None = None
        self._initialized: bool = False
        self._lock = asyncio.Lock()

    async def _get_channel(self) -> AbstractRobustChannel:
        if self._channel is None or self._channel.is_closed:
            self._channel = await get_rabbitmq_channel()
        return self._channel

    async def ensure_queue_exists(self) -> None:
        """Declare durable queue for webhook retries."""
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:  # double-check after acquiring lock
                return

            queue_name = settings.rabbit_webhook_retry_queue
            channel = await self._get_channel()

            await channel.declare_queue(queue_name, durable=True)

            self._initialized = True
            logger.info(f"[RabbitMQ] Initialized webhook retry queue: {queue_name}")

    async def publish_webhook_retry(
        self,
        payload_dict: dict,
        token: str,
        sender_id: int | str | None = None,
        retry_count: int = 0,
    ) -> None:
        """
        Publish a webhook for retry processing.

        Args:
            payload_dict: ChatwootWebhookPayload serialized as dict
            token: Chatwoot webhook token
            sender_id: Sender ID for logging
            retry_count: Current retry attempt number
        """
        await self.ensure_queue_exists()

        queue_name = settings.rabbit_webhook_retry_queue

        retry_msg = WebhookRetryMessage(
            payload=payload_dict,
            token=token,
            sender_id=sender_id,
            retry_count=retry_count,
        )

        message = Message(
            body=retry_msg.model_dump_json().encode(),
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
        )

        try:
            channel = await self._get_channel()

            await channel.default_exchange.publish(
                message,
                routing_key=queue_name,
            )

            logger.info(
                f"[WebhookRetry] Published retry: "
                f"sender_id={sender_id}, retry_count={retry_count}"
            )
        except Exception as e:
            logger.error(f"[WebhookRetry] Failed to publish retry: {e}")
            send_critical_alert(
                "RABBITMQ_PUBLISH_WEBHOOK_RETRY_FAILED",
                "webhook_retry_publisher.py:publish_webhook_retry",
                e,
                contact_id=sender_id,
                extra=f"retry_count={retry_count}",
            )
            raise


# Singleton instance
_publisher: WebhookRetryPublisher | None = None


def get_webhook_retry_publisher() -> WebhookRetryPublisher:
    """Get singleton publisher instance."""
    global _publisher
    if _publisher is None:
        _publisher = WebhookRetryPublisher()
    return _publisher


async def init_webhook_retry_queue() -> None:
    """Initialize webhook retry queue at startup."""
    publisher = get_webhook_retry_publisher()
    await publisher.ensure_queue_exists()
