"""Service for generating text embeddings via OpenAI."""

from openai import AsyncOpenAI


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
        response = await self.client.embeddings.create(
            model=model,
            input=text,
            dimensions=1536,
        )

        return response.data[0].embedding
