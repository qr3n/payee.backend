from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import structlog

from bot.client.schemas import (
    HealthCheckResponse,
    PaginatedResponse,
    PaymentCreate,
    PaymentRead,
    ReadinessResponse,
    ScenarioRead,
    TelegramAccountCheckResponse,
    TelegramAccountCreate,
    TelegramAccountRead,
)

logger = structlog.stdlib.get_logger(__name__)


class ApiClientError(Exception):
    """Base exception for API client errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ApiClient:
    """Asynchronous HTTP client for interacting with the FastAPI backend.

    Treats the backend as the single source of truth for business data,
    validation, background task scheduling, and persistence.
    """

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._timeout = timeout

    async def get_client(self) -> httpx.AsyncClient:
        """Obtain or initialize the underlying httpx AsyncClient."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self._timeout,
                headers={"Accept": "application/json", "User-Agent": "TelegramBot/1.0"},
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client session."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def get_health(self) -> HealthCheckResponse:
        """Check basic health status of the backend API."""
        client = await self.get_client()
        response = await client.get("/health")
        response.raise_for_status()
        return HealthCheckResponse.model_validate(response.json())

    async def get_readiness(self) -> ReadinessResponse:
        """Check readiness status of backend dependencies (Postgres, Redis)."""
        client = await self.get_client()
        response = await client.get("/ready")
        response.raise_for_status()
        return ReadinessResponse.model_validate(response.json())

    # =========================================================================
    # Telegram Accounts API
    # =========================================================================
    async def list_accounts(
        self, page: int = 1, size: int = 50
    ) -> PaginatedResponse[TelegramAccountRead]:
        """Fetch a paginated list of telegram accounts."""
        client = await self.get_client()
        response = await client.get(
            "/api/v1/accounts/", params={"page": page, "size": size}
        )
        response.raise_for_status()
        return PaginatedResponse[TelegramAccountRead].model_validate(response.json())

    async def get_account(self, account_id: str | UUID) -> TelegramAccountRead | None:
        """Retrieve a specific telegram account by UUID."""
        client = await self.get_client()
        response = await client.get(f"/api/v1/accounts/{account_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return TelegramAccountRead.model_validate(response.json())

    async def create_account(
        self,
        title: str,
        session_string: str,
        phone: str | None = None,
        proxy_url: str | None = None,
        verify_on_create: bool = True,
    ) -> TelegramAccountRead:
        """Register and optionally verify a new Telegram MTProto session."""
        client = await self.get_client()
        payload = TelegramAccountCreate(
            title=title,
            session_string=session_string,
            phone=phone,
            proxy_url=proxy_url,
            verify_on_create=verify_on_create,
        ).model_dump(exclude_none=True)
        response = await client.post("/api/v1/accounts/", json=payload)
        response.raise_for_status()
        return TelegramAccountRead.model_validate(response.json())

    async def check_account(
        self, account_id: str | UUID
    ) -> TelegramAccountCheckResponse:
        """Trigger an on-demand MTProto health check for an account."""
        client = await self.get_client()
        response = await client.post(f"/api/v1/accounts/{account_id}/check")
        response.raise_for_status()
        return TelegramAccountCheckResponse.model_validate(response.json())

    async def delete_account(self, account_id: str | UUID) -> None:
        """Delete an account from the pool."""
        client = await self.get_client()
        response = await client.delete(f"/api/v1/accounts/{account_id}")
        response.raise_for_status()

    # =========================================================================
    # Payments & Scenarios API
    # =========================================================================
    async def list_scenarios(self) -> list[ScenarioRead]:
        """Fetch list of available payment scenarios."""
        client = await self.get_client()
        response = await client.get("/api/v1/payments/scenarios")
        response.raise_for_status()
        data = response.json()
        return [ScenarioRead.model_validate(item) for item in data]

    async def create_payment(
        self,
        client_user_id: str,
        amount: Decimal,
        scenario_id: str = "starslly_bot",
        currency: str = "RUB",
        meta: dict[str, Any] | None = None,
    ) -> PaymentRead:
        """Create a payment and execute scenario to generate payment link."""
        client = await self.get_client()
        payload = PaymentCreate(
            client_user_id=client_user_id,
            scenario_id=scenario_id,
            amount=amount,
            currency=currency,
            meta=meta or {},
        ).model_dump(mode="json")
        response = await client.post("/api/v1/payments/", json=payload)
        response.raise_for_status()
        return PaymentRead.model_validate(response.json())

    async def get_payment(self, payment_id: str | UUID) -> PaymentRead | None:
        """Get details of a payment by UUID."""
        client = await self.get_client()
        response = await client.get(f"/api/v1/payments/{payment_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return PaymentRead.model_validate(response.json())

    async def mark_payment_paid(self, payment_id: str | UUID) -> PaymentRead:
        """Manually mark payment as PAID and release account."""
        client = await self.get_client()
        response = await client.post(f"/api/v1/payments/{payment_id}/paid")
        response.raise_for_status()
        return PaymentRead.model_validate(response.json())

    async def cancel_payment(self, payment_id: str | UUID) -> PaymentRead:
        """Cancel payment and release account."""
        client = await self.get_client()
        response = await client.post(f"/api/v1/payments/{payment_id}/cancel")
        response.raise_for_status()
        return PaymentRead.model_validate(response.json())
