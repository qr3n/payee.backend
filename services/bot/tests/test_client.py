import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from bot.client.api import ApiClient
from bot.client.schemas import (
    HealthCheckResponse,
    PaginatedResponse,
    PaymentRead,
    ReadinessResponse,
    ScenarioRead,
    TelegramAccountCheckResponse,
    TelegramAccountRead,
)


@pytest.mark.asyncio
async def test_client_get_health() -> None:
    now_str = datetime.now(UTC).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "service": "FastAPI Service",
                "version": "0.1.0",
                "environment": "test",
                "timestamp": now_str,
            },
        )

    transport = httpx.MockTransport(handler)
    api_client = ApiClient(base_url="http://test-server")
    api_client._client = httpx.AsyncClient(
        transport=transport, base_url="http://test-server"
    )

    try:
        health = await api_client.get_health()
        assert isinstance(health, HealthCheckResponse)
        assert health.status == "ok"
        assert health.service == "FastAPI Service"
    finally:
        await api_client.close()


@pytest.mark.asyncio
async def test_client_get_readiness() -> None:
    now_str = datetime.now(UTC).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ready"
        return httpx.Response(
            200,
            json={
                "status": "ready",
                "database": True,
                "redis": True,
                "timestamp": now_str,
            },
        )

    transport = httpx.MockTransport(handler)
    api_client = ApiClient(base_url="http://test-server")
    api_client._client = httpx.AsyncClient(
        transport=transport, base_url="http://test-server"
    )

    try:
        readiness = await api_client.get_readiness()
        assert isinstance(readiness, ReadinessResponse)
        assert readiness.status == "ready"
        assert readiness.database is True
        assert readiness.redis is True
    finally:
        await api_client.close()


@pytest.mark.asyncio
async def test_client_list_accounts() -> None:
    acc_id = str(uuid4())
    now_str = datetime.now(UTC).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/accounts/"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": acc_id,
                        "title": "Worker 1",
                        "phone": "+1234567890",
                        "proxy_url": None,
                        "status": "active",
                        "device_model": "iPhone 15",
                        "system_version": "iOS 17.5",
                        "app_version": "10.14.0",
                        "telegram_user_id": 999888,
                        "username": "worker_one",
                        "created_at": now_str,
                        "updated_at": now_str,
                    }
                ],
                "total": 1,
                "page": 1,
                "size": 50,
                "pages": 1,
            },
        )

    transport = httpx.MockTransport(handler)
    api_client = ApiClient(base_url="http://test-server")
    api_client._client = httpx.AsyncClient(
        transport=transport, base_url="http://test-server"
    )

    try:
        paginated = await api_client.list_accounts()
        assert isinstance(paginated, PaginatedResponse)
        assert paginated.total == 1
        assert len(paginated.items) == 1
        assert paginated.items[0].title == "Worker 1"
        assert paginated.items[0].username == "worker_one"
    finally:
        await api_client.close()


@pytest.mark.asyncio
async def test_client_get_account() -> None:
    acc_id = str(uuid4())
    now_str = datetime.now(UTC).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/v1/accounts/{acc_id}":
            return httpx.Response(
                200,
                json={
                    "id": acc_id,
                    "title": "Worker 1",
                    "status": "active",
                    "device_model": "Pixel 8",
                    "system_version": "Android 14",
                    "app_version": "10.14.0",
                    "created_at": now_str,
                    "updated_at": now_str,
                },
            )
        return httpx.Response(404, json={"detail": "Not found"})

    transport = httpx.MockTransport(handler)
    api_client = ApiClient(base_url="http://test-server")
    api_client._client = httpx.AsyncClient(
        transport=transport, base_url="http://test-server"
    )

    try:
        acc = await api_client.get_account(acc_id)
        assert acc is not None
        assert str(acc.id) == acc_id
        assert acc.title == "Worker 1"

        missing = await api_client.get_account(uuid4())
        assert missing is None
    finally:
        await api_client.close()


@pytest.mark.asyncio
async def test_client_create_account() -> None:
    acc_id = str(uuid4())
    now_str = datetime.now(UTC).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/accounts/"
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["title"] == "New Session"
        return httpx.Response(
            201,
            json={
                "id": acc_id,
                "title": body["title"],
                "status": "pending",
                "device_model": "Desktop",
                "system_version": "Windows 11",
                "app_version": "5.1.0",
                "created_at": now_str,
                "updated_at": now_str,
            },
        )

    transport = httpx.MockTransport(handler)
    api_client = ApiClient(base_url="http://test-server")
    api_client._client = httpx.AsyncClient(
        transport=transport, base_url="http://test-server"
    )

    try:
        acc = await api_client.create_account(
            title="New Session",
            session_string="1ApWqtestvalidsessionstringhere...",
        )
        assert isinstance(acc, TelegramAccountRead)
        assert acc.title == "New Session"
    finally:
        await api_client.close()


