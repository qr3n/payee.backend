"""
Payments domain service and pool locking business logic.
Provides reactive account acquisition, 30m TTL locks, user re-use,
and scenario dispatch. Follows Unit of Work: NEVER calls session.commit().
"""

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import uuid6
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.exceptions import AppException
from app.core.logging import get_logger
from app.core.redis import get_redis_client
from app.modules.accounts.models import AccountStatus, TelegramAccount
from app.modules.payments import race_buffer
from app.modules.payments.exceptions import NoAccountsAvailableException
from app.modules.payments.models import Payment, PaymentStatus
from app.modules.payments.resolvers import resolve_sbp_link
from app.modules.payments.scenarios import (
    ScenarioContext,
    acquire_account_generation_lock,
    acquire_scenario_pending_lock,
    is_account_generation_locked,
    is_scenario_pending_locked,
    release_account_generation_lock,
    release_all_account_generation_locks,
    release_all_scenario_pending_locks,
    release_all_scenario_stars_reservations,
    release_scenario_pending_lock,
    release_scenario_stars_reservation,
    scenario_registry,
)
from app.modules.payments.schemas import (
    PaymentCallback,
    PaymentCreate,
    PaymentRaceCreate,
    PaymentRaceFireResponse,
)
from app.shared.pagination import PageParams

logger = get_logger(__name__)

PAYMENT_TTL_MINUTES = 30

# Prevent garbage-collection of fire-and-forget background race tasks
_active_race_tasks: set[asyncio.Task[None]] = set()


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
    scenario_id: str | None = None,
) -> TelegramAccount | None:
    """
    Find an active Telegram account not currently locked by an active generation.
    Accounts are locked exclusively for invoice generation (5-15s), not 30m pending.
    If the scenario requires an exclusive pending slot (e.g. starslly_bot), ensures
    the account does not currently hold a pending slot for this scenario.
    Prioritizes accounts: for non-exclusive scenarios, prefers accounts that ALREADY
    have exclusive slots occupied to preserve free slots for exclusive scenarios.
    """
    statement = (
        select(TelegramAccount)
        .where(
            TelegramAccount.status == AccountStatus.ACTIVE,
        )
        .order_by(col(TelegramAccount.updated_at).asc())
    )
    result = await session.exec(statement)
    active_accounts = list(result.all())

    scenario = scenario_registry.get(scenario_id) if scenario_id else None
    requires_slot = getattr(scenario, "requires_exclusive_pending_slot", False)

    candidates: list[TelegramAccount] = []
    if requires_slot and scenario:
        for acc in active_accounts:
            if not await is_scenario_pending_locked(scenario.scenario_id, acc.id):
                candidates.append(acc)
    else:
        # Prioritize accounts where starslly_bot slot is already locked
        has_slot_busy: list[TelegramAccount] = []
        is_clean: list[TelegramAccount] = []
        for acc in active_accounts:
            if await is_scenario_pending_locked("starslly_bot", acc.id):
                has_slot_busy.append(acc)
            else:
                is_clean.append(acc)
        candidates = has_slot_busy + is_clean

    for account in candidates:
        if await acquire_account_generation_lock(account.id):
            return account
    return None


