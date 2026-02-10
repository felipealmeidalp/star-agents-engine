"""RabbitMQ integration for delayed follow-up messages."""

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
from app.rabbitmq.schemas import FollowUpMessage

__all__ = [
    "get_rabbitmq_connection",
    "close_rabbitmq_connection",
    "get_rabbitmq_channel",
    "FollowUpPublisher",
    "get_follow_up_publisher",
    "init_follow_up_queues",
    "FollowUpMessage",
]
