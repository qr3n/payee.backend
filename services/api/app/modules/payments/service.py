"""
Payments domain service and pool locking business logic.
Provides reactive account acquisition, 30m TTL locks, user re-use,
and scenario dispatch. Follows Unit of Work: NEVER calls session.commit().
"""

import asyncio
import contextlib
import hashlib
import json
import time
from collections.abc import AsyncGenerator, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import uuid6
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import db as core_db
from app.core.exceptions import AppException, NotFoundException
from app.core.logging import get_logger
from app.core.redis import get_redis_client
from app.modules.accounts.models import AccountStatus, TelegramAccount
from app.modules.payments import race_buffer
from app.modules.payments.exceptions import (
    NoAccountsAvailableException,
    UnknownScenarioException,
)
from app.modules.payments.models import Payment, PaymentStatus
from app.modules.payments.resolvers import resolve_sbp_link
from app.modules.payments.scenarios import (
    AccountLeaseRenewer,
    AcquiredAccount,
    BasePaymentScenario,
    GenerationLease,
    LeaseLostException,
    ScenarioContext,
    ScenarioResult,
    acquire_account_generation_lock,
    acquire_scenario_pending_lock,
    is_account_generation_locked,
    is_scenario_pending_locked,
    release_account_generation_lock,
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


def compute_payment_request_hash(
    payment_in: PaymentCreate | PaymentRaceCreate,
) -> str:
    """
    Compute canonical SHA-256 hash of payment request parameters to prevent
    idempotency key reuse with different parameters.
    Normalizes numeric amounts (e.g. 100 vs 100.00), canonicalizes recipients,
    and sorts canonical metadata.
    """
    amount_str = f"{Decimal(str(payment_in.amount)).quantize(Decimal('0.01')):.2f}"
    currency = payment_in.currency.upper()
    scenario_id = getattr(payment_in, "scenario_id", "") or ""
    scenario_ids = getattr(payment_in, "scenario_ids", None)
    scenario_ids_str = ",".join(sorted(scenario_ids)) if scenario_ids else ""
    client_user_id = str(payment_in.client_user_id)

    meta = getattr(payment_in, "meta", None) or {}
    canonical_meta: dict[str, Any] = {}
    for k in sorted(meta.keys()):
        k_str = str(k).lower()
        if k_str in (
            "recipient",
            "recipient_username",
            "bot_username",
            "rate",
            "stars_count",
            "order_id",
            "source",
            "channel",
        ):
            v = meta[k]
            if k_str in (
                "recipient",
                "recipient_username",
                "bot_username",
            ) and isinstance(v, str):
                canonical_meta[k_str] = v.lstrip("@").lower()
            else:
                canonical_meta[k_str] = v

    meta_json = json.dumps(canonical_meta, sort_keys=True, default=str)

    callback_url = str(getattr(payment_in, "callback_url", "") or "")

    raw_data = (
        f"{client_user_id}|{scenario_id}|{scenario_ids_str}|"
        f"{amount_str}|{currency}|{callback_url}|{meta_json}"
    )
    return hashlib.sha256(raw_data.encode()).hexdigest()


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
) -> AcquiredAccount | None:
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
        token = await acquire_account_generation_lock(account.id)
        if token:
            return AcquiredAccount(
                account=account,
                lease=GenerationLease(account.id, token),
            )
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
    Enforces DB-level and Redis pre-registration for strict idempotency.
    """
    t_start = time.perf_counter()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=PAYMENT_TTL_MINUTES)
    scenario = scenario_registry.get(payment_in.scenario_id)

    # 0. Check idempotency if idempotency_key is provided
    # Ensure idempotency key is always present so all payments enjoy
    # DB pre-registration, transaction isolation, and reconciliation safety
    if not payment_in.idempotency_key:
        payment_in.idempotency_key = f"auto_{uuid6.uuid7().hex}"

    payment_id: UUID = uuid6.uuid7()
    payment: Payment | None = None
    lock_key: str | None = None
    idem_owner_token: str | None = None
    payload_hash: str | None = None

    payload_hash = compute_payment_request_hash(payment_in)
    redis = get_redis_client()
    idem_owner_token = str(uuid6.uuid7())
    lock_key = (
        f"lock:idempotency:{payment_in.client_user_id}:{payment_in.idempotency_key}"
    )

    # 0a. Check DB first
    stmt = select(Payment).where(
        Payment.client_user_id == payment_in.client_user_id,
        Payment.idempotency_key == payment_in.idempotency_key,
    )
    existing_idem = (await session.exec(stmt)).first()
    if existing_idem:
        stored_hash = existing_idem.meta.get("request_hash")
        is_same = (
            stored_hash == payload_hash
            if stored_hash
            else (
                existing_idem.scenario_id == payment_in.scenario_id
                and Decimal(str(existing_idem.amount)).quantize(Decimal("0.01"))
                == Decimal(str(payment_in.amount)).quantize(Decimal("0.01"))
                and existing_idem.currency.upper() == payment_in.currency.upper()
            )
        )
        if not is_same:
            raise AppException(
                message=(
                    "Idempotency-Key already used with different payment parameters."
                ),
                code="IDEMPOTENCY_CONFLICT",
                status_code=409,
            )
        if existing_idem.status == PaymentStatus.GENERATING:
            # Check if distributed lock is still active or timed out
            lock_active = await redis.exists(lock_key)
            created_dt = existing_idem.created_at
            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=UTC)
            age_sec = (datetime.now(UTC) - created_dt).total_seconds()
            if not lock_active or age_sec > 120:
                async with core_db.async_session_maker() as rec_session:
                    rec_payment = await get_payment_for_update(
                        rec_session, existing_idem.id
                    )
                    if rec_payment and rec_payment.status == PaymentStatus.GENERATING:
                        rec_payment.status = PaymentStatus.RECONCILIATION_REQUIRED
                        rec_payment.meta = {
                            **rec_payment.meta,
                            "recovery_reason": "stuck_generating_timeout",
                            "recovered_at": datetime.now(UTC).isoformat(),
                        }
                        rec_session.add(rec_payment)
                        await rec_session.commit()
                raise AppException(
                    message=(
                        "Payment generation timed out and requires manual"
                        " reconciliation."
                    ),
                    code="RECONCILIATION_REQUIRED",
                    status_code=409,
                )
            raise AppException(
                message=("A request with this idempotency key is already in progress."),
                code="CONCURRENT_IDEMPOTENT_REQUEST",
                status_code=409,
            )
        if existing_idem.status == PaymentStatus.RECONCILIATION_REQUIRED:
            raise AppException(
                message=(
                    "Payment with this idempotency key requires manual reconciliation."
                ),
                code="RECONCILIATION_REQUIRED",
                status_code=409,
            )

        logger.info(
            "payment_returned_by_idempotency_key",
            payment_id=str(existing_idem.id),
            idempotency_key=payment_in.idempotency_key,
        )
        return existing_idem

    # 0b. Acquire distributed lease lock in Redis
    acquired_lock = await redis.set(
        lock_key,
        idem_owner_token,
        nx=True,
        ex=120,
    )
    if not acquired_lock:
        for _ in range(20):
            await asyncio.sleep(0.5)
            existing_idem = (await session.exec(stmt)).first()
            if existing_idem:
                stored_hash = existing_idem.meta.get("request_hash")
                if stored_hash and stored_hash != payload_hash:
                    raise AppException(
                        message=(
                            "Idempotency-Key already used with "
                            "different payment parameters."
                        ),
                        code="IDEMPOTENCY_CONFLICT",
                        status_code=409,
                    )
                if existing_idem.status in (
                    PaymentStatus.PENDING,
                    PaymentStatus.PAID,
                    PaymentStatus.CANCELLED,
                    PaymentStatus.EXPIRED,
                    PaymentStatus.FAILED,
                ):
                    return existing_idem
                if existing_idem.status == PaymentStatus.RECONCILIATION_REQUIRED:
                    raise AppException(
                        message=(
                            "Payment with this idempotency key "
                            "requires manual reconciliation."
                        ),
                        code="RECONCILIATION_REQUIRED",
                        status_code=409,
                    )

        raise AppException(
            message="A request with this idempotency key is already in progress.",
            code="CONCURRENT_IDEMPOTENT_REQUEST",
            status_code=409,
        )

    # 0c. DB-level pre-registration before external action in a dedicated transaction
    async with core_db.async_session_maker() as reg_session:
        try:
            reg_payment = Payment(
                id=payment_id,
                client_user_id=payment_in.client_user_id,
                scenario_id=payment_in.scenario_id,
                amount=payment_in.amount,
                currency=payment_in.currency,
                idempotency_key=payment_in.idempotency_key,
                callback_url=payment_in.callback_url,
                status=PaymentStatus.GENERATING,
                expires_at=expires_at,
                meta={
                    **payment_in.meta,
                    "request_hash": payload_hash,
                    "execution_stage": "registered",
                },
            )
            reg_session.add(reg_payment)
            await reg_session.commit()
        except IntegrityError as err:
            await reg_session.rollback()
            existing_idem = (await session.exec(stmt)).first()
            if existing_idem:
                stored_hash = existing_idem.meta.get("request_hash")
                if stored_hash and stored_hash != payload_hash:
                    raise AppException(
                        message=(
                            "Idempotency-Key already used with "
                            "different payment parameters."
                        ),
                        code="IDEMPOTENCY_CONFLICT",
                        status_code=409,
                    ) from err
                if existing_idem.status == PaymentStatus.GENERATING:
                    raise AppException(
                        message=(
                            "A request with this idempotency key "
                            "is already in progress."
                        ),
                        code="CONCURRENT_IDEMPOTENT_REQUEST",
                        status_code=409,
                    ) from err
                if existing_idem.status == PaymentStatus.RECONCILIATION_REQUIRED:
                    raise AppException(
                        message=(
                            "Payment with this idempotency key "
                            "requires manual reconciliation."
                        ),
                        code="RECONCILIATION_REQUIRED",
                        status_code=409,
                    ) from err
                return existing_idem
            raise

    acquired_account: AcquiredAccount | None = None
    assigned_account_id: UUID | None = None
    assigned_lease_token: str | None = None
    slot_lock_token: str | None = None
    generation_lock_released = False
    external_call_started = False
    current_stage = "pre_registered"
    result = None

    try:
        # 1. Check if user already has an active pending payment
        account_lookup_start = time.perf_counter()
        existing_payment = await get_active_payment_for_user(
            session, payment_in.client_user_id, now
        )

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
                ) and await is_scenario_pending_locked(
                    scenario.scenario_id, candidate.id
                ):
                    candidate_available = False
                if candidate_available:
                    gen_token = await acquire_account_generation_lock(candidate.id)
                    if gen_token:
                        acquired_account = AcquiredAccount(
                            account=candidate,
                            lease=GenerationLease(candidate.id, gen_token),
                        )

        # If no existing active payment or candidate was busy, acquire a free one
        if not acquired_account:
            acquired_account = await acquire_free_account(
                session, scenario_id=scenario.scenario_id
            )

        account_lookup_duration = round(time.perf_counter() - account_lookup_start, 2)

        if not acquired_account:
            raise NoAccountsAvailableException()

        account = acquired_account.account
        lease = acquired_account.lease
        assigned_account_id = account.id
        assigned_lease_token = lease.owner_token

        # If scenario requires exclusive pending slot, acquire it now
        if getattr(scenario, "requires_exclusive_pending_slot", False):
            slot_lock_token = await acquire_scenario_pending_lock(
                scenario.scenario_id, account.id
            )
            if not slot_lock_token:
                raise NoAccountsAvailableException(
                    "All account slots for this provider are busy."
                )

        # 1c. Record account assignment and context in DB before external call
        current_stage = "account_assigned"
        async with core_db.async_session_maker() as assign_session:
            assign_payment = await get_payment_for_update(assign_session, payment_id)
            assign_payment.account_id = account.id
            assign_payment.meta = {
                **assign_payment.meta,
                "execution_stage": "account_assigned",
                "assigned_account_id": str(account.id),
                "slot_lock_token": slot_lock_token,
            }
            assign_session.add(assign_payment)
            await assign_session.commit()

        # 2. Execute scenario to generate payment link with active lease renewal
        current_stage = "external_call_running"
        ctx = ScenarioContext(
            client_user_id=payment_in.client_user_id,
            amount=payment_in.amount,
            currency=payment_in.currency,
            account=account,
            meta=dict(payment_in.meta),
        )
        async with AccountLeaseRenewer(account.id, lease.owner_token):
            external_call_started = True
            result = await scenario.create_payment(ctx)

        current_stage = "external_call_completed"
        # Release generation lock immediately after Telegram dialog finishes
        # (before external SBP resolution)
        await release_account_generation_lock(account.id, owner_token=lease.owner_token)
        generation_lock_released = True

        # 3. Attempt extraction of direct SBP (NSPK) link if supported gateway link
        current_stage = "sbp_resolving"
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
                    "description": "Попытка извлечения СБП (оригинал)",
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
            "slot_lock_token": slot_lock_token,
        }
        if payload_hash:
            merged_meta["request_hash"] = payload_hash

        # 4. Finalize payment record in a dedicated committed transaction
        current_stage = "finalizing"
        async with core_db.async_session_maker() as fin_session:
            fin_payment = await get_payment_for_update(fin_session, payment_id)
            fin_payment.account_id = account.id
            fin_payment.status = PaymentStatus.PENDING
            fin_payment.payment_link = resolved_link
            fin_payment.meta = merged_meta
            fin_session.add(fin_payment)

            acc_stmt = select(TelegramAccount).where(TelegramAccount.id == account.id)
            fin_acc = (await fin_session.exec(acc_stmt)).first()
            if fin_acc:
                fin_acc.updated_at = now
                fin_session.add(fin_acc)

            await fin_session.commit()
            await fin_session.refresh(fin_payment)
            payment = fin_payment

        # 5. Save completed idempotency cache in Redis (best-effort)
        cache_key = (
            f"idempotency:payment:{payment_in.client_user_id}:"
            f"{payment_in.idempotency_key}"
        )
        with contextlib.suppress(Exception):
            await redis.set(
                cache_key,
                json.dumps(
                    {
                        "status": "completed",
                        "payment_id": str(payment.id),
                        "payload_hash": payload_hash,
                    }
                ),
                ex=86400,
            )

        # 6. Trigger non-blocking background re-preparation for subsequent use
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

        # Attach payment to caller's session for identity-map consistency
        caller_payment = await session.get(Payment, payment_id)
        return caller_payment or payment

    except (Exception, asyncio.CancelledError) as exc:
        with contextlib.suppress(Exception):
            await session.rollback()

        stars_tok = (
            (result.meta if result else ctx.meta).get("stars_reservation_token")
            if "ctx" in locals()
            else None
        )
        stars_cnt = (
            (result.meta if result else ctx.meta).get("stars_count")
            if "ctx" in locals()
            else None
        )
        order_id = (
            (result.meta if result else ctx.meta).get("order_id")
            or (result.meta if result else ctx.meta).get("invoice_id")
            if "ctx" in locals()
            else None
        )

        async with core_db.async_session_maker() as err_session:
            try:
                err_payment = await get_payment_for_update(err_session, payment_id)
                is_reconciliation = external_call_started or isinstance(
                    exc, (LeaseLostException, TimeoutError, asyncio.CancelledError)
                )
                if is_reconciliation:
                    err_payment.status = PaymentStatus.RECONCILIATION_REQUIRED
                else:
                    err_payment.status = PaymentStatus.FAILED

                if assigned_account_id:
                    err_payment.account_id = assigned_account_id

                if result and result.payment_link:
                    err_payment.payment_link = result.payment_link

                err_payment.meta = {
                    **err_payment.meta,
                    **(result.meta if result else {}),
                    **(ctx.meta if "ctx" in locals() else {}),
                    "execution_stage": current_stage,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "failed_at": datetime.now(UTC).isoformat(),
                    "slot_lock_token": slot_lock_token,
                    "stars_reservation_token": stars_tok,
                    "stars_count": stars_cnt,
                    "order_id": order_id,
                }
                err_session.add(err_payment)
                await err_session.commit()
            except Exception as db_err:
                await err_session.rollback()
                logger.error(
                    "failed_committing_payment_error_status",
                    error=str(db_err),
                )

        if slot_lock_token and assigned_account_id:
            with contextlib.suppress(Exception):
                await release_scenario_pending_lock(
                    scenario.scenario_id,
                    assigned_account_id,
                    owner_token=slot_lock_token,
                )
        if stars_tok and stars_cnt:
            with contextlib.suppress(Exception):
                await release_scenario_stars_reservation(
                    scenario.scenario_id, int(stars_cnt), owner_token=str(stars_tok)
                )
        raise

    finally:
        if (
            not generation_lock_released
            and assigned_account_id
            and assigned_lease_token
        ):
            with contextlib.suppress(Exception):
                await release_account_generation_lock(
                    assigned_account_id,
                    owner_token=assigned_lease_token,
                )
        if lock_key and idem_owner_token:
            from app.modules.payments.scenarios.state import _eval_release_lock

            try:
                await _eval_release_lock(redis, lock_key, idem_owner_token)
            except Exception as lock_err:
                logger.warning(
                    "failed_releasing_idempotency_lock",
                    lock_key=lock_key,
                    error=str(lock_err),
                )


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
    "slot_lock_token",
    "stars_reservation_token",
    "request_hash",
}


async def get_payment_for_update(
    session: AsyncSession,
    payment_id: UUID,
) -> Payment:
    """
    Load payment under an exclusive row-level lock (FOR UPDATE), ensuring
    the in-memory entity reflects latest database state via populate_existing.
    Raises NotFoundException if row does not exist.
    """
    stmt = (
        select(Payment)
        .where(Payment.id == payment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    payment = (await session.exec(stmt)).one_or_none()
    if payment is None:
        raise NotFoundException("Payment not found.")
    return payment


ALLOWED_CALLBACK_TRANSITIONS: dict[PaymentStatus, set[PaymentStatus]] = {
    PaymentStatus.PENDING: {
        PaymentStatus.PAID,
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
        PaymentStatus.FAILED,
    },
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
    Uses row-level locking to prevent concurrent state transitions.
    """
    locked_payment = await get_payment_for_update(session, db_payment.id)

    now = datetime.now(UTC)

    # 1. State machine transition check
    if locked_payment.status == callback.status:
        # Idempotent re-delivery
        return locked_payment

    allowed = ALLOWED_CALLBACK_TRANSITIONS.get(locked_payment.status, set())
    if callback.status not in allowed:
        if locked_payment.status == PaymentStatus.PAID:
            raise AppException(
                message="Платёж уже оплачен и не может изменить статус.",
                code="PAYMENT_ALREADY_PAID",
                status_code=409,
            )
        curr_val = getattr(locked_payment.status, "value", locked_payment.status)
        target_val = getattr(callback.status, "value", callback.status)
        if locked_payment.status in (
            PaymentStatus.CANCELLED,
            PaymentStatus.EXPIRED,
            PaymentStatus.FAILED,
        ):
            raise AppException(
                message=f"Платёж уже закрыт со статусом {curr_val}.",
                code="PAYMENT_ALREADY_CLOSED",
                status_code=409,
            )
        raise AppException(
            message=(
                f"Недопустимый переход статуса платежа из {curr_val} в {target_val}."
            ),
            code="INVALID_PAYMENT_TRANSITION",
            status_code=409,
        )

    # Valid transition from PENDING
    locked_payment.status = callback.status

    if callback.status == PaymentStatus.PAID:
        locked_payment.paid_at = now
    elif callback.status == PaymentStatus.CANCELLED:
        locked_payment.cancelled_at = now

    # Read original tokens BEFORE merging any callback metadata
    stars_count = locked_payment.meta.get("stars_count")
    stars_token = locked_payment.meta.get("stars_reservation_token")
    slot_token = locked_payment.meta.get("slot_lock_token")

    if callback.external_transaction_id or callback.meta:
        updated_meta = dict(locked_payment.meta)
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
        locked_payment.meta = updated_meta

    # Release any reserved stars count for the scenario (on terminal states)
    if callback.status in (
        PaymentStatus.PAID,
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
        PaymentStatus.FAILED,
    ):
        if stars_count and locked_payment.scenario_id and stars_token:
            await release_scenario_stars_reservation(
                locked_payment.scenario_id,
                int(stars_count),
                owner_token=str(stars_token),
            )

        # Release any pending scenario slot lock (e.g. starslly_bot)
        if locked_payment.scenario_id and locked_payment.account_id and slot_token:
            await release_scenario_pending_lock(
                locked_payment.scenario_id,
                locked_payment.account_id,
                owner_token=str(slot_token),
            )

    session.add(locked_payment)
    await session.flush()
    await session.refresh(locked_payment)

    if locked_payment.callback_url and callback.status in (
        PaymentStatus.PAID,
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
        PaymentStatus.FAILED,
    ):
        with contextlib.suppress(Exception):
            from app.modules.payments.tasks import dispatch_payment_webhook

            await dispatch_payment_webhook(locked_payment.id)

    return locked_payment