async def create_payment(
    session: AsyncSession,
    payment_in: PaymentCreate,
) -> Payment:
    """
    Create a new payment. Accounts are locked strictly during invoice generation.
    Once the payment link is obtained, the account lock is released and the account
    is immediately re-warmed in the background for the next customer.
    If the same user creates a new payment, cancels their previous payment.
    """
    t_start = time.perf_counter()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=PAYMENT_TTL_MINUTES)
    scenario = scenario_registry.get(payment_in.scenario_id)

    # 0. Check idempotency if idempotency_key is provided
    if payment_in.idempotency_key:
        stmt = select(Payment).where(
            Payment.client_user_id == payment_in.client_user_id,
            Payment.idempotency_key == payment_in.idempotency_key,
        )
        existing_idem = (await session.exec(stmt)).first()
        if existing_idem:
            if (
                existing_idem.scenario_id == payment_in.scenario_id
                and existing_idem.amount == payment_in.amount
                and existing_idem.currency == payment_in.currency
            ):
                logger.info(
                    "payment_returned_by_idempotency_key",
                    payment_id=str(existing_idem.id),
                    idempotency_key=payment_in.idempotency_key,
                )
                return existing_idem
            else:
                raise AppException(
                    message=(
                        "Idempotency-Key already used with "
                        "different payment parameters."
                    ),
                    code="IDEMPOTENCY_CONFLICT",
                    status_code=409,
                )

    # 1. Check if user already has an active pending payment
    account_lookup_start = time.perf_counter()
    existing_payment = await get_active_payment_for_user(
        session, payment_in.client_user_id, now
    )

    account: TelegramAccount | None = None

    if existing_payment:
        # Cancel previous payment cleanly and release its reservations
        await cancel_payment(session, existing_payment)
        await session.flush()

        # Re-use the same account if it's still active and not currently generating
        acc_stmt = select(TelegramAccount).where(
            TelegramAccount.id == existing_payment.account_id,
            TelegramAccount.status == AccountStatus.ACTIVE,
        )
        acc_result = await session.exec(acc_stmt)
        candidate = acc_result.first()
        if candidate:
            candidate_available = True
            if getattr(
                scenario, "requires_exclusive_pending_slot", False
            ) and await is_scenario_pending_locked(scenario.scenario_id, candidate.id):
                candidate_available = False
            if candidate_available and await acquire_account_generation_lock(
                candidate.id
            ):
                account = candidate

    # If no existing active payment or candidate was busy, acquire a free one
    if not account:
        account = await acquire_free_account(session, scenario_id=scenario.scenario_id)

    account_lookup_duration = round(time.perf_counter() - account_lookup_start, 2)

    if not account:
        raise NoAccountsAvailableException()

    slot_locked = False
    try:
        # If scenario requires exclusive pending slot, acquire it now
        if getattr(scenario, "requires_exclusive_pending_slot", False):
            if not await acquire_scenario_pending_lock(
                scenario.scenario_id, account.id
            ):
                raise NoAccountsAvailableException(
                    "All account slots for this payment provider are currently busy."
                )
            slot_locked = True

        # 2. Execute scenario to generate payment link
        ctx = ScenarioContext(
            client_user_id=payment_in.client_user_id,
            amount=payment_in.amount,
            currency=payment_in.currency,
            account=account,
            meta=payment_in.meta,
        )
        result = await scenario.create_payment(ctx)

        # Release generation lock immediately after Telegram dialog finishes
        # (before external SBP resolution)
        await release_account_generation_lock(account.id)

        # 3. Attempt extraction of direct SBP (NSPK) link if supported gateway link
        t_resolve_start = time.perf_counter()
        resolved_link, is_resolved = await resolve_sbp_link(result.payment_link)
        resolve_duration = round(time.perf_counter() - t_resolve_start, 2)

        total_duration_sec = round(time.perf_counter() - t_start, 2)

        scenario_stages = list(result.meta.get("stage_timings", []))
        all_stages = [
            {
                "stage": "account_acquisition",
                "description": "Поиск и выделение аккаунта в пуле",
                "duration_sec": account_lookup_duration,
            },
            *scenario_stages,
        ]

        if is_resolved:
            all_stages.append(
                {
                    "stage": "resolve_sbp_link",
                    "description": "Извлечение прямой ссылки СБП (НСПК)",
                    "duration_sec": resolve_duration,
                }
            )
        elif "gate.antilopay.com" in (result.payment_link or "") or "cardlink.link" in (
            result.payment_link or ""
        ):
            all_stages.append(
                {
                    "stage": "resolve_sbp_link",
                    "description": "Попытка извлечения ссылки СБП (оставлен оригинал)",
                    "duration_sec": resolve_duration,
                }
            )

        merged_meta = {
            **payment_in.meta,
            **result.meta,
            "original_payment_link": result.payment_link,
            "resolved_sbp_link": resolved_link if is_resolved else None,
            "is_sbp_resolved": is_resolved,
            "generation_time_sec": total_duration_sec,
            "stage_timings": all_stages,
        }

        # 4. Create new payment record
        payment = Payment(
            client_user_id=payment_in.client_user_id,
            scenario_id=scenario.scenario_id,
            amount=payment_in.amount,
            currency=payment_in.currency,
            idempotency_key=payment_in.idempotency_key,
            account_id=account.id,
            status=PaymentStatus.PENDING,
            payment_link=resolved_link,
            expires_at=expires_at,
            meta=merged_meta,
        )

        account.updated_at = now
        session.add(account)
        session.add(payment)
        await session.flush()
        await session.refresh(payment)
    except Exception:
        if slot_locked:
            await release_scenario_pending_lock(scenario.scenario_id, account.id)
        raise
    finally:
        # Release the generation lock immediately once link is obtained or upon error
        await release_account_generation_lock(account.id)

    # 5. Trigger non-blocking background re-preparation for subsequent use
    try:
        from app.modules.payments.tasks import dispatch_single_scenario_warmup

        await dispatch_single_scenario_warmup(
            account_id=account.id,
            scenario_id=scenario.scenario_id,
        )
    except Exception as exc:
        logger.warning(
            "failed_dispatching_post_payment_warmup",
            account_id=str(account.id),
            scenario_id=scenario.scenario_id,
            error=str(exc),
        )

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


