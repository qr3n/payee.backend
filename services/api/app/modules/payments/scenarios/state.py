"""
State management for pre-warmed / prepared MTProto scenario chats in Redis.
Tracks whether an account's chat with a target bot is currently sitting
at the amount input prompt (fast-path ready).
Implements fail-closed distributed locks with owner tokens and Lua release scripts.
"""

import asyncio
import contextlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

import uuid6

from app.core.logging import get_logger
from app.core.redis import get_redis

if TYPE_CHECKING:
    from app.modules.accounts.models import TelegramAccount

logger = get_logger(__name__)

DEFAULT_PREPARATION_TTL_SECONDS = 900  # 15 minutes


@dataclass(frozen=True, slots=True)
class GenerationLease:
    """Distributed lock lease token holding exclusive ownership of an account."""

    account_id: UUID
    owner_token: str


@dataclass(slots=True)
class AcquiredAccount:
    """Account coupled with its exclusive generation lease."""

    account: "TelegramAccount"
    lease: GenerationLease


# Atomic Lua release script: deletes key ONLY if its value matches the owner token
LUA_RELEASE_LOCK = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("DEL", KEYS[1])
else
    return 0
end
"""

# Atomic Lua extend script: extends TTL ONLY if its value matches the owner token
LUA_EXTEND_LOCK = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    return redis.call("EXPIRE", KEYS[1], ARGV[2])
else
    return 0
end
"""


def _prep_key(account_id: UUID | str, scenario_id: str) -> str:
    return f"scenario_prep:{account_id}:{scenario_id}"


async def get_scenario_prepared_context(
    account_id: UUID | str, scenario_id: str
) -> dict[str, Any] | None:
    """Retrieve full preparation context from Redis."""
    try:
        redis = get_redis()
        raw = await redis.get(_prep_key(account_id, scenario_id))
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if raw.startswith("{"):
            return json.loads(raw)  # type: ignore[no-any-return]
        # Backward compatibility with plain string status
        return {"status": raw}
    except Exception as exc:
        logger.debug(
            "redis_prep_context_get_failed",
            account_id=str(account_id),
            scenario_id=scenario_id,
            error=str(exc),
        )
        return None


async def is_scenario_prepared(
    account_id: UUID | str,
    scenario_id: str,
    expected_recipient: str | None = None,
    expected_bot_username: str | None = None,
    expected_fingerprint: str | None = None,
) -> bool:
    """
    Check if the account's dialog with the scenario bot is ready for amount input.
    Validates status, recipient, bot username, and connection fingerprint.
    """
    ctx = await get_scenario_prepared_context(account_id, scenario_id)
    if not ctx or not isinstance(ctx, dict) or ctx.get("status") != "ready_for_amount":
        return False

    if expected_bot_username is not None:
        stored_bot = ctx.get("bot_username")
        if not isinstance(stored_bot, str):
            return False
        if stored_bot.lstrip("@").lower() != expected_bot_username.lstrip("@").lower():
            return False

    if (
        expected_fingerprint is not None
        and ctx.get("fingerprint") != expected_fingerprint
    ):
        return False

    if expected_recipient is not None:
        stored_recipient = ctx.get("recipient")
        # For scenarios where recipient is entered during warmup,
        # missing stored recipient is a cache miss
        if scenario_id in ("helperstars_bot", "starslly_bot"):
            if not stored_recipient:
                return False
            norm_expected = expected_recipient.lstrip("@").lower()
            norm_stored = str(stored_recipient).lstrip("@").lower()
            if norm_expected != norm_stored:
                return False
        elif stored_recipient is not None:
            norm_expected = expected_recipient.lstrip("@").lower()
            norm_stored = str(stored_recipient).lstrip("@").lower()
            if norm_expected != norm_stored:
                return False

    return True


