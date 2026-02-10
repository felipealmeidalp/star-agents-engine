"""Chat endpoints for message orchestration."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.exceptions import (
    MaxIterationsExceededError,
    OpenAIAuthenticationError,
    OpenAIError,
    OpenAIRateLimitError,
    OpenAITimeoutError,
)
from app.models.schemas import ChatRequest
from app.services.chat_processor import process_chat

router = APIRouter()


@router.post("/chat")
async def chat(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Process a chat message with full tool calling support.

    Args:
        request: Chat request with session_id, message and company_id
        db: Database session from dependency injection

    Returns:
        Dict with the assistant's final response
    """
    try:
        return await process_chat(
            session_id=request.session_id,
            message=request.message,
            company_id=request.company_id,
            db=db,
        )

    except MaxIterationsExceededError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
    except ValueError as e:
        error_msg = str(e)
        if "API key" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=error_msg,
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_msg,
        )
    except OpenAIAuthenticationError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )
    except OpenAIRateLimitError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
        )
    except OpenAITimeoutError as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(e),
        )
    except OpenAIError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(e),
        )
