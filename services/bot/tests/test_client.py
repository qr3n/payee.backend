import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from bot.client.api import ApiClient
from bot.client.schemas import HealthCheckResponse, ReadinessResponse


@pytest.mark.asyncio
async def test_client_get_health() -> None:
    now_str = datetime.now(UTC).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "service": "FastAPI Service",
                "version": "0.1.0",
                "environment": "test",
                "timestamp": now_str,
            },
        )

    transport = httpx.MockTransport(handler)
    api_client = ApiClient(base_url="http://test-server")
    # Inject mock client
    api_client._client = httpx.AsyncClient(
        transport=transport, base_url="http://test-server"
    )

    try:
        health = await api_client.get_health()
        assert isinstance(health, HealthCheckResponse)
        assert health.status == "ok"
        assert health.service == "FastAPI Service"
    finally:
        await api_client.close()


@pytest.mark.asyncio
async def test_client_get_readiness() -> None:
    now_str = datetime.now(UTC).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ready"
        return httpx.Response(
            200,
            json={
                "status": "ready",
                "database": True,
                "redis": True,
                "timestamp": now_str,
            },
        )

    transport = httpx.MockTransport(handler)
    api_client = ApiClient(base_url="http://test-server")
    api_client._client = httpx.AsyncClient(
        transport=transport, base_url="http://test-server"
    )

    try:
        readiness = await api_client.get_readiness()
        assert isinstance(readiness, ReadinessResponse)
        assert readiness.status == "ready"
        assert readiness.database is True
        assert readiness.redis is True
    finally:
        await api_client.close()


@pytest.mark.asyncio
async def test_client_list_items() -> None:
    item_id = str(uuid4())
    now_str = datetime.now(UTC).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/items/"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": item_id,
                        "title": "Test Item",
                        "description": "Test Desc",
                        "is_active": True,
                        "created_at": now_str,
                        "updated_at": now_str,
                    }
                ],
                "total": 1,
                "page": 1,
                "size": 10,
                "pages": 1,
            },
        )

    transport = httpx.MockTransport(handler)
    api_client = ApiClient(base_url="http://test-server")
    api_client._client = httpx.AsyncClient(
        transport=transport, base_url="http://test-server"
    )

    try:
        paginated = await api_client.list_items()
        assert paginated.total == 1
        assert len(paginated.items) == 1
        assert paginated.items[0].title == "Test Item"
    finally:
        await api_client.close()


@pytest.mark.asyncio
async def test_client_get_item_not_found() -> None:
    item_id = str(uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/items/{item_id}"
        return httpx.Response(404, json={"detail": "Not found"})

    transport = httpx.MockTransport(handler)
    api_client = ApiClient(base_url="http://test-server")
    api_client._client = httpx.AsyncClient(
        transport=transport, base_url="http://test-server"
    )

    try:
        item = await api_client.get_item(item_id)
        assert item is None
    finally:
        await api_client.close()


@pytest.mark.asyncio
async def test_client_create_item() -> None:
    item_id = str(uuid4())
    now_str = datetime.now(UTC).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/items/"
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["title"] == "New Item"
        return httpx.Response(
            201,
            json={
                "id": item_id,
                "title": body["title"],
                "description": body.get("description"),
                "is_active": True,
                "created_at": now_str,
                "updated_at": now_str,
            },
        )

    transport = httpx.MockTransport(handler)
    api_client = ApiClient(base_url="http://test-server")
    api_client._client = httpx.AsyncClient(
        transport=transport, base_url="http://test-server"
    )

    try:
        created = await api_client.create_item("New Item", "Description")
        assert str(created.id) == item_id
        assert created.title == "New Item"
    finally:
        await api_client.close()


@pytest.mark.asyncio
async def test_client_analyze_item() -> None:
    item_id = str(uuid4())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/items/{item_id}/analyze"
        assert request.method == "POST"
        return httpx.Response(202, json={"task_id": "task-uuid-12345"})

    transport = httpx.MockTransport(handler)
    api_client = ApiClient(base_url="http://test-server")
    api_client._client = httpx.AsyncClient(
        transport=transport, base_url="http://test-server"
    )

    try:
        result = await api_client.analyze_item(item_id)
        assert result["task_id"] == "task-uuid-12345"
    finally:
        await api_client.close()


@pytest.mark.asyncio
async def test_client_ask_ai() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/ai/chat"
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["prompt"] == "Hello AI"
        return httpx.Response(
            200,
            json={
                "conversation_id": "conv-123",
                "response": "Hello human!",
                "model": "deepseek-v3",
                "search_enabled": False,
                "file_ids": [],
                "citations": [
                    {
                        "title": "Example Source",
                        "url": "https://example.com",
                        "snippet": "Example snippet",
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    api_client = ApiClient(base_url="http://test-server")
    api_client._client = httpx.AsyncClient(
        transport=transport, base_url="http://test-server"
    )

    try:
        resp = await api_client.ask_ai("Hello AI")
        assert resp.conversation_id == "conv-123"
        assert resp.response == "Hello human!"
        assert len(resp.citations) == 1
        assert resp.citations[0].title == "Example Source"
    finally:
        await api_client.close()


@pytest.mark.asyncio
async def test_client_reset_ai_conversation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/ai/conversations/conv-123"
        assert request.method == "DELETE"
        return httpx.Response(
            200,
            json={
                "conversation_id": "conv-123",
                "reset": True,
            },
        )

    transport = httpx.MockTransport(handler)
    api_client = ApiClient(base_url="http://test-server")
    api_client._client = httpx.AsyncClient(
        transport=transport, base_url="http://test-server"
    )

    try:
        resp = await api_client.reset_ai_conversation("conv-123")
        assert resp.conversation_id == "conv-123"
        assert resp.reset is True
    finally:
        await api_client.close()
