from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Message, User

from bot.middlewares.acl import AdminAclMiddleware


@pytest.mark.asyncio
async def test_admin_acl_middleware_blocks_unauthorized_user() -> None:
    middleware = AdminAclMiddleware(admin_chat_ids=[12345])
    handler = AsyncMock()

    message = AsyncMock(spec=Message)
    message.answer = AsyncMock()
    user = User(id=99999, is_bot=False, first_name="Stranger")
    data = {"event_from_user": user}

    result = await middleware(handler, message, data)

    assert result is None
    handler.assert_not_called()
    message.answer.assert_awaited_once()
    assert "Доступ ограничен" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_admin_acl_middleware_allows_authorized_admin() -> None:
    middleware = AdminAclMiddleware(admin_chat_ids=[12345])
    handler = AsyncMock(return_value="handler_called")

    message = AsyncMock(spec=Message)
    user = User(id=12345, is_bot=False, first_name="Admin")
    data = {"event_from_user": user}

    result = await middleware(handler, message, data)

    assert result == "handler_called"
    handler.assert_awaited_once_with(message, data)


@pytest.mark.asyncio
async def test_admin_acl_middleware_empty_admins_blocks_all() -> None:
    middleware = AdminAclMiddleware(admin_chat_ids=[])
    handler = AsyncMock()

    callback = AsyncMock(spec=CallbackQuery)
    callback.answer = AsyncMock()
    user = User(id=12345, is_bot=False, first_name="User")
    data = {"event_from_user": user}

    result = await middleware(handler, callback, data)

    assert result is None
    handler.assert_not_called()
    callback.answer.assert_awaited_once()
    assert "Доступ ограничен" in callback.answer.call_args[0][0]
