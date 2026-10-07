"""
Taskiq background asynchronous tasks for payments module.
Discovered automatically by worker via --fs-discover.
"""

import asyncio
import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from sqlmodel.ext.asyncio.session import AsyncSession
from taskiq import TaskiqDepends

from app.api.deps import get_db
from app.core.broker import broker
from app.core.config import settings
from app.core.logging import get_logger
from app.modules.payments import service as payment_service
from app.modules.payments.models import Payment
from app.modules.payments.schemas import PaymentWebhookPayload

logger = get_logger(__name__)


@broker.task(task_name="payments:expire_overdue_payments")
async def expire_overdue_payments_task(
    db: AsyncSession = TaskiqDepends(get_db),
) -> dict[str, int]:
    """
    Periodic background job to scan and expire payments that exceeded 30m TTL.
    """
    logger.info("Executing periodic payment expiration task")
    expired_count = await payment_service.expire_overdue_payments(session=db)
    logger.info("Expired overdue payments", count=expired_count)
    return {"expired_count": expired_count}


@broker.task(task_name="payments:prepare_account_scenarios")
async def prepare_account_scenarios_task(
    account_id: UUID,
    db: AsyncSession = TaskiqDepends(get_db),
) -> dict[str, Any]:
    """
    Background worker job to prepare/warm up an account for all
    registered payment scenarios.
    """
    logger.info(
        "Executing account scenarios preparation task",
        account_id=str(account_id),
    )
    result = await payment_service.prepare_account_scenarios(
        session=db,
        account_id=account_id,
    )
    logger.info(
        "Account scenarios preparation finished",
        account_id=str(account_id),
        result=result,
    )
    return result


@broker.task(task_name="payments:prepare_single_scenario")
async def prepare_single_scenario_task(
    account_id: UUID,
    scenario_id: str,
    db: AsyncSession = TaskiqDepends(get_db),
) -> dict[str, Any]:
    """
    Background worker job to prepare/warm up a specific scenario
    for the given Telegram account.
    """
    logger.info(
        "Executing single scenario preparation task",
        account_id=str(account_id),
        scenario_id=scenario_id,
    )
    result = await payment_service.prepare_single_scenario(
        session=db,
        account_id=account_id,
        scenario_id=scenario_id,
    )
    logger.info(
        "Single scenario preparation finished",
        account_id=str(account_id),
        scenario_id=scenario_id,
        result=result,
    )
    return result


@broker.task(task_name="payments:refresh_idle_account_scenarios")
async def refresh_idle_account_scenarios_task(
    db: AsyncSession = TaskiqDepends(get_db),
) -> dict[str, Any]:
    """
    Periodic background job to check active accounts without active pending
    payments and re-prepare any scenarios whose preparation is missing or
    expired in Redis.
    """
    logger.info("Executing refresh idle account scenarios task")
    result = await payment_service.refresh_idle_account_scenarios(session=db)
    logger.info("Refresh idle account scenarios finished", result=result)
    return result


async def dispatch_account_scenarios_warmup(account_id: UUID) -> None:
    """
    Safely enqueue background scenarios preparation for an account.
    Non-blocking, gracefully logs if broker enqueue fails.
    """
    try:
        await prepare_account_scenarios_task.kiq(account_id=account_id)
        logger.info(
            "Enqueued background account warmup task",
            account_id=str(account_id),
        )
    except Exception as exc:
        logger.warning(
            "Failed to enqueue account warmup task",
            account_id=str(account_id),
            error=str(exc),
        )


async def dispatch_single_scenario_warmup(
    account_id: UUID,
    scenario_id: str,
) -> None:
    """
    Safely enqueue background preparation for a single scenario on an account.
    Non-blocking, gracefully logs if broker enqueue fails.
    """
    try:
        await prepare_single_scenario_task.kiq(
            account_id=account_id,
            scenario_id=scenario_id,
        )
        logger.info(
            "Enqueued single scenario warmup task",
            account_id=str(account_id),
            scenario_id=scenario_id,
        )
    except Exception as exc:
        logger.warning(
            "Failed to enqueue single scenario warmup task",
            account_id=str(account_id),
            scenario_id=scenario_id,
            error=str(exc),
        )


@broker.task(task_name="payments:check_pending_bot_notifications")
async def check_pending_bot_notifications_task(
    db: AsyncSession = TaskiqDepends(get_db),
) -> dict[str, int]:
    """
    Fallback background check for pending payments: queries recent messages
    from scenario bots for all accounts that currently have pending payments.
    """
    from app.modules.payments.notifications import (
        check_all_pending_payments_notifications,
    )

    logger.debug("Executing fallback pending bot notifications check")
    confirmed_count = await check_all_pending_payments_notifications(session=db)
    if confirmed_count > 0:
        logger.info(
            "Confirmed pending payments via fallback check",
            count=confirmed_count,
        )
    return {"confirmed_count": confirmed_count}


