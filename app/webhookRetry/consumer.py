"""Consumer for webhook retry messages from RabbitMQ."""

import asyncio
import json
import logging

from aio_pika.abc import AbstractIncomingMessage

from app.chatwoot.schemas import ChatwootWebhookPayload
from app.config import settings
from app.rabbitmq.connection import get_rabbitmq_connection
from app.utils.alerter import send_critical_alert

logger = logging.getLogger(__name__)

# Max backoff delay in seconds
MAX_BACKOFF_SECONDS = 60

# Limita quantos retries rodam simultaneamente.
# prefetch_count sozinho não funciona com ACK-first, então usamos semaphore.
_concurrency_semaphore = asyncio.Semaphore(5)

# Channel dedicado do consumer — mantido em módulo para evitar GC
_consumer_channel = None


async def on_webhook_retry_message(message: AbstractIncomingMessage) -> None:
    """
    Process a webhook retry message.

    Trade-off: ACK imediato significa que se o processo morrer (SIGKILL/OOM)
    durante o backoff, a mensagem é perdida. Aceitável porque são retries
    de webhooks que já falharam — o Chatwoot pode reenviar.

    Flow:
    1. ACK the message immediately (frees the channel for other messages)
    2. Deserialize message
    3. Calculate backoff delay: 5 * 2^(retry_count-1), cap at 60s
    4. Sleep for delay (outside semaphore — all messages sleep in parallel)
    5. Reconstruct ChatwootWebhookPayload
    6. Acquire semaphore (limits concurrent DB access)
    7. Call process_webhook_background with retry_count
       - If pool exhaustion again, process_webhook_background re-enqueues automatically
    """
    # ACK imediato — libera o channel durante o sleep.
    await message.ack()

    sender_id = None
    retry_count = 0

    try:
        data = json.loads(message.body)
        payload_dict = data.get("payload", {})
        token = data.get("token", "")
        sender_id = data.get("sender_id")
        retry_count = data.get("retry_count", 0)

        logger.info(
            f"[WebhookRetry Consumer] Processing retry: "
            f"sender_id={sender_id}, retry_count={retry_count}"
        )

        # Backoff fora do semaphore — todas as mensagens dormem em paralelo
        delay = min(5 * (2 ** (retry_count - 1)), MAX_BACKOFF_SECONDS)
        logger.info(
            f"[WebhookRetry Consumer] Waiting {delay}s before retry "
            f"(attempt {retry_count})"
        )
        await asyncio.sleep(delay)

        # Reconstruct payload
        payload = ChatwootWebhookPayload.model_validate(payload_dict)

        # Import here to avoid circular imports
        from app.routes.chatwoot import process_webhook_background

        # Semaphore só protege o acesso ao DB (prefetch_count não basta com ACK-first)
        async with _concurrency_semaphore:
            await process_webhook_background(
                payload=payload,
                token=token,
                retry_count=retry_count,
            )

        logger.info(
            f"[WebhookRetry Consumer] Retry processing complete: "
            f"sender_id={sender_id}, retry_count={retry_count}"
        )

    except Exception as e:
        # Mensagem já foi ACK'd — apenas logar o erro
        logger.exception(
            "[WebhookRetry Consumer] Unhandled error: sender_id=%s, retry=%s: %s",
            sender_id,
            retry_count,
            e,
        )
        send_critical_alert(
            "WEBHOOK_RETRY_UNHANDLED_ERROR",
            "webhookRetry/consumer.py:on_webhook_retry_message",
            e,
            contact_id=sender_id,
        )


async def start_webhook_retry_consumer() -> None:
    """Start consumer for the webhook retry queue with a dedicated channel."""
    global _consumer_channel

    # Guard contra dupla chamada — evita consumers duplicados e channel leak
    if _consumer_channel is not None and not _consumer_channel.is_closed:
        logger.warning("[WebhookRetry Consumer] Already running, skipping start")
        return

    queue_name = settings.rabbit_webhook_retry_queue

    # Channel dedicado — não compartilha com publishers ou outros consumers
    connection = await get_rabbitmq_connection()
    _consumer_channel = await connection.channel()

    queue = await _consumer_channel.declare_queue(queue_name, durable=True)

    await queue.consume(on_webhook_retry_message)

    logger.info(f"[WebhookRetry Consumer] Listening on queue: {queue_name}")