async def cancel_payment(
    session: AsyncSession,
    db_payment: Payment,
) -> Payment:
    """
    Cancel a pending payment and immediately unlock its assigned account.
    If payment is already PAID, raises 409 conflict.
    If already CANCELLED, EXPIRED, or FAILED, returns idempotently.
    Uses row-level locking with populate_existing to prevent concurrent
    state transitions.
    """
    locked_payment = await get_payment_for_update(session, db_payment.id)

    if locked_payment.status == PaymentStatus.PAID:
        raise AppException(
            message="Нельзя отменить уже оплаченный платёж.",
            code="PAYMENT_ALREADY_PAID",
            status_code=409,
        )

    if locked_payment.status in (
        PaymentStatus.CANCELLED,
        PaymentStatus.EXPIRED,
        PaymentStatus.FAILED,
    ):
        return locked_payment

    if locked_payment.status != PaymentStatus.PENDING:
        curr_val = getattr(locked_payment.status, "value", locked_payment.status)
        raise AppException(
            message=f"Нельзя отменить платёж со статусом {curr_val}.",
            code="INVALID_PAYMENT_TRANSITION",
            status_code=409,
        )

    now = datetime.now(UTC)
    locked_payment.status = PaymentStatus.CANCELLED
    locked_payment.cancelled_at = now

    stars_count = locked_payment.meta.get("stars_count")
    stars_token = locked_payment.meta.get("stars_reservation_token")
    if stars_count and locked_payment.scenario_id and stars_token:
        await release_scenario_stars_reservation(
            locked_payment.scenario_id,
            int(stars_count),
            owner_token=str(stars_token),
        )

    slot_token = locked_payment.meta.get("slot_lock_token")
    if locked_payment.scenario_id and locked_payment.account_id and slot_token:
        await release_scenario_pending_lock(
            locked_payment.scenario_id,
            locked_payment.account_id,
            owner_token=str(slot_token),
        )

    session.add(locked_payment)
    await session.flush()
    await session.refresh(locked_payment)

    if locked_payment.callback_url:
        with contextlib.suppress(Exception):
            from app.modules.payments.tasks import dispatch_payment_webhook

            await dispatch_payment_webhook(locked_payment.id)

    return locked_payment


