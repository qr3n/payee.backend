"""
Pydantic DTO schemas for Payments module.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlmodel import SQLModel

from app.modules.payments.models import PaymentBase, PaymentStatus


class PaymentCreate(PaymentBase):
    """Input payload for initiating a payment request."""

    amount: Decimal = Field(
        gt=0,
        description="Monetary amount (must be positive)",
        examples=["500.00"],
    )
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description="Scenario-specific arguments (e.g. order_id, product_name)",
        examples=[{"order_id": "ORD-12345"}],
    )


class PaymentRead(SQLModel):
    """Response representation of a Payment entity."""

    id: UUID = Field(description="Unique payment ID (UUIDv7)")
    client_user_id: str = Field(description="Client/user identifier")
    account_id: UUID = Field(description="Assigned Telegram account ID")
    scenario_id: str = Field(description="Executed payment scenario ID")
    amount: Decimal = Field(description="Payment amount")
    currency: str = Field(description="Payment currency")
    status: PaymentStatus = Field(description="Current payment status")
    payment_link: str | None = Field(description="Generated payment/invoice URL")
    expires_at: datetime = Field(description="UTC expiration deadline")
    paid_at: datetime | None = Field(description="UTC payment completion timestamp")
    cancelled_at: datetime | None = Field(description="UTC cancellation timestamp")
    meta: dict[str, Any] = Field(description="Scenario payload and responses")
    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")


class PaymentCallback(BaseModel):
    """Payload sent by webhooks or external providers to update payment status."""

    status: PaymentStatus = Field(
        default=PaymentStatus.PAID,
        description="Target status (e.g. paid, cancelled, failed)",
    )
    external_transaction_id: str | None = Field(
        default=None,
        description="External transaction reference ID",
    )
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional callback payload or provider response",
    )


class ScenarioRead(BaseModel):
    """Schema describing an available payment scenario."""

    scenario_id: str = Field(description="Unique scenario identifier")
    name: str = Field(description="Human-readable scenario title")
    description: str = Field(description="Scenario description")