@broker.task(task_name="payments:dispatch_payment_webhook")
async def dispatch_payment_webhook_task(
    payment_id: UUID,
    db: AsyncSession = TaskiqDepends(get_db),
) -> dict[str, Any]:
    """
    Background worker job to dispatch an HTTP POST callback/webhook to the merchant.
    Applies HMAC-SHA256 signature if PAYMENT_WEBHOOK_SECRET is configured.
    Retries up to PAYMENT_WEBHOOK_MAX_RETRIES times on transient failures.
    Records delivery outcome in payment metadata.
    """
    logger.info("Executing payment webhook dispatch task", payment_id=str(payment_id))
    payment = await db.get(Payment, payment_id)
    if not payment:
        logger.warning(
            "Payment not found for webhook dispatch", payment_id=str(payment_id)
        )
        return {"status": "skipped", "reason": "payment_not_found"}

    if not payment.callback_url:
        logger.debug(
            "Payment has no callback_url, skipping webhook",
            payment_id=str(payment_id),
        )
        return {"status": "skipped", "reason": "no_callback_url"}

    # Exclude internal secrets and reservation tokens from public webhook meta
    safe_meta = {
        k: v
        for k, v in payment.meta.items()
        if k
        not in (
            "slot_lock_token",
            "stars_reservation_token",
            "request_hash",
            "webhook_delivery",
        )
    }

    ext_tx_id = payment.meta.get("external_transaction_id") or payment.meta.get(
        "order_id"
    )
    if ext_tx_id is not None:
        ext_tx_id = str(ext_tx_id)

    payload = PaymentWebhookPayload(
        event="payment.status_changed",
        payment_id=payment.id,
        client_user_id=payment.client_user_id,
        scenario_id=payment.scenario_id,
        amount=payment.amount,
        currency=payment.currency,
        status=payment.status,
        idempotency_key=payment.idempotency_key,
        batch_id=payment.batch_id,
        payment_link=payment.payment_link,
        created_at=payment.created_at,
        paid_at=payment.paid_at,
        cancelled_at=payment.cancelled_at,
        external_transaction_id=ext_tx_id,
        meta=safe_meta,
    )
    body_str = payload.model_dump_json()

    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"Payee-Webhook/{settings.VERSION}",
    }
    if settings.PAYMENT_WEBHOOK_SECRET:
        secret_bytes = settings.PAYMENT_WEBHOOK_SECRET.get_secret_value().encode(
            "utf-8"
        )
        timestamp = str(int(datetime.now(UTC).timestamp()))
        signature = hmac.new(
            secret_bytes,
            f"{timestamp}.{body_str}".encode(),
            hashlib.sha256,
        ).hexdigest()
        headers["X-Payee-Timestamp"] = timestamp
        headers["X-Payee-Signature"] = signature

    max_retries = max(1, settings.PAYMENT_WEBHOOK_MAX_RETRIES)
    timeout = settings.PAYMENT_WEBHOOK_TIMEOUT_SECONDS
    delivered = False
    last_status_code = None
    last_error = None
    attempts = 0

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(1, max_retries + 1):
            attempts = attempt
            try:
                resp = await client.post(
                    payment.callback_url,
                    content=body_str,
                    headers=headers,
                )
                last_status_code = resp.status_code
                if 200 <= resp.status_code < 300:
                    delivered = True
                    break
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except Exception as req_err:
                last_error = str(req_err)

            if attempt < max_retries:
                await asyncio.sleep(0.5 * (2 ** (attempt - 1)))

    delivery_record = {
        "delivered": delivered,
        "status_code": last_status_code,
        "attempts": attempts,
        "last_error": last_error if not delivered else None,
        "timestamp": datetime.now(UTC).isoformat(),
        "target_url": payment.callback_url,
    }
    payment.meta = {
        **payment.meta,
        "webhook_delivery": delivery_record,
    }
    db.add(payment)
    await db.flush()

    if delivered:
        logger.info(
            "Payment webhook delivered successfully",
            payment_id=str(payment_id),
            callback_url=payment.callback_url,
            attempts=attempts,
            status_code=last_status_code,
        )
    else:
        logger.warning(
            "Payment webhook delivery failed",
            payment_id=str(payment_id),
            callback_url=payment.callback_url,
            attempts=attempts,
            error=last_error,
        )

    return {
        "status": "delivered" if delivered else "failed",
        "payment_id": str(payment_id),
        "callback_url": payment.callback_url,
        "attempts": attempts,
        "status_code": last_status_code,
        "error": last_error,
    }


async def dispatch_payment_webhook(payment_id: UUID) -> None:
    """
    Safely enqueue background webhook delivery for a payment.
    Non-blocking, gracefully logs if broker enqueue fails.
    """
    try:
        await dispatch_payment_webhook_task.kiq(payment_id=payment_id)
        logger.info(
            "Enqueued background payment webhook task",
            payment_id=str(payment_id),
        )
    except Exception as exc:
        logger.warning(
            "Failed to enqueue payment webhook task",
            payment_id=str(payment_id),
            error=str(exc),
        )
