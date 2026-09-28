from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.client.api import ApiClient


class ApiClientMiddleware(BaseMiddleware):
    """Outer middleware injecting the ApiClient instance into handler contexts."""

    def __init__(self, api_client: ApiClient) -> None:
        self.api_client = api_client

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["api_client"] = self.api_client
        return await handler(event, data)
