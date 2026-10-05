"""
Unit and integration tests for Payments service and pool management logic.
"""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.exceptions import AppException
from app.core.redis import get_redis_client
from app.modules.accounts.models import AccountStatus, TelegramAccount
from app.modules.payments.exceptions import (
    NoAccountsAvailableException,
    UnknownScenarioException,
)
from app.modules.payments.models import Payment, PaymentStatus
from app.modules.payments.scenarios import (
    BasePaymentScenario,
    PreparationResult,
    ScenarioContext,
    ScenarioResult,
    acquire_account_generation_lock,
    is_account_generation_locked,
    release_account_generation_lock,
    scenario_registry,
)
from app.modules.payments.scenarios.state import AcquiredAccount
from app.modules.payments.schemas import (
    PaymentCallback,
    PaymentCreate,
    PaymentRaceCreate,
)
from app.modules.payments.service import (
    cancel_payment,
    create_payment,
    expire_overdue_payments,
    get_payment,
    list_payments_paginated,
    mark_payment_status,
    release_all_locked_accounts,
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
    """
    Test account acquisition from pool and error raised when all are generation-locked.
    """
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
    # Account generation lock is released immediately after link creation
    assert not await is_account_generation_locked(p1.account_id)

    # When all accounts are generation-locked by concurrent tasks:
    tok1 = await acquire_account_generation_lock(acc1.id)
    tok2 = await acquire_account_generation_lock(acc2.id)
    assert tok1 is not None
    assert tok2 is not None

    # Next user tries to create payment -> Pool is full!
    with pytest.raises(NoAccountsAvailableException):
        await create_payment(
            db_session,
            PaymentCreate(
                client_user_id="user_gamma",
                scenario_id="mock_bot",
                amount=Decimal("300.00"),
            ),
        )

    # Releasing generation lock allows acquisition again
    await release_account_generation_lock(acc1.id, owner_token=tok1)
    p2 = await create_payment(
        db_session,
        PaymentCreate(
            client_user_id="user_gamma",
            scenario_id="mock_bot",
            amount=Decimal("300.00"),
        ),
    )
    assert p2.account_id == acc1.id
    await release_account_generation_lock(acc2.id, owner_token=tok2)


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
    """Test paying an invoice reactively updates status to PAID."""
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

    # P1 is paid via callback
    await mark_payment_status(
        db_session,
        p1,
        PaymentCallback(
            status=PaymentStatus.PAID,
            external_transaction_id="TX-999888",
        ),
    )

    refreshed = await get_payment(db_session, p1.id)
    assert refreshed is not None
    assert refreshed.status == PaymentStatus.PAID
    assert refreshed.paid_at is not None

    # Another user can create payment smoothly
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
async def test_release_all_locked_accounts(db_session: AsyncSession) -> None:
    """Test releasing all locked accounts cancels pending payments."""
    acc1 = await _create_test_account(db_session, "Locked Acc 1")
    acc2 = await _create_test_account(db_session, "Locked Acc 2")

    p1 = await create_payment(
        db_session,
        PaymentCreate(
            client_user_id="u1",
            scenario_id="mock_bot",
            amount=Decimal("100.00"),
        ),
    )
    p2 = await create_payment(
        db_session,
        PaymentCreate(
            client_user_id="u2",
            scenario_id="mock_bot",
            amount=Decimal("200.00"),
        ),
    )
    assert p1.status == PaymentStatus.PENDING
    assert p2.status == PaymentStatus.PENDING

    cancelled_count, released_count = await release_all_locked_accounts(db_session)
    assert cancelled_count == 2
    assert released_count == 2

    # Verify both accounts are now free for new payments
    p3 = await create_payment(
        db_session,
        PaymentCreate(
            client_user_id="u3",
            scenario_id="mock_bot",
            amount=Decimal("300.00"),
        ),
    )
    assert p3.account_id in (acc1.id, acc2.id)


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
            new=AsyncMock(return_value=PreparationResult(status="ok")),
        ),
        patch(
            "app.modules.payments.scenarios.starshoppik_scenario.StarShoppikBotScenario.prepare",
            new=AsyncMock(return_value=PreparationResult(status="ok")),
        ),
        patch(
            "app.modules.payments.scenarios.helperstars_scenario.HelperStarsBotScenario.prepare",
            new=AsyncMock(return_value=PreparationResult(status="ok")),
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
            new=AsyncMock(return_value=PreparationResult(status="ok")),
        ),
        patch(
            "app.modules.payments.scenarios.starshoppik_scenario.StarShoppikBotScenario.prepare",
            new=AsyncMock(return_value=PreparationResult(status="ok")),
        ),
        patch(
            "app.modules.payments.scenarios.helperstars_scenario.HelperStarsBotScenario.prepare",
            new=AsyncMock(return_value=PreparationResult(status="ok")),
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


@pytest.mark.asyncio
async def test_allocate_unique_stars_for_scenario() -> None:
    """Test allocating unique stars with delta upon collisions."""
    from app.modules.payments.scenarios.stars_calculator import (
        allocate_unique_stars_for_scenario,
    )
    from app.modules.payments.scenarios.state import (
        release_all_scenario_stars_reservations,
    )

    await release_all_scenario_stars_reservations()

    # 1. First allocation for 100 stars gets delta 0
    stars1, delta1 = await allocate_unique_stars_for_scenario("starslly_bot", 100)
    assert stars1 == 100
    assert delta1 == 0

    # 2. Second allocation for 100 stars collides and gets +1 delta (101 stars)
    stars2, delta2 = await allocate_unique_stars_for_scenario("starslly_bot", 100)
    assert stars2 == 101
    assert delta2 == 1

    # 3. Third allocation gets +2 delta (102 stars)
    stars3, delta3 = await allocate_unique_stars_for_scenario("starslly_bot", 100)
    assert stars3 == 102
    assert delta3 == 2

    # 4. Different scenario_id is isolated:
    # helperstars_bot gets 100 stars without delta
    stars_other, delta_other = await allocate_unique_stars_for_scenario(
        "helperstars_bot", 100
    )
    assert stars_other == 100
    assert delta_other == 0

    # Cleanup
    await release_all_scenario_stars_reservations()


@pytest.mark.asyncio
async def test_generation_lock_owner_token_safety(
    db_session: AsyncSession,
) -> None:
    """Test that a request cannot release a lock owned by another token."""
    from app.modules.payments.scenarios.state import (
        acquire_account_generation_lock,
        is_account_generation_locked,
        release_account_generation_lock,
    )

    acc = await _create_test_account(db_session, "Owner Token Test")

    # A acquires lock
    token_a = await acquire_account_generation_lock(acc.id)
    assert token_a is not None

    # Suppose A's lock expired or was overwritten with token B
    redis = get_redis_client()
    token_b = "foreign_owner_token"
    await redis.set(f"lock:account_generation:{acc.id}", token_b)

    # A tries to release with old token A -> must NOT delete B's lock!
    released = await release_account_generation_lock(acc.id, owner_token=token_a)
    assert not released
    assert await is_account_generation_locked(acc.id)

    # Releasing with valid token B succeeds
    released_b = await release_account_generation_lock(acc.id, owner_token=token_b)
    assert released_b
    assert not await is_account_generation_locked(acc.id)


@pytest.mark.asyncio
async def test_concurrent_idempotency_pre_registration(
    db_session: AsyncSession,
) -> None:
    """
    Test that concurrent requests with same idempotency key return the
    same payment and execute external scenario creation only once.
    """
    from app.core.db import async_session_maker
    from app.modules.payments.scenarios.base import ScenarioResult
    from app.modules.payments.scenarios.registry import scenario_registry

    await _create_test_account(db_session, "Idem Account")
    await db_session.commit()

    req = PaymentCreate(
        client_user_id="idem_user_1",
        scenario_id="mock_bot",
        amount=Decimal("150.00"),
        idempotency_key="key_concurrent_123",
    )

    scenario = scenario_registry.get("mock_bot")
    assert scenario is not None
    original_create = scenario.create_payment
    call_count = 0

    async def counting_create(ctx: ScenarioContext) -> ScenarioResult:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return await original_create(ctx)

    scenario.create_payment = counting_create  # type: ignore[method-assign]
    try:

        async def run_in_session() -> Payment:
            async with async_session_maker() as sess:
                p = await create_payment(sess, req)
                await sess.commit()
                return p

        p1, p2 = await asyncio.gather(run_in_session(), run_in_session())

        assert p1.id == p2.id
        assert p1.status == PaymentStatus.PENDING
        assert call_count == 1
    finally:
        scenario.create_payment = original_create  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_concurrent_status_transitions_paid_blocks_cancel(
    db_session: AsyncSession,
) -> None:
    """
    Test that two independent sessions reading PENDING row under FOR UPDATE
    enforce proper isolation: first session commits PAID, second session's
    cancel_payment sees PAID via populate_existing and raises 409 conflict.
    """
    from app.core.db import async_session_maker

    await _create_test_account(db_session, "Conflict Acc")
    payment = await create_payment(
        db_session,
        PaymentCreate(
            client_user_id="user_conflict",
            scenario_id="mock_bot",
            amount=Decimal("200.00"),
        ),
    )
    await db_session.commit()
    payment_id = payment.id

    async with async_session_maker() as session_a, async_session_maker() as session_b:
        # Both sessions load the payment as PENDING
        p_a = await session_a.get(Payment, payment_id)
        p_b = await session_b.get(Payment, payment_id)
        assert p_a is not None and p_b is not None
        assert p_a.status == PaymentStatus.PENDING
        assert p_b.status == PaymentStatus.PENDING

        # Session A marks PAID and commits
        await mark_payment_status(
            session_a,
            p_a,
            PaymentCallback(status=PaymentStatus.PAID),
        )
        await session_a.commit()

        # Session B attempts to cancel using its stale in-memory entity
        # get_payment_for_update with populate_existing=True ensures it reloads from DB
        with pytest.raises(AppException) as exc_info:
            await cancel_payment(session_b, p_b)
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == "PAYMENT_ALREADY_PAID"


@pytest.mark.asyncio
async def test_race_cancellation_saves_terminal_status() -> None:
    """
    Test that cancelling the race background runner finishes race with
    status 'cancelled' using a deterministic blocked scenario.
    """
    from unittest.mock import patch
    from uuid import uuid4

    from app.modules.payments import race_buffer
    from app.modules.payments.scenarios.state import GenerationLease
    from app.modules.payments.service import _race_background_runner

    batch_id = uuid4()
    await race_buffer.init_race_buffer(batch_id, ["mock_bot"])

    acc = TelegramAccount(
        id=uuid4(),
        title="Runner Acc",
        phone="+1234567890",
        session_string="mock_session",
        device_model="PC 64bit",
        system_version="Windows 11",
        app_version="5.2.2 x64",
    )
    lease = GenerationLease(account_id=acc.id, owner_token="lease_tok")
    acquired = AcquiredAccount(account=acc, lease=lease)

    race_in = PaymentRaceCreate(
        client_user_id="user_cancel",
        amount=Decimal("100.00"),
        timeout_sec=5.0,
    )

    started = asyncio.Event()
    release = asyncio.Event()
    stopped = asyncio.Event()

    async def blocked_scenario(**_kwargs: object) -> str:
        started.set()
        try:
            await release.wait()
            return "event: payment\ndata: {}\n\n"
        finally:
            stopped.set()

    with patch(
        "app.modules.payments.service._run_race_scenario_task",
        new=blocked_scenario,
    ):
        runner_task = asyncio.create_task(
            _race_background_runner(
                pairs=[(acquired, "mock_bot")],
                batch_id=batch_id,
                race_in=race_in,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
                race_start=time.perf_counter(),
            )
        )

        try:
            await asyncio.wait_for(started.wait(), timeout=1.0)
            assert not runner_task.done()

            runner_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await runner_task

            assert stopped.is_set()
            assert await race_buffer.get_race_status(batch_id) == "cancelled"
        finally:
            if not runner_task.done():
                runner_task.cancel()
            await asyncio.gather(runner_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_race_timeout_saves_timeout_status() -> None:
    """
    Test that when the race timeout deadline is exceeded, the race finishes
    with terminal status 'timeout' (not 'cancelled').
    """
    from unittest.mock import patch
    from uuid import uuid4

    from app.modules.payments import race_buffer
    from app.modules.payments.scenarios.state import GenerationLease
    from app.modules.payments.service import _race_background_runner

    batch_id = uuid4()
    await race_buffer.init_race_buffer(batch_id, ["mock_bot"])

    acc = TelegramAccount(
        id=uuid4(),
        title="Timeout Acc",
        phone="+1234567890",
        session_string="mock_session",
        device_model="PC 64bit",
        system_version="Windows 11",
        app_version="5.2.2 x64",
    )
    lease = GenerationLease(account_id=acc.id, owner_token="lease_timeout_tok")
    acquired = AcquiredAccount(account=acc, lease=lease)

    race_in = PaymentRaceCreate(
        client_user_id="user_timeout",
        amount=Decimal("100.00"),
        timeout_sec=0.1,  # Short timeout
    )

    async def slow_scenario(**_kwargs: object) -> str:
        await asyncio.sleep(1.0)
        return "event: payment\ndata: {}\n\n"

    with patch(
        "app.modules.payments.service._run_race_scenario_task",
        new=slow_scenario,
    ):
        runner_task = asyncio.create_task(
            _race_background_runner(
                pairs=[(acquired, "mock_bot")],
                batch_id=batch_id,
                race_in=race_in,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
                race_start=time.perf_counter(),
            )
        )

        await runner_task
        status = await race_buffer.get_race_status(batch_id)
        assert status == "timeout"
