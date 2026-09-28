from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from aiogram_dialog import DialogManager

from bot.client.schemas import (
    HealthCheckResponse,
    ItemRead,
    PaginatedResponse,
    ReadinessResponse,
)
from bot.dialogs.items import get_item_detail, get_items_list
from bot.dialogs.main_menu import get_system_status


@pytest.mark.asyncio
async def test_get_system_status_success() -> None:
    api_client = AsyncMock()
    api_client.get_health.return_value = HealthCheckResponse(
        status="ok",
        service="FastAPI Backend",
        version="0.1.0",
        environment="development",
        timestamp=datetime.now(UTC),
    )
    api_client.get_readiness.return_value = ReadinessResponse(
        status="ready",
        database=True,
        redis=True,
        timestamp=datetime.now(UTC),
    )

    manager = MagicMock(spec=DialogManager)
    result = await get_system_status(dialog_manager=manager, api_client=api_client)

    assert result["service"] == "FastAPI Backend"
    assert result["version"] == "0.1.0"
    assert "🟢" in result["status"]
    assert "✅" in result["db_status"]
    assert "✅" in result["redis_status"]


@pytest.mark.asyncio
async def test_get_system_status_error() -> None:
    api_client = AsyncMock()
    api_client.get_health.side_effect = RuntimeError("Connection refused")

    manager = MagicMock(spec=DialogManager)
    result = await get_system_status(dialog_manager=manager, api_client=api_client)

    assert "🔴" in result["status"]
    assert result["db_status"] == "—"


@pytest.mark.asyncio
async def test_get_items_list() -> None:
    api_client = AsyncMock()
    item_id = uuid4()
    now = datetime.now(UTC)
    api_client.list_items.return_value = PaginatedResponse[ItemRead](
        items=[
            ItemRead(
                id=item_id,
                title="Gadget",
                description="Cool gadget",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        ],
        total=1,
        page=1,
        size=10,
        pages=1,
    )

    manager = MagicMock(spec=DialogManager)
    manager.dialog_data = {}

    result = await get_items_list(dialog_manager=manager, api_client=api_client)

    assert result["total"] == 1
    assert result["has_items"] is True
    assert len(result["items"]) == 1
    assert result["items"][0]["title"] == "Gadget"


@pytest.mark.asyncio
async def test_get_item_detail() -> None:
    api_client = AsyncMock()
    item_id = uuid4()
    now = datetime.now(UTC)
    api_client.get_item.return_value = ItemRead(
        id=item_id,
        title="Specific Item",
        description="Detailed description",
        is_active=True,
        created_at=now,
        updated_at=now,
    )

    manager = MagicMock(spec=DialogManager)
    manager.dialog_data = {"selected_item_id": str(item_id)}

    result = await get_item_detail(dialog_manager=manager, api_client=api_client)

    assert result["id"] == str(item_id)
    assert result["title"] == "Specific Item"
    assert result["description"] == "Detailed description"
    assert "✅" in result["status"]
