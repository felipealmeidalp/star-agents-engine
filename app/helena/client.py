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
    """Client for sending messages back to Helena via `send/text`."""

    def __init__(self, timeout: int = 30) -> None:
        """Initialize client with configurable timeout."""
        self.timeout = timeout

    async def send_text(
        self,
        session_id: str,
        text: str,
        apikey: str,
    ) -> dict[str, Any]:
        """
        Send a single text message to a Helena session.

        Args:
            session_id: Helena conversation sessionId (also the customer key)
            text: Message content
            apikey: Bearer token (company.helena_apikey) for authentication

        Returns:
            Dict with API response (e.g. {id, sessionId, status: "QUEUED"})

        Raises:
            httpx.HTTPStatusError: If Helena returns a non-2xx status
            httpx.RequestError: If the connection fails
        """
        url = f"{settings.helena_base_url}{settings.helena_send_text_path}"
        headers = {
            "Authorization": f"Bearer {apikey}",
            "Content-Type": "application/json",
        }
        # ponytail: confirmar no primeiro envio real que `send/text` aceita responder
        # só com sessionId (sem `to`). Se o Helena exigir `to`, cair para o telefone
        # do lead em details.from (já disponível via custom_information do customer).
        payload = {"sessionId": session_id, "text": text}

        logger.info("[HelenaClient] Sending message to session %s", session_id)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, json=payload, headers=headers)
            logger.info("[HelenaClient] Response: status=%d", response.status_code)
            response.raise_for_status()
            return response.json()

    async def send_messages(
        self,
        session_id: str,
        messages: list[str],
        apikey: str,
    ) -> list[dict[str, Any]]:
        """
        Send multiple messages to a Helena session with humanized delays.

        The first message is sent immediately (AI processing already provides a
        natural pause). Each subsequent message is preceded by a humanized delay
        based on its length. A per-message send failure fires a critical alert
        and processing continues with the next message.

        Args:
            session_id: Helena conversation sessionId
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
                result = await self.send_text(session_id, message, apikey)
                results.append(result)
            except Exception as e:
                logger.error("[HelenaClient] Failed to send message: %s", e)
                send_critical_alert(
                    "HELENA_SEND_FAILED",
                    "helena/client.py:send_messages",
                    e,
                    extra=f"session={session_id}",
                )
                results.append({"error": str(e)})

        return results