PROTECTED_INTERNAL_META_KEYS = {
    "stars_count",
    "stars_delta",
    "base_stars_count",
    "account_used",
    "account_id",
    "stage_timings",
    "scenario_duration_sec",
    "is_fast_path",
}


async def mark_payment_status(
    session: AsyncSession,
    db_payment: Payment,
    callback: PaymentCallback,
) -> Payment:
    """
    Update payment status from a webhook or callback.
    Enforces valid state machine transitions:
    - PENDING -> PAID, CANCELLED, EXPIRED, FAILED
    - Terminal states (PAID, CANCELLED, etc.) cannot be undone.
    - Idempotent if target status matches current status.
    - Protected internal meta fields cannot be overwritten by callback.
    """
    now = datetime.now(UTC)

    # 1. State machine transition check
    if db_payment.status == callback.status:
        # Idempotent re-delivery
        return db_payment

    if db_payment.status == PaymentStatus.PAID:
        raise AppException(
            message="Платёж уже оплачен и не может изменить статус.",
            code="PAYMENT_ALREADY_PAID",
            status_code=409,
        )

    if db_payment.status in (
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
        PaymentStatus.FAILED,
    ):
        raise AppException(
            message=f"Платёж уже закрыт со статусом {db_payment.status.value}.",
            code="PAYMENT_ALREADY_CLOSED",
            status_code=409,
        )

    # Valid transition from PENDING
    db_payment.status = callback.status

    if callback.status == PaymentStatus.PAID:
        db_payment.paid_at = now
    elif callback.status == PaymentStatus.CANCELLED:
        db_payment.cancelled_at = now

    # Read original stars_count BEFORE merging any callback metadata
    stars_count = db_payment.meta.get("stars_count")

    if callback.external_transaction_id or callback.meta:
        updated_meta = dict(db_payment.meta)
        if callback.external_transaction_id:
            updated_meta["external_transaction_id"] = callback.external_transaction_id
        if callback.meta:
            # Filter out protected internal keys
            safe_meta = {
                k: v
                for k, v in callback.meta.items()
                if k not in PROTECTED_INTERNAL_META_KEYS
            }
            updated_meta.update(safe_meta)
        db_payment.meta = updated_meta

    # Release any reserved stars count for the scenario (on terminal states)
    if callback.status in (
        PaymentStatus.PAID,
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
        PaymentStatus.FAILED,
    ):
        if stars_count and db_payment.scenario_id:
            await release_scenario_stars_reservation(
                db_payment.scenario_id, int(stars_count)
            )

        # Release any pending scenario slot lock (e.g. starslly_bot)
        if db_payment.scenario_id and db_payment.account_id:
            await release_scenario_pending_lock(
                db_payment.scenario_id, db_payment.account_id
            )

    session.add(db_payment)
    await session.flush()
    await session.refresh(db_payment)
    return db_payment


async def cancel_payment(
    session: AsyncSession,
    db_payment: Payment,
) -> Payment:
    """
    Cancel a pending payment and immediately unlock its assigned account.
    If payment is already PAID, raises 409 conflict.
    If already CANCELLED, EXPIRED, or FAILED, returns idempotently.
    """
    if db_payment.status == PaymentStatus.PAID:
        raise AppException(
            message="Нельзя отменить уже оплаченный платёж.",
            code="PAYMENT_ALREADY_PAID",
            status_code=409,
        )

    if db_payment.status in (
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
        PaymentStatus.FAILED,
    ):
        return db_payment

    now = datetime.now(UTC)
    db_payment.status = PaymentStatus.CANCELLED
    db_payment.cancelled_at = now

    stars_count = db_payment.meta.get("stars_count")
    if stars_count and db_payment.scenario_id:
        await release_scenario_stars_reservation(
            db_payment.scenario_id, int(stars_count)
        )

    if db_payment.scenario_id and db_payment.account_id:
        await release_scenario_pending_lock(
            db_payment.scenario_id, db_payment.account_id
        )

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
        stars_count = payment.meta.get("stars_count")
        if stars_count and payment.scenario_id:
            await release_scenario_stars_reservation(
                payment.scenario_id, int(stars_count)
            )
        if payment.scenario_id and payment.account_id:
            await release_scenario_pending_lock(payment.scenario_id, payment.account_id)
        session.add(payment)

    if overdue:
        await session.flush()

    return len(overdue)


