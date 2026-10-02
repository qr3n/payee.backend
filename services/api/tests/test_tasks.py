"""
Integration tests for Taskiq background tasks.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

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
