from contextlib import suppress
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import structlog

from bot.client.schemas import (
    CheckAllAccountsResponse,
    HealthCheckResponse,
    PaginatedResponse,
    PaymentCreate,
    PaymentRead,
    PhoneCodeResponse,
    PhoneSignInResponse,
    ReadinessResponse,
    ReleaseAccountsResponse,
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

    def __init__(
        self,
        base_url: str,
        timeout: float = 90.0,
        api_key: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._timeout = timeout
        self._api_key = api_key

    async def get_client(self) -> httpx.AsyncClient:
        """Obtain or initialize the underlying httpx AsyncClient."""
        if self._client is None or self._client.is_closed:
            headers = {"Accept": "application/json", "User-Agent": "TelegramBot/1.0"}
            if self._api_key:
                headers["X-API-Key"] = self._api_key
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self._timeout,
                headers=headers,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client session."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _handle_response(response: httpx.Response) -> httpx.Response:
        """Validate response status and extract clean error messages."""
        if response.is_error:
            msg = response.text
            with suppress(Exception):
                data = response.json()
                if isinstance(data, dict):
                    err = data.get("error", {})
                    msg = err.get("message") or data.get("detail") or msg
            req_id = response.headers.get("x-request-id")
            if req_id:
                msg = f"{msg} (request_id: {req_id})"
            raise ApiClientError(msg, status_code=response.status_code)
        return response

    async def get_health(self) -> HealthCheckResponse:
        """Check basic health status of the backend API."""
        client = await self.get_client()
        response = await client.get("/health")
        self._handle_response(response)
        return HealthCheckResponse.model_validate(response.json())

    async def get_readiness(self) -> ReadinessResponse:
        """Check readiness status of backend dependencies (Postgres, Redis)."""
        client = await self.get_client()
        response = await client.get("/api/v1/ready")
        self._handle_response(response)
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
        self._handle_response(response)
        return PaginatedResponse[TelegramAccountRead].model_validate(response.json())

    async def get_account(self, account_id: str | UUID) -> TelegramAccountRead | None:
        """Retrieve a specific telegram account by UUID."""
        client = await self.get_client()
        response = await client.get(f"/api/v1/accounts/{account_id}")
        if response.status_code == 404:
            return None
        self._handle_response(response)
        return TelegramAccountRead.model_validate(response.json())

    async def create_account(
        self,
        title: str,
        session_string: str,
        phone: str | None = None,
        proxy_url: str | None = None,
        api_id: int | None = None,
        api_hash: str | None = None,
        verify_on_create: bool = True,
    ) -> TelegramAccountRead:
        """Register and optionally verify a new Telegram MTProto session."""
        client = await self.get_client()
        payload = TelegramAccountCreate(
            title=title,
            session_string=session_string,
            phone=phone,
            proxy_url=proxy_url,
            api_id=api_id,
            api_hash=api_hash,
            verify_on_create=verify_on_create,
        ).model_dump(exclude_none=True)
        response = await client.post("/api/v1/accounts/", json=payload)
        self._handle_response(response)
        return TelegramAccountRead.model_validate(response.json())

    async def upload_account_session(
        self,
        session_bytes: bytes,
        session_filename: str,
        json_bytes: bytes,
        json_filename: str,
        title: str | None = None,
        proxy_url: str | None = None,
        verify: bool = True,
    ) -> TelegramAccountRead:
        """Upload .session file and .json client metadata."""
        client = await self.get_client()
        files = {
            "session_file": (
                session_filename,
                session_bytes,
                "application/octet-stream",
            ),
            "json_file": (json_filename, json_bytes, "application/json"),
        }
        data: dict[str, Any] = {"verify": str(verify).lower()}
        if title:
            data["title"] = title
        if proxy_url:
            data["proxy_url"] = proxy_url

        response = await client.post("/api/v1/accounts/upload", files=files, data=data)
        self._handle_response(response)
        return TelegramAccountRead.model_validate(response.json())

    async def send_phone_code(
        self,
        phone: str,
        title: str | None = None,
        api_id: int | None = None,
        api_hash: str | None = None,
        proxy_url: str | None = None,
    ) -> PhoneCodeResponse:
        """Request confirmation code for phone number."""
        client = await self.get_client()
        payload: dict[str, Any] = {"phone": phone}
        if title:
            payload["title"] = title
        if api_id:
            payload["api_id"] = api_id
        if api_hash:
            payload["api_hash"] = api_hash
        if proxy_url:
            payload["proxy_url"] = proxy_url

        response = await client.post("/api/v1/accounts/auth/send-code", json=payload)
        self._handle_response(response)
        return PhoneCodeResponse.model_validate(response.json())

    async def sign_in_phone(
        self,
        phone_code_hash: str,
        code: str,
        phone: str | None = None,
        two_fa_password: str | None = None,
    ) -> PhoneSignInResponse:
        """Complete sign in using phone code or 2FA password."""
        client = await self.get_client()
        payload: dict[str, Any] = {
            "phone_code_hash": phone_code_hash,
            "code": code,
        }
        if phone:
            payload["phone"] = phone
        if two_fa_password:
            payload["two_fa_password"] = two_fa_password

        response = await client.post("/api/v1/accounts/auth/sign-in", json=payload)
        self._handle_response(response)
        return PhoneSignInResponse.model_validate(response.json())

    async def check_account(
        self, account_id: str | UUID
    ) -> TelegramAccountCheckResponse:
        """Trigger an on-demand MTProto health check for an account."""
        client = await self.get_client()
        response = await client.post(f"/api/v1/accounts/{account_id}/check")
        self._handle_response(response)
        return TelegramAccountCheckResponse.model_validate(response.json())

    async def check_all_accounts(self) -> CheckAllAccountsResponse:
        """Trigger batch health verification across all non-disabled accounts."""
        client = await self.get_client()
        response = await client.post("/api/v1/accounts/check-all")
        self._handle_response(response)
        return CheckAllAccountsResponse.model_validate(response.json())

    async def delete_account(self, account_id: str | UUID) -> None:
        """Delete an account from the pool."""
        client = await self.get_client()
        response = await client.delete(f"/api/v1/accounts/{account_id}")
        self._handle_response(response)

    async def prepare_account(self, account_id: str | UUID) -> dict[str, Any]:
        """Trigger background preparation/warmup of all scenarios for an account."""
        client = await self.get_client()
        response = await client.post(f"/api/v1/accounts/{account_id}/prepare")
        self._handle_response(response)
        return response.json()  # type: ignore[no-any-return]

    # =========================================================================
    # Payments & Scenarios API
    # =========================================================================
    async def list_scenarios(self) -> list[ScenarioRead]:
        """Fetch list of available payment scenarios."""
        client = await self.get_client()
        response = await client.get("/api/v1/payments/scenarios")
        self._handle_response(response)
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
        self._handle_response(response)
        return PaymentRead.model_validate(response.json())

    async def get_payment(self, payment_id: str | UUID) -> PaymentRead | None:
        """Get details of a payment by UUID."""
        client = await self.get_client()
        response = await client.get(f"/api/v1/payments/{payment_id}")
        if response.status_code == 404:
            return None
        self._handle_response(response)
        return PaymentRead.model_validate(response.json())

    async def mark_payment_paid(self, payment_id: str | UUID) -> PaymentRead:
        """Manually mark payment as PAID and release account."""
        client = await self.get_client()
        response = await client.post(f"/api/v1/payments/{payment_id}/paid")
        self._handle_response(response)
        return PaymentRead.model_validate(response.json())

    async def cancel_payment(self, payment_id: str | UUID) -> PaymentRead:
        """Cancel payment and release account."""
        client = await self.get_client()
        response = await client.post(f"/api/v1/payments/{payment_id}/cancel")
        self._handle_response(response)
        return PaymentRead.model_validate(response.json())

    async def release_all_accounts(self) -> ReleaseAccountsResponse:
        """Cancel all active pending payments and release all locked accounts."""
        client = await self.get_client()
        response = await client.post("/api/v1/payments/release-all-accounts")
        self._handle_response(response)
        return ReleaseAccountsResponse.model_validate(response.json())
