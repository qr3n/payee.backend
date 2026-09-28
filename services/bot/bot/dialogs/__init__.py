"""Dialogs package for aiogram-dialog flows."""

from bot.dialogs.items import items_dialog
from bot.dialogs.main_menu import main_dialog
from bot.dialogs.states import ItemsSG, MainSG

__all__ = [
    "ItemsSG",
    "MainSG",
    "items_dialog",
    "main_dialog",
]
