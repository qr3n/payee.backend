from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram_dialog import DialogManager, StartMode

from bot.dialogs.states import MainSG

router = Router(name="common_handlers")


@router.message(CommandStart())
async def cmd_start(
    message: Message,  # noqa: ARG001
    dialog_manager: DialogManager,
) -> None:
    """Handle /start command by launching the main menu dialog."""
    await dialog_manager.start(MainSG.menu, mode=StartMode.RESET_STACK)


@router.message(Command("menu"))
async def cmd_menu(
    message: Message,  # noqa: ARG001
    dialog_manager: DialogManager,
) -> None:
    """Handle /menu command by resetting the dialog stack to main menu."""
    await dialog_manager.start(MainSG.menu, mode=StartMode.RESET_STACK)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Handle /help command with user documentation."""
    await message.answer(
        "📖 <b>Справка по командам бота:</b>\n\n"
        "• /start — Открыть интерактивное главное меню\n"
        "• /menu — Быстрый возврат в главное меню\n"
        "• /help — Вывести эту справку\n\n"
        "Все действия (просмотр, создание элементов, запуск Taskiq задач) "
        "выполняются через интерактивные диалоги aiogram-dialog."
    )
