from aiogram.fsm.storage.base import BaseStorage, DefaultKeyBuilder
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage

from bot.core.config import BotSettings


def create_storage(settings: BotSettings) -> BaseStorage:
    """Create and return an FSM storage instance.

    Uses RedisStorage with key prefix and TTLs for production resilience,
    falling back to MemoryStorage only if Redis is explicitly disabled.
    """
    if settings.REDIS_HOST == "memory":
        return MemoryStorage()

    key_builder = DefaultKeyBuilder(
        prefix=settings.REDIS_FSM_PREFIX,
        with_destiny=True,
    )
    return RedisStorage.from_url(
        url=settings.redis_fsm_uri,
        key_builder=key_builder,
        state_ttl=settings.REDIS_STATE_TTL,
        data_ttl=settings.REDIS_DATA_TTL,
    )