@pytest.mark.asyncio
async def test_client_check_and_delete_account() -> None:
    acc_id = str(uuid4())
    now_str = datetime.now(UTC).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/v1/accounts/{acc_id}/check":
            return httpx.Response(
                200,
                json={
                    "account_id": acc_id,
                    "status": "active",
                    "is_authorized": True,
                    "telegram_user_id": 123456,
                    "username": "checked_user",
                    "checked_at": now_str,
                },
            )
        if (
            request.url.path == f"/api/v1/accounts/{acc_id}"
            and request.method == "DELETE"
        ):
            return httpx.Response(204)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    api_client = ApiClient(base_url="http://test-server")
    api_client._client = httpx.AsyncClient(
        transport=transport, base_url="http://test-server"
    )

    try:
        check_res = await api_client.check_account(acc_id)
        assert isinstance(check_res, TelegramAccountCheckResponse)
        assert check_res.is_authorized is True
        assert check_res.username == "checked_user"

        await api_client.delete_account(acc_id)
    finally:
        await api_client.close()


@pytest.mark.asyncio
async def test_client_payments_and_scenarios() -> None:
    pay_id = str(uuid4())
    acc_id = str(uuid4())
    now_str = datetime.now(UTC).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/payments/scenarios":
            return httpx.Response(
                200,
                json=[
                    {
                        "scenario_id": "starslly_bot",
                        "name": "@starslly_bot",
                        "description": "Buy Telegram Stars",
                    }
                ],
            )
        if request.url.path == "/api/v1/payments/" and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": pay_id,
                    "client_user_id": "user123",
                    "account_id": acc_id,
                    "scenario_id": "starslly_bot",
                    "amount": "495.00",
                    "currency": "RUB",
                    "status": "pending",
                    "payment_link": "https://t.me/$invoice123",
                    "expires_at": now_str,
                    "meta": {"calculated_stars": 300},
                    "created_at": now_str,
                    "updated_at": now_str,
                },
            )
        if request.url.path == f"/api/v1/payments/{pay_id}/paid":
            return httpx.Response(
                200,
                json={
                    "id": pay_id,
                    "client_user_id": "user123",
                    "account_id": acc_id,
                    "scenario_id": "starslly_bot",
                    "amount": "495.00",
                    "currency": "RUB",
                    "status": "paid",
                    "payment_link": "https://t.me/$invoice123",
                    "expires_at": now_str,
                    "paid_at": now_str,
                    "created_at": now_str,
                    "updated_at": now_str,
                },
            )
        if request.url.path == f"/api/v1/payments/{pay_id}/cancel":
            return httpx.Response(
                200,
                json={
                    "id": pay_id,
                    "client_user_id": "user123",
                    "account_id": acc_id,
                    "scenario_id": "starslly_bot",
                    "amount": "495.00",
                    "currency": "RUB",
                    "status": "cancelled",
                    "payment_link": "https://t.me/$invoice123",
                    "expires_at": now_str,
                    "cancelled_at": now_str,
                    "created_at": now_str,
                    "updated_at": now_str,
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    api_client = ApiClient(base_url="http://test-server")
    api_client._client = httpx.AsyncClient(
        transport=transport, base_url="http://test-server"
    )

    try:
        scenarios = await api_client.list_scenarios()
        assert len(scenarios) == 1
        assert isinstance(scenarios[0], ScenarioRead)
        assert scenarios[0].scenario_id == "starslly_bot"

        payment = await api_client.create_payment(
            client_user_id="user123",
            amount=Decimal("495"),
            scenario_id="starslly_bot",
        )
        assert isinstance(payment, PaymentRead)
        assert str(payment.id) == pay_id
        assert payment.payment_link == "https://t.me/$invoice123"

        paid = await api_client.mark_payment_paid(pay_id)
        assert paid.status == "paid"

        cancelled = await api_client.cancel_payment(pay_id)
        assert cancelled.status == "cancelled"
    finally:
        await api_client.close()
