"""
Domain modules registry.
Import all domain models here to ensure SQLModel and Alembic metadata registration.
"""

from app.modules.accounts.models import TelegramAccount
from app.modules.health import router as health_router
from app.modules.items import router as items_router
from app.modules.items.models import Item

__all__ = [
    "Item",
    "TelegramAccount",
    "health_router",
    "items_router",
]