async def release_all_locked_accounts(session: AsyncSession) -> tuple[int, int]:
    """
    Cancel all active PENDING payments to immediately free all reserved
    Telegram accounts. Returns (cancelled_payments_count, released_accounts_count).
    """
    now = datetime.now(UTC)
    statement = select(Payment).where(
        Payment.status == PaymentStatus.PENDING,
        Payment.expires_at > now,
    )
    result = await session.exec(statement)
    active_pending = result.all()

    released_account_ids: set[UUID] = set()
    for payment in active_pending:
        payment.status = PaymentStatus.CANCELLED
        payment.cancelled_at = now
        if payment.account_id:
            released_account_ids.add(payment.account_id)
        session.add(payment)

    if active_pending:
        await session.flush()

    # Release lingering Redis generation, stars, and pending slot locks
    cleared_redis_locks = await release_all_account_generation_locks()
    await release_all_scenario_stars_reservations()
    await release_all_scenario_pending_locks()

    logger.info(
        "all_locked_accounts_released",
        cancelled_payments=len(active_pending),
        released_accounts=len(released_account_ids),
        cleared_redis_locks=cleared_redis_locks,
    )
    return len(active_pending), max(len(released_account_ids), cleared_redis_locks)


async def prepare_account_scenarios(
    session: AsyncSession,
    account_id: UUID,
) -> dict[str, Any]:
    """
    Execute background warmup/preparation for all registered payment scenarios
    for the given Telegram account (e.g. pre-joining channels, sending /start,
    confirming subscriptions).
    """

    from app.modules.accounts.session_pool import telegram_session_pool

    account = await session.get(TelegramAccount, account_id)
    if not account:
        logger.warning(
            "Account not found for scenario preparation",
            account_id=str(account_id),
        )
        return {"status": "error", "error": "Account not found"}

    if account.status != AccountStatus.ACTIVE:
        logger.warning(
            "Skipping scenario preparation for inactive account",
            account_id=str(account_id),
            status=account.status.value,
        )
        return {
            "status": "skipped",
            "account_id": str(account.id),
            "reason": f"Account is {account.status.value}",
        }

    # Acquire exclusive generation lock to prevent collision with active payment
    lock_token = await acquire_account_generation_lock(account.id, ttl_seconds=120)
    if not lock_token:
        logger.info(
            "scenario_prepare_skipped_account_busy",
            account_id=str(account.id),
        )
        return {
            "status": "skipped",
            "account_id": str(account.id),
            "reason": "Account is busy generating a payment",
        }

    try:
        client = await telegram_session_pool.get_connected_client(account)
        results: dict[str, str] = {}
        all_ok = True

        for scenario in scenario_registry.list():
            try:
                logger.info(
                    "preparing_scenario_for_account",
                    scenario_id=scenario.scenario_id,
                    account_id=str(account.id),
                )
                res = await scenario.prepare(account=account, client=client)
                results[scenario.scenario_id] = res.status
                if res.status not in ("ok", "skipped"):
                    all_ok = False
            except Exception as exc:
                logger.error(
                    "scenario_prepare_error",
                    scenario_id=scenario.scenario_id,
                    account_id=str(account.id),
                    error=str(exc),
                )
                results[scenario.scenario_id] = f"error: {exc}"
                all_ok = False

        return {
            "status": "completed" if all_ok else "failed",
            "account_id": str(account.id),
            "results": results,
        }
    finally:
        await release_account_generation_lock(account.id, owner_token=lock_token)


