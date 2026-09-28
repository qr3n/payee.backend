from aiogram import Router

from bot.dialogs.items import items_dialog
from bot.dialogs.main_menu import main_dialog
from bot.handlers.common import router as common_router


def get_root_router() -> Router:
    """Create and return the root router containing all handlers and dialogs."""
    root_router = Router(name="root_router")

    # 1. Standard command handlers (/start, /help, etc.)
    root_router.include_router(common_router)

    # 2. aiogram-dialog interactive flows
    root_router.include_router(main_dialog)
    root_router.include_router(items_dialog)

    return root_router
