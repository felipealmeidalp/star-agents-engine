"""HTTP client for Helena API communication."""

import asyncio
import logging
from typing import Any

import httpx

from app.chatwoot.client import calculate_humanized_delay
from app.config import settings
from app.utils.alerter import send_critical_alert

logger = logging.getLogger(__name__)


class HelenaClient:
    """Client for sending messages back to a lead via Helena's `message/send`."""

    def __init__(self, timeout: int = 30) -> None:
        """Initialize client with configurable timeout."""
        self.timeout = timeout

    async def send_text(
        self,
        to: str,
        text: str,
        apikey: str,
    ) -> dict[str, Any]:
        """
        Send a single text message to a lead through Helena.

        Helena's `POST /v1/message/send` routes by the recipient phone (`to`),
        not by a sessionId — there is no sessionId in the send contract; a new
        Helena session auto-creates if needed. See
        https://helena.readme.io/reference/post_v1-message-send.

        Args:
            to: Lead phone number (from the webhook's `details.from`) — required
            text: Message content
            apikey: Bearer token (company.helena_apikey) for authentication

        Returns:
            Dict with API response (e.g. {id, status: "QUEUED", ...})

        Raises:
            httpx.HTTPStatusError: If Helena returns a non-2xx status
            httpx.RequestError: If the connection fails
        """
        url = f"{settings.helena_base_url}{settings.helena_send_text_path}"
        headers = {
            "Authorization": f"Bearer {apikey}",
            "Content-Type": "application/json",
        }
        payload = {"to": to, "body": {"text": text}}

        logger.info("[HelenaClient] Sending message to %s", to)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, json=payload, headers=headers)
            logger.info("[HelenaClient] Response: status=%d", response.status_code)
            response.raise_for_status()
            return response.json()

    async def send_messages(
        self,
        to: str,
        messages: list[str],
        apikey: str,
    ) -> list[dict[str, Any]]:
        """
        Send multiple messages to a lead with humanized delays.

        The first message is sent immediately (AI processing already provides a
        natural pause). Each subsequent message is preceded by a humanized delay
        based on its length. A per-message send failure fires a critical alert
        and processing continues with the next message.

        Args:
            to: Lead phone number (Helena routes the reply by phone)
            messages: List of message strings
            apikey: Bearer token for authentication

        Returns:
            List of API responses (an {"error": ...} entry for a failed message)
        """
        results: list[dict[str, Any]] = []

        for i, message in enumerate(messages):
            if i != 0:
                delay = calculate_humanized_delay(message)
                logger.debug(
                    "[HelenaClient] Waiting %.1fs before sending message (%d chars)",
                    delay,
                    len(message),
                )
                await asyncio.sleep(delay)

            try:
                result = await self.send_text(to, message, apikey)
                results.append(result)
            except Exception as e:
                logger.error("[HelenaClient] Failed to send message: %s", e)
                send_critical_alert(
                    "HELENA_SEND_FAILED",
                    "helena/client.py:send_messages",
                    e,
                    extra=f"to={to}",
                )
                results.append({"error": str(e)})

        return results
