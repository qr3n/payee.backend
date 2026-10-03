"""
Integration tests for Telegram Accounts HTTP API endpoints.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient

from tests.test_accounts_service import VALID_SESSION_STRING

TELETHON_CLIENT_PATH = "app.modules.accounts.telethon_checker.TelegramClient"


@pytest.mark.asyncio
async def test_create_account_api_success(client: AsyncClient) -> None:
    """Test POST /api/v1/accounts successfully creates an account."""
    payload = {
        "title": "API Test Account",
        "session_string": VALID_SESSION_STRING,
        "phone": "+79998887766",
        "proxy_url": "socks5://proxyuser:proxypass@10.0.0.1:1080",
    }
    response = await client.post("/api/v1/accounts/", json=payload)
    assert response.status_code == 201

    data = response.json()
    assert data["title"] == "API Test Account"
    assert data["phone"] == "+79998887766"
    assert data["proxy_url"] == "socks5://proxyuser:proxypass@10.0.0.1:1080"
    # Verify auto-generation of device info
    assert data["device_model"] != ""
    assert data["system_version"] != ""
    assert data["app_version"] != ""
    assert data["status"] == "active"
    assert "id" in data


@pytest.mark.asyncio
async def test_create_account_api_invalid_proxy(client: AsyncClient) -> None:
    """Test POST /api/v1/accounts with invalid proxy returns 422."""
    payload = {
        "title": "Bad Proxy Account",
        "session_string": VALID_SESSION_STRING,
        "proxy_url": "invalid_scheme://host:port",
    }
    response = await client.post("/api/v1/accounts/", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_and_get_account_api(client: AsyncClient) -> None:
    """Test GET /api/v1/accounts and GET /api/v1/accounts/{id}."""
    create_payload = {
        "title": "Listable Account",
        "session_string": VALID_SESSION_STRING,
    }
    create_resp = await client.post("/api/v1/accounts/", json=create_payload)
    assert create_resp.status_code == 201
    account_id = create_resp.json()["id"]

    # 1. List accounts
    list_resp = await client.get("/api/v1/accounts/")
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert list_data["total"] >= 1
    assert any(acc["id"] == account_id for acc in list_data["items"])

    # 2. Get specific account
    get_resp = await client.get(f"/api/v1/accounts/{account_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == account_id
    assert get_resp.json()["title"] == "Listable Account"


@pytest.mark.asyncio
async def test_get_account_not_found(client: AsyncClient) -> None:
    """Test GET /api/v1/accounts/{id} returns 404 for unknown ID."""
    unknown_id = str(uuid4())
    response = await client.get(f"/api/v1/accounts/{unknown_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_account_api(client: AsyncClient) -> None:
    """Test PATCH /api/v1/accounts/{id} updates fields."""
    create_resp = await client.post(
        "/api/v1/accounts/",
        json={"title": "To Update", "session_string": VALID_SESSION_STRING},
    )
    account_id = create_resp.json()["id"]

    update_payload = {
        "title": "Updated Title via API",
        "proxy_url": "http://127.0.0.1:8080",
        "status": "disabled",
    }
    patch_resp = await client.patch(
        f"/api/v1/accounts/{account_id}", json=update_payload
    )
    assert patch_resp.status_code == 200
    data = patch_resp.json()
    assert data["title"] == "Updated Title via API"
    assert data["proxy_url"] == "http://127.0.0.1:8080"
    assert data["status"] == "disabled"


@pytest.mark.asyncio
async def test_delete_account_api(client: AsyncClient) -> None:
    """Test DELETE /api/v1/accounts/{id} removes account."""
    create_resp = await client.post(
        "/api/v1/accounts/",
        json={"title": "To Delete", "session_string": VALID_SESSION_STRING},
    )
    account_id = create_resp.json()["id"]

    delete_resp = await client.delete(f"/api/v1/accounts/{account_id}")
    assert delete_resp.status_code == 204

    get_resp = await client.get(f"/api/v1/accounts/{account_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_check_account_status_api(client: AsyncClient) -> None:
    """Test POST /api/v1/accounts/{id}/check endpoint."""
    create_resp = await client.post(
        "/api/v1/accounts/",
        json={"title": "To Check", "session_string": VALID_SESSION_STRING},
    )
    account_id = create_resp.json()["id"]

    mock_me = AsyncMock()
    mock_me.id = 555666777
    mock_me.first_name = "ApiWorker"
    mock_me.last_name = None
    mock_me.username = "api_worker_bot"
    mock_me.phone = "79991112233"
    mock_me.premium = True

    with patch(TELETHON_CLIENT_PATH) as mock_client_cls:
        mock_instance = AsyncMock()
        mock_instance.connect = AsyncMock()
        mock_instance.is_user_authorized = AsyncMock(return_value=True)
        mock_instance.get_me = AsyncMock(return_value=mock_me)
        mock_instance.is_connected = MagicMock(return_value=True)
        mock_instance.disconnect = AsyncMock()
        mock_client_cls.return_value = mock_instance

        check_resp = await client.post(f"/api/v1/accounts/{account_id}/check")
        assert check_resp.status_code == 200
        data = check_resp.json()
        assert data["account_id"] == account_id
        assert data["status"] == "active"
        assert data["is_authorized"] is True
        assert data["telegram_user_id"] == 555666777
        assert data["username"] == "api_worker_bot"
        assert data["is_premium"] is True


@pytest.mark.asyncio
async def test_check_all_accounts_api(client: AsyncClient) -> None:
    """Test POST /api/v1/accounts/check-all endpoint."""
    with patch(
        "app.modules.accounts.service.check_all_accounts",
        new=AsyncMock(
            return_value={
                "total": 3,
                "active": 2,
                "revoked": 1,
                "banned": 0,
                "flood_wait": 0,
                "error": 0,
            }
        ),
    ):
        response = await client.post("/api/v1/accounts/check-all")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert data["active"] == 2
        assert data["revoked"] == 1
        assert data["banned"] == 0


@pytest.mark.asyncio
async def test_prepare_account_endpoint(
    client: AsyncClient,
) -> None:
    """Test POST /api/v1/accounts/{id}/prepare endpoint."""
    with patch(
        "app.modules.accounts.router.dispatch_account_scenarios_warmup",
        new=AsyncMock(),
    ) as mock_dispatch:
        # Create account first
        payload = {
            "title": "Prepare Endpoint Test",
            "session_string": VALID_SESSION_STRING,
            "phone": "+79997776655",
        }
        create_resp = await client.post("/api/v1/accounts/", json=payload)
        assert create_resp.status_code == 201
        account_id = create_resp.json()["id"]

        # Call prepare endpoint
        resp = await client.post(f"/api/v1/accounts/{account_id}/prepare")
        assert resp.status_code == 200
        assert resp.json()["account_id"] == account_id
        assert "dispatched" in resp.json()["message"]
        mock_dispatch.assert_called()
