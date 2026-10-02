"""
Telethon MTProto session verification and health check utility.
Validates session authorization, retrieves account info, and handles MTProto errors.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from telethon import TelegramClient, errors
from telethon.sessions import StringSession

from app.core.config import settings
from app.modules.accounts.models import AccountStatus
from app.modules.accounts.proxy_utils import parse_proxy_url


@dataclass(slots=True)
class AccountCheckResult:
    """Outcome of a Telegram MTProto account verification check."""

    status: AccountStatus
    is_authorized: bool
    telegram_user_id: int | None = None
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    phone: str | None = None
    is_premium: bool | None = None
    flood_wait_seconds: int | None = None
    error: str | None = None


async def check_telegram_account_status(
    session_string: str,
    api_id: int | None = None,
    api_hash: str | None = None,
    device_model: str = "PC 64bit",
    system_version: str = "Windows 11",
    app_version: str = "5.2.2 x64",
    system_lang_code: str = "ru-RU",
    lang_code: str = "ru",
    proxy_url: str | None = None,
    timeout: int = 15,
) -> AccountCheckResult:
    """
    Connect to Telegram via Telethon, check authorization status,
    and retrieve profile info. Always safely disconnects client upon completion.
    """
    resolved_api_id = api_id or settings.TELEGRAM_DEFAULT_API_ID
    resolved_api_hash = api_hash or settings.TELEGRAM_DEFAULT_API_HASH

    client: TelegramClient | None = None
    try:
        proxy_dict = parse_proxy_url(proxy_url) if proxy_url else None
        try:
            session = StringSession(session_string)
        except Exception as e:
            return AccountCheckResult(
                status=AccountStatus.REVOKED,
                is_authorized=False,
                error=f"Invalid session string format: {e}",
            )

        client = TelegramClient(
            session,
            api_id=resolved_api_id,
            api_hash=resolved_api_hash,
            proxy=proxy_dict,
            device_model=device_model,
            system_version=system_version,
            app_version=app_version,
            system_lang_code=system_lang_code,
            lang_code=lang_code,
            timeout=timeout,
            connection_retries=2,
            auto_reconnect=False,
        )

        await client.connect()

        if not await client.is_user_authorized():
            return AccountCheckResult(
                status=AccountStatus.REVOKED,
                is_authorized=False,
                error="Session is not authorized or has been revoked.",
            )

        me = await client.get_me()
        if me is None:
            return AccountCheckResult(
                status=AccountStatus.REVOKED,
                is_authorized=False,
                error="Failed to retrieve user profile (empty response).",
            )

        return AccountCheckResult(
            status=AccountStatus.ACTIVE,
            is_authorized=True,
            telegram_user_id=getattr(me, "id", None),
            first_name=getattr(me, "first_name", None),
            last_name=getattr(me, "last_name", None),
            username=getattr(me, "username", None),
            phone=getattr(me, "phone", None),
            is_premium=getattr(me, "premium", False),
        )

    except (
        errors.UserDeactivatedError,
        errors.UserDeactivatedBanError,
        errors.PhoneNumberBannedError,
    ) as e:
        return AccountCheckResult(
            status=AccountStatus.BANNED,
            is_authorized=False,
            error=f"Account is banned/deactivated: {e}",
        )

    except (
        errors.AuthKeyDuplicatedError,
        errors.SessionRevokedError,
        errors.SessionExpiredError,
        errors.AuthKeyUnregisteredError,
    ) as e:
        return AccountCheckResult(
            status=AccountStatus.REVOKED,
            is_authorized=False,
            error=f"Session revoked or duplicate key detected: {e}",
        )

    except errors.FloodWaitError as e:
        seconds = int(getattr(e, "seconds", 60))
        return AccountCheckResult(
            status=AccountStatus.FLOOD_WAIT,
            is_authorized=True,
            flood_wait_seconds=seconds,
            error=f"Flood wait required: {seconds} seconds.",
        )

    except Exception as e:
        return AccountCheckResult(
            status=AccountStatus.ERROR,
            is_authorized=False,
            error=f"Connection/MTProto error: {type(e).__name__}: {e}",
        )

    finally:
        try:
            if client is not None:
                is_conn = client.is_connected()
                if hasattr(is_conn, "__await__"):
                    is_conn = await is_conn
                if is_conn:
                    await client.disconnect()
        except Exception:
            pass


def calculate_flood_wait_until(seconds: int | None) -> datetime | None:
    """Calculate UTC expiration timestamp given flood wait duration in seconds."""
    if not seconds:
        return None
    return datetime.now(UTC) + timedelta(seconds=seconds)
