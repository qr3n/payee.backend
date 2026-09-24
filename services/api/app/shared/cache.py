from typing import Any

import orjson
from redis.asyncio import Redis


class CacheService:
    """Service encapsulating Redis caching operations using orjson serialization."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def get(self, key: str) -> str | None:
        """Retrieve a string value from Redis by key."""
        value = await self.redis.get(key)
        return str(value) if value is not None else None

    async def set(
        self, key: str, value: str, expire_seconds: int | None = None
    ) -> bool:
        """Store a string value in Redis with an optional TTL in seconds."""
        if expire_seconds is not None:
            return bool(await self.redis.set(key, value, ex=expire_seconds))
        return bool(await self.redis.set(key, value))

    async def delete(self, key: str) -> bool:
        """Remove a key from Redis."""
        return bool(await self.redis.delete(key))

    async def get_json(self, key: str) -> Any | None:
        """Retrieve and deserialize a value from Redis using orjson."""
        raw = await self.get(key)
        if raw is None:
            return None
        return orjson.loads(raw)

    async def set_json(
        self, key: str, value: Any, expire_seconds: int | None = None
    ) -> bool:
        """Serialize with orjson and store a value in Redis with an optional TTL."""
        serialized = orjson.dumps(value, default=str).decode("utf-8")
        return await self.set(key, serialized, expire_seconds=expire_seconds)
