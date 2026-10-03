"""
Pydantic DTO schemas for communicating with backend API.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class HealthCheckResponse(BaseModel):
    """Health check status returned by the FastAPI backend."""

    status: str
    service: str
    version: str
    environment: str
    timestamp: datetime


class ReadinessResponse(BaseModel):
    """Readiness probe status returned by the FastAPI backend."""

    status: str
    database: bool
    redis: bool
    timestamp: datetime


class PaginatedResponse[T](BaseModel):
    """Generic envelope matching the backend's pagination format."""

    items: list[T]
    total: int
    page: int
    size: int
    pages: int


class TelegramAccountRead(BaseModel):
    """Telegram account details from backend."""

    id: UUID
    title: str
    phone: str | None = None
    proxy_url: str | None = None
    status: str
    device_model: str
    system_version: str
    app_version: str
    api_id: int | None = None
    api_hash: str | None = None
    telegram_user_id: int | None = None
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    is_premium: bool | None = None
    flood_wait_until: datetime | None = None
    last_checked_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class TelegramAccountCreate(BaseModel):
    """Payload to add a new session."""

    title: str = Field(min_length=1, max_length=128)
    session_string: str = Field(min_length=10)
    phone: str | None = None
    proxy_url: str | None = None
    api_id: int | None = None
    api_hash: str | None = None
    verify_on_create: bool = True


class PhoneCodeResponse(BaseModel):
    """Response returned after Telegram sends login code."""

    phone_code_hash: str
    timeout_seconds: int = 120
    phone: str


class PhoneSignInResponse(BaseModel):
    """Response returned upon sign-in attempt."""

    status: str
    account: TelegramAccountRead | None = None
    message: str | None = None
    phone_code_hash: str | None = None


class TelegramAccountCheckResponse(BaseModel):
    """Result of MTProto session verification check."""

    account_id: UUID
    status: str
    is_authorized: bool
    telegram_user_id: int | None = None
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    phone: str | None = None
    is_premium: bool | None = None
    flood_wait_seconds: int | None = None
    error: str | None = None
    checked_at: datetime


class ScenarioRead(BaseModel):
    """Information on registered bot payment scenario."""

    scenario_id: str
    name: str
    description: str


class PaymentRead(BaseModel):
    """Payment transaction details."""

    id: UUID
    client_user_id: str
    account_id: UUID | None = None
    scenario_id: str
    amount: Decimal
    currency: str
    status: str
    payment_link: str | None = None
    expires_at: datetime
    paid_at: datetime | None = None
    cancelled_at: datetime | None = None
    meta: dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime


class PaymentCreate(BaseModel):
    """Payload to initiate a test payment."""

    client_user_id: str
    scenario_id: str = "starslly_bot"
    amount: Decimal
    currency: str = "RUB"
    meta: dict[str, Any] = {}


class CheckAllAccountsResponse(BaseModel):
    """Batch verification results."""

    total: int
    active: int = 0
    revoked: int = 0
    banned: int = 0
    flood_wait: int = 0
    error: int = 0
