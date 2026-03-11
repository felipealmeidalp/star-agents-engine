"""RabbitMQ integration for delayed follow-up messages and webhook retries."""

from app.rabbitmq.connection import (
    close_rabbitmq_connection,
    get_rabbitmq_channel,
    get_rabbitmq_connection,
)
from app.rabbitmq.publisher import (
    FollowUpPublisher,
    get_follow_up_publisher,
    init_follow_up_queues,
)
from app.rabbitmq.schemas import FollowUpMessage, WebhookRetryMessage
from app.rabbitmq.webhook_retry_publisher import (
    WebhookRetryPublisher,
    get_webhook_retry_publisher,
    init_webhook_retry_queue,
)

__all__ = [
    "get_rabbitmq_connection",
    "close_rabbitmq_connection",
    "get_rabbitmq_channel",
    "FollowUpPublisher",
    "get_follow_up_publisher",
    "init_follow_up_queues",
    "FollowUpMessage",
    "WebhookRetryMessage",
    "WebhookRetryPublisher",
    "get_webhook_retry_publisher",
    "init_webhook_retry_queue",
]
