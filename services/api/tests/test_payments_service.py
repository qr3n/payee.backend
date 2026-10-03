"""
Unit and integration tests for Payments service and pool management logic.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.accounts.models import AccountStatus, TelegramAccount
from app.modules.payments.exceptions import (
    NoAccountsAvailableException,
    UnknownScenarioException,
)
from app.modules.payments.models import PaymentStatus
from app.modules.payments.scenarios import (
    BasePaymentScenario,
    ScenarioContext,
    ScenarioResult,
    scenario_registry,
)
from app.modules.payments.schemas import PaymentCallback, PaymentCreate
from app.modules.payments.service import (
    cancel_payment,
    create_payment,
    expire_overdue_payments,
    get_payment,
    list_payments_paginated,
    mark_payment_status,
)
from app.shared.pagination import PageParams
from tests.test_accounts_service import VALID_SESSION_STRING


async def _create_test_account(session: AsyncSession, title: str) -> TelegramAccount:
    """Helper to populate an active TelegramAccount in test DB."""
    account = TelegramAccount(
        title=title,
        session_string=VALID_SESSION_STRING,
        device_model="Test Phone",
        system_version="Android 14",
        app_version="10.14.0",
        lang_code="ru",
        system_lang_code="ru-RU",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    await session.flush()
    await session.refresh(account)
    return account


@pytest.mark.asyncio
async def test_pool_exhaustion_and_account_acquisition(
    db_session: AsyncSession,
) -> None:
    """Test accounts are acquired from the pool and error raised when exhausted."""
    # Create 2 accounts in the pool
    acc1 = await _create_test_account(db_session, "Pool Account 1")
    acc2 = await _create_test_account(db_session, "Pool Account 2")

    # User 1 creates payment
    p1 = await create_payment(
        db_session,
        PaymentCreate(
            client_user_id="user_alpha",
            scenario_id="mock_bot",
            amount=Decimal("100.00"),
        ),
    )
    assert p1.account_id in (acc1.id, acc2.id)
    assert p1.status == PaymentStatus.PENDING
    assert "stage_timings" in p1.meta
    assert len(p1.meta["stage_timings"]) >= 1
    assert p1.meta["stage_timings"][0]["stage"] == "account_acquisition"
    assert "generation_time_sec" in p1.meta

    # User 2 creates payment
    p2 = await create_payment(
        db_session,
        PaymentCreate(
            client_user_id="user_beta",
            scenario_id="mock_bot",
            amount=Decimal("200.00"),
        ),
    )
    assert p2.account_id in (acc1.id, acc2.id)
    assert p2.account_id != p1.account_id

    # User 3 tries to create payment -> Pool is full!
    with pytest.raises(NoAccountsAvailableException):
        await create_payment(
            db_session,
            PaymentCreate(
                client_user_id="user_gamma",
                scenario_id="mock_bot",
                amount=Decimal("300.00"),
            ),
        )


@pytest.mark.asyncio
async def test_same_user_cancels_previous_and_reuses_account(
    db_session: AsyncSession,
) -> None:
    """Test subsequent payment by same user cancels old and reuses account."""
    acc = await _create_test_account(db_session, "Solo Account")

    # 1. First payment
    p1 = await create_payment(
        db_session,
        PaymentCreate(
            client_user_id="same_user_123",
            scenario_id="mock_bot",
            amount=Decimal("500.00"),
        ),
    )
    assert p1.account_id == acc.id
    assert p1.status == PaymentStatus.PENDING

    # 2. Second payment from same user
    p2 = await create_payment(
        db_session,
        PaymentCreate(
            client_user_id="same_user_123",
            scenario_id="mock_bot",
            amount=Decimal("750.00"),
        ),
    )

    # p1 should now be cancelled
    refreshed_p1 = await get_payment(db_session, p1.id)
    assert refreshed_p1 is not None
    assert refreshed_p1.status == PaymentStatus.CANCELLED
    assert refreshed_p1.cancelled_at is not None

    # p2 should reuse the same account
    assert p2.account_id == acc.id
    assert p2.status == PaymentStatus.PENDING
    assert p2.amount == Decimal("750.00")


@pytest.mark.asyncio
async def test_reactive_account_release_on_paid_callback(
    db_session: AsyncSession,
) -> None:
    """Test paying an invoice reactively and immediately frees the account."""
    acc = await _create_test_account(db_session, "Quick Account")

    p1 = await create_payment(
        db_session,
        PaymentCreate(
            client_user_id="payer_one",
            scenario_id="mock_bot",
            amount=Decimal("150.00"),
        ),
    )
    assert p1.account_id == acc.id

    # Another user cannot pay yet
    with pytest.raises(NoAccountsAvailableException):
        await create_payment(
            db_session,
            PaymentCreate(
                client_user_id="payer_two",
                scenario_id="mock_bot",
                amount=Decimal("300.00"),
            ),
        )

    # P1 is paid via callback
    await mark_payment_status(
        db_session,
        p1,
        PaymentCallback(
            status=PaymentStatus.PAID,
            external_transaction_id="TX-999888",
        ),
    )

    # Account is now reactively freed! Payer two can now create payment
    p2 = await create_payment(
        db_session,
        PaymentCreate(
            client_user_id="payer_two",
            scenario_id="mock_bot",
            amount=Decimal("300.00"),
        ),
    )
    assert p2.account_id == acc.id
    assert p2.status == PaymentStatus.PENDING


@pytest.mark.asyncio
async def test_manual_cancel_payment_frees_account(
    db_session: AsyncSession,
) -> None:
    """Test manual cancellation frees account for new requests."""
    acc = await _create_test_account(db_session, "Cancellable Account")

    p1 = await create_payment(
        db_session,
        PaymentCreate(
            client_user_id="user_cancel",
            scenario_id="mock_bot",
            amount=Decimal("100.00"),
        ),
    )

    # Cancel payment
    await cancel_payment(db_session, p1)
    assert p1.status == PaymentStatus.CANCELLED

    # New user can immediately acquire the freed account
    p2 = await create_payment(
        db_session,
        PaymentCreate(
            client_user_id="user_new",
            scenario_id="mock_bot",
            amount=Decimal("200.00"),
        ),
    )
    assert p2.account_id == acc.id


@pytest.mark.asyncio
async def test_expire_overdue_payments(db_session: AsyncSession) -> None:
    """Test expiring payments with past expiration date."""
    await _create_test_account(db_session, "Expiring Account")

    p = await create_payment(
        db_session,
        PaymentCreate(
            client_user_id="user_timeout",
            scenario_id="mock_bot",
            amount=Decimal("50.00"),
        ),
    )
    # Manually backdate expires_at
    p.expires_at = datetime.now(UTC) - timedelta(minutes=5)
    db_session.add(p)
    await db_session.flush()

    count = await expire_overdue_payments(db_session)
    assert count >= 1

    refreshed = await get_payment(db_session, p.id)
    assert refreshed is not None
    assert refreshed.status == PaymentStatus.EXPIRED


@pytest.mark.asyncio
async def test_custom_scenario_registration_and_dispatch(
    db_session: AsyncSession,
) -> None:
    """Test registering a custom scenario and creating payment through it."""

    class CustomStarsScenario(BasePaymentScenario):
        scenario_id = "stars_bot"
        name = "Telegram Stars Scenario"
        description = "Handles Stars invoice generation"

        async def create_payment(self, ctx: ScenarioContext) -> ScenarioResult:
            return ScenarioResult(
                payment_link=f"https://t.me/StarsBot?start=invoice_{ctx.amount}",
                meta={"stars_fee": 0},
            )

    scenario_registry.register(CustomStarsScenario())

    await _create_test_account(db_session, "Stars Account")
    payment = await create_payment(
        db_session,
        PaymentCreate(
            client_user_id="user_stars",
            scenario_id="stars_bot",
            amount=Decimal("250.00"),
        ),
    )
    assert payment.scenario_id == "stars_bot"
    assert payment.payment_link == "https://t.me/StarsBot?start=invoice_250.00"
    assert payment.meta.get("stars_fee") == 0


@pytest.mark.asyncio
async def test_unknown_scenario_raises_exception(
    db_session: AsyncSession,
) -> None:
    """Test unknown scenario_id raises UnknownScenarioException."""
    await _create_test_account(db_session, "Any Account")
    with pytest.raises(UnknownScenarioException):
        await create_payment(
            db_session,
            PaymentCreate(
                client_user_id="user_bad_scenario",
                scenario_id="non_existent_scenario",
                amount=Decimal("100.00"),
            ),
        )


@pytest.mark.asyncio
async def test_list_payments_paginated(db_session: AsyncSession) -> None:
    """Test listing payments with pagination."""
    await _create_test_account(db_session, "Pagination Account")
    await create_payment(
        db_session,
        PaymentCreate(
            client_user_id="paginated_user",
            scenario_id="mock_bot",
            amount=Decimal("99.00"),
        ),
    )
    items, total = await list_payments_paginated(
        db_session, PageParams(page=1, size=10)
    )
    assert total >= 1
    assert len(items) >= 1


@pytest.mark.asyncio
async def test_prepare_account_scenarios_service(db_session: AsyncSession) -> None:
    """Test prepare_account_scenarios logic."""
    from app.modules.payments.service import prepare_account_scenarios

    acc = await _create_test_account(db_session, "Prep Acc")

    mock_client = AsyncMock()
    with (
        patch(
            "app.modules.accounts.session_pool.telegram_session_pool.get_connected_client",
            new=AsyncMock(return_value=mock_client),
        ),
        patch(
            "app.modules.payments.scenarios.starslly_scenario.StarsllyBotScenario.prepare",
            new=AsyncMock(),
        ),
        patch(
            "app.modules.payments.scenarios.starshoppik_scenario.StarShoppikBotScenario.prepare",
            new=AsyncMock(),
        ),
        patch(
            "app.modules.payments.scenarios.helperstars_scenario.HelperStarsBotScenario.prepare",
            new=AsyncMock(),
        ),
    ):
        result = await prepare_account_scenarios(db_session, acc.id)
        assert result["status"] == "completed"
        assert result["account_id"] == str(acc.id)
        assert "mock_bot" in result["results"]


@pytest.mark.asyncio
async def test_prepare_single_scenario_service(db_session: AsyncSession) -> None:
    """Test prepare_single_scenario logic."""
    from app.modules.payments.service import prepare_single_scenario

    acc = await _create_test_account(db_session, "Prep Single Acc")

    mock_client = AsyncMock()
    with patch(
        "app.modules.accounts.session_pool.telegram_session_pool.get_connected_client",
        new=AsyncMock(return_value=mock_client),
    ):
        result = await prepare_single_scenario(
            db_session, acc.id, scenario_id="mock_bot"
        )
        assert result["status"] == "completed"
        assert result["scenario_id"] == "mock_bot"


@pytest.mark.asyncio
async def test_refresh_idle_account_scenarios_service(
    db_session: AsyncSession,
) -> None:
    """Test refresh_idle_account_scenarios finds and refreshes idle accounts."""
    from app.modules.payments.service import refresh_idle_account_scenarios

    _acc = await _create_test_account(db_session, "Idle Refresh Acc")

    mock_client = AsyncMock()
    with (
        patch(
            "app.modules.accounts.session_pool.telegram_session_pool.get_connected_client",
            new=AsyncMock(return_value=mock_client),
        ),
        patch(
            "app.modules.payments.scenarios.state.is_scenario_prepared",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.modules.payments.scenarios.starslly_scenario.StarsllyBotScenario.prepare",
            new=AsyncMock(),
        ),
        patch(
            "app.modules.payments.scenarios.starshoppik_scenario.StarShoppikBotScenario.prepare",
            new=AsyncMock(),
        ),
        patch(
            "app.modules.payments.scenarios.helperstars_scenario.HelperStarsBotScenario.prepare",
            new=AsyncMock(),
        ),
    ):
        result = await refresh_idle_account_scenarios(db_session)
        assert result["status"] == "completed"
        assert result["idle_accounts_count"] >= 1
        assert result["refreshed_count"] >= 1


@pytest.mark.asyncio
async def test_fast_path_payment_execution(db_session: AsyncSession) -> None:
    """Test that pre-warmed account uses fast-path when creating payment."""
    from app.modules.payments.scenarios.state import set_scenario_prepared

    acc = await _create_test_account(db_session, "Fast Path Acc")
    await set_scenario_prepared(acc.id, "mock_bot")

    payment = await create_payment(
        db_session,
        PaymentCreate(
            client_user_id="fast_user",
            scenario_id="mock_bot",
            amount=Decimal("150.00"),
        ),
    )
    assert payment.meta.get("is_fast_path") is True
    timings = payment.meta.get("stage_timings", [])
    fast_stages = [s for s in timings if "быстрый путь" in s.get("description", "")]
    assert len(fast_stages) >= 1
