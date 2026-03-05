"""HTTP client for Chatwoot API communication."""

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Constantes para cálculo de delay humanizado
DELAY_PER_CHARACTER = 0.05  # segundos por caractere
DELAY_DISCOUNT = 3.5  # desconto fixo em segundos
MIN_DELAY = 2.0  # delay mínimo em segundos
MAX_DELAY = 15.0  # delay máximo em segundos


def calculate_humanized_delay(message: str) -> float:
    """
    Calcula o delay humanizado baseado no tamanho da mensagem.

    Fórmula: tempo = (caracteres × 0.1) - 2.5
    Com limites de 2s (mínimo) e 25s (máximo).

    Args:
        message: Texto da mensagem para calcular o delay

    Returns:
        Delay em segundos (entre 2.0 e 25.0)
    """
    char_count = len(message)
    delay = (char_count * DELAY_PER_CHARACTER) - DELAY_DISCOUNT
    return max(MIN_DELAY, min(MAX_DELAY, delay))


class ChatwootClient:
    """Client for sending messages to Chatwoot API."""

    def __init__(self, timeout: int = 30) -> None:
        """Initialize client with configurable timeout."""
        self.timeout = timeout

    async def send_message(
        self,
        base_url: str,
        account_id: int,
        conversation_id: int,
        message: str,
        api_key: str,
        private: bool = False,
    ) -> dict[str, Any]:
        """
        Send a message to a Chatwoot conversation.

        Args:
            base_url: Chatwoot instance base URL (e.g., https://app.chatwoot.com)
            account_id: Chatwoot account ID
            conversation_id: Conversation ID to send message to
            message: Message content
            api_key: API key for authentication
            private: If True, send as private note (invisible to contact)

        Returns:
            Dict with API response

        Raises:
            httpx.TimeoutException: If request times out
            httpx.RequestError: If connection fails
        """
        url = (
            f"{base_url}/api/v1/accounts/{account_id}"
            f"/conversations/{conversation_id}/messages"
        )

        headers = {
            "api_access_token": api_key,
            "Content-Type": "application/json",
        }

        payload = {
            "content": message,
            "message_type": "outgoing",
            "private": private,
        }

        logger.info(
            f"[ChatwootClient] Sending message to conversation {conversation_id}"
        )

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                url,
                json=payload,
                headers=headers,
            )

            logger.info(
                f"[ChatwootClient] Response: status={response.status_code}"
            )

            response.raise_for_status()
            return response.json()

    async def assign_conversation_to_team(
        self,
        base_url: str,
        account_id: int,
        conversation_id: int,
        team_id: int,
        api_key: str,
    ) -> dict[str, Any]:
        """
        Assign a conversation to a team in Chatwoot.

        Args:
            base_url: Chatwoot instance base URL
            account_id: Chatwoot account ID
            conversation_id: Conversation ID to assign
            team_id: Team ID to assign the conversation to
            api_key: API key for authentication

        Returns:
            Dict with API response

        Raises:
            httpx.TimeoutException: If request times out
            httpx.RequestError: If connection fails
        """
        url = (
            f"{base_url}/api/v1/accounts/{account_id}"
            f"/conversations/{conversation_id}/assignments"
        )

        headers = {
            "api_access_token": api_key,
            "Content-Type": "application/json",
        }

        payload = {"team_id": team_id}

        logger.info(
            "[ChatwootClient] Assigning conversation %d to team %d",
            conversation_id,
            team_id,
        )

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                url,
                json=payload,
                headers=headers,
            )

            logger.info(
                "[ChatwootClient] Assignment response: status=%d",
                response.status_code,
            )

            response.raise_for_status()
            return response.json()

    async def add_label_to_conversation(
        self,
        base_url: str,
        account_id: int,
        conversation_id: int,
        label: str,
        api_key: str,
    ) -> None:
        """
        Add a label to a Chatwoot conversation (idempotent).

        The Chatwoot API replaces all labels on POST, so this method
        first GETs existing labels and appends the new one if missing.

        Args:
            base_url: Chatwoot instance base URL
            account_id: Chatwoot account ID
            conversation_id: Conversation ID
            label: Label to add
            api_key: API key for authentication
        """
        url = (
            f"{base_url}/api/v1/accounts/{account_id}"
            f"/conversations/{conversation_id}/labels"
        )
        headers = {
            "api_access_token": api_key,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            # GET current labels
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            current_labels: list[str] = response.json().get("payload", [])

            if label in current_labels:
                logger.info(
                    "[ChatwootClient] Label '%s' already exists on conversation %d",
                    label,
                    conversation_id,
                )
                return

            # POST with new label appended
            updated_labels = current_labels + [label]
            response = await client.post(
                url,
                json={"labels": updated_labels},
                headers=headers,
            )
            response.raise_for_status()

            logger.info(
                "[ChatwootClient] Label '%s' added to conversation %d",
                label,
                conversation_id,
            )

    async def swap_label(
        self,
        base_url: str,
        account_id: int,
        conversation_id: int,
        add: str,
        remove: str,
        api_key: str,
    ) -> None:
        """
        Add a label and remove another atomically (idempotent).

        GETs current labels, removes `remove` if present, appends `add` if missing,
        then POSTs the updated list only if it changed.

        Args:
            base_url: Chatwoot instance base URL
            account_id: Chatwoot account ID
            conversation_id: Conversation ID
            add: Label to add
            remove: Label to remove
            api_key: API key for authentication
        """
        url = (
            f"{base_url}/api/v1/accounts/{account_id}"
            f"/conversations/{conversation_id}/labels"
        )
        headers = {
            "api_access_token": api_key,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            current_labels: list[str] = response.json().get("payload", [])

            updated = [label for label in current_labels if label != remove]
            if add not in updated:
                updated.append(add)

            if updated != current_labels:
                await client.post(url, json={"labels": updated}, headers=headers)
                logger.info(
                    "[ChatwootClient] swap_label: added '%s', removed '%s' on conversation %d",
                    add,
                    remove,
                    conversation_id,
                )

    async def send_messages(
        self,
        base_url: str,
        account_id: int,
        conversation_id: int,
        messages: list[str],
        api_key: str,
        private: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Send multiple messages to a Chatwoot conversation with humanized delays.

        The first message is sent immediately (no delay, since AI processing time
        already provides a natural pause). Subsequent messages have a delay based
        on the previous message length to simulate human typing speed.

        Args:
            base_url: Chatwoot instance base URL
            account_id: Chatwoot account ID
            conversation_id: Conversation ID
            messages: List of message strings
            api_key: API key for authentication

        Returns:
            List of API responses
        """
        results = []

        for i, message in enumerate(messages):
            # Aplica delay antes de enviar (exceto para a primeira mensagem)
            # O delay é baseado no tamanho da mensagem atual (simula tempo de digitação)
            is_first_message = i == 0
            if not is_first_message:
                delay = calculate_humanized_delay(message)
                logger.debug(
                    f"[ChatwootClient] Waiting {delay:.1f}s before sending message "
                    f"({len(message)} chars)"
                )
                await asyncio.sleep(delay)

            try:
                result = await self.send_message(
                    base_url=base_url,
                    account_id=account_id,
                    conversation_id=conversation_id,
                    message=message,
                    api_key=api_key,
                    private=private,
                )
                results.append(result)
            except Exception as e:
                logger.error(f"[ChatwootClient] Failed to send message: {e}")
                results.append({"error": str(e)})

        return results
