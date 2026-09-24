from redis.asyncio import ConnectionPool, Redis

from app.core.config import settings

# Create async connection pool for Redis
redis_pool: ConnectionPool = ConnectionPool.from_url(
    settings.redis_uri,
    max_connections=settings.REDIS_MAX_CONNECTIONS,
    socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
    socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
    decode_responses=True,
)

# Shared async Redis client instance
redis_client: Redis = Redis(connection_pool=redis_pool)


async def ping_redis() -> bool:
    """Check whether Redis is reachable and responding."""
    try:
        return bool(await redis_client.ping())
    except Exception:
        return False


async def close_redis() -> None:
    """Gracefully close Redis client and connection pool."""
    try:
        await redis_client.aclose()
        await redis_pool.disconnect()
    except Exception:
        pass