async def prepare_single_scenario(
    session: AsyncSession,
    account_id: UUID,
    scenario_id: str,
) -> dict[str, Any]:
    """
    Execute background warmup/preparation for a specific scenario on an account.
    """
    from app.modules.accounts.session_pool import telegram_session_pool

    account = await session.get(TelegramAccount, account_id)
    if not account or account.status != AccountStatus.ACTIVE:
        return {"status": "skipped", "reason": "Account missing or inactive"}

    scenario = scenario_registry.get(scenario_id)
    if not scenario:
        return {"status": "skipped", "reason": f"Scenario {scenario_id} not found"}

    lock_token = await acquire_account_generation_lock(account.id, ttl_seconds=60)
    if not lock_token:
        logger.info(
            "single_scenario_prepare_skipped_account_busy",
            account_id=str(account.id),
            scenario_id=scenario_id,
        )
        return {
            "status": "skipped",
            "account_id": str(account.id),
            "scenario_id": scenario_id,
            "reason": "Account is busy generating a payment",
        }

    try:
        client = await telegram_session_pool.get_connected_client(account)
        logger.info(
            "preparing_single_scenario_for_account",
            scenario_id=scenario_id,
            account_id=str(account.id),
        )
        res = await scenario.prepare(account=account, client=client)
        return {
            "status": "completed" if res.status == "ok" else res.status,
            "account_id": str(account.id),
            "scenario_id": scenario_id,
            "reason": res.reason,
        }
    except Exception as exc:
        logger.error(
            "single_scenario_prepare_error",
            scenario_id=scenario_id,
            account_id=str(account.id),
            error=str(exc),
        )
        return {"status": "error", "error": str(exc)}
    finally:
        await release_account_generation_lock(account.id, owner_token=lock_token)


async def refresh_idle_account_scenarios(
    session: AsyncSession,
) -> dict[str, Any]:
    """
    Scan active accounts that are not currently in the middle of invoice generation
    and re-prepare any scenarios whose preparation is missing or expired in Redis.
    """
    from app.modules.payments.scenarios.state import is_scenario_prepared

    acc_query = select(TelegramAccount).where(
        TelegramAccount.status == AccountStatus.ACTIVE,
    )
    acc_res = await session.exec(acc_query)
    all_active = acc_res.all()

    # Filter out accounts currently performing invoice generation
    active_accounts = []
    for acc in all_active:
        if not await is_account_generation_locked(acc.id):
            active_accounts.append(acc)

    scenarios = scenario_registry.list()
    refreshed: list[dict[str, str]] = []

    for acc in active_accounts:
        for scen in scenarios:
            try:
                is_prep = await is_scenario_prepared(acc.id, scen.scenario_id)
                if not is_prep:
                    logger.info(
                        "refreshing_idle_unprepared_scenario",
                        account_id=str(acc.id),
                        scenario_id=scen.scenario_id,
                    )
                    await prepare_single_scenario(
                        session=session,
                        account_id=acc.id,
                        scenario_id=scen.scenario_id,
                    )
                    refreshed.append(
                        {
                            "account_id": str(acc.id),
                            "scenario_id": scen.scenario_id,
                        }
                    )
            except Exception as exc:
                logger.warning(
                    "failed_refreshing_scenario",
                    account_id=str(acc.id),
                    scenario_id=scen.scenario_id,
                    error=str(exc),
                )

    return {
        "status": "completed",
        "idle_accounts_count": len(active_accounts),
        "refreshed_count": len(refreshed),
        "refreshed": refreshed,
    }


async def acquire_N_free_accounts(
    session: AsyncSession,
    n: int,
) -> list[TelegramAccount]:
    """
    Acquire up to N free Telegram accounts (not currently locked by generation).
    Returns LRU accounts first and atomically locks them for generation.
    """
    statement = (
        select(TelegramAccount)
        .where(
            TelegramAccount.status == AccountStatus.ACTIVE,
        )
        .order_by(col(TelegramAccount.updated_at).asc())
    )
    result = await session.exec(statement)
    active_accounts = result.all()

    acquired: list[TelegramAccount] = []
    for acc in active_accounts:
        if await acquire_account_generation_lock(acc.id):
            acquired.append(acc)
            if len(acquired) == n:
                break
    return acquired


