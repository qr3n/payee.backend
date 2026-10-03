"""
Taskiq background asynchronous tasks for Telegram accounts module.
Discovered automatically by worker via --fs-discover.
"""

from typing import Any
from uuid import UUID

from sqlmodel.ext.asyncio.session import AsyncSession
from taskiq import TaskiqDepends

from app.api.deps import get_db
from app.core.broker import broker
from app.core.logging import get_logger
from app.modules.accounts import service as account_service

logger = get_logger(__name__)


@broker.task(task_name="accounts:check_all_accounts")
async def check_all_accounts_task(
    db: AsyncSession = TaskiqDepends(get_db),
) -> dict[str, int]:
    """
    Background job to verify health of all active Telegram sessions,
    update database statuses, and alert administrators if any are revoked/banned.
    """
    logger.info("Executing batch accounts health check task")
    counts = await account_service.check_all_accounts(session=db)
    logger.info("Batch accounts health check completed", **counts)
    return counts


@broker.task(task_name="accounts:check_single_account")
async def check_single_account_task(
    account_id: UUID,
    db: AsyncSession = TaskiqDepends(get_db),
) -> dict[str, Any]:
    """
    Background job to check a single Telegram account by UUID.
    """
    logger.info(
        "Executing single account health check task", account_id=str(account_id)
    )
    account = await account_service.get_account(db, account_id)
    if not account:
        logger.warning(
            "Account not found for background check", account_id=str(account_id)
        )
        return {"error": "Account not found"}

    _, check_resp = await account_service.verify_and_update_account(db, account)
    return {
        "account_id": str(account_id),
        "status": check_resp.status.value,
        "is_authorized": check_resp.is_authorized,
        "error": check_resp.error,
    }
