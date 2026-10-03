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
