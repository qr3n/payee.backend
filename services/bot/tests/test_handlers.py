from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User
from aiogram_dialog import DialogManager, StartMode

from bot.dialogs.states import MainSG
from bot.handlers.common import cmd_help, cmd_start


@pytest.mark.asyncio
async def test_cmd_start_triggers_main_dialog() -> None:
    message = AsyncMock(spec=Message)
    manager = AsyncMock(spec=DialogManager)

    await cmd_start(message=message, dialog_manager=manager)

    manager.start.assert_awaited_once_with(
        MainSG.menu,
        mode=StartMode.RESET_STACK,
    )


@pytest.mark.asyncio
async def test_cmd_help_replies_with_instructions() -> None:
    message = AsyncMock(spec=Message)
    message.answer = AsyncMock()
    message.from_user = User(id=12345, is_bot=False, first_name="TestUser")
    message.chat = Chat(id=12345, type="private")

    await cmd_help(message=message)

    message.answer.assert_awaited_once()
    called_text = message.answer.call_args[0][0]
    assert "Справка по командам бота" in called_text
