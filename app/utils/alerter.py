"""Centralized critical alerting via WhatsApp (Evolution API) + DB persistence.

Fire-and-forget alerts that NEVER raise exceptions or block the caller.
Uses in-memory rate limiting (no Redis dependency) to prevent alert storms.
All alerts are also persisted to the error_logs table for history/search.
"""

import asyncio
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# In-memory rate limiting: {error_type: monotonic_timestamp}
_last_alert_time: dict[str, float] = {}

_SP_TZ = ZoneInfo("America/Sao_Paulo")


def _is_rate_limited(error_type: str) -> bool:
    """Check if this error_type was alerted recently."""
    last = _last_alert_time.get(error_type)
    if last is None:
        return False
    return (time.monotonic() - last) < settings.alert_rate_limit_seconds


def _mark_sent(error_type: str) -> None:
    """Record that an alert was just sent for this error_type."""
    _last_alert_time[error_type] = time.monotonic()


def _format_message(
    error_type: str,
    location: str,
    error: str,
    contact_id: int | str | None = None,
    company_id: int | str | None = None,
    extra: str | None = None,
) -> str:
    """Format alert message with WhatsApp bold markers."""
    now = datetime.now(_SP_TZ).strftime("%d/%m/%Y %H:%M:%S")
    error_truncated = str(error)[:300]

    lines = [
        "*[STAR AGENTS ALERT]*",
        f"*Tipo:* {error_type}",
        f"*Local:* {location}",
        f"*Erro:* {error_truncated}",
        f"*Hora:* {now}",
    ]

    if contact_id is not None:
        lines.append(f"*Contact:* {contact_id}")
    if company_id is not None:
        lines.append(f"*Company:* {company_id}")
    if extra:
        lines.append(f"*Info:* {str(extra)[:200]}")

    return "\n".join(lines)


async def _persist_error_log(
    error_type: str,
    location: str,
    error: str,
    contact_id: int | str | None = None,
    company_id: int | str | None = None,
    extra: str | None = None,
) -> None:
    """Persist error to DB. Never raises — failures only logged."""
    try:
        from app.db.database import AsyncSessionLocal
        from app.repositories.error_log import ErrorLogRepository

        # Determine severity from error_type
        critical_types = {
            "DATABASE_CONNECTION_FAILED", "RABBITMQ_CONNECTION_FAILED",
            "CONTEXT_BUILD_FAILED", "OPENAI_AUTH_ERROR",
            "WEBHOOK_UNHANDLED_ERROR", "WEBHOOK_POOL_EXHAUSTION_MAX_RETRIES",
            "WEBHOOK_RETRY_PUBLISH_FAILED",
        }
        warning_types = {
            "OPENAI_EXTRA_CALL_FAILED", "META_FORWARD_TIMEOUT",
        }

        if error_type in critical_types:
            severity = "critical"
        elif error_type in warning_types:
            severity = "warning"
        else:
            severity = "error"

        async with AsyncSessionLocal() as session:
            repo = ErrorLogRepository(session)
            await repo.create(
                error_type=error_type,
                location=location,
                error_message=str(error),
                severity=severity,
                company_id=int(company_id) if company_id is not None else None,
                contact_id=str(contact_id) if contact_id is not None else None,
                extra={"info": extra} if extra else None,
            )
    except Exception as exc:
        logger.warning("[Alerter] Failed to persist error log to DB: %s", exc)


async def _send_alert(text: str) -> None:
    """Send a WhatsApp message via Evolution API. Never raises."""
    url = (
        f"{settings.alert_evo_api_url}/message/sendText/"
        f"{settings.alert_evo_instance}"
    )
    headers = {"apikey": settings.alert_evo_api_key}
    payload = {
        "number": settings.alert_phone_number,
        "text": text,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                logger.warning(
                    "[Alerter] Evolution API returned %d: %s",
                    resp.status_code,
                    resp.text[:200],
                )
    except Exception as exc:
        logger.warning("[Alerter] Failed to send alert: %s", exc)


def send_critical_alert(
    error_type: str,
    location: str,
    error: str | Exception,
    contact_id: int | str | None = None,
    company_id: int | str | None = None,
    extra: str | None = None,
) -> None:
    """Public API - fire-and-forget alert.

    Creates an asyncio task to send the alert without blocking the caller.
    Applies rate limiting per error_type. Never raises exceptions.

    Args:
        error_type: Unique identifier for this error category (e.g. OPENAI_RATE_LIMIT)
        location: File and function where the error occurred
        error: Error message or exception
        contact_id: Optional contact/sender ID for context
        company_id: Optional company ID for context
        extra: Optional extra info
    """
    try:
        # Always persist to DB (independent of WhatsApp rate limiting)
        asyncio.create_task(
            _persist_error_log(
                error_type=error_type,
                location=location,
                error=str(error),
                contact_id=contact_id,
                company_id=company_id,
                extra=extra,
            )
        )

        if not settings.alert_enabled:
            return

        if not settings.alert_evo_api_key:
            return

        if _is_rate_limited(error_type):
            logger.debug("[Alerter] Rate-limited: %s", error_type)
            return

        _mark_sent(error_type)

        text = _format_message(
            error_type=error_type,
            location=location,
            error=str(error),
            contact_id=contact_id,
            company_id=company_id,
            extra=extra,
        )

        asyncio.create_task(_send_alert(text))

    except Exception as exc:
        # The alerter must NEVER break the caller
        logger.warning("[Alerter] Error in send_critical_alert: %s", exc)


async def send_critical_alert_sync(
    error_type: str,
    location: str,
    error: str | Exception,
    contact_id: int | str | None = None,
    company_id: int | str | None = None,
    extra: str | None = None,
) -> None:
    """Awaitable version for use during startup (before event loop tasks work).

    Same as send_critical_alert but awaits the HTTP call directly.
    """
    try:
        # Always persist to DB (independent of WhatsApp rate limiting)
        await _persist_error_log(
            error_type=error_type,
            location=location,
            error=str(error),
            contact_id=contact_id,
            company_id=company_id,
            extra=extra,
        )

        if not settings.alert_enabled:
            return

        if not settings.alert_evo_api_key:
            return

        if _is_rate_limited(error_type):
            return

        _mark_sent(error_type)

        text = _format_message(
            error_type=error_type,
            location=location,
            error=str(error),
            contact_id=contact_id,
            company_id=company_id,
            extra=extra,
        )

        await _send_alert(text)

    except Exception as exc:
        logger.warning("[Alerter] Error in send_critical_alert_sync: %s", exc)
