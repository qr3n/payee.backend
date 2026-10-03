"""
Domain modules registry.
Import all domain models here to ensure SQLModel and Alembic metadata registration.
"""

from app.modules.accounts.models import TelegramAccount
from app.modules.health import router as health_router
from app.modules.payments.models import NotificationEvent, Payment

__all__ = [
    "NotificationEvent",
    "Payment",
    "TelegramAccount",
    "health_router",
]