async def set_scenario_prepared(
    account_id: UUID | str,
    scenario_id: str,
    context: dict[str, Any] | None = None,
    ttl_seconds: int = DEFAULT_PREPARATION_TTL_SECONDS,
) -> None:
    """Mark the account's dialog with the scenario bot as ready for amount input."""
    try:
        redis = get_redis()
        data = {
            "status": "ready_for_amount",
            **(context or {}),
        }
        await redis.set(
            _prep_key(account_id, scenario_id),
            json.dumps(data, ensure_ascii=False),
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
    owner_token: str | None = None,
    ttl_seconds: int = 90,
) -> str | None:
    """
    Atomically acquire an exclusive generation lock for an account in Redis.
    Returns the unique owner_token (truthy) if acquired, or None if busy/failed.
    Fails closed (returns None) on Redis errors.
    """
    token = owner_token or str(uuid6.uuid7())
    try:
        redis = get_redis()
        res = await redis.set(
            _generation_lock_key(account_id), token, nx=True, ex=ttl_seconds
        )
        return token if res else None
    except Exception as exc:
        logger.error(
            "failed_acquiring_account_generation_lock",
            account_id=str(account_id),
            error=str(exc),
        )
        return None


async def _eval_release_lock(redis: Any, key: str, owner_token: str) -> bool:
    """Atomically release key only if current value matches owner_token via Lua."""
    if not owner_token:
        raise ValueError(
            "owner_token is strictly required to release distributed lock safely"
        )
    res = await redis.eval(LUA_RELEASE_LOCK, 1, key, owner_token)
    return bool(res)


async def extend_account_generation_lock(
    account_id: UUID | str,
    owner_token: str,
    ttl_seconds: int = 90,
) -> bool:
    """Atomically extend generation lock TTL if owned by owner_token."""
    if not owner_token:
        raise ValueError("owner_token is strictly required to extend lock safely")
    try:
        redis = get_redis()
        key = _generation_lock_key(account_id)
        res = await redis.eval(LUA_EXTEND_LOCK, 1, key, owner_token, ttl_seconds)
        return bool(res)
    except Exception as exc:
        logger.warning(
            "failed_extending_account_generation_lock",
            account_id=str(account_id),
            error=str(exc),
        )
        return False


class AccountLeaseRenewer:
    """
    Context manager that periodically extends the account generation lock lease
    in the background while a long-running dialog or preparation is in progress.
    """

    def __init__(
        self,
        account_id: UUID | str,
        owner_token: str,
        interval_seconds: float = 30.0,
        lease_ttl_seconds: int = 90,
    ) -> None:
        self.account_id = account_id
        self.owner_token = owner_token
        self.interval_seconds = interval_seconds
        self.lease_ttl_seconds = lease_ttl_seconds
        self._task: asyncio.Task[None] | None = None
        self._lost_event = asyncio.Event()

    async def __aenter__(self) -> "AccountLeaseRenewer":
        self._task = asyncio.create_task(self._renew_loop())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _renew_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.interval_seconds)
                success = await extend_account_generation_lock(
                    self.account_id,
                    self.owner_token,
                    ttl_seconds=self.lease_ttl_seconds,
                )
                if not success:
                    logger.warning(
                        "lease_renewal_failed_lock_lost",
                        account_id=str(self.account_id),
                    )
                    self._lost_event.set()
                    break
        except asyncio.CancelledError:
            pass

    @property
    def is_lost(self) -> bool:
        return self._lost_event.is_set()


async def release_account_generation_lock(
    account_id: UUID | str,
    owner_token: str,
) -> bool:
    """
    Release the generation lock.
    Verifies ownership via owner_token before deletion.
    """
    try:
        redis = get_redis()
        key = _generation_lock_key(account_id)
        return await _eval_release_lock(redis, key, owner_token)
    except Exception as exc:
        logger.debug(
            "failed_releasing_account_generation_lock",
            account_id=str(account_id),
            error=str(exc),
        )
        return False


async def is_account_generation_locked(account_id: UUID | str) -> bool:
    """Check if account is currently actively generating a payment link."""
    try:
        redis = get_redis()
        val = await redis.get(_generation_lock_key(account_id))
        return val is not None
    except Exception:
        return False


async def release_all_account_generation_locks() -> int:
    """Release all active account generation locks from Redis using scan_iter."""
    try:
        redis = get_redis()
        deleted = 0
        async for key in redis.scan_iter("lock:account_generation:*", count=100):
            await redis.delete(key)
            deleted += 1
        return deleted
    except Exception as exc:
        logger.warning("failed_releasing_all_account_generation_locks", error=str(exc))
        return 0


def _scenario_stars_lock_key(scenario_id: str, stars_count: int) -> str:
    return f"lock:scenario_stars:{scenario_id}:{stars_count}"


