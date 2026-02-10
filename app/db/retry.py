"""Database retry utilities with exponential backoff."""

from functools import wraps
from typing import Any, Callable, TypeVar

from sqlalchemy.exc import InterfaceError, OperationalError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.exceptions import TransientDatabaseError

T = TypeVar("T")


def with_db_retry(
    max_attempts: int = 3,
    min_wait: float = 1,
    max_wait: float = 10,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator for database operations with automatic retry on transient failures.

    Args:
        max_attempts: Maximum number of retry attempts (default: 3)
        min_wait: Minimum wait time in seconds (default: 1)
        max_wait: Maximum wait time in seconds (default: 10)

    Returns:
        Decorated async function with retry logic.

    Example:
        @with_db_retry(max_attempts=3)
        async def get_agent(session: AsyncSession, agent_id: int) -> Agent:
            result = await session.execute(select(Agent).where(Agent.id == agent_id))
            return result.scalar_one()
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
            retry=retry_if_exception_type(
                (
                    TransientDatabaseError,
                    OperationalError,
                    InterfaceError,
                )
            ),
            reraise=True,
        )
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            return await func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
