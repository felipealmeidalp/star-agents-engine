"""VOE client-specific endpoints."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.dependencies import verify_api_key
from app.exceptions import (
    MaxIterationsExceededError,
    OpenAIAuthenticationError,
    OpenAIError,
    OpenAIRateLimitError,
    OpenAITimeoutError,
)
from app.models.schemas import VoeChatRequest, VoeCreateCustomerRequest
from app.repositories.customer import CustomerRepository
from app.services.chat_processor import process_chat

logger = logging.getLogger(__name__)

router = APIRouter()

VOE_COMPANY_ID = 4
VOE_AGENT_ID = 37
VOE_SUB_AGENT_ID = 127


@router.post("/voe/create_customer")
async def create_customer(
    request: VoeCreateCustomerRequest,
    _api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Create a customer for VOE popup chat.

    Uses hardcoded company/agent/sub-agent IDs for VOE.
    If a customer with the same session_id already exists, returns it.
    """
    customer_repo = CustomerRepository(db)

    custom_information = {
        "user_id": request.user_id,
        "bar_event_id": request.bar_event_id,
        "ticket_event_id": request.ticket_event_id,
        "enterprise_id": request.enterprise_id,
    }

    try:
        customer, is_new = await customer_repo.get_or_create_api_customer(
            session_id=request.session_id,
            company_id=VOE_COMPANY_ID,
            agent_id=VOE_AGENT_ID,
            sub_agent_id=VOE_SUB_AGENT_ID,
            customer_context=request.customer_context,
            custom_information=custom_information,
        )
    except Exception:
        logger.exception("[VOE] Failed to create customer session_id=%s", request.session_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create customer",
        )

    return {
        "customer_id": customer.id,
        "session_id": customer.sessionId,
        "company_id": customer.company_id,
        "agent_id": customer.agent_id,
        "sub_agent_id": customer.sub_agent_id,
        "is_new": is_new,
    }


@router.post("/voe/chat")
async def voe_chat(
    request: VoeChatRequest,
    _api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Process a chat message for VOE popup.

    Same as /chat but with company_id hardcoded to VOE.
    """
    try:
        return await process_chat(
            session_id=request.session_id,
            message=request.message,
            company_id=VOE_COMPANY_ID,
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