async def _run_race_scenario_task(
    account: TelegramAccount,
    scenario_id: str,
    batch_id: UUID,
    race_in: PaymentRaceCreate,
    expires_at: datetime,
    race_start: float,
) -> str:
    """
    Run a single scenario for the race. Each task owns its own DB session.
    Returns an SSE-formatted 'event: payment\\ndata: {...}\\n\\n' string on success.
    Releases the generation lock immediately upon link generation or failure.
    Raises on failure so asyncio.as_completed can propagate it.
    """
    from app.core.db import async_session_maker
    from app.modules.payments.scenarios.registry import (
        scenario_registry as _registry,
    )

    slot_locked = False
    try:
        scenario = _registry.get(scenario_id)
        if not scenario:
            raise ValueError(f"Scenario {scenario_id!r} not found in registry")

        if getattr(scenario, "requires_exclusive_pending_slot", False):
            if not await acquire_scenario_pending_lock(
                scenario.scenario_id, account.id
            ):
                raise ValueError(f"Account slot for {scenario_id} is already occupied")
            slot_locked = True

        ctx = ScenarioContext(
            client_user_id=race_in.client_user_id,
            amount=race_in.amount,
            currency=race_in.currency,
            account=account,
            meta=race_in.meta,
        )

        result = await scenario.create_payment(ctx)

        # Release generation lock immediately after Telegram dialog finishes
        # (before external SBP resolution)
        await release_account_generation_lock(account.id)

        # SBP resolution
        t_resolve_start = time.perf_counter()
        resolved_link, is_resolved = await resolve_sbp_link(result.payment_link)
        resolve_duration = round(time.perf_counter() - t_resolve_start, 2)

        generation_time_sec = round(time.perf_counter() - race_start, 2)

        scenario_stages = list(result.meta.get("stage_timings", []))
        all_stages = [*scenario_stages]
        if is_resolved:
            all_stages.append(
                {
                    "stage": "resolve_sbp_link",
                    "description": "Извлечение прямой ссылки СБП (НСПК)",
                    "duration_sec": resolve_duration,
                }
            )
        elif "gate.antilopay.com" in (result.payment_link or "") or "cardlink.link" in (
            result.payment_link or ""
        ):
            all_stages.append(
                {
                    "stage": "resolve_sbp_link",
                    "description": "Попытка извлечения ссылки СБП (оставлен оригинал)",
                    "duration_sec": resolve_duration,
                }
            )

        merged_meta = {
            **race_in.meta,
            **result.meta,
            "original_payment_link": result.payment_link,
            "resolved_sbp_link": resolved_link if is_resolved else None,
            "is_sbp_resolved": is_resolved,
            "generation_time_sec": generation_time_sec,
            "stage_timings": all_stages,
            "batch_id": str(batch_id),
        }

        # Persist payment record (own session + own commit — SSE lifecycle exception)
        payment: Payment
        async with async_session_maker() as task_session:
            try:
                payment = Payment(
                    client_user_id=race_in.client_user_id,
                    scenario_id=scenario_id,
                    amount=race_in.amount,
                    currency=race_in.currency,
                    account_id=account.id,
                    batch_id=batch_id,
                    status=PaymentStatus.PENDING,
                    payment_link=resolved_link,
                    expires_at=expires_at,
                    meta=merged_meta,
                )
                task_session.add(payment)
                await task_session.flush()
                await task_session.refresh(payment)
                await task_session.commit()
            except Exception:
                await task_session.rollback()
                raise
    except asyncio.CancelledError:
        if slot_locked:
            await release_scenario_pending_lock(scenario_id, account.id)
        raise
    except Exception:
        if slot_locked:
            await release_scenario_pending_lock(scenario_id, account.id)
        raise
    finally:
        # Guarantee generation lock is released once link is obtained or upon error
        await release_account_generation_lock(account.id)

    # Trigger warmup for next use (non-blocking, best-effort)
    try:
        from app.modules.payments.tasks import dispatch_single_scenario_warmup

        await dispatch_single_scenario_warmup(
            account_id=account.id,
            scenario_id=scenario_id,
        )
    except Exception as exc:
        logger.warning(
            "failed_dispatching_post_race_warmup",
            account_id=str(account.id),
            scenario_id=scenario_id,
            error=str(exc),
        )

    event_payload = {
        "batch_id": str(batch_id),
        "payment_id": str(payment.id),
        "scenario_id": scenario_id,
        "payment_link": resolved_link,
        "is_sbp_resolved": is_resolved,
        "generation_time_sec": generation_time_sec,
        "stage_timings": all_stages,
    }
    return f"event: payment\ndata: {json.dumps(event_payload, ensure_ascii=False)}\n\n"


