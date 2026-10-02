"""HTTP client for the Instagram API (Instagram Login)."""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_BASE_URL = "https://graph.instagram.com/v25.0"


class InstagramClient:
    """The outbound calls of the Instagram comment welcome."""

    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout

    async def _request(
        self, method: str, path: str, access_token: str, **kwargs: Any
    ) -> dict[str, Any]:
        # token in the header, never in the query string: httpx logs every request URL
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(
                method, f"{API_BASE_URL}/{path}", headers=headers, **kwargs
            )
            logger.info("[InstagramClient] %s %s: status=%d", method, path, response.status_code)
            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result

    async def get_media(self, media_id: str, access_token: str) -> dict[str, Any]:
        return await self._request(
            "GET", media_id, access_token, params={"fields": "caption,permalink"}
        )

    async def send_private_reply(
        self, comment_id: str, text: str, access_token: str
    ) -> dict[str, Any]:
        """Send the one DM Meta allows per comment; returns recipient_id and message_id."""
        return await self._request(
            "POST",
            "me/messages",
            access_token,
            json={"recipient": {"comment_id": comment_id}, "message": {"text": text}},
        )

    async def reply_to_comment(
        self, comment_id: str, text: str, access_token: str
    ) -> dict[str, Any]:
        return await self._request(
            "POST", f"{comment_id}/replies", access_token, data={"message": text}
        )
