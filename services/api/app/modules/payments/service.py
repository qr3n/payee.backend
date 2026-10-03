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

from app.core.logging import get_logger
from app.modules.accounts.models import AccountStatus, TelegramAccount
from app.modules.payments.exceptions import NoAccountsAvailableException
from app.modules.payments.models import Payment, PaymentStatus
from app.modules.payments.resolvers import resolve_sbp_link
from app.modules.payments.scenarios import (
    ScenarioContext,
    scenario_registry,
)
from app.modules.payments.schemas import (
    PaymentCallback,
    PaymentCreate,
    PaymentRaceCreate,
)
from app.shared.pagination import PageParams

logger = get_logger(__name__)

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
    t_start = time.perf_counter()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=PAYMENT_TTL_MINUTES)
    scenario = scenario_registry.get(payment_in.scenario_id)

    # 1. Check if user already has an active pending payment
    account_lookup_start = time.perf_counter()
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

    account_lookup_duration = round(time.perf_counter() - account_lookup_start, 2)

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
        account_id=account.id,
        status=PaymentStatus.PENDING,
        payment_link=resolved_link,
        expires_at=expires_at,
        meta=merged_meta,
    )

    session.add(payment)
    await session.flush()
    await session.refresh(payment)

    # 4. Trigger non-blocking background re-preparation for subsequent use
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

    client = await telegram_session_pool.get_connected_client(account)
    results: dict[str, str] = {}

    for scenario in scenario_registry.list():
        try:
            logger.info(
                "preparing_scenario_for_account",
                scenario_id=scenario.scenario_id,
                account_id=str(account.id),
            )
            await scenario.prepare(account=account, client=client)
            results[scenario.scenario_id] = "ok"
        except Exception as exc:
            logger.error(
                "scenario_prepare_error",
                scenario_id=scenario.scenario_id,
                account_id=str(account.id),
                error=str(exc),
            )
            results[scenario.scenario_id] = f"error: {exc}"

    return {
        "status": "completed",
        "account_id": str(account.id),
        "results": results,
    }


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

    try:
        client = await telegram_session_pool.get_connected_client(account)
        logger.info(
            "preparing_single_scenario_for_account",
            scenario_id=scenario_id,
            account_id=str(account.id),
        )
        await scenario.prepare(account=account, client=client)
        return {
            "status": "completed",
            "account_id": str(account.id),
            "scenario_id": scenario_id,
        }
    except Exception as exc:
        logger.error(
            "single_scenario_prepare_error",
            scenario_id=scenario_id,
            account_id=str(account.id),
            error=str(exc),
        )
        return {"status": "error", "error": str(exc)}


async def refresh_idle_account_scenarios(
    session: AsyncSession,
) -> dict[str, Any]:
    """
    Scan active accounts without active pending payments and re-prepare
    any scenarios whose preparation is missing or expired in Redis.
    """
    from app.modules.payments.scenarios.state import is_scenario_prepared

    now = datetime.now(UTC)
    pending_accounts_query = select(Payment.account_id).where(
        Payment.status == PaymentStatus.PENDING,
        Payment.expires_at > now,
    )
    pending_res = await session.exec(pending_accounts_query)
    busy_account_ids = set(pending_res.all())

    acc_query = select(TelegramAccount).where(
        TelegramAccount.status == AccountStatus.ACTIVE,
    )
    acc_res = await session.exec(acc_query)
    active_accounts = [acc for acc in acc_res.all() if acc.id not in busy_account_ids]

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
    now: datetime,
    n: int,
) -> list[TelegramAccount]:
    """
    Acquire up to N free Telegram accounts (not locked by active payments).
    Returns the least-recently-used accounts first.
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
        .limit(n)
    )
    result = await session.exec(statement)
    return list(result.all())


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
    Raises on failure so asyncio.as_completed can propagate it.
    """
    from app.core.db import async_session_maker
    from app.modules.payments.scenarios.registry import (
        scenario_registry as _registry,
    )

    scenario = _registry.get(scenario_id)
    if not scenario:
        raise ValueError(f"Scenario {scenario_id!r} not found in registry")

    ctx = ScenarioContext(
        client_user_id=race_in.client_user_id,
        amount=race_in.amount,
        currency=race_in.currency,
        account=account,
        meta=race_in.meta,
    )

    result = await scenario.create_payment(ctx)

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


