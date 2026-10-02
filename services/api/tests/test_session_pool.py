"""
Unit tests for TelegramSessionPool.
Verifies connection reuse, keep-alive touch, idle eviction, and clean shutdown.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import AppException
from app.modules.accounts.models import AccountStatus, TelegramAccount
from app.modules.accounts.session_pool import TelegramSessionPool
from tests.test_accounts_service import VALID_SESSION_STRING


def make_account(session_string: str = VALID_SESSION_STRING) -> TelegramAccount:
    return TelegramAccount(
        id=uuid4(),
        title="Test Account",
        phone="+1234567890",
        session_string=session_string,
        device_model="PC 64bit",
        system_version="Windows 11",
        app_version="5.2.2 x64",
        status=AccountStatus.ACTIVE,
    )


@pytest.mark.asyncio
async def test_session_pool_creates_and_reuses_connection() -> None:
    pool = TelegramSessionPool(idle_ttl=60.0, auto_cleanup=False)
    account = make_account()

    mock_client = AsyncMock()
    mock_client.is_connected = MagicMock(return_value=False)
    mock_client.connect = AsyncMock()
    mock_client.is_user_authorized = AsyncMock(return_value=True)

    with patch(
        "app.modules.accounts.session_pool.create_telethon_client",
        return_value=mock_client,
    ) as mock_create:
        # First call: creates and connects
        client1 = await pool.get_connected_client(account)
        assert client1 == mock_client
        mock_create.assert_called_once_with(account)
        mock_client.connect.assert_awaited_once()

        # Simulate connection is now active
        mock_client.is_connected.return_value = True

        # Second call: reuses existing instance without calling connect again
        client2 = await pool.get_connected_client(account)
        assert client2 == mock_client
        assert mock_create.call_count == 1
        assert mock_client.connect.await_count == 1

    await pool.close_all()


@pytest.mark.asyncio
async def test_session_pool_unauthorized_account_raises_and_evicts() -> None:
    pool = TelegramSessionPool(idle_ttl=60.0, auto_cleanup=False)
    account = make_account()

    mock_client = AsyncMock()
    mock_client.is_connected = MagicMock(return_value=False)
    mock_client.connect = AsyncMock()
    mock_client.is_user_authorized = AsyncMock(return_value=False)
    mock_client.disconnect = AsyncMock()

    with patch(
        "app.modules.accounts.session_pool.create_telethon_client",
        return_value=mock_client,
    ):
        with pytest.raises(AppException) as exc_info:
            await pool.get_connected_client(account)

        assert exc_info.value.code == "ACCOUNT_UNAUTHORIZED"
        # Account should not be in pool
        assert account.id not in pool._sessions

    await pool.close_all()


@pytest.mark.asyncio
async def test_session_pool_recreates_client_on_session_string_change() -> None:
    pool = TelegramSessionPool(idle_ttl=60.0, auto_cleanup=False)
    account1 = make_account("1BVtsOLQ=first_session")
    account2 = make_account("1BVtsOLQ=second_session")
    account2.id = account1.id  # Same account ID, updated session string

    client_mock_1 = AsyncMock()
    client_mock_1.is_connected = MagicMock(return_value=True)
    client_mock_1.disconnect = AsyncMock()
    client_mock_1.is_user_authorized = AsyncMock(return_value=True)

    client_mock_2 = AsyncMock()
    client_mock_2.is_connected = MagicMock(return_value=False)
    client_mock_2.connect = AsyncMock()
    client_mock_2.is_user_authorized = AsyncMock(return_value=True)

    with patch(
        "app.modules.accounts.session_pool.create_telethon_client",
        side_effect=[client_mock_1, client_mock_2],
    ):
        c1 = await pool.get_connected_client(account1)
        assert c1 == client_mock_1

        c2 = await pool.get_connected_client(account2)
        assert c2 == client_mock_2
        client_mock_1.disconnect.assert_awaited_once()
        client_mock_2.connect.assert_awaited_once()

    await pool.close_all()


@pytest.mark.asyncio
async def test_session_pool_close_account_and_close_all() -> None:
    pool = TelegramSessionPool(idle_ttl=60.0, auto_cleanup=False)
    acc1 = make_account()
    acc2 = make_account()

    client1 = AsyncMock()
    client1.is_connected = MagicMock(return_value=True)
    client1.is_user_authorized = AsyncMock(return_value=True)
    client1.disconnect = AsyncMock()

    client2 = AsyncMock()
    client2.is_connected = MagicMock(return_value=True)
    client2.is_user_authorized = AsyncMock(return_value=True)
    client2.disconnect = AsyncMock()

    with patch(
        "app.modules.accounts.session_pool.create_telethon_client",
        side_effect=[client1, client2],
    ):
        await pool.get_connected_client(acc1)
        await pool.get_connected_client(acc2)

        # Close acc1 specifically
        await pool.close_account(acc1.id)
        client1.disconnect.assert_awaited_once()
        assert acc1.id not in pool._sessions
        assert acc2.id in pool._sessions

        # Close all
        await pool.close_all()
        client2.disconnect.assert_awaited_once()
        assert len(pool._sessions) == 0


@pytest.mark.asyncio
async def test_session_pool_idle_eviction() -> None:
    pool = TelegramSessionPool(idle_ttl=10.0, auto_cleanup=False)
    acc = make_account()

    client = AsyncMock()
    client.is_connected = MagicMock(return_value=True)
    client.is_user_authorized = AsyncMock(return_value=True)
    client.disconnect = AsyncMock()

    with patch(
        "app.modules.accounts.session_pool.create_telethon_client",
        return_value=client,
    ):
        await pool.get_connected_client(acc)
        assert acc.id in pool._sessions

        # Simulate idle by backdating last_used
        pool._sessions[acc.id].last_used -= 20.0

        evicted = await pool.evict_idle_sessions()
        assert evicted == 1
        assert acc.id not in pool._sessions
        client.disconnect.assert_awaited_once()
