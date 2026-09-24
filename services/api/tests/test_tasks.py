from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.broker import broker
from app.modules.items import Item, process_item_analysis


@pytest.mark.asyncio
async def test_process_item_analysis_task_direct(db_session: AsyncSession) -> None:
    """
    Test direct execution of process_item_analysis task with an isolated DB session.
    """
    # Create item in database
    item = Item(title="Analysis Target", description="To be analyzed")
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)

    # Execute task function directly
    result = await process_item_analysis.original_func(
        item_id=item.id,
        db=db_session,
    )

    assert result["status"] == "completed"
    assert result["item_id"] == str(item.id)
    assert result["title"] == "Analysis Target"


@pytest.mark.asyncio
async def test_process_item_analysis_task_not_found(
    db_session: AsyncSession,
) -> None:
    """Test task execution with non-existent item UUID."""
    random_id = uuid4()
    result = await process_item_analysis.original_func(
        item_id=random_id,
        db=db_session,
    )

    assert result["status"] == "not_found"
    assert result["item_id"] == str(random_id)


@pytest.mark.asyncio
async def test_analyze_item_endpoint_enqueues_task(client: AsyncClient) -> None:
    """Test POST /api/v1/items/{item_id}/analyze enqueues background task."""
    # Create item first
    create_resp = await client.post(
        "/api/v1/items/",
        json={"title": "Async Item", "description": "Trigger task"},
    )
    assert create_resp.status_code == 201
    item_id = create_resp.json()["id"]

    # Mock broker.kick so Redis is not required in tests
    with patch.object(broker, "kick", new_callable=AsyncMock) as mock_kick:
        resp = await client.post(f"/api/v1/items/{item_id}/analyze")

        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "queued"
        assert data["item_id"] == item_id
        assert "task_id" in data
        assert mock_kick.called


@pytest.mark.asyncio
async def test_analyze_non_existent_item_returns_404(client: AsyncClient) -> None:
    """Test enqueueing analysis for non-existent item returns 404."""
    random_uuid = str(uuid4())
    resp = await client.post(f"/api/v1/items/{random_uuid}/analyze")

    assert resp.status_code == 404
    data = resp.json()
    assert data["error"]["code"] == "ITEM_NOT_FOUND"
