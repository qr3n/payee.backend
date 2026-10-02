"""
Payments domain service and pool locking business logic.
Provides reactive account acquisition, 30m TTL locks, user re-use,
and scenario dispatch. Follows Unit of Work: NEVER calls session.commit().
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.accounts.models import AccountStatus, TelegramAccount
from app.modules.payments.exceptions import NoAccountsAvailableException
from app.modules.payments.models import Payment, PaymentStatus
from app.modules.payments.scenarios import (
    ScenarioContext,
    scenario_registry,
)
from app.modules.payments.schemas import PaymentCallback, PaymentCreate
from app.shared.pagination import PageParams

PAYMENT_TTL_MINUTES = 30


async def get_active_payment_for_user(
    session: AsyncSession,
    client_user_id: str,
    now: datetime,
) -> Payment | None:
    """Find an existing active pending payment for the client user."""
    statement = (
        select(Payment)
        .where(
            Payment.client_user_id == client_user_id,
            Payment.status == PaymentStatus.PENDING,
            Payment.expires_at > now,
        )
        .order_by(col(Payment.created_at).desc())
    )
    result = await session.exec(statement)
    return result.first()


async def acquire_free_account(
    session: AsyncSession,
    now: datetime,
) -> TelegramAccount | None:
    """
    Find an active Telegram account not currently locked by an active payment.
    Releases automatically and reactively when payment status is not pending or expired.
    """
    active_locked_account_ids = (
        select(Payment.account_id)
        .where(
            Payment.status == PaymentStatus.PENDING,
            Payment.expires_at > now,
        )
        .scalar_subquery()
    )

    statement = (
        select(TelegramAccount)
        .where(
            TelegramAccount.status == AccountStatus.ACTIVE,
            col(TelegramAccount.id).not_in(active_locked_account_ids),
        )
        .order_by(col(TelegramAccount.updated_at).asc())
        .limit(1)
    )
    result = await session.exec(statement)
    return result.first()


async def create_payment(
    session: AsyncSession,
    payment_in: PaymentCreate,
) -> Payment:
    """
    Create a new payment and reserve an account for 30 minutes.
    If the same user creates a new payment, cancels their previous payment
    and re-uses the same account.
    """
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=PAYMENT_TTL_MINUTES)
    scenario = scenario_registry.get(payment_in.scenario_id)

    # 1. Check if user already has an active pending payment
    existing_payment = await get_active_payment_for_user(
        session, payment_in.client_user_id, now
    )

    account: TelegramAccount | None = None

    if existing_payment:
        # Cancel previous payment
        existing_payment.status = PaymentStatus.CANCELLED
        existing_payment.cancelled_at = now
        session.add(existing_payment)
        await session.flush()

        # Re-use the same account if it's still active
        acc_stmt = select(TelegramAccount).where(
            TelegramAccount.id == existing_payment.account_id,
            TelegramAccount.status == AccountStatus.ACTIVE,
        )
        acc_result = await session.exec(acc_stmt)
        account = acc_result.first()

    # If no existing active payment or account was deactivated, acquire a new free one
    if not account:
        account = await acquire_free_account(session, now)

    if not account:
        raise NoAccountsAvailableException()

    # 2. Execute scenario to generate payment link
    ctx = ScenarioContext(
        client_user_id=payment_in.client_user_id,
        amount=payment_in.amount,
        currency=payment_in.currency,
        account=account,
        meta=payment_in.meta,
    )
    result = await scenario.create_payment(ctx)

    merged_meta = {**payment_in.meta, **result.meta}

    # 3. Create new payment record
    payment = Payment(
        client_user_id=payment_in.client_user_id,
        scenario_id=scenario.scenario_id,
        amount=payment_in.amount,
        currency=payment_in.currency,
        account_id=account.id,
        status=PaymentStatus.PENDING,
        payment_link=result.payment_link,
        expires_at=expires_at,
        meta=merged_meta,
    )

    session.add(payment)
    await session.flush()
    await session.refresh(payment)
    return payment


async def get_payment(
    session: AsyncSession,
    payment_id: UUID,
) -> Payment | None:
    """Retrieve payment by ID."""
    statement = select(Payment).where(Payment.id == payment_id)
    result = await session.exec(statement)
    return result.first()


async def list_payments_paginated(
    session: AsyncSession,
    params: PageParams,
) -> tuple[Sequence[Payment], int]:
    """Retrieve paginated payments."""
    count_statement = select(func.count()).select_from(Payment)
    total_result = await session.exec(count_statement)
    total = total_result.one() or 0

    statement = (
        select(Payment)
        .order_by(col(Payment.created_at).desc())
        .offset(params.offset)
        .limit(params.size)
    )
    result = await session.exec(statement)
    return result.all(), total


async def mark_payment_status(
    session: AsyncSession,
    db_payment: Payment,
    callback: PaymentCallback,
) -> Payment:
    """
    Update payment status from a webhook or callback.
    Transitioning to PAID or CANCELLED immediately and reactively frees the account.
    """
    now = datetime.now(UTC)
    db_payment.status = callback.status

    if callback.status == PaymentStatus.PAID:
        db_payment.paid_at = now
    elif callback.status == PaymentStatus.CANCELLED:
        db_payment.cancelled_at = now

    if callback.external_transaction_id or callback.meta:
        updated_meta = dict(db_payment.meta)
        if callback.external_transaction_id:
            updated_meta["external_transaction_id"] = callback.external_transaction_id
        if callback.meta:
            updated_meta.update(callback.meta)
        db_payment.meta = updated_meta

    session.add(db_payment)
    await session.flush()
    await session.refresh(db_payment)
    return db_payment


async def cancel_payment(
    session: AsyncSession,
    db_payment: Payment,
) -> Payment:
    """Cancel a pending payment and immediately unlock its assigned account."""
    now = datetime.now(UTC)
    db_payment.status = PaymentStatus.CANCELLED
    db_payment.cancelled_at = now
    session.add(db_payment)
    await session.flush()
    await session.refresh(db_payment)
    return db_payment


async def expire_overdue_payments(session: AsyncSession) -> int:
    """
    Expire all payments that have passed their 30m TTL.
    Returns the count of expired payments.
    """
    now = datetime.now(UTC)
    statement = select(Payment).where(
        Payment.status == PaymentStatus.PENDING,
        Payment.expires_at <= now,
    )
    result = await session.exec(statement)
    overdue = result.all()

    for payment in overdue:
        payment.status = PaymentStatus.EXPIRED
        session.add(payment)

    if overdue:
        await session.flush()

    return len(overdue)
