"""Pydantic schemas for Helena webhook payloads (MESSAGE_RECEIVED)."""

from pydantic import BaseModel, ConfigDict, Field


class HelenaDetails(BaseModel):
    """Extra details of a Helena message. Carries the lead phone in `from`."""

    # `from` is a Python keyword, so it is mapped via alias.
    model_config = ConfigDict(populate_by_name=True)

    from_: str | None = Field(default=None, alias="from")


class HelenaContent(BaseModel):
    """The `content` block of a Helena webhook event."""

    sessionId: str
    text: str | None = None
    id: str | None = None
    type: str | None = None
    direction: str | None = None
    timestamp: str | None = None
    details: HelenaDetails | None = None


class HelenaWebhookPayload(BaseModel):
    """Helena webhook envelope. Extra fields are ignored (tolerant parsing)."""

    eventType: str
    date: str | None = None
    content: HelenaContent
