"""Chat endpoints for message orchestration."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.chatwoot.client import ChatwootClient
from app.db.database import AsyncSessionLocal, get_db
from app.dependencies import verify_api_key
from app.exceptions import (
    MaxIterationsExceededError,
    OpenAIAuthenticationError,
    OpenAIError,
    OpenAIRateLimitError,
    OpenAITimeoutError,
)
from app.models.schemas import ChatRequest, ReprocessRequest
from app.repositories.company import CompanyRepository
from app.repositories.customer import CustomerRepository
from app.services.chat_processor import process_chat, reprocess_chat

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/chat")
async def chat(
    request: ChatRequest,
    _api_key: str = Depends(verify_api_key),
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


@router.post("/chat/reprocess")
async def reprocess(
    request: ReprocessRequest,
    _api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Reprocess chat for a customer using existing history.

    Used when AI is re-enabled — validates customer/company synchronously,
    then fires off the AI pipeline + Chatwoot send in the background.
    Returns immediately with 202 Accepted.
    """
    # 1. Fetch customer (sync validation)
    customer_repo = CustomerRepository(db)
    customer = await customer_repo.get_by_id(request.customer_id)
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found",
        )

    # 2. Fetch company (sync validation)
    company_repo = CompanyRepository(db)
    company = await company_repo.get_by_id(customer.company_id)
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found",
        )

    # Capture values before the request DB session closes
    session_id = customer.sessionId
    company_id = customer.company_id
    cw_base_url = company.cw_base_url
    cw_account_id = company.cw_account_id
    cw_conversation_id = customer.cw_conversation_id
    cw_apikey = company.cw_apikey

    # 3. Fire background task with its own DB session
    asyncio.create_task(
        _reprocess_background(
            session_id=session_id,
            company_id=company_id,
            cw_base_url=cw_base_url,
            cw_account_id=cw_account_id,
            cw_conversation_id=cw_conversation_id,
            cw_apikey=cw_apikey,
        )
    )

    return {"status": "accepted", "session_id": session_id}


async def _reprocess_background(
    session_id: str,
    company_id: int,
    cw_base_url: str | None,
    cw_account_id: int | None,
    cw_conversation_id: int | None,
    cw_apikey: str | None,
) -> None:
    """Run AI pipeline and send to Chatwoot in background."""
    try:
        async with AsyncSessionLocal() as db:
            async def _send_private_notes(messages: list[str]) -> None:
                """Send tool results as private notes during reprocessing."""
                if cw_base_url and cw_apikey and cw_conversation_id and cw_account_id:
                    client = ChatwootClient()
                    # Consolidate into single note
                    if len(messages) > 1:
                        messages = ["\n\n".join(messages)]
                    await client.send_messages(
                        base_url=cw_base_url,
                        account_id=cw_account_id,
                        conversation_id=cw_conversation_id,
                        messages=messages,
                        api_key=cw_apikey,
                        private=True,
                    )

            async def _send_messages(messages: list[str]) -> None:
                """Send pre-tool messages during reprocessing."""
                if cw_base_url and cw_apikey and cw_conversation_id and cw_account_id:
                    client = ChatwootClient()
                    await client.send_messages(
                        base_url=cw_base_url,
                        account_id=cw_account_id,
                        conversation_id=cw_conversation_id,
                        messages=messages,
                        api_key=cw_apikey,
                    )

            response = await reprocess_chat(
                session_id=session_id,
                company_id=company_id,
                db=db,
                on_send_messages=_send_messages,
                on_send_private_notes=_send_private_notes,
            )

            messages = response.get("resposta", [])
            if messages and cw_base_url and cw_apikey:
                client = ChatwootClient()
                await client.send_messages(
                    base_url=cw_base_url,
                    account_id=cw_account_id,
                    conversation_id=cw_conversation_id,
                    messages=messages,
                    api_key=cw_apikey,
                )

        logger.info(
            "[Reprocess] Done for session=%s, sent %d messages",
            session_id,
            len(messages),
        )
    except Exception:
        logger.exception("[Reprocess] Background task failed for session=%s", session_id)
