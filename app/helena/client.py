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
        session_id: str,
        text: str,
        apikey: str,
    ) -> dict[str, Any]:
        """
        Send a single text message into an existing Helena session.

        Replies go through `POST /v1/session/{id}/message`, which routes by the
        conversation's sessionId. This is the only send contract that works for
        non-WhatsApp channels (Instagram/Facebook): `POST /v1/message/send` by
        phone returns 200 QUEUED but the status resolves to FAILED with "não
        suportado neste tipo de canal de atendimento". See
        https://helena.readme.io/reference/post_v1-session-id-message.

        Args:
            session_id: Helena session id (webhook's content.sessionId) — required
            text: Message content
            apikey: Bearer token (company.helena_apikey) for authentication

        Returns:
            Dict with API response (e.g. {id, status: "QUEUED", ...})

        Raises:
            httpx.HTTPStatusError: If Helena returns a non-2xx status
            httpx.RequestError: If the connection fails
        """
        url = f"{settings.helena_base_url}/v1/session/{session_id}/message"
        headers = {
            "Authorization": f"Bearer {apikey}",
            "Content-Type": "application/json",
        }
        payload = {"text": text}

        logger.info("[HelenaClient] Sending message to session %s", session_id)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(url, json=payload, headers=headers)
            logger.info("[HelenaClient] Response: status=%d", response.status_code)
            response.raise_for_status()
            return response.json()

    async def assign_session(
        self,
        session_id: str,
        user_id: str,
        apikey: str,
    ) -> dict[str, Any]:
        """
        Assign a Helena session to a fixed attendant, stopping Helena's own bot.

        Used by transfer_to_human on the Helena channel to hand the conversation
        to a human (`stopBotInExecution: true`). Modeled on send_text (Bearer,
        raise_for_status).

        Args:
            session_id: Helena session id (Customer.sessionId == content.sessionId)
            user_id: Helena attendant userId (company.helena_assignee_id)
            apikey: Bearer token (company.helena_apikey)

        Returns:
            Dict with API response

        Raises:
            httpx.HTTPStatusError: If Helena returns a non-2xx status
            httpx.RequestError: If the connection fails
        """
        # Assignee lives under /chat (same messaging service as send). Helena's
        # assignee endpoint requires Content-Type application/*+json, not
        # application/json.
        # ponytail: content.sessionId assumed == the {id} the session API wants;
        #           confirmed working on the first real escalation test.
        url = f"{settings.helena_base_url}/v1/session/{session_id}/assignee"
        headers = {
            "Authorization": f"Bearer {apikey}",
            "Content-Type": "application/json",
        }
        # ponytail: doc lists both `id` and `userId` on the agent object — using
        #           `userId`; confirm on the first real escalation.
        payload = {"userId": user_id, "options": {"stopBotInExecution": True}}

        logger.info("[HelenaClient] Assigning session %s to %s", session_id, user_id)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.put(url, json=payload, headers=headers)
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
        Send multiple messages into a session with humanized delays.

        The first message is sent immediately (AI processing already provides a
        natural pause). Each subsequent message is preceded by a humanized delay
        based on its length. A per-message send failure fires a critical alert
        and processing continues with the next message.

        Args:
            session_id: Helena session id (Helena routes the reply by session)
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
