"""Pydantic schemas for Chatwoot webhook payloads."""

from pydantic import BaseModel


class ChatwootAccount(BaseModel):
    """Chatwoot account information."""

    id: int
    name: str | None = None


class ChatwootSender(BaseModel):
    """Sender information from Chatwoot webhook."""

    id: int
    name: str | None = None
    phone_number: str | None = None
    email: str | None = None
    thumbnail: str | None = None  # Avatar URL
    type: str | None = None  # "contact" or "user"


class ChatwootContactInbox(BaseModel):
    """Contact inbox info - used to get contact_id from outgoing messages."""

    contact_id: int
    inbox_id: int | None = None
    source_id: str | None = None


class ChatwootConversation(BaseModel):
    """Conversation information from Chatwoot webhook."""

    id: int
    inbox_id: int | None = None
    status: str | None = None
    contact_inbox: ChatwootContactInbox | None = None


class ChatwootInbox(BaseModel):
    """Inbox information from Chatwoot webhook."""

    id: int
    name: str | None = None


class ChatwootAttachment(BaseModel):
    """Attachment from Chatwoot webhook message."""

    id: int
    message_id: int | None = None
    file_type: str | None = None  # "audio", "image", "video", "file"
    data_url: str | None = None
    file_size: int | None = None


class ChatwootWebhookPayload(BaseModel):
    """Chatwoot webhook payload - received directly without wrapper."""

    account: ChatwootAccount
    content: str | None = None
    content_type: str | None = None  # "text", "image", etc.
    conversation: ChatwootConversation
    message_type: str  # "incoming" or "outgoing"
    sender: ChatwootSender | None = None
    event: str  # "message_created"
    inbox: ChatwootInbox | None = None
    private: bool = False
    attachments: list[ChatwootAttachment] | None = None
