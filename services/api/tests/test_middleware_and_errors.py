"""
Integration tests for middlewares, RFC 9457 error formatting, and rate limiting.
"""

import uuid
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.exceptions import RateLimitException
from app.core.rate_limit import RateLimiter
from app.modules.accounts.models import TelegramAccount


@pytest.mark.asyncio
async def test_custom_request_id_header(client: AsyncClient) -> None:
    """Verify that an incoming X-Request-ID is preserved and echoed in response."""
    custom_id = f"custom-req-{uuid.uuid4()}"
    response = await client.get("/health", headers={"X-Request-ID": custom_id})

    assert response.status_code == 200
    assert response.headers.get("x-request-id") == custom_id


@pytest.mark.asyncio
async def test_generated_request_id_header(client: AsyncClient) -> None:
    """Verify that a missing X-Request-ID is automatically generated as UUID."""
    response = await client.get("/health")

    assert response.status_code == 200
    generated_id = response.headers.get("x-request-id")
    assert generated_id is not None
    parsed = uuid.UUID(generated_id)
    assert str(parsed) == generated_id


@pytest.mark.asyncio
async def test_validation_error_unified_format(client: AsyncClient) -> None:
    """Verify that Pydantic validation errors return unified ErrorResponse."""
    # Send empty payload to POST /api/v1/accounts/ where 'title' is required
    response = await client.post("/api/v1/accounts/", json={})

    assert response.status_code == 422
    assert "x-request-id" in response.headers

    data = response.json()
    assert "error" in data
    error = data["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert "Request payload or parameters validation failed." in error["message"]
    assert isinstance(error["details"], list)
    assert len(error["details"]) > 0
    assert error["request_id"] == response.headers["x-request-id"]


@pytest.mark.asyncio
async def test_pagination_out_of_bounds(client: AsyncClient) -> None:
    """Verify pagination behavior for out-of-range page requests."""
    response = await client.get("/api/v1/accounts/?page=999&size=10")

    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["page"] == 999
    assert data["size"] == 10


@pytest.mark.asyncio
async def test_prometheus_metrics_endpoint(client: AsyncClient) -> None:
    """Verify that Prometheus metrics are exposed at /metrics."""
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "process_cpu" in response.text or "http" in response.text


@pytest.mark.asyncio
async def test_rate_limiter_exceeded_unit(fake_redis: Redis) -> None:
    """Verify that RateLimiter raises RateLimitException when limit exceeded."""
    limiter = RateLimiter(max_requests=5, window_seconds=60, key_prefix="test_rl")
    mock_request = MagicMock()
    mock_request.headers.get.return_value = None
    mock_request.client.host = "192.168.1.1"
    mock_request.scope = {"path": "/test"}

    # Execute 5 allowed requests
    for _ in range(5):
        await limiter(request=mock_request, redis=fake_redis)

    # 6th request must trigger RateLimitException
    with pytest.raises(RateLimitException) as exc_info:
        await limiter(request=mock_request, redis=fake_redis)
    assert exc_info.value.code == "RATE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_transaction_rollback_on_error(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Verify that failed requests roll back database changes."""
    count_before = (
        await db_session.exec(select(func.count()).select_from(TelegramAccount))
    ).one()

    # Attempt to post an invalid account payload that fails validation
    resp = await client.post(
        "/api/v1/accounts/", json={"title": "", "session_string": "short"}
    )
    assert resp.status_code == 422

    count_after = (
        await db_session.exec(select(func.count()).select_from(TelegramAccount))
    ).one()
    assert count_before == count_after
