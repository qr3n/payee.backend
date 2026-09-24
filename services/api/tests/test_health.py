import pytest
from httpx import AsyncClient

from app.core.config import settings


@pytest.mark.asyncio
async def test_root_health_check(client: AsyncClient) -> None:
    """Test the root /health endpoint used by load balancers and orchestrators."""
    response = await client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == settings.PROJECT_NAME
    assert data["version"] == settings.VERSION
    assert data["environment"] == settings.ENVIRONMENT
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_api_v1_health_check(client: AsyncClient) -> None:
    """Test the versioned /api/v1/health endpoint."""
    response = await client.get("/api/v1/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == settings.PROJECT_NAME
    assert data["version"] == settings.VERSION
    assert data["environment"] == settings.ENVIRONMENT
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_api_v1_readiness_check(client: AsyncClient) -> None:
    """Test the /api/v1/ready endpoint verifying DB and Redis availability."""
    response = await client.get("/api/v1/ready")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "ready"
    assert data["database"] is True
    assert data["redis"] is True
    assert "timestamp" in data