async def start_payment_race(
    session: AsyncSession,
    race_in: PaymentRaceCreate,
) -> PaymentRaceFireResponse:
    """
    Fire-and-forget race initiation.

    Acquires free Telegram accounts, initializes a Redis event buffer,
    and launches scenario generation in a background ``asyncio.Task``.
    Returns immediately with batch metadata so the caller can redirect
    the user to the SSE subscription endpoint.
    """
    race_start = time.perf_counter()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=PAYMENT_TTL_MINUTES)
    batch_id: UUID = uuid6.uuid7()

    # Resolve scenarios
    if race_in.scenario_ids:
        scenarios = [
            s
            for s in (scenario_registry.get(sid) for sid in race_in.scenario_ids)
            if s is not None
        ]
    else:
        scenarios = scenario_registry.list_primary()

    num_scenarios = len(scenarios)
    if num_scenarios == 0:
        raise NoAccountsAvailableException("No valid scenarios to race")

    # Acquire free accounts (one per scenario)
    free_accounts = await acquire_N_free_accounts(session, n=num_scenarios)

    if not free_accounts:
        raise NoAccountsAvailableException()

    # Pair accounts with scenarios (exclusive slots first)
    pairs: list[tuple[TelegramAccount, str]] = []
    remaining_accounts = list(free_accounts)

    exclusive_scenarios = [
        s for s in scenarios if getattr(s, "requires_exclusive_pending_slot", False)
    ]
    non_exclusive_scenarios = [
        s for s in scenarios if not getattr(s, "requires_exclusive_pending_slot", False)
    ]

    for s in exclusive_scenarios:
        assigned_acc = None
        for acc in remaining_accounts:
            if not await is_scenario_pending_locked(s.scenario_id, acc.id):
                assigned_acc = acc
                break
        if assigned_acc:
            remaining_accounts.remove(assigned_acc)
            pairs.append((assigned_acc, s.scenario_id))

    for s in non_exclusive_scenarios:
        if remaining_accounts:
            acc = remaining_accounts.pop(0)
            pairs.append((acc, s.scenario_id))

    # Release locks on unused accounts
    for unused_acc in remaining_accounts:
        await release_account_generation_lock(unused_acc.id)

    scenario_ids = [sid for _, sid in pairs]

    # Initialize Redis event buffer (pushes "started" event)
    await race_buffer.init_race_buffer(batch_id, scenario_ids)

    # Launch background runner (fire-and-forget)
    task = asyncio.create_task(
        _race_background_runner(
            pairs=pairs,
            batch_id=batch_id,
            race_in=race_in,
            expires_at=expires_at,
            race_start=race_start,
        )
    )
    _active_race_tasks.add(task)
    task.add_done_callback(_active_race_tasks.discard)

    logger.info(
        "payment_race_fired",
        batch_id=str(batch_id),
        scenarios=scenario_ids,
        accounts=[str(a.id) for a, _ in pairs],
    )

    return PaymentRaceFireResponse(
        batch_id=batch_id,
        status="running",
        scenarios=scenario_ids,
    )


