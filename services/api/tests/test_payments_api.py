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
    """
    Test POST /api/v1/payments/ returns 409 when all accounts are generation-locked.
    """
    from app.modules.payments.scenarios import acquire_account_generation_lock

    # Only 1 account in DB
    acc = await _create_test_account(db_session, "Solo API Account")

    # 1st user creates payment successfully
    p1_resp = await client.post(
        "/api/v1/payments/",
        json={
            "client_user_id": "payer_1",
            "scenario_id": "mock_bot",
            "amount": "100.00",
        },
    )
    assert p1_resp.status_code == 201

    # When all accounts are locked for active generation:
    await acquire_account_generation_lock(acc.id)

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


@pytest.mark.asyncio
async def test_create_payment_race_api_fire_and_stream(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Test POST /api/v1/payments/race returns JSON, then GET stream yields SSE."""
    await _create_test_account(db_session, "Race Account 1")

    payload = {
        "client_user_id": "race_payer_1",
        "amount": "100.00",
        "currency": "RUB",
        "timeout_sec": 10.0,
    }

    # Step 1: Fire — should return JSON with batch_id
    fire_response = await client.post("/api/v1/payments/race", json=payload)
    assert fire_response.status_code == 200
    fire_data = fire_response.json()
    assert "batch_id" in fire_data
    assert fire_data["status"] == "running"
    assert isinstance(fire_data["scenarios"], list)

    batch_id = fire_data["batch_id"]

    # Allow background runner a moment to complete
    import asyncio

    await asyncio.sleep(2)

    # Step 2: Subscribe — should return SSE with replay
    stream_response = await client.get(f"/api/v1/payments/race/{batch_id}/stream")
    assert stream_response.status_code == 200
    assert "text/event-stream" in stream_response.headers.get("content-type", "")

    events = stream_response.text.strip().split("\n\n")
    event_types = []
    for ev in events:
        lines = ev.split("\n")
        for line in lines:
            if line.startswith("event: "):
                event_types.append(line.replace("event: ", "").strip())

    assert "started" in event_types
    assert "done" in event_types


@pytest.mark.asyncio
async def test_release_all_accounts_api(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Test POST /api/v1/payments/release-all-accounts frees locked accounts."""
    await _create_test_account(db_session, "Release Test Acc")

    # Create pending payment
    p_resp = await client.post(
        "/api/v1/payments/",
        json={
            "client_user_id": "test_release_user",
            "scenario_id": "mock_bot",
            "amount": "150.00",
        },
    )
    assert p_resp.status_code == 201

    # Call release-all-accounts
    rel_resp = await client.post("/api/v1/payments/release-all-accounts")
    assert rel_resp.status_code == 200
    data = rel_resp.json()
    assert data["cancelled_payments_count"] >= 1
    assert data["released_accounts_count"] >= 1
