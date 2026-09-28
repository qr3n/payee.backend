from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from pydantic import SecretStr

from bot.client.api import ApiClient
from bot.core.config import BotSettings


@pytest.fixture
def test_settings() -> BotSettings:
    """Fixture providing isolated settings for testing."""
    return BotSettings(
        TELEGRAM_BOT_TOKEN=SecretStr("123456789:ABCDEF_mock_token_for_tests"),
        TELEGRAM_BOT_MODE="polling",
        TELEGRAM_WEBHOOK_URL="https://bot.example.com/webhook",
        TELEGRAM_WEBHOOK_SECRET=SecretStr("mock_secret_token"),
        API_BASE_URL="http://test-api:8000",
        REDIS_HOST="memory",
        ENVIRONMENT="test",
        DEBUG=True,
    )


@pytest.fixture
def mock_api_client() -> AsyncMock:
    """Fixture providing an AsyncMock simulating ApiClient."""
    client = AsyncMock(spec=ApiClient)
    return client


@pytest.fixture
def memory_storage() -> MemoryStorage:
    """Fixture providing isolated in-memory storage for dialogs/FSM."""
    return MemoryStorage()


@pytest.fixture
async def bot(test_settings: BotSettings) -> AsyncGenerator[Bot]:
    """Fixture providing a Bot instance with dummy token."""
    bot_instance = Bot(token=test_settings.TELEGRAM_BOT_TOKEN.get_secret_value())
    yield bot_instance
    await bot_instance.session.close()


@pytest.fixture
def dispatcher(memory_storage: MemoryStorage) -> Dispatcher:
    """Fixture providing a clean Dispatcher instance."""
    return Dispatcher(storage=memory_storage)
