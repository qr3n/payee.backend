from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from aiogram_dialog import DialogManager

from bot.client.schemas import (
    HealthCheckResponse,
    PaginatedResponse,
    PaymentRead,
    ReadinessResponse,
    ScenarioRead,
    TelegramAccountRead,
)
from bot.dialogs.accounts import get_account_detail, get_accounts_list
from bot.dialogs.main_menu import get_system_status
from bot.dialogs.scenarios import (
    format_stage_timings,
    get_payment_result,
    get_scenarios_list,
)


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
async def test_get_accounts_list() -> None:
    api_client = AsyncMock()
    acc_id = uuid4()
    now = datetime.now(UTC)
    api_client.list_accounts.return_value = PaginatedResponse[TelegramAccountRead](
        items=[
            TelegramAccountRead(
                id=acc_id,
                title="Main Worker",
                phone="+123456789",
                status="active",
                device_model="iPhone 15",
                system_version="iOS 17.5",
                app_version="10.14.0",
                telegram_user_id=12345,
                username="main_worker",
                is_premium=True,
                created_at=now,
                updated_at=now,
            )
        ],
        total=1,
        page=1,
        size=50,
        pages=1,
    )

    manager = MagicMock(spec=DialogManager)
    manager.dialog_data = {}

    result = await get_accounts_list(dialog_manager=manager, api_client=api_client)

    assert result["total"] == 1
    assert result["has_accounts"] is True
    assert len(result["accounts"]) == 1
    assert "Main Worker" in result["accounts"][0]["display_name"]
    assert "🟢" in result["accounts"][0]["status_badge"]


@pytest.mark.asyncio
async def test_get_account_detail() -> None:
    api_client = AsyncMock()
    acc_id = uuid4()
    now = datetime.now(UTC)
    api_client.get_account.return_value = TelegramAccountRead(
        id=acc_id,
        title="Main Worker",
        phone="+123456789",
        status="active",
        device_model="iPhone 15",
        system_version="iOS 17.5",
        app_version="10.14.0",
        telegram_user_id=12345,
        username="main_worker",
        is_premium=True,
        created_at=now,
        updated_at=now,
    )

    manager = MagicMock(spec=DialogManager)
    manager.dialog_data = {"selected_account_id": str(acc_id)}

    result = await get_account_detail(dialog_manager=manager, api_client=api_client)

    assert result["id"] == str(acc_id)
    assert result["title"] == "Main Worker"
    assert result["username"] == "@main_worker"
    assert "⭐️ Да" in result["is_premium"]


@pytest.mark.asyncio
async def test_get_scenarios_list() -> None:
    api_client = AsyncMock()
    api_client.list_scenarios.return_value = [
        ScenarioRead(
            scenario_id="starslly_bot",
            name="@starslly_bot",
            description="Buy Telegram Stars",
        )
    ]

    manager = MagicMock(spec=DialogManager)
    manager.dialog_data = {}

    result = await get_scenarios_list(dialog_manager=manager, api_client=api_client)

    assert result["has_scenarios"] is True
    assert len(result["scenarios"]) == 1
    assert result["scenarios"][0]["id"] == "starslly_bot"


@pytest.mark.asyncio
async def test_get_payment_result() -> None:
    api_client = AsyncMock()
    pay_id = uuid4()
    acc_id = uuid4()
    now = datetime.now(UTC)
    api_client.get_payment.return_value = PaymentRead(
        id=pay_id,
        client_user_id="user_admin",
        account_id=acc_id,
        scenario_id="starslly_bot",
        amount=Decimal("495.00"),
        currency="RUB",
        status="pending",
        payment_link="https://t.me/$invoice_abc",
        expires_at=now,
        meta={
            "calculated_stars": 300,
            "generation_time_sec": 1.25,
            "stage_timings": [
                {
                    "stage": "account_acquisition",
                    "description": "Поиск и выделение аккаунта в пуле",
                    "duration_sec": 0.05,
                },
                {
                    "stage": "connect_session",
                    "description": "Подключение к сессии из пула",
                    "duration_sec": 0.20,
                },
            ],
        },
        created_at=now,
        updated_at=now,
    )

    manager = MagicMock(spec=DialogManager)
    manager.dialog_data = {"last_payment_id": str(pay_id)}

    result = await get_payment_result(dialog_manager=manager, api_client=api_client)

    assert result["id"] == str(pay_id)
    assert result["scenario_id"] == "starslly_bot"
    assert "495.00 RUB" in result["amount"]
    assert result["has_link"] is True
    assert result["payment_link"] == "https://t.me/$invoice_abc"
    assert "300 ⭐️" in result["meta_info"]
    assert (
        "Поиск и выделение аккаунта в пуле: <code>0.05 сек.</code>"
        in result["timings_breakdown"]
    )
    assert (
        "Подключение к сессии из пула: <code>0.20 сек.</code>"
        in result["timings_breakdown"]
    )
    assert "Итого:</b> <code>1.25 сек.</code>" in result["timings_breakdown"]


def test_format_stage_timings_empty() -> None:
    assert format_stage_timings(None) == "—"
    assert "1.50 сек." in format_stage_timings(None, total_sec=1.5)


def test_format_stage_timings_populated() -> None:
    stages = [
        {"stage": "stage_1", "description": "Этап 1", "duration_sec": 0.12},
        {"stage": "stage_2", "description": "Этап 2", "duration_sec": 0.88},
    ]
    res = format_stage_timings(stages, total_sec=1.00)
    assert "• Этап 1: <code>0.12 сек.</code>" in res
    assert "• Этап 2: <code>0.88 сек.</code>" in res
    assert "⏱ <b>Итого:</b> <code>1.00 сек.</code>" in res