async def create_payment_race_generator(
    race_in: PaymentRaceCreate,
) -> AsyncGenerator[str, None]:
    """
    Async generator for SSE payment race.

    Acquires up to N free accounts (one per registered scenario), locks them via
    PENDING stub payments, then runs all scenarios concurrently. Yields each
    successful result as it arrives. Cancels remaining tasks after timeout.

    SSE event types:
      - 'started'  — emitted immediately with batch_id and scenario count
      - 'payment'  — emitted for each successful scenario result
      - 'error'    — emitted for each failed scenario
      - 'done'     — emitted when all tasks have resolved or timed out
    """
    from app.core.db import async_session_maker

    race_start = time.perf_counter()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=PAYMENT_TTL_MINUTES)
    batch_id: UUID = uuid6.uuid7()
    scenarios = scenario_registry.list()
    num_scenarios = len(scenarios)

    # Acquire free accounts (one per scenario, up to num_scenarios)
    async with async_session_maker() as setup_session:
        try:
            free_accounts = await acquire_N_free_accounts(
                setup_session, now, n=num_scenarios
            )

            if not free_accounts:
                err_data = json.dumps(
                    {
                        "error": "no_accounts_available",
                        "message": "Нет свободных аккаунтов",
                    },
                    ensure_ascii=False,
                )
                yield f"event: error\ndata: {err_data}\n\n"
                done_data = json.dumps(
                    {"total": 0, "succeeded": 0, "failed": 0},
                    ensure_ascii=False,
                )
                yield f"event: done\ndata: {done_data}\n\n"
                return

            # Build (account, scenario_id) pairs
            pairs: list[tuple[TelegramAccount, str]] = [
                (account, scenario.scenario_id)
                for account, scenario in zip(free_accounts, scenarios, strict=False)
            ]

            # Lock accounts by creating PENDING stub payments
            for account, scenario_id in pairs:
                stub = Payment(
                    client_user_id=race_in.client_user_id,
                    scenario_id=scenario_id,
                    amount=race_in.amount,
                    currency=race_in.currency,
                    account_id=account.id,
                    batch_id=batch_id,
                    status=PaymentStatus.PENDING,
                    payment_link=None,
                    expires_at=expires_at,
                    meta={"batch_id": str(batch_id), "race_stub": True},
                )
                setup_session.add(stub)

            await setup_session.commit()
        except Exception:
            await setup_session.rollback()
            raise

    started_data = json.dumps(
        {"batch_id": str(batch_id), "scenarios": [s for _, s in pairs]},
        ensure_ascii=False,
    )
    yield f"event: started\ndata: {started_data}\n\n"

    # Launch all scenario tasks concurrently
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

    try:
        for fut in asyncio.as_completed(tasks, timeout=race_in.timeout_sec):
            try:
                sse_event = await fut
                succeeded += 1
                yield sse_event
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
                yield f"event: error\ndata: {err_payload}\n\n"
    except TimeoutError:
        logger.warning(
            "race_timed_out",
            batch_id=str(batch_id),
            timeout_sec=race_in.timeout_sec,
            succeeded=succeeded,
        )
        for task in tasks:
            task.cancel()
        timeout_payload = json.dumps(
            {
                "error": "timeout",
                "message": f"Таймаут {race_in.timeout_sec}с достигнут",
            },
            ensure_ascii=False,
        )
        yield f"event: error\ndata: {timeout_payload}\n\n"

    total_duration = round(time.perf_counter() - race_start, 2)
    done_payload = json.dumps(
        {
            "batch_id": str(batch_id),
            "total": len(pairs),
            "succeeded": succeeded,
            "failed": failed,
            "duration_sec": total_duration,
        },
        ensure_ascii=False,
    )
    yield f"event: done\ndata: {done_payload}\n\n"
