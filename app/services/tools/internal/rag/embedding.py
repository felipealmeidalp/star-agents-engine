"""Service for generating text embeddings via OpenAI."""

import logging

from openai import AsyncOpenAI

from app.utils.alerter import send_critical_alert

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Service for generating text embeddings via OpenAI API."""

    def __init__(self, api_key: str) -> None:
        """
        Initialize embedding service with OpenAI API key.

        Args:
            api_key: OpenAI API key
        """
        self.client = AsyncOpenAI(api_key=api_key)

    async def generate(
        self,
        text: str,
        model: str = "text-embedding-3-large",
    ) -> list[float]:
        """
        Generate embedding vector for the given text.

        Args:
            text: Text to generate embedding for
            model: OpenAI embedding model to use

        Returns:
            List of floats representing the embedding vector

        Raises:
            Exception: If OpenAI API call fails
        """
        try:
            response = await self.client.embeddings.create(
                model=model,
                input=text,
                dimensions=1536,
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error("[EmbeddingService] Failed to generate embedding: %s", e)
            send_critical_alert(
                "EMBEDDING_GENERATION_FAILED",
                "rag/embedding.py:generate",
                e,
                extra=f"model={model}, text_len={len(text)}",
            )
            raise
