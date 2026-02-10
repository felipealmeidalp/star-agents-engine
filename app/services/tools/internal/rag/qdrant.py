"""Service for vector search in Qdrant."""

import asyncio
from typing import Any

import httpx

from app.config import settings


class QdrantService:
    """Service for vector search in Qdrant database."""

    MAX_RETRIES = 3
    RETRY_DELAY = 0.5  # seconds

    def __init__(self, base_url: str | None = None) -> None:
        """
        Initialize Qdrant service.

        Args:
            base_url: Qdrant server URL (defaults to settings.qdrant_url)
        """
        self.base_url = base_url or settings.qdrant_url

    async def search(
        self,
        collection: str,
        vector: list[float],
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Search for similar vectors in a Qdrant collection with automatic retry.

        Args:
            collection: Collection name to search in
            vector: Query vector for similarity search
            limit: Maximum number of results to return

        Returns:
            List of search results with payloads

        Raises:
            httpx.HTTPStatusError: If Qdrant API returns error after all retries
            httpx.RequestError: If connection to Qdrant fails after all retries
        """
        url = f"{self.base_url}/collections/{collection}/points/search"
        last_exception: Exception | None = None

        for attempt in range(self.MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=600.0) as client:
                    response = await client.post(
                        url,
                        headers={"Content-Type": "application/json"},
                        json={
                            "vector": vector,
                            "limit": limit,
                            "with_payload": True,
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                    return data.get("result", [])

            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                last_exception = e
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY * (attempt + 1))

        raise last_exception  # type: ignore[misc]