async def _race_background_runner(
    pairs: list[tuple[TelegramAccount, str]],
    batch_id: UUID,
    race_in: PaymentRaceCreate,
    expires_at: datetime,
    race_start: float,
) -> None:
    """
    Background coroutine: runs all race scenario tasks concurrently and
    writes each result to the Redis event buffer as it arrives.

    Handles timeouts, errors, and ensures all generation locks are released.
    """
    tasks = [
        asyncio.create_task(
            _run_race_scenario_task(
                account=account,
                scenario_id=scenario_id,
                batch_id=batch_id,
                race_in=race_in,
                expires_at=expires_at,
                race_start=race_start,
            )
        )
        for account, scenario_id in pairs
    ]

    succeeded = 0
    failed = 0
    timed_out = False

    try:
        async with asyncio.timeout(race_in.timeout_sec):
            for fut in asyncio.as_completed(tasks):
                try:
                    sse_event = await fut
                    succeeded += 1
                    await race_buffer.push_race_event(batch_id, sse_event)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    failed += 1
                    logger.error(
                        "race_scenario_task_failed",
                        batch_id=str(batch_id),
                        error=str(exc),
                    )
                    err_payload = json.dumps(
                        {"error": "scenario_failed", "message": str(exc)},
                        ensure_ascii=False,
                    )
                    err_event = f"event: error\ndata: {err_payload}\n\n"
                    await race_buffer.push_race_event(batch_id, err_event)
    except TimeoutError:
        timed_out = True
        logger.warning(
            "race_timed_out",
            batch_id=str(batch_id),
            timeout_sec=race_in.timeout_sec,
            succeeded=succeeded,
        )
        timeout_payload = json.dumps(
            {
                "error": "timeout",
                "message": f"Таймаут {race_in.timeout_sec}с достигнут",
            },
            ensure_ascii=False,
        )
        timeout_event = f"event: error\ndata: {timeout_payload}\n\n"
        await race_buffer.push_race_event(batch_id, timeout_event)
    except Exception as exc:
        logger.error(
            "race_background_runner_error",
            batch_id=str(batch_id),
            error=str(exc),
        )
    finally:
        # Cancel all pending tasks and wait for them to finish cleanly
        pending = [t for t in tasks if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        for account, _ in pairs:
            await release_account_generation_lock(account.id)

    total_duration = round(time.perf_counter() - race_start, 2)
    await race_buffer.finish_race(
        batch_id,
        status="timeout" if timed_out else "done",
        total=len(pairs),
        succeeded=succeeded,
        failed=failed,
        duration_sec=total_duration,
    )


async def race_stream_generator(
    batch_id: UUID,
) -> AsyncGenerator[str, None]:
    """
    SSE generator with replay from Redis event buffer.

    1. Subscribes to the batch Pub/Sub channel **first** (to avoid gaps)
    2. Replays all already-buffered events (instant delivery)
    3. If the race is still running, streams new events in real-time
    4. Closes when the race finishes or the absolute SSE timeout is reached

    This allows the client to connect at any point — before, during, or
    after generation — and always receive the full event sequence.
    """
    redis = get_redis_client()
    channel = race_buffer.get_channel_name(batch_id)

    # Check if batch exists
    status = await race_buffer.get_race_status(batch_id)
    if status is None:
        err = json.dumps(
            {
                "error": "batch_not_found",
                "message": f"Race batch {batch_id} not found or expired",
            },
            ensure_ascii=False,
        )
        yield f"event: error\ndata: {err}\n\n"
        return

    # Subscribe FIRST so we never miss events between replay and listen
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)

    try:
        # Replay: yield all existing events from buffer
        existing = await race_buffer.get_buffered_events(batch_id, start=0)
        cursor = len(existing)
        for event in existing:
            yield event

        # If already finished, drain any newly added events up to cursor and exit
        status = await race_buffer.get_race_status(batch_id)
        if status != "running":
            rem = await race_buffer.get_buffered_events(batch_id, start=cursor)
            for event in rem:
                yield event
            return

        # Live: poll Pub/Sub with periodic status fallback checks
        max_wait = 300.0  # 5 minutes absolute SSE timeout
        start_time = time.monotonic()

        while (time.monotonic() - start_time) < max_wait:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=5.0
            )

            if message is not None:
                # New events available — read from buffer at our cursor
                new_events = await race_buffer.get_buffered_events(
                    batch_id, start=cursor
                )
                cursor += len(new_events)
                for event in new_events:
                    yield event

                # Exit when race is done
                if message.get("data") == "done":
                    rem = await race_buffer.get_buffered_events(batch_id, start=cursor)
                    for event in rem:
                        yield event
                    return
            else:
                # Heartbeat comment to keep connection alive through proxies
                yield ": ping\n\n"
                # Periodic fallback: race may have finished without us
                # receiving the Pub/Sub notification
                status = await race_buffer.get_race_status(batch_id)
                if status != "running":
                    new_events = await race_buffer.get_buffered_events(
                        batch_id, start=cursor
                    )
                    for event in new_events:
                        yield event
                    return

        # Absolute SSE timeout reached
        timeout_err = json.dumps(
            {"error": "sse_timeout", "message": "SSE stream timeout reached"},
            ensure_ascii=False,
        )
        yield f"event: error\ndata: {timeout_err}\n\n"
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()  # type: ignore[no-untyped-call]


async def shutdown_active_races(timeout: float = 5.0) -> None:
    """Gracefully cancel and await all active race tasks on application shutdown."""
    if not _active_race_tasks:
        return
    tasks = list(_active_race_tasks)
    logger.info("shutting_down_active_races", count=len(tasks))
    for t in tasks:
        t.cancel()
    try:
        async with asyncio.timeout(timeout):
            await asyncio.gather(*tasks, return_exceptions=True)
    except TimeoutError:
        logger.warning("timeout_waiting_for_active_races_shutdown")
