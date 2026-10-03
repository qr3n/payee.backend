"""
Unit and integration tests for Telegram accounts service and utilities.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession
from telethon import errors

from app.modules.accounts.device_profiles import generate_device_profile
from app.modules.accounts.models import AccountStatus, TelegramAccount
from app.modules.accounts.proxy_utils import parse_proxy_url
from app.modules.accounts.schemas import (
    TelegramAccountCreate,
    TelegramAccountUpdate,
)
from app.modules.accounts.service import (
    create_account,
    delete_account,
    get_account,
    list_accounts_paginated,
    update_account,
    verify_and_update_account,
)
from app.modules.accounts.telethon_checker import check_telegram_account_status
from app.shared.pagination import PageParams

TELETHON_CLIENT_PATH = "app.modules.accounts.telethon_checker.TelegramClient"


def test_device_profile_generation_automatic() -> None:
    """Test device profile generator fills all fields automatically."""
    profile = generate_device_profile()
    assert profile.device_model
    assert profile.system_version
    assert profile.app_version
    assert profile.lang_code == "ru"
    assert profile.system_lang_code == "ru-RU"


def test_device_profile_generation_preserves_custom_values() -> None:
    """Test device profile generator respects explicitly provided values."""
    profile = generate_device_profile(
        device_model="Custom Device X",
        system_version="CustomOS 1.0",
        app_version="99.9.9",
        lang_code="en",
        system_lang_code="en-US",
    )
    assert profile.device_model == "Custom Device X"
    assert profile.system_version == "CustomOS 1.0"
    assert profile.app_version == "99.9.9"
    assert profile.lang_code == "en"
    assert profile.system_lang_code == "en-US"


def test_parse_proxy_url_valid_socks5() -> None:
    """Test parsing socks5 proxy URL with authentication."""
    proxy = parse_proxy_url("socks5://user:secret@192.168.1.50:1080")
    assert proxy is not None
    assert proxy["proxy_type"] == "socks5"
    assert proxy["addr"] == "192.168.1.50"
    assert proxy["port"] == 1080
    assert proxy["username"] == "user"
    assert proxy["password"] == "secret"
    assert proxy["rdns"] is True


def test_parse_proxy_url_valid_http() -> None:
    """Test parsing http proxy URL without authentication."""
    proxy = parse_proxy_url("http://proxy.example.com:8080")
    assert proxy is not None
    assert proxy["proxy_type"] == "http"
    assert proxy["addr"] == "proxy.example.com"
    assert proxy["port"] == 8080
    assert "username" not in proxy
    assert "rdns" not in proxy


def test_parse_proxy_url_empty_returns_none() -> None:
    """Test empty or None proxy string returns None."""
    assert parse_proxy_url(None) is None
    assert parse_proxy_url("") is None
    assert parse_proxy_url("   ") is None


def test_parse_proxy_url_invalid_scheme() -> None:
    """Test unsupported proxy scheme raises ValueError."""
    with pytest.raises(ValueError, match="Unsupported proxy scheme"):
        parse_proxy_url("ftp://127.0.0.1:21")


def test_parse_proxy_url_invalid_format() -> None:
    """Test malformed proxy URL raises ValueError."""
    with pytest.raises(ValueError, match="Invalid proxy URL"):
        parse_proxy_url("not_a_valid_url")


@pytest.mark.asyncio
async def test_create_account_auto_generates_device_params(
    db_session: AsyncSession,
) -> None:
    """Test account creation automatically generates device attributes when omitted."""
    account_in = TelegramAccountCreate(
        title="Test Account 1",
        session_string="1ApWapzMBu7fake_session_string_for_testing",
    )
    account = await create_account(db_session, account_in)

    assert account.id is not None
    assert account.title == "Test Account 1"
    assert account.device_model != ""
    assert account.system_version != ""
    assert account.app_version != ""
    assert account.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_account_crud_lifecycle(db_session: AsyncSession) -> None:
    """Test full CRUD lifecycle for TelegramAccount entity."""
    # 1. Create
    account_in = TelegramAccountCreate(
        title="Lifecycle Account",
        phone="+12025550199",
        proxy_url="socks5://127.0.0.1:9050",
        session_string="1ApWapzMBu7fake_session_string_for_testing",
    )
    account = await create_account(db_session, account_in)
    account_id = account.id

    # 2. Get
    fetched = await get_account(db_session, account_id)
    assert fetched is not None
    assert fetched.title == "Lifecycle Account"
    assert fetched.phone == "+12025550199"

    # 3. List
    params = PageParams(page=1, size=10)
    items, total = await list_accounts_paginated(db_session, params)
    assert total >= 1
    assert any(a.id == account_id for a in items)

    # 4. Update
    updated = await update_account(
        db_session,
        fetched,
        TelegramAccountUpdate(title="Updated Title", status=AccountStatus.DISABLED),
    )
    assert updated.title == "Updated Title"
    assert updated.status == AccountStatus.DISABLED

    # 5. Delete
    await delete_account(db_session, updated)
    deleted = await get_account(db_session, account_id)
    assert deleted is None


@pytest.mark.asyncio
async def test_delete_account_with_associated_payments(
    db_session: AsyncSession,
) -> None:
    """Test that deleting an account disassociates payments cleanly."""
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    from app.modules.payments.models import Payment, PaymentStatus

    acc = TelegramAccount(
        title="Account With Payment",
        session_string=VALID_SESSION_STRING,
        device_model="Dev",
        system_version="Sys",
        app_version="App",
        status=AccountStatus.ACTIVE,
    )
    db_session.add(acc)
    await db_session.flush()

    payment = Payment(
        client_user_id="user_del_test",
        scenario_id="mock_bot",
        amount=Decimal("100.00"),
        account_id=acc.id,
        status=PaymentStatus.PENDING,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    db_session.add(payment)
    await db_session.flush()

    # Deleting account should succeed and set payment.account_id = None
    await delete_account(db_session, acc)
    assert await get_account(db_session, acc.id) is None

    await db_session.refresh(payment)
    assert payment.account_id is None
    assert payment.client_user_id == "user_del_test"


def _create_test_session_string() -> str:
    from telethon.crypto import AuthKey
    from telethon.sessions import StringSession

    s = StringSession()
    s.set_dc(2, "149.154.167.50", 443)
    s.auth_key = AuthKey(b"a" * 256)
    return str(s.save())


VALID_SESSION_STRING = _create_test_session_string()


@pytest.mark.asyncio
async def test_check_telegram_account_status_active() -> None:
    """Test status check when Telethon client connects and is authorized."""
    mock_me = AsyncMock()
    mock_me.id = 123456789
    mock_me.first_name = "Alice"
    mock_me.last_name = "Wonderland"
    mock_me.username = "alice_wonder"
    mock_me.phone = "12025550100"
    mock_me.premium = True

    with patch(TELETHON_CLIENT_PATH) as mock_client_cls:
        mock_instance = AsyncMock()
        mock_instance.connect = AsyncMock()
        mock_instance.is_user_authorized = AsyncMock(return_value=True)
        mock_instance.get_me = AsyncMock(return_value=mock_me)
        mock_instance.is_connected = MagicMock(return_value=True)
        mock_instance.disconnect = AsyncMock()
        mock_client_cls.return_value = mock_instance

        result = await check_telegram_account_status(VALID_SESSION_STRING)

        assert result.status == AccountStatus.ACTIVE
        assert result.is_authorized is True
        assert result.telegram_user_id == 123456789
        assert result.first_name == "Alice"
        assert result.is_premium is True
        mock_instance.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_telegram_account_status_revoked() -> None:
    """Test status check when session is unauthorized / revoked."""
    with patch(TELETHON_CLIENT_PATH) as mock_client_cls:
        mock_instance = AsyncMock()
        mock_instance.connect = AsyncMock()
        mock_instance.is_user_authorized = AsyncMock(return_value=False)
        mock_instance.is_connected = MagicMock(return_value=True)
        mock_instance.disconnect = AsyncMock()
        mock_client_cls.return_value = mock_instance

        result = await check_telegram_account_status(VALID_SESSION_STRING)

        assert result.status == AccountStatus.REVOKED
        assert result.is_authorized is False
        assert "not authorized" in (result.error or "")


@pytest.mark.asyncio
async def test_check_telegram_account_status_banned() -> None:
    """Test status check when UserDeactivatedBanError is raised."""
    with patch(TELETHON_CLIENT_PATH) as mock_client_cls:
        mock_instance = AsyncMock()
        mock_instance.connect = AsyncMock(
            side_effect=errors.UserDeactivatedBanError(request=None)
        )
        mock_instance.is_connected = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_instance

        result = await check_telegram_account_status(VALID_SESSION_STRING)

        assert result.status == AccountStatus.BANNED
        assert result.is_authorized is False
        assert "banned" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_check_telegram_account_status_flood_wait() -> None:
    """Test status check when FloodWaitError is raised."""
    with patch(TELETHON_CLIENT_PATH) as mock_client_cls:
        mock_instance = AsyncMock()
        err = errors.FloodWaitError(request=None)
        err.seconds = 120
        mock_instance.connect = AsyncMock(side_effect=err)
        mock_instance.is_connected = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_instance

        result = await check_telegram_account_status(VALID_SESSION_STRING)

        assert result.status == AccountStatus.FLOOD_WAIT
        assert result.flood_wait_seconds == 120


@pytest.mark.asyncio
async def test_check_telegram_account_status_invalid_format() -> None:
    """Test status check handles completely invalid session string gracefully."""
    result = await check_telegram_account_status("completely_invalid_session_format")
    assert result.status == AccountStatus.REVOKED
    assert result.is_authorized is False
    assert "Invalid session string" in (result.error or "")


@pytest.mark.asyncio
async def test_verify_and_update_account(db_session: AsyncSession) -> None:
    """Test verify_and_update_account updates database model attributes."""
    account = TelegramAccount(
        title="Checkable Account",
        session_string=VALID_SESSION_STRING,
        device_model="Pixel 8",
        system_version="Android 14",
        app_version="10.14.0",
        lang_code="ru",
        system_lang_code="ru-RU",
    )
    db_session.add(account)
    await db_session.flush()

    mock_me = AsyncMock()
    mock_me.id = 987654321
    mock_me.first_name = "Bob"
    mock_me.last_name = None
    mock_me.username = "bob_tg"
    mock_me.phone = "18005550199"
    mock_me.premium = False

    with patch(TELETHON_CLIENT_PATH) as mock_client_cls:
        mock_instance = AsyncMock()
        mock_instance.connect = AsyncMock()
        mock_instance.is_user_authorized = AsyncMock(return_value=True)
        mock_instance.get_me = AsyncMock(return_value=mock_me)
        mock_instance.is_connected = MagicMock(return_value=True)
        mock_instance.disconnect = AsyncMock()
        mock_client_cls.return_value = mock_instance

        updated_acc, check_resp = await verify_and_update_account(db_session, account)

        assert updated_acc.status == AccountStatus.ACTIVE
        assert updated_acc.telegram_user_id == 987654321
        assert updated_acc.username == "bob_tg"
        assert updated_acc.last_checked_at is not None
        assert check_resp.is_authorized is True
        assert check_resp.account_id == account.id
