"""
State management for pre-warmed / prepared MTProto scenario chats in Redis.
Tracks whether an account's chat with a target bot is currently sitting
at the amount input prompt (fast-path ready).
"""

from uuid import UUID

from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

DEFAULT_PREPARATION_TTL_SECONDS = 900  # 15 minutes


def _prep_key(account_id: UUID | str, scenario_id: str) -> str:
    return f"scenario_prep:{account_id}:{scenario_id}"


async def is_scenario_prepared(account_id: UUID | str, scenario_id: str) -> bool:
    """Check if the account's dialog with the scenario bot is ready for amount input."""
    try:
        redis = get_redis()
        val = await redis.get(_prep_key(account_id, scenario_id))
        return val == "ready_for_amount"
    except Exception as exc:
        logger.debug(
            "redis_prep_state_check_failed",
            account_id=str(account_id),
            scenario_id=scenario_id,
            error=str(exc),
        )
        return False


async def set_scenario_prepared(
    account_id: UUID | str,
    scenario_id: str,
    ttl_seconds: int = DEFAULT_PREPARATION_TTL_SECONDS,
) -> None:
    """Mark the account's dialog with the scenario bot as ready for amount input."""
    try:
        redis = get_redis()
        await redis.set(
            _prep_key(account_id, scenario_id),
            "ready_for_amount",
            ex=ttl_seconds,
        )
        logger.info(
            "scenario_dialog_marked_prepared",
            account_id=str(account_id),
            scenario_id=scenario_id,
            ttl_seconds=ttl_seconds,
        )
    except Exception as exc:
        logger.warning(
            "failed_setting_scenario_prepared_state",
            account_id=str(account_id),
            scenario_id=scenario_id,
            error=str(exc),
        )


async def clear_scenario_prepared(account_id: UUID | str, scenario_id: str) -> None:
    """
    Clear the prepared state (e.g. after invoice creation
    or when dialog is invalidated).
    """
    try:
        redis = get_redis()
        await redis.delete(_prep_key(account_id, scenario_id))
    except Exception as exc:
        logger.debug(
            "failed_clearing_scenario_prepared_state",
            account_id=str(account_id),
            scenario_id=scenario_id,
            error=str(exc),
        )


def _generation_lock_key(account_id: UUID | str) -> str:
    return f"lock:account_generation:{account_id}"


async def acquire_account_generation_lock(
    account_id: UUID | str,
    ttl_seconds: int = 90,
) -> bool:
    """
    Atomically acquire an exclusive generation lock for an account in Redis.
    Locks the account ONLY for the duration of invoice generation (5-15s).
    Auto-expires in 90 seconds if the process dies unexpectedly.
    """
    try:
        redis = get_redis()
        res = await redis.set(
            _generation_lock_key(account_id), "1", nx=True, ex=ttl_seconds
        )
        return bool(res)
    except Exception as exc:
        logger.warning(
            "failed_acquiring_account_generation_lock",
            account_id=str(account_id),
            error=str(exc),
        )
        # Fallback to True if Redis check fails to avoid completely halting
        return True


async def release_account_generation_lock(account_id: UUID | str) -> None:
    """Immediately release the generation lock once payment link is produced."""
    try:
        redis = get_redis()
        await redis.delete(_generation_lock_key(account_id))
    except Exception as exc:
        logger.debug(
            "failed_releasing_account_generation_lock",
            account_id=str(account_id),
            error=str(exc),
        )


async def is_account_generation_locked(account_id: UUID | str) -> bool:
    """Check if account is currently actively generating a payment link."""
    try:
        redis = get_redis()
        val = await redis.get(_generation_lock_key(account_id))
        return val is not None
    except Exception:
        return False


async def release_all_account_generation_locks() -> int:
    """Release all active account generation locks from Redis."""
    try:
        redis = get_redis()
        keys = await redis.keys("lock:account_generation:*")
        if keys:
            deleted = await redis.delete(*keys)
            return int(deleted)
        return 0
    except Exception as exc:
        logger.warning("failed_releasing_all_account_generation_locks", error=str(exc))
        return 0
