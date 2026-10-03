"""
Tests for administrative API key authentication (R01).
"""

from unittest.mock import patch

import pytest
from httpx import AsyncClient
from pydantic import SecretStr


@pytest.mark.asyncio
async def test_admin_endpoints_require_api_key_when_configured(
    client: AsyncClient,
) -> None:
    secret_key = "super_secret_admin_key"
    with patch("app.api.deps.settings.ADMIN_API_KEY", SecretStr(secret_key)):
        # 1. Accounts list without key -> 401
        resp = await client.get("/api/v1/accounts/")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "UNAUTHORIZED"

        # 2. Accounts list with invalid key -> 401
        resp = await client.get("/api/v1/accounts/", headers={"X-API-Key": "wrong_key"})
        assert resp.status_code == 401

        # 3. Accounts list with correct key -> 200
        resp = await client.get("/api/v1/accounts/", headers={"X-API-Key": secret_key})
        assert resp.status_code == 200

        # 4. Release all accounts without key -> 401
        resp = await client.post("/api/v1/payments/release-all-accounts")
        assert resp.status_code == 401

        # 5. Release all accounts with correct key -> 200
        resp = await client.post(
            "/api/v1/payments/release-all-accounts",
            headers={"X-API-Key": secret_key},
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_production_mode_requires_api_key(
    client: AsyncClient,
) -> None:
    with (
        patch("app.api.deps.settings.ADMIN_API_KEY", None),
        patch("app.api.deps.settings.ENVIRONMENT", "production"),
    ):
        resp = await client.get("/api/v1/accounts/")
        assert resp.status_code == 401
        assert "production" in resp.json()["error"]["message"]
