"""Authentication dependencies for API key validation."""

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config import settings

# API Key header configuration
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str | None = Security(api_key_header)) -> str:
    """
    Validate the API key from the request header.

    Args:
        api_key: The API key from the X-API-Key header.

    Returns:
        The validated API key.

    Raises:
        HTTPException: 401 if API key is missing or invalid.
    """
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Please provide X-API-Key header.",
        )

    if api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )

    return api_key
