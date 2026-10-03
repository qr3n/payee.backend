"""
Payment entity model and lifecycle statuses.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, Column, ForeignKey, Numeric, String, Text
from sqlmodel import Field, SQLModel

from app.shared.models import BaseUUIDModel


class PaymentStatus(StrEnum):
    """Lifecycle statuses for a payment transaction."""

    PENDING = "pending"
    PAID = "paid"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


class PaymentBase(SQLModel):
    """Shared properties for a Payment entity."""

    client_user_id: str = Field(
        min_length=1,
        max_length=128,
        index=True,
        description="External client/user identifier requesting the payment",
        schema_extra={"examples": ["user_987654"]},
    )
    scenario_id: str = Field(
        min_length=1,
        max_length=64,
        index=True,
        description="Payment scenario identifier to execute",
        schema_extra={"examples": ["demo_bot"]},
    )
    amount: Decimal = Field(
        default=Decimal("0.00"),
        sa_column=Column(Numeric(14, 2), nullable=False),
        description="Transaction monetary amount",
        schema_extra={"examples": ["1500.00"]},
    )
    currency: str = Field(
        default="RUB",
        max_length=16,
        description="Currency code (e.g. RUB, USDT, USD, XTR)",
        schema_extra={"examples": ["RUB"]},
    )


class Payment(PaymentBase, BaseUUIDModel, table=True):
    """Database entity representing an active or completed payment."""

    __tablename__ = "payments"

    account_id: UUID | None = Field(
        default=None,
        sa_column=Column(
            ForeignKey("telegram_accounts.id", ondelete="SET NULL"),
            index=True,
            nullable=True,
        ),
        description="Telegram account reserved for creating this payment",
    )
    status: PaymentStatus = Field(
        default=PaymentStatus.PENDING,
        sa_column=Column(
            String(32),
            nullable=False,
            index=True,
            default=PaymentStatus.PENDING.value,
        ),
        description="Current status of the payment",
    )
    payment_link: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="Payment URL or bot invoice link",
    )
    expires_at: datetime = Field(
        index=True,
        nullable=False,
        description="UTC expiration timestamp (typically creation time + 30m)",
    )
    paid_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when payment was confirmed",
    )
    cancelled_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when payment was cancelled",
    )
    meta: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default="{}"),
        description="Scenario-specific execution parameters and responses",
    )
