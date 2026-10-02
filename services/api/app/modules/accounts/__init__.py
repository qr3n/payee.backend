"""
Telegram Accounts vertical slice module.
Manages account pool, MTProto session verification, proxies, and device profiles.
"""

from app.modules.accounts.models import AccountStatus, TelegramAccount
from app.modules.accounts.router import router
from app.modules.accounts.schemas import (
    TelegramAccountCheckResponse,
    TelegramAccountCreate,
    TelegramAccountRead,
    TelegramAccountUpdate,
)

__all__ = [
    "AccountStatus",
    "TelegramAccount",
    "TelegramAccountCheckResponse",
    "TelegramAccountCreate",
    "TelegramAccountRead",
    "TelegramAccountUpdate",
    "router",
]
