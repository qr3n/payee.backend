"""
Domain modules registry.
Import all domain models here to ensure SQLModel and Alembic metadata registration.
"""

from app.modules.accounts.models import TelegramAccount
from app.modules.health import router as health_router
from app.modules.payments.models import Payment

__all__ = [
    "Payment",
    "TelegramAccount",
    "health_router",
]
