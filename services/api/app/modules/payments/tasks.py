"""
Taskiq background asynchronous tasks for payments module.
Discovered automatically by worker via --fs-discover.
"""

from typing import Any
from uuid import UUID

from sqlmodel.ext.asyncio.session import AsyncSession
from taskiq import TaskiqDepends

from app.api.deps import get_db
from app.core.broker import broker
from app.core.logging import get_logger
from app.modules.payments import service as payment_service

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
