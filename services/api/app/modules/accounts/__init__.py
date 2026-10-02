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
from app.modules.accounts.session_pool import (
    TelegramSessionPool,
    create_telethon_client,
    telegram_session_pool,
)

__all__ = [
    "AccountStatus",
    "TelegramAccount",
    "TelegramAccountCheckResponse",
    "TelegramAccountCreate",
    "TelegramAccountRead",
    "TelegramAccountUpdate",
    "TelegramSessionPool",
    "create_telethon_client",
    "router",
    "telegram_session_pool",
]
