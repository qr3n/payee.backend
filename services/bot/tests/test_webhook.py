from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp.test_utils import TestClient, TestServer

from bot.client.api import ApiClient
from bot.core.config import BotSettings
from bot.webhook.server import create_webhook_app


@pytest.mark.asyncio
async def test_webhook_health_route(test_settings: BotSettings) -> None:
    bot = AsyncMock(spec=Bot)
    bot.session = AsyncMock()
    dp = Dispatcher(storage=MemoryStorage())
    api_client = AsyncMock(spec=ApiClient)

    app = create_webhook_app(
        bot=bot,
        dp=dp,
        settings=test_settings,
        api_client=api_client,
    )

    client = TestClient(TestServer(app))
    await client.start_server()

    try:
        response = await client.get("/health")
        assert response.status == 200
        data = await response.json()
        assert data["status"] == "ok"
        assert data["mode"] == "webhook"
        assert data["service"] == "telegram-bot"
    finally:
        await client.close()
