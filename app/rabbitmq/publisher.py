"""Publisher for delayed follow-up messages using TTL + DLX pattern."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import aio_pika
from aio_pika import DeliveryMode, Message
from aio_pika.abc import AbstractRobustChannel

from app.config import settings
from app.rabbitmq.connection import get_rabbitmq_channel
from app.rabbitmq.schemas import FollowUpMessage

logger = logging.getLogger(__name__)


class FollowUpPublisher:
    """
    Publisher for scheduling delayed follow-up messages.

    Uses TTL + Dead Letter Queue pattern:
    1. Message published to delay queue with per-message TTL
    2. Delay queue has no consumers, messages expire
    3. Expired messages route to work queue via DLX
    4. Consumer processes from work queue

    Queue naming:
    - Delay queue: {queue_name}.delay
    - Work queue: {queue_name} (from settings.rabbit_follow_up_queue)
    """

    def __init__(self) -> None:
        """Initialize publisher."""
        self._channel: AbstractRobustChannel | None = None
        self._initialized: bool = False

    async def _get_channel(self) -> AbstractRobustChannel:
        """Get shared channel, creating if needed."""
        if self._channel is None or self._channel.is_closed:
            self._channel = await get_rabbitmq_channel()
        return self._channel

    async def ensure_queues_exist(self) -> None:
        """
        Ensure delay and work queues exist.

        Creates:
        - Work queue: {queue_name} (durable)
        - Delay queue: {queue_name}.delay (durable, with DLX pointing to work queue)
        - Exchanges for routing
        """
        if self._initialized:
            return

        queue_name = settings.rabbit_follow_up_queue
        channel = await self._get_channel()

        # 1. Declare work exchange (direct)
        work_exchange = await channel.declare_exchange(
            f"{queue_name}.work.exchange",
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )

        # 2. Declare work queue (where messages go after delay)
        work_queue = await channel.declare_queue(
            queue_name,
            durable=True,
        )
        await work_queue.bind(work_exchange, routing_key=queue_name)

        # 3. Declare delay exchange (direct)
        await channel.declare_exchange(
            f"{queue_name}.delay.exchange",
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )

        # 4. Declare delay queue with DLX settings
        delay_queue_name = f"{queue_name}.delay"
        delay_queue = await channel.declare_queue(
            delay_queue_name,
            durable=True,
            arguments={
                # When messages expire (TTL), route them here:
                "x-dead-letter-exchange": f"{queue_name}.work.exchange",
                "x-dead-letter-routing-key": queue_name,
            },
        )

        # 5. Bind delay queue to delay exchange
        delay_exchange = await channel.get_exchange(f"{queue_name}.delay.exchange")
        await delay_queue.bind(delay_exchange, routing_key=delay_queue_name)

        self._initialized = True
        logger.info(f"[RabbitMQ] Initialized queues: {queue_name}, {delay_queue_name}")

    async def publish_follow_up(
        self,
        customer_id: int,
        company_id: int,
        cw_conversation_id: int,
        step_order: int,
        message_payload: dict[str, Any] | list[Any],
        last_message: datetime,
        next_follow: datetime,
        dev_command_retry_count: int = 0,
    ) -> None:
        """
        Publish a follow-up message with delay.

        The delay is calculated as: next_follow - now

        Args:
            customer_id: Customer ID
            company_id: Company ID
            cw_conversation_id: Chatwoot conversation ID
            step_order: Follow-up step number
            message_payload: Message content from follow_ups table
            last_message: Exact timestamp from customer.last_message for verification
            next_follow: When the follow-up should be sent (from customer.next_follow)
            dev_command_retry_count: Number of times rescheduled due to dev_command_state

        Raises:
            Exception: If publishing fails
        """
        # Ensure queues exist
        await self.ensure_queues_exist()

        queue_name = settings.rabbit_follow_up_queue

        # Calculate delay in milliseconds
        now = datetime.now(UTC)

        # Ensure next_follow is timezone-aware
        if next_follow.tzinfo is None:
            next_follow = next_follow.replace(tzinfo=UTC)

        delay_seconds = (next_follow - now).total_seconds()

        # Don't allow negative delays (message should be sent immediately)
        if delay_seconds < 0:
            logger.warning(
                f"[RabbitMQ] next_follow is in the past, setting delay to 0. "
                f"next_follow={next_follow}, now={now}"
            )
            delay_seconds = 0

        # Build message
        follow_up_msg = FollowUpMessage(
            customer_id=customer_id,
            company_id=company_id,
            cw_conversation_id=cw_conversation_id,
            step_order=step_order,
            message_payload=message_payload,
            last_message=last_message,
            dev_command_retry_count=dev_command_retry_count,
        )

        message_body = follow_up_msg.model_dump_json().encode()

        message = Message(
            body=message_body,
            delivery_mode=DeliveryMode.PERSISTENT,  # Survive broker restart
            expiration=timedelta(seconds=delay_seconds),  # TTL as timedelta
            content_type="application/json",
        )

        try:
            channel = await self._get_channel()
            delay_exchange = await channel.get_exchange(f"{queue_name}.delay.exchange")

            await delay_exchange.publish(
                message,
                routing_key=f"{queue_name}.delay",
            )

            retry_info = (
                f", retry={dev_command_retry_count}" if dev_command_retry_count > 0 else ""
            )
            logger.info(
                f"[RabbitMQ] Published follow-up: "
                f"customer_id={customer_id}, "
                f"step={step_order}, "
                f"delay_seconds={delay_seconds:.0f}{retry_info}"
            )

        except Exception as e:
            logger.error(f"[RabbitMQ] Failed to publish follow-up: {e}")
            raise


# Singleton instance
_publisher: FollowUpPublisher | None = None


def get_follow_up_publisher() -> FollowUpPublisher:
    """Get singleton publisher instance."""
    global _publisher
    if _publisher is None:
        _publisher = FollowUpPublisher()
    return _publisher


async def init_follow_up_queues() -> None:
    """Initialize follow-up queues at startup."""
    publisher = get_follow_up_publisher()
    await publisher.ensure_queues_exist()
