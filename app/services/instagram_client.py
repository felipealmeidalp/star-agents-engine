"""HTTP client for the Instagram API (Instagram Login)."""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_BASE_URL = "https://graph.instagram.com/v25.0"
# Meta documents this one endpoint without a version segment
REFRESH_URL = "https://graph.instagram.com/refresh_access_token"

# the refresh URL carries the token and httpx logs every request URL at INFO
logging.getLogger("httpx").setLevel(logging.WARNING)


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

    async def get_user_name(self, user_id: str, access_token: str) -> str:
        """Display name of a commenter; empty when the profile has none."""
        profile = await self._request("GET", user_id, access_token, params={"fields": "name"})
        return profile.get("name") or ""

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

    async def release_thread_control(
        self, recipient_id: str, access_token: str
    ) -> dict[str, Any]:
        """Give the conversation back so the person's reply reaches the other connected apps."""
        return await self._request(
            "POST",
            "me/release_thread_control",
            access_token,
            json={"recipient": {"id": recipient_id}},
        )

    async def reply_to_comment(
        self, comment_id: str, text: str, access_token: str
    ) -> dict[str, Any]:
        return await self._request(
            "POST", f"{comment_id}/replies", access_token, data={"message": text}
        )

    async def refresh_access_token(self, access_token: str) -> str:
        """Exchange a long-lived token for a new 60-day one."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                REFRESH_URL,
                params={"grant_type": "ig_refresh_token", "access_token": access_token},
            )
        logger.info("[InstagramClient] GET refresh_access_token: status=%d", response.status_code)
        if not response.is_success:
            # not raise_for_status(): its message quotes the URL, which here carries the token
            raise httpx.HTTPStatusError(
                f"refresh_access_token: status={response.status_code}",
                request=response.request,
                response=response,
            )
        new_token: str = response.json().get("access_token") or ""
        if not new_token:
            raise ValueError("refresh_access_token: response without access_token")
        return new_token
