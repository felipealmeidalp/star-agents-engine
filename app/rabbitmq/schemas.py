"""Pydantic schemas for RabbitMQ messages."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WebhookRetryMessage(BaseModel):
    """Payload para retry de webhook Chatwoot quando pool de DB esgota."""

    payload: dict[str, Any] = Field(..., description="ChatwootWebhookPayload serializado")
    token: str = Field(..., description="Token do webhook para lookup da company")
    sender_id: int | str | None = Field(default=None, description="ID do sender para logs")
    retry_count: int = Field(default=0, description="Tentativas de retry realizadas")


class FollowUpMessage(BaseModel):
    """
    Message payload for scheduled follow-up.

    This is published to RabbitMQ with a delay (TTL).
    """

    # Identifiers
    customer_id: int = Field(..., description="Customer ID to send follow-up to")
    company_id: int = Field(..., description="Company ID for multi-tenancy")

    # Chatwoot context (needed to send the message)
    cw_conversation_id: int = Field(..., description="Chatwoot conversation ID")

    # Follow-up config
    step_order: int = Field(..., description="Follow-up step number (1, 2, 3...)")
    message_payload: dict[str, Any] | list[Any] = Field(
        ..., description="Message content from follow_ups.message_payload (dict or list)"
    )

    # Verification - exact timestamp from DB to check if customer sent new message
    last_message: datetime = Field(
        ..., description="Exact last_message timestamp from customer record"
    )

    # Retry tracking for dev_command_state conflicts
    dev_command_retry_count: int = Field(
        default=0,
        description="Number of times this follow-up was rescheduled due to dev_command_state"
    )
