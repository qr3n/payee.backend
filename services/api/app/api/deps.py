from collections.abc import AsyncGenerator

from fastapi import Security
from fastapi.security import APIKeyHeader
from redis.asyncio import Redis
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.db import async_session_maker
from app.core.exceptions import AppException
from app.core.redis import redis_client

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_admin_key(
    api_key: str | None = Security(api_key_header),
) -> None:
    """
    Enforces administrative authentication via X-API-Key header.
    If ADMIN_API_KEY is configured in settings or running in production,
    requests must provide a valid key.
    """
    expected = (
        settings.ADMIN_API_KEY.get_secret_value() if settings.ADMIN_API_KEY else None
    )
    if expected:
        if not api_key or api_key != expected:
            raise AppException(
                message="Invalid or missing administrative API key.",
                code="UNAUTHORIZED",
                status_code=401,
            )
    elif settings.ENVIRONMENT == "production":
        raise AppException(
            message="Administrative API key is not configured in production.",
            code="UNAUTHORIZED",
            status_code=401,
        )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides an isolated async database session per request.
    Automatically commits the transaction on successful response,
    or rolls back on unhandled error before closing the session.
    """
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_redis() -> AsyncGenerator[Redis, None]:
    """
    FastAPI dependency yielding the shared async Redis client.
    Can be overridden in tests using app.dependency_overrides[get_redis].
    """
    yield redis_client
