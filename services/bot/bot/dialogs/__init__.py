"""Dialogs package for aiogram-dialog flows."""

from bot.dialogs.accounts import accounts_dialog
from bot.dialogs.main_menu import main_dialog
from bot.dialogs.scenarios import scenarios_dialog
from bot.dialogs.states import AccountsSG, MainSG, ScenariosSG

__all__ = [
    "AccountsSG",
    "MainSG",
    "ScenariosSG",
    "accounts_dialog",
    "main_dialog",
    "scenarios_dialog",
]
