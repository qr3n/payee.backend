"""Dialogs package for aiogram-dialog flows."""

from bot.dialogs.ai import ai_dialog
from bot.dialogs.items import items_dialog
from bot.dialogs.main_menu import main_dialog
from bot.dialogs.states import AISG, ItemsSG, MainSG

__all__ = [
    "AISG",
    "ItemsSG",
    "MainSG",
    "ai_dialog",
    "items_dialog",
    "main_dialog",
]
