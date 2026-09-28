import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = structlog.stdlib.get_logger(__name__)


class LoggingMiddleware(BaseMiddleware):
    """Middleware for structured access logging of Telegram updates."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        start_time = time.perf_counter()
        user_id = None
        event_type = type(event).__name__

        if (isinstance(event, Message) and event.from_user) or (
            isinstance(event, CallbackQuery) and event.from_user
        ):
            user_id = event.from_user.id

        try:
            result = await handler(event, data)
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.info(
                "telegram_event_processed",
                event_type=event_type,
                user_id=user_id,
                duration_ms=duration_ms,
            )
            return result
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.error(
                "telegram_event_failed",
                event_type=event_type,
                user_id=user_id,
                duration_ms=duration_ms,
                error=str(exc),
                exc_info=True,
            )
            raise