async def expire_overdue_payments(session: AsyncSession) -> int:
    """
    Expire all payments that have passed their 30m TTL.
    Returns the count of expired payments.
    """
    now = datetime.now(UTC)
    statement = (
        select(Payment)
        .where(
            Payment.status == PaymentStatus.PENDING,
            Payment.expires_at <= now,
        )
        .with_for_update()
    )
    result = await session.exec(statement)
    overdue = result.all()

    for payment in overdue:
        payment.status = PaymentStatus.EXPIRED
        stars_count = payment.meta.get("stars_count")
        stars_token = payment.meta.get("stars_reservation_token")
        if stars_count and payment.scenario_id and stars_token:
            await release_scenario_stars_reservation(
                payment.scenario_id,
                int(stars_count),
                owner_token=str(stars_token),
            )
        slot_token = payment.meta.get("slot_lock_token")
        if payment.scenario_id and payment.account_id and slot_token:
            await release_scenario_pending_lock(
                payment.scenario_id,
                payment.account_id,
                owner_token=str(slot_token),
            )
        session.add(payment)
        if payment.callback_url:
            with contextlib.suppress(Exception):
                from app.modules.payments.tasks import dispatch_payment_webhook

                await dispatch_payment_webhook(payment.id)

    if overdue:
        await session.flush()

    return len(overdue)


