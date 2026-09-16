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
    # ponytail: shape assumed — no real Helena attachment payload captured yet.
    # Minimal mirror of ChatwootAttachment (file_type, data_url): a file URL plus
    # a type/mimetype to tell audio from image/video/file. Confirm/adjust field
    # names against the first real Helena attachment webhook. Optional, so a
    # text-only payload is unaffected; the envelope already ignores extras.
    attachment_url: str | None = None
    attachment_type: str | None = None
    # Numeric channel id injected by the n8n pre-processor so the engine can gate
    # by allowed_inbox. Helena's native payload has no channel field. None → no
    # channel info → gate treats it as allowed (same as a company without config).
    channel: int | None = None


class HelenaWebhookPayload(BaseModel):
    """Helena webhook envelope. Extra fields are ignored (tolerant parsing)."""

    eventType: str
    date: str | None = None
    content: HelenaContent
