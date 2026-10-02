"""
Taskiq background asynchronous tasks for payments module.
Discovered automatically by worker via --fs-discover.
"""

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
