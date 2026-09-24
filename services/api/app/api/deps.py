from collections.abc import AsyncGenerator

from redis.asyncio import Redis
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.db import async_session_maker
from app.core.redis import redis_client


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
