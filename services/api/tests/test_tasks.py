"""
Integration tests for Taskiq background tasks.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.broker import broker
from app.modules.payments.models import Payment, PaymentStatus
from app.modules.payments.tasks import expire_overdue_payments_task
from tests.test_payments_service import _create_test_account


@pytest.mark.asyncio
async def test_expire_overdue_payments_task_direct(
    db_session: AsyncSession,
) -> None:
    """Test direct execution of expire_overdue_payments_task with DB session."""
    acc = await _create_test_account(db_session, "Task Account")

    payment = Payment(
        client_user_id="task_user",
        scenario_id="mock_bot",
        amount=Decimal("10.00"),
        account_id=acc.id,
        status=PaymentStatus.PENDING,
        expires_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    db_session.add(payment)
    await db_session.flush()

    result = await expire_overdue_payments_task.original_func(db=db_session)
    assert result["expired_count"] >= 1


@pytest.mark.asyncio
async def test_kick_task_on_broker() -> None:
    """Test dispatching task to broker."""
    with patch.object(broker, "kick", new_callable=AsyncMock) as mock_kick:
        await expire_overdue_payments_task.kiq()
        assert mock_kick.called


@pytest.mark.asyncio
async def test_accounts_tasks_direct(db_session: AsyncSession) -> None:
    """Test accounts background tasks direct invocation."""
    from app.modules.accounts.tasks import (
        check_all_accounts_task,
        check_single_account_task,
    )

    acc = await _create_test_account(db_session, "Tasks Test Acc")

    with patch(
        "app.modules.accounts.service.check_all_accounts",
        new=AsyncMock(
            return_value={
                "total": 1,
                "active": 1,
                "revoked": 0,
                "banned": 0,
                "flood_wait": 0,
                "error": 0,
            }
        ),
    ):
        res = await check_all_accounts_task.original_func(db=db_session)
        assert res["total"] == 1
        assert res["active"] == 1

    with patch(
        "app.modules.accounts.service.verify_and_update_account",
        new=AsyncMock(
            return_value=(
                acc,
                MagicMock(
                    status=MagicMock(value="active"),
                    is_authorized=True,
                    error=None,
                ),
            )
        ),
    ):
        single_res = await check_single_account_task.original_func(
            account_id=acc.id, db=db_session
        )
        assert single_res["account_id"] == str(acc.id)
        assert single_res["status"] == "active"
