import pytest
from redis.asyncio import Redis

from app.shared.cache import CacheService


@pytest.mark.asyncio
async def test_redis_ping_and_operations(fake_redis: Redis) -> None:
    """Test standard async Redis operations (ping, set, get, delete)."""
    assert await fake_redis.ping() is True

    # String operations
    await fake_redis.set("test_key", "test_value")
    value = await fake_redis.get("test_key")
    assert value == "test_value"

    # Delete
    deleted = await fake_redis.delete("test_key")
    assert deleted == 1
    assert await fake_redis.get("test_key") is None


@pytest.mark.asyncio
async def test_cache_service_strings(fake_redis: Redis) -> None:
    """Test CacheService basic string caching methods."""
    cache = CacheService(fake_redis)

    # Initial state
    assert await cache.get("missing_key") is None

    # Set and Get
    assert await cache.set("my_key", "my_val") is True
    assert await cache.get("my_key") == "my_val"

    # Delete
    assert await cache.delete("my_key") is True
    assert await cache.get("my_key") is None


@pytest.mark.asyncio
async def test_cache_service_json(fake_redis: Redis) -> None:
    """Test CacheService JSON serialization and deserialization."""
    cache = CacheService(fake_redis)

    payload = {"user_id": 42, "roles": ["admin", "developer"], "active": True}

    assert await cache.get_json("user_payload") is None
    assert await cache.set_json("user_payload", payload, expire_seconds=60) is True

    cached_payload = await cache.get_json("user_payload")
    assert cached_payload == payload
    assert cached_payload["user_id"] == 42
    assert cached_payload["roles"] == ["admin", "developer"]


@pytest.mark.asyncio
async def test_cache_service_orjson_complex_types(fake_redis: Redis) -> None:
    """Test CacheService handles UUID and datetime seamlessly with orjson."""
    from datetime import UTC, datetime

    from uuid6 import uuid7

    cache = CacheService(fake_redis)
    test_id = uuid7()
    test_time = datetime.now(UTC)

    payload = {"id": test_id, "timestamp": test_time}
    assert await cache.set_json("complex_key", payload) is True

    result = await cache.get_json("complex_key")
    assert isinstance(result, dict)
    assert result["id"] == str(test_id)
    assert "timestamp" in result
