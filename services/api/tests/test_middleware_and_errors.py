import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.broker import broker
from app.modules.items import Item


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
    # Validate it is a valid UUID
    parsed = uuid.UUID(generated_id)
    assert str(parsed) == generated_id


@pytest.mark.asyncio
async def test_validation_error_unified_format(client: AsyncClient) -> None:
    """Verify that Pydantic validation errors return unified ErrorResponse."""
    # Send empty payload to POST /api/v1/items/ where 'title' is required
    response = await client.post("/api/v1/items/", json={})

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
    response = await client.get("/api/v1/items/?page=999&size=10")

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
async def test_rate_limiter_exceeded(client: AsyncClient) -> None:
    """Verify that rate limiter returns 429 when max requests are exceeded."""
    create_resp = await client.post(
        "/api/v1/items/", json={"title": "Rate Limit Target", "description": "Desc"}
    )
    item_id = create_resp.json()["id"]

    with patch.object(broker, "kick", new_callable=AsyncMock):
        # Endpoint allows 10 requests per minute; exhaust the quota
        for _ in range(10):
            resp = await client.post(f"/api/v1/items/{item_id}/analyze")
            assert resp.status_code == 202

        # 11th request must be blocked with HTTP 429
        blocked_resp = await client.post(f"/api/v1/items/{item_id}/analyze")
        assert blocked_resp.status_code == 429
        data = blocked_resp.json()
        assert "error" in data
        assert data["error"]["code"] == "RATE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_transaction_rollback_on_error(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Verify that failed requests roll back database changes."""
    count_before = (await db_session.exec(select(func.count()).select_from(Item))).one()

    # Attempt to post an invalid item payload that fails validation
    resp = await client.post(
        "/api/v1/items/", json={"title": "", "description": "Invalid"}
    )
    assert resp.status_code == 422

    count_after = (await db_session.exec(select(func.count()).select_from(Item))).one()
    assert count_before == count_after
