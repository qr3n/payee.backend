"""
Integration tests for Payments HTTP API endpoints.
"""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel.ext.asyncio.session import AsyncSession

from tests.test_payments_service import _create_test_account


@pytest.mark.asyncio
async def test_list_scenarios_api(client: AsyncClient) -> None:
    """Test GET /api/v1/payments/scenarios lists registered scenarios."""
    response = await client.get("/api/v1/payments/scenarios")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert any(s["scenario_id"] == "mock_bot" for s in data)


@pytest.mark.asyncio
async def test_create_payment_api_success(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Test POST /api/v1/payments/ creates payment and reserves account."""
    await _create_test_account(db_session, "API Payment Account")

    payload = {
        "client_user_id": "api_payer_1",
        "scenario_id": "mock_bot",
        "amount": "250.00",
        "currency": "RUB",
        "meta": {"order_ref": "REF-001"},
    }
    response = await client.post("/api/v1/payments/", json=payload)
    assert response.status_code == 201

    data = response.json()
    assert data["client_user_id"] == "api_payer_1"
    assert data["scenario_id"] == "mock_bot"
    assert data["amount"] == "250.00"
    assert data["status"] == "pending"
    assert "https://t.me/" in data["payment_link"]
    assert "account_id" in data
    assert "expires_at" in data


@pytest.mark.asyncio
async def test_create_payment_pool_exhausted_returns_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Test POST /api/v1/payments/ returns 409 when all accounts are reserved."""
    # Only 1 account in DB
    await _create_test_account(db_session, "Solo API Account")

    # 1st user takes the account
    p1_resp = await client.post(
        "/api/v1/payments/",
        json={
            "client_user_id": "payer_1",
            "scenario_id": "mock_bot",
            "amount": "100.00",
        },
    )
    assert p1_resp.status_code == 201

    # 2nd user tries to create payment -> 409 NO_ACCOUNTS_AVAILABLE
    p2_resp = await client.post(
        "/api/v1/payments/",
        json={
            "client_user_id": "payer_2",
            "scenario_id": "mock_bot",
            "amount": "200.00",
        },
    )
    assert p2_resp.status_code == 409
    error_data = p2_resp.json()
    assert error_data["error"]["code"] == "NO_ACCOUNTS_AVAILABLE"


@pytest.mark.asyncio
async def test_same_user_reuses_account_and_cancels_previous(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Test same user calling create payment cancels prior and reuses account."""
    await _create_test_account(db_session, "Reused API Account")

    # Payment 1
    resp1 = await client.post(
        "/api/v1/payments/",
        json={
            "client_user_id": "repeat_buyer",
            "scenario_id": "mock_bot",
            "amount": "100.00",
        },
    )
    p1_id = resp1.json()["id"]
    acc_id = resp1.json()["account_id"]

    # Payment 2 from same user
    resp2 = await client.post(
        "/api/v1/payments/",
        json={
            "client_user_id": "repeat_buyer",
            "scenario_id": "mock_bot",
            "amount": "300.00",
        },
    )
    assert resp2.status_code == 201
    assert resp2.json()["account_id"] == acc_id

    # Verify Payment 1 was cancelled
    check_p1 = await client.get(f"/api/v1/payments/{p1_id}")
    assert check_p1.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_mark_payment_paid_and_cancelled_api(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Test /paid and /cancel endpoints reactively update status."""
    await _create_test_account(db_session, "Callback Test Account")

    create_resp = await client.post(
        "/api/v1/payments/",
        json={
            "client_user_id": "callback_user",
            "scenario_id": "mock_bot",
            "amount": "500.00",
        },
    )
    payment_id = create_resp.json()["id"]

    # 1. Mark as paid
    paid_resp = await client.post(
        f"/api/v1/payments/{payment_id}/paid",
        json={"external_transaction_id": "EXT-12345"},
    )
    assert paid_resp.status_code == 200
    assert paid_resp.json()["status"] == "paid"
    assert paid_resp.json()["paid_at"] is not None

    # 2. Test cancel endpoint on another payment
    create2_resp = await client.post(
        "/api/v1/payments/",
        json={
            "client_user_id": "cancel_user",
            "scenario_id": "mock_bot",
            "amount": "120.00",
        },
    )
    p2_id = create2_resp.json()["id"]
    cancel_resp = await client.post(f"/api/v1/payments/{p2_id}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"
    assert cancel_resp.json()["cancelled_at"] is not None


@pytest.mark.asyncio
async def test_get_payment_not_found(client: AsyncClient) -> None:
    """Test GET /api/v1/payments/{unknown_id} returns 404."""
    unknown_id = str(uuid4())
    resp = await client.get(f"/api/v1/payments/{unknown_id}")
    assert resp.status_code == 404
