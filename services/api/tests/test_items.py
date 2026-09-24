from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_item(client: AsyncClient) -> None:
    """Test creating a new item via POST /api/v1/items/ with UUIDv7."""
    payload = {
        "title": "Build Microservice Template",
        "description": "A production-ready template with FastAPI and SQLModel.",
        "is_active": True,
    }
    response = await client.post("/api/v1/items/", json=payload)
    assert response.status_code == 201
    assert "x-request-id" in response.headers

    data = response.json()
    assert data["title"] == payload["title"]
    assert data["description"] == payload["description"]
    assert data["is_active"] is True
    assert "id" in data
    # Verify the ID is a valid UUIDv7
    parsed_uuid = UUID(data["id"])
    assert parsed_uuid.version == 7
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_list_items(client: AsyncClient) -> None:
    """Test listing items via GET /api/v1/items/ with pagination."""
    # Create two items
    await client.post(
        "/api/v1/items/", json={"title": "Item 1", "description": "Desc 1"}
    )
    await client.post(
        "/api/v1/items/", json={"title": "Item 2", "description": "Desc 2"}
    )

    response = await client.get("/api/v1/items/?page=1&size=10")
    assert response.status_code == 200
    assert "x-request-id" in response.headers

    data = response.json()
    assert "items" in data
    assert len(data["items"]) >= 2
    assert data["total"] >= 2
    assert data["page"] == 1
    assert data["size"] == 10
    assert data["pages"] >= 1


@pytest.mark.asyncio
async def test_list_items_cursor(client: AsyncClient) -> None:
    """Test listing items via GET /api/v1/items/cursor with keyset pagination."""
    # Create 3 distinct items
    for i in range(1, 4):
        await client.post(
            "/api/v1/items/",
            json={"title": f"Cursor Item {i}", "description": f"Desc {i}"},
        )

    # First page: size=2
    response1 = await client.get("/api/v1/items/cursor?size=2")
    assert response1.status_code == 200
    assert "x-request-id" in response1.headers

    data1 = response1.json()
    assert "items" in data1
    assert len(data1["items"]) == 2
    assert data1["has_more"] is True
    assert data1["next_cursor"] is not None

    # Second page: use returned cursor
    next_cursor = data1["next_cursor"]
    response2 = await client.get(f"/api/v1/items/cursor?cursor={next_cursor}&size=2")
    assert response2.status_code == 200

    data2 = response2.json()
    assert len(data2["items"]) >= 1

    # Check that item IDs on page 2 do not overlap with page 1
    page1_ids = {item["id"] for item in data1["items"]}
    page2_ids = {item["id"] for item in data2["items"]}
    assert page1_ids.isdisjoint(page2_ids)


@pytest.mark.asyncio
async def test_get_item_by_id(client: AsyncClient) -> None:
    """Test getting an item by its UUID via GET /api/v1/items/{id}."""
    create_resp = await client.post(
        "/api/v1/items/", json={"title": "Target Item", "description": "Find me"}
    )
    created_id = create_resp.json()["id"]

    response = await client.get(f"/api/v1/items/{created_id}")
    assert response.status_code == 200
    assert response.json()["id"] == created_id
    assert response.json()["title"] == "Target Item"


@pytest.mark.asyncio
async def test_get_item_not_found(client: AsyncClient) -> None:
    """Test 404 response with standardized ErrorResponse."""
    random_uuid = str(uuid4())
    response = await client.get(f"/api/v1/items/{random_uuid}")
    assert response.status_code == 404
    assert "x-request-id" in response.headers

    data = response.json()
    assert "error" in data
    error = data["error"]
    assert error["code"] == "ITEM_NOT_FOUND"
    assert error["message"] == "Item not found"
    assert "request_id" in error


@pytest.mark.asyncio
async def test_update_item(client: AsyncClient) -> None:
    """Test updating an item via PATCH /api/v1/items/{id}."""
    create_resp = await client.post(
        "/api/v1/items/", json={"title": "Old Title", "is_active": True}
    )
    created_id = create_resp.json()["id"]

    update_payload = {"title": "New Title", "is_active": False}
    response = await client.patch(f"/api/v1/items/{created_id}", json=update_payload)
    assert response.status_code == 200

    data = response.json()
    assert data["id"] == created_id
    assert data["title"] == "New Title"
    assert data["is_active"] is False


@pytest.mark.asyncio
async def test_delete_item(client: AsyncClient) -> None:
    """Test deleting an item via DELETE /api/v1/items/{id}."""
    create_resp = await client.post(
        "/api/v1/items/", json={"title": "To Delete", "is_active": True}
    )
    created_id = create_resp.json()["id"]

    delete_resp = await client.delete(f"/api/v1/items/{created_id}")
    assert delete_resp.status_code == 204

    # Verify item no longer exists
    get_resp = await client.get(f"/api/v1/items/{created_id}")
    assert get_resp.status_code == 404
    assert get_resp.json()["error"]["code"] == "ITEM_NOT_FOUND"
