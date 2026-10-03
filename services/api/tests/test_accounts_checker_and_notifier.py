"""
Unit tests for Telegram accounts background health verification and admin alerts.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.modules.accounts.models import AccountStatus, TelegramAccount
from app.modules.accounts.notifier import (
    format_account_alert,
    get_target_admin_chat_ids,
    notify_status_change_if_needed,
    send_admin_notification,
)
from app.modules.accounts.service import check_all_accounts
from tests.test_accounts_service import VALID_SESSION_STRING


def _create_mock_account(
    status: AccountStatus = AccountStatus.ACTIVE,
    phone: str = "+79991234567",
    title: str = "Worker #1",
) -> TelegramAccount:
    return TelegramAccount(
        id=uuid4(),
        title=title,
        phone=phone,
        session_string=VALID_SESSION_STRING,
        device_model="Test Model",
        system_version="Linux 6.0",
        app_version="10.0",
        status=status,
        telegram_user_id=123456789,
        username="test_worker",
    )


def test_format_account_alert_variants() -> None:
    acc = _create_mock_account()

    # 1. REVOKED
    msg_revoked = format_account_alert(
        acc, AccountStatus.ACTIVE, AccountStatus.REVOKED, "Session terminated"
    )
    assert "Telegram-сессия отозвана" in msg_revoked
    assert "Worker #1" in msg_revoked
    assert "+79991234567" in msg_revoked
    assert "Session terminated" in msg_revoked

    # 2. BANNED
    msg_banned = format_account_alert(
        acc, AccountStatus.ACTIVE, AccountStatus.BANNED, "Phone banned"
    )
    assert "Telegram-аккаунт заблокирован" in msg_banned
    assert "Phone banned" in msg_banned

    # 3. FLOOD_WAIT
    acc.flood_wait_until = datetime.now(UTC) + timedelta(seconds=120)
    msg_flood = format_account_alert(
        acc, AccountStatus.ACTIVE, AccountStatus.FLOOD_WAIT, "Flood wait: 120s"
    )
    assert "Flood Wait" in msg_flood
    assert "Flood wait: 120s" in msg_flood

    # 4. ERROR
    msg_error = format_account_alert(
        acc, AccountStatus.ACTIVE, AccountStatus.ERROR, "Network timeout"
    )
    assert "Ошибка подключения" in msg_error
    assert "Network timeout" in msg_error

    # 5. RECOVERY
    msg_recovery = format_account_alert(
        acc, AccountStatus.REVOKED, AccountStatus.ACTIVE
    )
    assert "Telegram-сессия успешно восстановлена" in msg_recovery

    # 6. Unchanged active
    msg_same = format_account_alert(acc, AccountStatus.ACTIVE, AccountStatus.ACTIVE)
    assert msg_same == ""


@pytest.mark.asyncio
async def test_get_target_admin_chat_ids() -> None:
    mock_redis = AsyncMock()
    mock_redis.smembers.return_value = {b"999999", b"invalid"}

    with patch.object(settings, "ADMIN_CHAT_IDS", [111222]):
        chat_ids = await get_target_admin_chat_ids(mock_redis)
        assert 111222 in chat_ids
        assert 999999 in chat_ids


@pytest.mark.asyncio
async def test_send_admin_notification() -> None:
    with (
        patch.object(settings, "TELEGRAM_BOT_TOKEN", SecretStr("test_token")),
        patch.object(settings, "ADMIN_CHAT_IDS", [8227204801]),
        patch("httpx.AsyncClient.post") as mock_post,
    ):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        count = await send_admin_notification("<b>Test Alert</b>")
        assert count == 1
        assert mock_post.called
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["json"]["chat_id"] == 8227204801
        assert call_kwargs["json"]["text"] == "<b>Test Alert</b>"


@pytest.mark.asyncio
async def test_send_admin_notification_missing_credentials() -> None:
    with patch.object(settings, "TELEGRAM_BOT_TOKEN", None):
        count = await send_admin_notification("<b>Test Alert</b>")
        assert count == 0


@pytest.mark.asyncio
async def test_notify_status_change_deduplication() -> None:
    acc = _create_mock_account()
    stored_redis: dict[str, str] = {}

    mock_redis = AsyncMock()

    async def mock_get(key: str) -> bytes | None:
        val = stored_redis.get(key)
        return val.encode("utf-8") if val else None

    async def mock_set(key: str, val: str, **_kwargs: object) -> None:
        stored_redis[key] = val

    async def mock_delete(key: str) -> None:
        stored_redis.pop(key, None)

    mock_redis.get.side_effect = mock_get
    mock_redis.set.side_effect = mock_set
    mock_redis.delete.side_effect = mock_delete

    with (
        patch("app.modules.accounts.notifier.get_redis", return_value=mock_redis),
        patch(
            "app.modules.accounts.notifier.send_admin_notification", new=AsyncMock()
        ) as mock_send,
    ):
        # 1. First alert on REVOKED -> should notify
        notified = await notify_status_change_if_needed(
            acc, AccountStatus.ACTIVE, AccountStatus.REVOKED, "Unauthorized"
        )
        assert notified is True
        assert mock_send.call_count == 1

        # 2. Duplicate check on REVOKED -> should NOT notify
        notified = await notify_status_change_if_needed(
            acc, AccountStatus.REVOKED, AccountStatus.REVOKED, "Unauthorized"
        )
        assert notified is False
        assert mock_send.call_count == 1

        # 3. Restored to ACTIVE -> should notify recovery
        notified = await notify_status_change_if_needed(
            acc, AccountStatus.REVOKED, AccountStatus.ACTIVE
        )
        assert notified is True
        assert mock_send.call_count == 2
        assert f"account_alert_state:{acc.id}" not in stored_redis


@pytest.mark.asyncio
async def test_check_all_accounts_batch(db_session: AsyncSession) -> None:
    acc1 = TelegramAccount(
        title="Batch Acc 1",
        phone="+79991111111",
        session_string=VALID_SESSION_STRING,
        device_model="Dev1",
        system_version="OS1",
        app_version="1.0",
        status=AccountStatus.ACTIVE,
    )
    acc2 = TelegramAccount(
        title="Batch Acc 2",
        phone="+79992222222",
        session_string=VALID_SESSION_STRING,
        device_model="Dev2",
        system_version="OS2",
        app_version="1.0",
        status=AccountStatus.ACTIVE,
    )
    db_session.add(acc1)
    db_session.add(acc2)
    await db_session.flush()

    mock_me = AsyncMock()
    mock_me.id = 999
    mock_me.first_name = "Worker"
    mock_me.last_name = None
    mock_me.username = "worker_bot"
    mock_me.phone = "79991111111"
    mock_me.premium = False

    # Simulate client1 active, client2 revoked
    def create_mock_telethon(*_args: object, **kwargs: object) -> MagicMock:
        inst = MagicMock()
        inst.connect = AsyncMock()
        inst.disconnect = AsyncMock()
        inst.is_connected = MagicMock(return_value=True)

        # Distinguish by device_model
        if kwargs.get("device_model") == "Dev1":
            inst.is_user_authorized = AsyncMock(return_value=True)
            inst.get_me = AsyncMock(return_value=mock_me)
        else:
            inst.is_user_authorized = AsyncMock(return_value=False)
            inst.get_me = AsyncMock(return_value=None)
        return inst

    with (
        patch(
            "app.modules.accounts.telethon_checker.TelegramClient",
            side_effect=create_mock_telethon,
        ),
        patch(
            "app.modules.accounts.service.notify_status_change_if_needed",
            new=AsyncMock(),
        ) as mock_notify,
    ):
        counts = await check_all_accounts(db_session)
        assert counts["total"] >= 2
        assert counts["active"] >= 1
        assert counts["revoked"] >= 1
        assert mock_notify.called