async def release_all_locked_accounts(session: AsyncSession) -> tuple[int, int]:
    """
    Cancel all active PENDING payments to immediately free all reserved
    Telegram accounts using unified cancel_payment under row locks.
    Returns (cancelled_payments_count, released_accounts_count).
    """
    now = datetime.now(UTC)
    statement = (
        select(Payment)
        .where(
            Payment.status == PaymentStatus.PENDING,
            Payment.expires_at > now,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    result = await session.exec(statement)
    active_pending = list(result.all())

    released_account_ids: set[UUID] = set()
    cancelled_count = 0
    for payment in active_pending:
        cancelled = await cancel_payment(session, payment)
        if cancelled.status == PaymentStatus.CANCELLED:
            cancelled_count += 1
            if cancelled.account_id:
                released_account_ids.add(cancelled.account_id)

    logger.info(
        "all_locked_accounts_released",
        cancelled_payments=cancelled_count,
        released_accounts=len(released_account_ids),
    )
    return cancelled_count, len(released_account_ids)


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

        async with AccountLeaseRenewer(account.id, lock_token):
            for scenario in scenario_registry.list():
                if getattr(
                    scenario, "requires_exclusive_pending_slot", False
                ) and await is_scenario_pending_locked(
                    scenario.scenario_id, account.id
                ):
                    results[scenario.scenario_id] = "skipped"
                    continue
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

    if getattr(
        scenario, "requires_exclusive_pending_slot", False
    ) and await is_scenario_pending_locked(scenario.scenario_id, account.id):
        return {
            "status": "skipped",
            "account_id": str(account.id),
            "scenario_id": scenario_id,
            "reason": "Account pending slot is currently locked",
        }

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

    # Re-check pending slot AFTER acquiring generation lock to eliminate race window
    if getattr(
        scenario, "requires_exclusive_pending_slot", False
    ) and await is_scenario_pending_locked(scenario.scenario_id, account.id):
        await release_account_generation_lock(account.id, owner_token=lock_token)
        return {
            "status": "skipped",
            "account_id": str(account.id),
            "scenario_id": scenario_id,
            "reason": "Account pending slot is currently locked",
        }

    try:
        async with AccountLeaseRenewer(account.id, lock_token):
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
                    res = await prepare_single_scenario(
                        session=session,
                        account_id=acc.id,
                        scenario_id=scen.scenario_id,
                    )
                    if res.get("status") == "completed":
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
) -> list[AcquiredAccount]:
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

    acquired: list[AcquiredAccount] = []
    for acc in active_accounts:
        token = await acquire_account_generation_lock(acc.id)
        if token:
            acquired.append(
                AcquiredAccount(
                    account=acc,
                    lease=GenerationLease(acc.id, token),
                )
            )
            if len(acquired) == n:
                break
    return acquired


async def _run_race_scenario_task(
    acquired: AcquiredAccount,
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

    account = acquired.account
    lease = acquired.lease
    slot_lock_token: str | None = None
    generation_lock_released = False
    result: ScenarioResult | None = None
    child_payment_id: UUID = uuid6.uuid7()
    external_call_started = False
    current_stage = "pre_registered"

    # Pre-register child payment row in DB
    async with async_session_maker() as reg_session:
        try:
            reg_payment = Payment(
                id=child_payment_id,
                client_user_id=race_in.client_user_id,
                scenario_id=scenario_id,
                amount=race_in.amount,
                currency=race_in.currency,
                account_id=account.id,
                batch_id=batch_id,
                callback_url=race_in.callback_url,
                status=PaymentStatus.GENERATING,
                expires_at=expires_at,
                meta={
                    **race_in.meta,
                    "batch_id": str(batch_id),
                    "execution_stage": "generating",
                    "slot_lock_token": slot_lock_token,
                },
            )
            reg_session.add(reg_payment)
            await reg_session.commit()
        except Exception as reg_err:
            logger.warning(
                "failed_pre_registering_race_child_payment", error=str(reg_err)
            )

    try:
        scenario = _registry.get(scenario_id)
        if not scenario:
            raise ValueError(f"Scenario {scenario_id!r} not found in registry")

        if getattr(scenario, "requires_exclusive_pending_slot", False):
            slot_lock_token = await acquire_scenario_pending_lock(
                scenario.scenario_id, account.id
            )
            if not slot_lock_token:
                raise ValueError(f"Account slot for {scenario_id} is already occupied")

        ctx = ScenarioContext(
            client_user_id=race_in.client_user_id,
            amount=race_in.amount,
            currency=race_in.currency,
            account=account,
            meta=dict(race_in.meta),
        )

        current_stage = "external_call_running"
        async with AccountLeaseRenewer(account.id, lease.owner_token):
            external_call_started = True
            result = await scenario.create_payment(ctx)

        current_stage = "external_call_completed"
        # Release generation lock immediately after Telegram dialog finishes
        # (before external SBP resolution)
        await release_account_generation_lock(account.id, owner_token=lease.owner_token)
        generation_lock_released = True

        # SBP resolution
        current_stage = "sbp_resolving"
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
            "slot_lock_token": slot_lock_token,
        }

        # Persist payment record (own session + own commit — SSE lifecycle exception)
        current_stage = "finalizing"
        payment: Payment
        async with async_session_maker() as task_session:
            try:
                payment = await get_payment_for_update(task_session, child_payment_id)
                payment.status = PaymentStatus.PENDING
                payment.payment_link = resolved_link
                payment.meta = merged_meta
                task_session.add(payment)
                await task_session.commit()
                await task_session.refresh(payment)
            except Exception:
                await task_session.rollback()
                raise
    except (asyncio.CancelledError, Exception) as exc:
        stars_tok = (
            (result.meta if result else ctx.meta).get("stars_reservation_token")
            if "ctx" in locals()
            else None
        )
        stars_cnt = (
            (result.meta if result else ctx.meta).get("stars_count")
            if "ctx" in locals()
            else None
        )
        order_id = (
            (result.meta if result else ctx.meta).get("order_id")
            or (result.meta if result else ctx.meta).get("invoice_id")
            if "ctx" in locals()
            else None
        )

        async with async_session_maker() as err_session:
            with contextlib.suppress(Exception):
                err_payment = await get_payment_for_update(
                    err_session, child_payment_id
                )
                is_reconciliation = external_call_started or isinstance(
                    exc,
                    (LeaseLostException, TimeoutError, asyncio.CancelledError),
                )
                if is_reconciliation:
                    err_payment.status = PaymentStatus.RECONCILIATION_REQUIRED
                else:
                    err_payment.status = PaymentStatus.FAILED

                if result and result.payment_link:
                    err_payment.payment_link = result.payment_link

                err_payment.meta = {
                    **err_payment.meta,
                    **(result.meta if result else {}),
                    **(ctx.meta if "ctx" in locals() else {}),
                    "execution_stage": current_stage,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "failed_at": datetime.now(UTC).isoformat(),
                    "slot_lock_token": slot_lock_token,
                    "stars_reservation_token": stars_tok,
                    "stars_count": stars_cnt,
                    "order_id": order_id,
                }
                err_session.add(err_payment)
                await err_session.commit()

        if slot_lock_token:
            with contextlib.suppress(Exception):
                await release_scenario_pending_lock(
                    scenario_id, account.id, owner_token=slot_lock_token
                )
        if stars_tok and stars_cnt:
            with contextlib.suppress(Exception):
                await release_scenario_stars_reservation(
                    scenario_id, int(stars_cnt), owner_token=str(stars_tok)
                )
        raise
    finally:
        # Guarantee generation lock is released once link is obtained or upon error
        if not generation_lock_released:
            with contextlib.suppress(Exception):
                await release_account_generation_lock(
                    account.id, owner_token=lease.owner_token
                )

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


async def _get_race_idempotency_response(
    redis: Redis,
    race_record_key: str,
    payload_hash: str,
    default_scenario_ids: list[str] | None,
) -> PaymentRaceFireResponse | None:
    """Helper to read and validate race idempotency record from Redis."""
    raw_record = await redis.get(race_record_key)
    if not raw_record:
        return None
    try:
        record = json.loads(raw_record)
        if record.get("payload_hash") and record["payload_hash"] != payload_hash:
            raise AppException(
                message="Idempotency-Key already used with different race parameters.",
                code="IDEMPOTENCY_CONFLICT",
                status_code=409,
            )
        batch_uuid = UUID(record["batch_id"])
        real_status = await race_buffer.get_race_status(batch_uuid)
        status_str = (
            real_status if real_status is not None else record.get("status", "running")
        )
        return PaymentRaceFireResponse(
            batch_id=batch_uuid,
            status=status_str,
            scenarios=record.get("scenarios", default_scenario_ids or []),
        )
    except (json.JSONDecodeError, KeyError, ValueError) as err:
        logger.warning("invalid_race_idempotency_record", error=str(err))
        return None


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

    # 0. Check idempotency for race if idempotency_key is provided
    payload_hash: str | None = None
    race_lock_key: str | None = None
    race_record_key: str | None = None
    race_lock_token: str | None = None
    redis = get_redis_client()

    if race_in.idempotency_key:
        payload_hash = compute_payment_request_hash(race_in)
        race_lock_key = f"lock:race:{race_in.client_user_id}:{race_in.idempotency_key}"
        race_record_key = (
            f"idempotency:race:{race_in.client_user_id}:{race_in.idempotency_key}"
        )
        race_lock_token = str(uuid6.uuid7())

        # Check DB first for persisted batch (survives Redis cache expiration)
        stmt = select(Payment).where(
            Payment.client_user_id == race_in.client_user_id,
            Payment.idempotency_key == race_in.idempotency_key,
        )
        existing_batch = (await session.exec(stmt)).first()
        if existing_batch:
            stored_hash = existing_batch.meta.get("request_hash")
            if stored_hash and stored_hash != payload_hash:
                raise AppException(
                    message=(
                        "Idempotency-Key already used with different race parameters."
                    ),
                    code="IDEMPOTENCY_CONFLICT",
                    status_code=409,
                )
            batch_uuid = existing_batch.batch_id or existing_batch.id
            real_status = await race_buffer.get_race_status(batch_uuid)
            status_str = (
                real_status
                if real_status is not None
                else existing_batch.meta.get("status", "running")
            )
            persisted_scenarios = (
                existing_batch.meta.get("scenarios") or race_in.scenario_ids or []
            )
            return PaymentRaceFireResponse(
                batch_id=batch_uuid,
                status=status_str,
                scenarios=persisted_scenarios,
            )

        # 0a. Check existing idempotency record before acquiring lock
        existing_resp = await _get_race_idempotency_response(
            redis, race_record_key, payload_hash, race_in.scenario_ids
        )
        if existing_resp:
            return existing_resp

        # 0b. Acquire distributed lock for race creation
        acquired = await redis.set(race_lock_key, race_lock_token, nx=True, ex=120)
        if not acquired:
            for _ in range(20):
                await asyncio.sleep(0.5)
                existing_resp = await _get_race_idempotency_response(
                    redis, race_record_key, payload_hash, race_in.scenario_ids
                )
                if existing_resp:
                    return existing_resp
            raise AppException(
                message="A race with this idempotency key is already in progress.",
                code="CONCURRENT_IDEMPOTENT_REQUEST",
                status_code=409,
            )

    pairs: list[tuple[AcquiredAccount, str]] = []
    try:
        # 0c. Double-check idempotency record under acquired lock
        if race_record_key and payload_hash:
            existing_resp = await _get_race_idempotency_response(
                redis, race_record_key, payload_hash, race_in.scenario_ids
            )
            if existing_resp:
                return existing_resp

        # Resolve scenarios
        candidate_scenarios: list[BasePaymentScenario] = []
        if race_in.scenario_ids:
            for sid in race_in.scenario_ids:
                with contextlib.suppress(UnknownScenarioException):
                    candidate_scenarios.append(scenario_registry.get(sid))
        else:
            candidate_scenarios = scenario_registry.list_primary()

        num_scenarios = len(candidate_scenarios)
        if num_scenarios == 0:
            raise NoAccountsAvailableException("No valid scenarios to race")

        # Acquire free accounts (one per scenario)
        free_accounts = await acquire_N_free_accounts(session, n=num_scenarios)

        if not free_accounts:
            raise NoAccountsAvailableException()

        # Pair accounts with scenarios (exclusive slots first)
        remaining_accounts = list(free_accounts)

        exclusive_scenarios = [
            s
            for s in candidate_scenarios
            if getattr(s, "requires_exclusive_pending_slot", False)
        ]
        non_exclusive_scenarios = [
            s
            for s in candidate_scenarios
            if not getattr(s, "requires_exclusive_pending_slot", False)
        ]

        for s in exclusive_scenarios:
            assigned_acc = None
            for acc in remaining_accounts:
                if not await is_scenario_pending_locked(s.scenario_id, acc.account.id):
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
            await release_account_generation_lock(
                unused_acc.account.id,
                owner_token=unused_acc.lease.owner_token,
            )

        if not pairs:
            raise NoAccountsAvailableException()

        scenario_ids = [sid for _, sid in pairs]

        # Pre-register batch in DB for persistent idempotency
        if race_in.idempotency_key:
            async with core_db.async_session_maker() as reg_session:
                try:
                    reg_batch = Payment(
                        id=batch_id,
                        client_user_id=race_in.client_user_id,
                        scenario_id="race",
                        amount=race_in.amount,
                        currency=race_in.currency,
                        idempotency_key=race_in.idempotency_key,
                        batch_id=batch_id,
                        status=PaymentStatus.PENDING,
                        expires_at=expires_at,
                        meta={
                            **race_in.meta,
                            "request_hash": payload_hash,
                            "scenarios": scenario_ids,
                            "is_race_batch": True,
                        },
                    )
                    reg_session.add(reg_batch)
                    await reg_session.commit()
                except IntegrityError:
                    await reg_session.rollback()
                    # Concurrent duplicate committed batch
                    existing_batch = (await session.exec(stmt)).first()
                    if existing_batch:
                        return PaymentRaceFireResponse(
                            batch_id=existing_batch.batch_id or existing_batch.id,
                            status="running",
                            scenarios=existing_batch.meta.get(
                                "scenarios", scenario_ids
                            ),
                        )

        # Initialize Redis event buffer (pushes "started" event)
        await race_buffer.init_race_buffer(batch_id, scenario_ids)

        if race_record_key:
            await redis.set(
                race_record_key,
                json.dumps(
                    {
                        "batch_id": str(batch_id),
                        "payload_hash": payload_hash,
                        "scenarios": scenario_ids,
                        "status": "running",
                    }
                ),
                ex=86400,  # 24 hours retention, matching payment idempotency retention
            )

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
            accounts=[str(a.account.id) for a, _ in pairs],
        )

        return PaymentRaceFireResponse(
            batch_id=batch_id,
            status="running",
            scenarios=scenario_ids,
        )
    except Exception:
        # Guarantee release of account generation locks if anything fails in setup
        for acc, _ in pairs:
            with contextlib.suppress(Exception):
                await release_account_generation_lock(
                    acc.account.id,
                    owner_token=acc.lease.owner_token,
                )
        raise
    finally:
        if race_lock_key and race_lock_token:
            from app.modules.payments.scenarios.state import _eval_release_lock

            with contextlib.suppress(Exception):
                await _eval_release_lock(redis, race_lock_key, race_lock_token)


