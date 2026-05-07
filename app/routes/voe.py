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
from app.models.schemas import VoeChatRequest, VoeCreateCustomerRequest, VoeGetCustomerRequest
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
    Create or update a customer for VOE popup chat (smart merge).

    On a brand-new session_id, falls back to the hardcoded VOE defaults for
    agent/sub-agent. On an existing session_id, only fields explicitly sent
    in the request are updated; missing fields are preserved. The
    custom_information JSON is shallow-merged (request keys override existing
    keys; absent keys are kept).
    """
    customer_repo = CustomerRepository(db)

    custom_information_patch = {
        k: v
        for k, v in {
            "user_id": request.user_id,
            "bar_event_id": request.bar_event_id,
            "ticket_event_id": request.ticket_event_id,
            "enterprise_id": request.enterprise_id,
        }.items()
        if v is not None
    }

    try:
        customer, is_new = await customer_repo.upsert_api_customer(
            session_id=request.session_id,
            company_id=VOE_COMPANY_ID,
            agent_id=request.agent_id,
            sub_agent_id=request.sub_agent_id,
            fallback_agent_id=VOE_AGENT_ID,
            fallback_sub_agent_id=VOE_SUB_AGENT_ID,
            customer_context=request.customer_context,
            custom_information_patch=custom_information_patch,
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


@router.post("/voe/get_customer")
async def get_customer(
    request: VoeGetCustomerRequest,
    _api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Retorna o JSON `custom_information` do customer diretamente no body.

    Busca o customer pelo `customer_id` (primary key, único) e retorna o conteúdo
    bruto da coluna `custom_information`. Sem validação do shape do JSON.
    """
    customer_repo = CustomerRepository(db)

    try:
        customer = await customer_repo.get_by_id(request.customer_id)
    except Exception:
        logger.exception("[VOE] Failed to get customer customer_id=%s", request.customer_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get customer",
        )

    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found",
        )

    return customer.custom_information or {}


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
