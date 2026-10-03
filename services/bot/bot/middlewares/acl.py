from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = structlog.stdlib.get_logger(__name__)


class AdminAclMiddleware(BaseMiddleware):
    """Middleware enforcing admin access by verifying Telegram user ID."""

    def __init__(self, admin_chat_ids: list[int]) -> None:
        self.admin_chat_ids = set(admin_chat_ids)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        if not self.admin_chat_ids or user.id not in self.admin_chat_ids:
            logger.warning(
                "unauthorized_bot_access_attempt",
                user_id=user.id,
                username=user.username,
            )
            if isinstance(event, Message):
                await event.answer(
                    "⛔ Доступ ограничен. Вы не являетесь администратором системы."
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Доступ ограничен.", show_alert=True)
            return None

        return await handler(event, data)