async def acquire_scenario_stars_reservation(
    scenario_id: str,
    stars_count: int,
    owner_token: str | None = None,
    ttl_seconds: int = 1800,  # 30 minutes (matches payment TTL)
) -> str | None:
    """
    Atomically reserve a specific stars_count for a scenario in Redis.
    Returns owner token if acquired, or None if occupied or failed.
    """
    token = owner_token or str(uuid6.uuid7())
    try:
        redis = get_redis()
        res = await redis.set(
            _scenario_stars_lock_key(scenario_id, stars_count),
            token,
            nx=True,
            ex=ttl_seconds,
        )
        return token if res else None
    except Exception as exc:
        logger.error(
            "failed_acquiring_scenario_stars_reservation",
            scenario_id=scenario_id,
            stars_count=stars_count,
            error=str(exc),
        )
        return None


async def release_scenario_stars_reservation(
    scenario_id: str,
    stars_count: int,
    owner_token: str,
) -> bool:
    """Release a reserved stars count for a scenario."""
    try:
        redis = get_redis()
        key = _scenario_stars_lock_key(scenario_id, stars_count)
        return await _eval_release_lock(redis, key, owner_token)
    except Exception as exc:
        logger.debug(
            "failed_releasing_scenario_stars_reservation",
            scenario_id=scenario_id,
            stars_count=stars_count,
            error=str(exc),
        )
        return False


async def release_all_scenario_stars_reservations() -> int:
    """Release all reserved stars count keys across all scenarios using scan_iter."""
    try:
        redis = get_redis()
        deleted = 0
        async for key in redis.scan_iter("lock:scenario_stars:*", count=100):
            await redis.delete(key)
            deleted += 1
        return deleted
    except Exception as exc:
        logger.warning(
            "failed_releasing_all_scenario_stars_reservations", error=str(exc)
        )
        return 0


def _scenario_pending_lock_key(scenario_id: str, account_id: UUID | str) -> str:
    return f"lock:scenario_pending:{scenario_id}:{account_id}"


async def acquire_scenario_pending_lock(
    scenario_id: str,
    account_id: UUID | str,
    owner_token: str | None = None,
    ttl_seconds: int = 1800,  # 30 minutes
) -> str | None:
    """
    Atomically acquire a pending order reservation for a scenario on a specific account.
    Used for scenarios (like starslly_bot) that do not provide order IDs in confirmation
    messages, ensuring at most 1 pending order exists per account.
    Returns owner token if acquired, or None if occupied or failed.
    """
    token = owner_token or str(uuid6.uuid7())
    try:
        redis = get_redis()
        res = await redis.set(
            _scenario_pending_lock_key(scenario_id, account_id),
            token,
            nx=True,
            ex=ttl_seconds,
        )
        return token if res else None
    except Exception as exc:
        logger.error(
            "failed_acquiring_scenario_pending_lock",
            scenario_id=scenario_id,
            account_id=str(account_id),
            error=str(exc),
        )
        return None


async def release_scenario_pending_lock(
    scenario_id: str,
    account_id: UUID | str,
    owner_token: str,
) -> bool:
    """Release pending reservation for a scenario on an account."""
    try:
        redis = get_redis()
        key = _scenario_pending_lock_key(scenario_id, account_id)
        return await _eval_release_lock(redis, key, owner_token)
    except Exception as exc:
        logger.debug(
            "failed_releasing_scenario_pending_lock",
            scenario_id=scenario_id,
            account_id=str(account_id),
            error=str(exc),
        )
        return False


async def is_scenario_pending_locked(
    scenario_id: str,
    account_id: UUID | str,
) -> bool:
    """Check if an account already has an active pending order for this scenario."""
    try:
        redis = get_redis()
        val = await redis.get(_scenario_pending_lock_key(scenario_id, account_id))
        return val is not None
    except Exception:
        return False


async def release_all_scenario_pending_locks() -> int:
    """Release all pending scenario lock keys across all accounts using scan_iter."""
    try:
        redis = get_redis()
        deleted = 0
        async for key in redis.scan_iter("lock:scenario_pending:*", count=100):
            await redis.delete(key)
            deleted += 1
        return deleted
    except Exception as exc:
        logger.warning("failed_releasing_all_scenario_pending_locks", error=str(exc))
        return 0