async def _race_background_runner(
    pairs: list[tuple[AcquiredAccount, str]],
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
                acquired=acquired,
                scenario_id=scenario_id,
                batch_id=batch_id,
                race_in=race_in,
                expires_at=expires_at,
                race_start=race_start,
            )
        )
        for acquired, scenario_id in pairs
    ]

    succeeded = 0
    failed = 0
    terminal_status = "done"

    async def _cleanup(
        final_status: str, count_succeeded: int, count_failed: int
    ) -> None:
        # Cancel all pending tasks and wait for them to finish cleanly
        pending = [t for t in tasks if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        for acquired, _ in pairs:
            await release_account_generation_lock(
                acquired.account.id,
                owner_token=acquired.lease.owner_token,
            )

        total_duration = round(time.perf_counter() - race_start, 2)
        try:
            await race_buffer.finish_race(
                batch_id,
                status=final_status,
                total=len(pairs),
                succeeded=count_succeeded,
                failed=count_failed,
                duration_sec=total_duration,
            )
        except Exception as exc:
            logger.error(
                "race_finish_race_failed",
                batch_id=str(batch_id),
                error=str(exc),
            )

    try:
        async with asyncio.timeout(race_in.timeout_sec):
            for fut in asyncio.as_completed(tasks):
                sse_event: str | None = None
                try:
                    sse_event = await fut
                    succeeded += 1
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
                    with contextlib.suppress(Exception):
                        await race_buffer.push_race_event(batch_id, err_event)
                    continue

                if sse_event:
                    try:
                        await race_buffer.push_race_event(batch_id, sse_event)
                    except Exception as exc:
                        logger.error(
                            "race_push_event_failed",
                            batch_id=str(batch_id),
                            error=str(exc),
                        )
    except TimeoutError:
        terminal_status = "timeout"
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
        with contextlib.suppress(Exception):
            await race_buffer.push_race_event(batch_id, timeout_event)
    except asyncio.CancelledError:
        terminal_status = "cancelled"
        raise
    except Exception as exc:
        terminal_status = "failed"
        logger.error(
            "race_background_runner_error",
            batch_id=str(batch_id),
            error=str(exc),
        )
        raise
    finally:
        await asyncio.shield(_cleanup(terminal_status, succeeded, failed))


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
