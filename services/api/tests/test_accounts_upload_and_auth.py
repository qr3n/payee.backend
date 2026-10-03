"""
Tests for session file upload (.session + .json) and interactive phone auth endpoints.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis

from app.modules.accounts.session_converter import (
    convert_sqlite_session_bytes_to_string,
    parse_client_json,
    parse_json_proxy,
)
from tests.test_accounts_service import VALID_SESSION_STRING

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "examples"


def test_parse_json_proxy() -> None:
    assert parse_json_proxy(None) is None
    assert parse_json_proxy("socks5://1.2.3.4:1080") == "socks5://1.2.3.4:1080"
    assert (
        parse_json_proxy("1.2.3.4:1080:user:pass") == "socks5://user:pass@1.2.3.4:1080"
    )
    assert (
        parse_json_proxy(
            {
                "type": "socks5",
                "host": "1.2.3.4",
                "port": 1080,
                "user": "u",
                "pass": "p",
            }
        )
        == "socks5://u:p@1.2.3.4:1080"
    )
    assert (
        parse_json_proxy(["socks5", "1.2.3.4", 1080, "u", "p"])
        == "socks5://u:p@1.2.3.4:1080"
    )


def create_synthetic_sqlite_session_bytes() -> bytes:
    """Generate a synthetic SQLite session with a random AuthKey for testing."""
    import os
    import tempfile
    from pathlib import Path

    from telethon.crypto import AuthKey
    from telethon.sessions import SQLiteSession

    with tempfile.TemporaryDirectory() as tmp_dir:
        base = os.path.join(tmp_dir, "test")
        session = SQLiteSession(base)
        session.set_dc(2, "149.154.167.50", 443)
        session.auth_key = AuthKey(data=os.urandom(256))
        session.save()
        session.close()
        return Path(f"{base}.session").read_bytes()


def test_parse_client_json_and_sqlite_session() -> None:
    json_path = EXAMPLES_DIR / "263161426.json"
    session_bytes = create_synthetic_sqlite_session_bytes()

    with open(json_path, "rb") as f:
        meta = parse_client_json(f.read())
    session_str = convert_sqlite_session_bytes_to_string(session_bytes)

    assert meta["api_id"] == 2496
    assert meta["api_hash"] == "8da85b0d5bfe62527e5b244c209159c3"
    assert "Macintosh" in meta["device_model"]
    assert "macOS" in meta["system_version"]
    assert meta["app_version"] == "12.0.29 A"
    assert len(session_str) > 100


@pytest.mark.asyncio
async def test_upload_account_api(client: AsyncClient) -> None:
    json_path = EXAMPLES_DIR / "263161426.json"
    session_bytes = create_synthetic_sqlite_session_bytes()

    with open(json_path, "rb") as f:
        json_bytes = f.read()

    files = {
        "session_file": (
            "263161426.session",
            session_bytes,
            "application/octet-stream",
        ),
        "json_file": ("263161426.json", json_bytes, "application/json"),
    }
    data = {
        "title": "Uploaded Test Account",
        "verify": "false",
    }

    response = await client.post("/api/v1/accounts/upload", files=files, data=data)
    assert response.status_code == 201
    resp_data = response.json()
    assert resp_data["title"] == "Uploaded Test Account"
    assert resp_data["api_id"] == 2496
    assert resp_data["app_version"] == "12.0.29 A"
    assert "id" in resp_data


@pytest.mark.asyncio
async def test_phone_auth_send_code_and_sign_in_flow(
    client: AsyncClient, fake_redis: Redis
) -> None:
    # 1. Mock send_code_request
    mock_sent_code = MagicMock()
    mock_sent_code.phone_code_hash = "mock_hash_12345"
    mock_sent_code.timeout = 120

    mock_telethon = AsyncMock()
    mock_telethon.connect.return_value = None
    mock_telethon.send_code_request.return_value = mock_sent_code
    mock_telethon.session = MagicMock()
    mock_telethon.session.save.return_value = VALID_SESSION_STRING
    mock_telethon.disconnect.return_value = None

    with (
        patch(
            "app.modules.accounts.phone_auth_service.TelegramClient",
            return_value=mock_telethon,
        ),
        patch(
            "app.modules.accounts.phone_auth_service.redis_client",
            fake_redis,
        ),
    ):
        send_code_resp = await client.post(
            "/api/v1/accounts/auth/send-code",
            json={
                "phone": "+79991234567",
                "title": "Phone Worker",
                "api_id": 2040,
                "api_hash": "b18441a1ff607e10a989891a5462e627",
            },
        )
        assert send_code_resp.status_code == 200
        code_data = send_code_resp.json()
        assert code_data["phone_code_hash"] == "mock_hash_12345"

        # 2. Mock sign_in
        mock_user = MagicMock()
        mock_user.id = 11223344
        mock_user.first_name = "Auth"
        mock_user.last_name = "Tester"
        mock_user.username = "auth_tester"
        mock_user.premium = True

        mock_telethon.sign_in.return_value = mock_user
        mock_telethon.get_me.return_value = mock_user

        sign_in_resp = await client.post(
            "/api/v1/accounts/auth/sign-in",
            json={
                "phone_code_hash": "mock_hash_12345",
                "code": "12345",
            },
        )
        assert sign_in_resp.status_code == 200
        sign_data = sign_in_resp.json()
        assert sign_data["status"] == "success"
        assert sign_data["account"]["telegram_user_id"] == 11223344
        assert sign_data["account"]["username"] == "auth_tester"
