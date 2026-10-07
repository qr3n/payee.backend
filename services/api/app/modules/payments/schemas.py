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
        max_digits=14,
        decimal_places=2,
        allow_inf_nan=False,
        description="Monetary amount (must be positive, max 2 decimal places)",
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
    account_id: UUID | None = Field(
        default=None, description="Assigned Telegram account ID"
    )
    batch_id: UUID | None = Field(
        default=None, description="Race batch ID (groups multiple scenario results)"
    )
    idempotency_key: str | None = Field(
        default=None, description="Client idempotency key"
    )
    scenario_id: str = Field(description="Executed payment scenario ID")
    amount: Decimal = Field(description="Payment amount")
    currency: str = Field(description="Payment currency")
    status: PaymentStatus = Field(description="Current payment status")
    payment_link: str | None = Field(description="Generated payment/invoice URL")
    callback_url: str | None = Field(default=None, description="Webhook callback URL")
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
    is_fallback: bool = Field(
        default=False,
        description="Whether this scenario is an emergency fallback",
    )


class PaymentRaceCreate(BaseModel):
    """Input payload for initiating a concurrent multi-scenario payment race."""

    client_user_id: str = Field(
        min_length=1,
        max_length=128,
        description="External client/user identifier requesting the payment",
        examples=["user_987654"],
    )
    amount: Decimal = Field(
        gt=0,
        max_digits=14,
        decimal_places=2,
        allow_inf_nan=False,
        description="Monetary amount (must be positive, max 2 decimal places)",
        examples=["500.00"],
    )
    currency: str = Field(
        default="RUB",
        max_length=16,
        description="Currency code (e.g. RUB, USDT, USD)",
        examples=["RUB"],
    )
    scenario_ids: list[str] | None = Field(
        default=None,
        description=(
            "Optional list of specific scenario IDs to race. "
            "If omitted, races primary non-fallback scenarios."
        ),
        examples=[["starshoppik_bot", "helperstars_bot"]],
    )
    timeout_sec: float = Field(
        default=120.0,
        gt=0,
        le=300.0,
        description="Maximum seconds to wait for all scenarios to complete",
        examples=[120.0],
    )
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional scenario-specific arguments",
        examples=[{}],
    )
    idempotency_key: str | None = Field(
        default=None,
        max_length=128,
        description="Optional idempotency key to prevent duplicate race launches",
        examples=["race_idem_123"],
    )
    callback_url: str | None = Field(
        default=None,
        max_length=512,
        description="Optional webhook URL to notify on payment completion",
        examples=["https://merchant.example.com/api/payment-callback"],
    )


class PaymentRaceEvent(BaseModel):
    """Single SSE event emitted as each scenario completes during a race."""

    batch_id: UUID = Field(description="Shared batch UUID grouping all race payments")
    payment_id: UUID = Field(description="Individual payment UUID")
    scenario_id: str = Field(description="Scenario that generated this link")
    payment_link: str = Field(description="Generated payment/invoice URL")
    is_sbp_resolved: bool = Field(description="Whether link was resolved to NSPK SBP")
    generation_time_sec: float = Field(
        description="Total time from race start to this link"
    )
    stage_timings: list[dict[str, Any]] = Field(
        default_factory=list, description="Per-stage timing breakdown"
    )


class PaymentRaceFireResponse(BaseModel):
    """Response from fire-and-forget race initiation. Generation starts immediately."""

    batch_id: UUID = Field(description="Batch ID — use for SSE subscription")
    status: str = Field(
        default="running",
        description="Initial race status (always 'running' at creation time)",
    )
    scenarios: list[str] = Field(
        description="List of scenario IDs being raced concurrently"
    )


class ReleaseAccountsResponse(BaseModel):
    """Result of releasing all locked accounts."""

    cancelled_payments_count: int = Field(
        description="Number of active pending payments cancelled"
    )
    released_accounts_count: int = Field(
        description="Number of distinct Telegram accounts unlocked"
    )
    message: str = Field(description="Status description")


class PaymentWebhookPayload(BaseModel):
    """Payload dispatched to merchant callback_url on terminal payment status."""

    event: str = Field(
        default="payment.status_changed",
        description="Webhook event type",
        examples=["payment.status_changed"],
    )
    payment_id: UUID = Field(description="Unique payment ID (UUIDv7)")
    client_user_id: str = Field(description="Client/user identifier")
    scenario_id: str = Field(description="Executed payment scenario ID")
    amount: Decimal = Field(description="Transaction monetary amount")
    currency: str = Field(description="Transaction currency code")
    status: PaymentStatus = Field(description="Current payment status")
    idempotency_key: str | None = Field(
        default=None, description="Client idempotency key"
    )
    batch_id: UUID | None = Field(
        default=None, description="Race batch ID if applicable"
    )
    payment_link: str | None = Field(default=None, description="Payment/invoice URL")
    created_at: datetime = Field(description="Payment creation timestamp")
    paid_at: datetime | None = Field(
        default=None, description="Payment confirmation timestamp"
    )
    cancelled_at: datetime | None = Field(
        default=None, description="Payment cancellation timestamp"
    )
    external_transaction_id: str | None = Field(
        default=None, description="Provider/bot transaction or order ID"
    )
    meta: dict[str, Any] = Field(
        default_factory=dict, description="Public metadata associated with the payment"
    )


class PaymentWebhookResendResponse(BaseModel):
    """Result of manual webhook re-dispatch."""

    payment_id: UUID = Field(description="Unique payment ID")
    callback_url: str = Field(description="Target callback URL")
    status: str = Field(description="Dispatch status (delivered, skipped, failed)")
    message: str = Field(description="Detailed delivery status or error message")
