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


@pytest.mark.asyncio
async def test_prepare_account_scenarios_task_direct(
    db_session: AsyncSession,
) -> None:
    """Test direct execution of prepare_account_scenarios_task."""
    from app.modules.payments.tasks import (
        dispatch_account_scenarios_warmup,
        prepare_account_scenarios_task,
    )

    acc = await _create_test_account(db_session, "Warmup Test Acc")

    with patch(
        "app.modules.payments.service.prepare_account_scenarios",
        new=AsyncMock(
            return_value={
                "status": "completed",
                "account_id": str(acc.id),
                "results": {"mock_bot": "ok"},
            }
        ),
    ) as mock_service:
        res = await prepare_account_scenarios_task.original_func(
            account_id=acc.id,
            db=db_session,
        )
        assert res["status"] == "completed"
        assert res["account_id"] == str(acc.id)
        mock_service.assert_awaited_once_with(
            session=db_session,
            account_id=acc.id,
        )

    with patch.object(
        prepare_account_scenarios_task, "kiq", new_callable=AsyncMock
    ) as mock_kiq:
        await dispatch_account_scenarios_warmup(acc.id)
        mock_kiq.assert_awaited_once_with(account_id=acc.id)


@pytest.mark.asyncio
async def test_dispatch_payment_webhook_task_success(
    db_session: AsyncSession,
) -> None:
    """Test successful webhook dispatch with HMAC signature and metadata update."""
    import json

    import httpx
    from pydantic import SecretStr

    from app.core.config import settings
    from app.modules.payments.tasks import (
        dispatch_payment_webhook,
        dispatch_payment_webhook_task,
    )

    acc = await _create_test_account(db_session, "Webhook Test Acc")
    payment = Payment(
        client_user_id="user_wh_1",
        scenario_id="mock_bot",
        amount=Decimal("150.00"),
        currency="RUB",
        account_id=acc.id,
        status=PaymentStatus.PAID,
        callback_url="https://merchant.example.com/api/callback",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        paid_at=datetime.now(UTC),
        meta={"order_id": "ORD-999"},
    )
    db_session.add(payment)
    await db_session.flush()

    posted_content = None
    posted_headers = None

    async def mock_post(
        url: str, content: str, headers: dict[str, str]
    ) -> httpx.Response:
        nonlocal posted_content, posted_headers
        posted_content = json.loads(content)
        posted_headers = headers
        req = httpx.Request("POST", url)
        return httpx.Response(200, request=req, text="OK")

    with (
        patch.object(settings, "PAYMENT_WEBHOOK_SECRET", SecretStr("test_secret_key")),
        patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=mock_post)),
    ):
        result = await dispatch_payment_webhook_task.original_func(
            payment_id=payment.id,
            db=db_session,
        )

    assert result["status"] == "delivered"
    assert result["attempts"] == 1
    assert result["status_code"] == 200

    # Verify payload content
    assert posted_content is not None
    assert posted_content["payment_id"] == str(payment.id)
    assert posted_content["status"] == "paid"
    assert posted_content["amount"] == "150.00"
    assert posted_content["client_user_id"] == "user_wh_1"
    assert posted_content["external_transaction_id"] == "ORD-999"

    # Verify signature headers
    assert posted_headers is not None
    assert "X-Payee-Signature" in posted_headers
    assert "X-Payee-Timestamp" in posted_headers

    # Verify metadata saved to payment
    await db_session.refresh(payment)
    assert payment.meta["webhook_delivery"]["delivered"] is True
    assert payment.meta["webhook_delivery"]["attempts"] == 1

    # Verify non-blocking helper
    with patch.object(
        dispatch_payment_webhook_task, "kiq", new_callable=AsyncMock
    ) as mock_kiq:
        await dispatch_payment_webhook(payment.id)
        mock_kiq.assert_awaited_once_with(payment_id=payment.id)


@pytest.mark.asyncio
async def test_dispatch_payment_webhook_task_retry_failure(
    db_session: AsyncSession,
) -> None:
    """Test webhook dispatch retries on 500 error and records failure."""
    import httpx

    from app.core.config import settings
    from app.modules.payments.tasks import dispatch_payment_webhook_task

    payment = Payment(
        client_user_id="user_wh_fail",
        scenario_id="mock_bot",
        amount=Decimal("50.00"),
        status=PaymentStatus.PAID,
        callback_url="https://merchant.example.com/fail-callback",
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    db_session.add(payment)
    await db_session.flush()

    attempts_count = 0

    async def mock_post_500(url: str, **_kwargs: object) -> httpx.Response:
        nonlocal attempts_count
        attempts_count += 1
        req = httpx.Request("POST", url)
        return httpx.Response(500, request=req, text="Internal Server Error")

    with (
        patch.object(settings, "PAYMENT_WEBHOOK_MAX_RETRIES", 2),
        patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=mock_post_500)),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await dispatch_payment_webhook_task.original_func(
            payment_id=payment.id,
            db=db_session,
        )

    assert result["status"] == "failed"
    assert result["attempts"] == 2
    assert result["status_code"] == 500
    assert "HTTP 500" in result["error"]

    await db_session.refresh(payment)
    assert payment.meta["webhook_delivery"]["delivered"] is False
    assert payment.meta["webhook_delivery"]["attempts"] == 2


@pytest.mark.asyncio
async def test_dispatch_payment_webhook_task_skipped_conditions(
    db_session: AsyncSession,
) -> None:
    """Test webhook is skipped if no callback_url or non-existent payment."""
    import uuid6

    from app.modules.payments.tasks import dispatch_payment_webhook_task

    # Non-existent payment
    res_none = await dispatch_payment_webhook_task.original_func(
        payment_id=uuid6.uuid7(),
        db=db_session,
    )
    assert res_none["status"] == "skipped"
    assert res_none["reason"] == "payment_not_found"

    # Payment without callback_url
    payment_no_url = Payment(
        client_user_id="user_wh_none",
        scenario_id="mock_bot",
        amount=Decimal("20.00"),
        status=PaymentStatus.PAID,
        callback_url=None,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    db_session.add(payment_no_url)
    await db_session.flush()

    res_no_url = await dispatch_payment_webhook_task.original_func(
        payment_id=payment_no_url.id,
        db=db_session,
    )
    assert res_no_url["status"] == "skipped"
    assert res_no_url["reason"] == "no_callback_url"
