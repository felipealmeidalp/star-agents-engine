"""Follow-up module for processing delayed messages."""

from app.followUp.consumer import start_follow_up_consumer

__all__ = [
    "start_follow_up_consumer",
]
