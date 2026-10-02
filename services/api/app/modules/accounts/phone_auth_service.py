"""
Service for interactive Telegram MTProto phone authorization flow.
Handles phone code requests, SMS/code verification, and 2FA password sign-in.
Maintains ephemeral connection state in Redis.
"""

import json
from datetime import UTC, datetime
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession
from telethon import TelegramClient, errors
from telethon.sessions import StringSession

from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logging import get_logger
from app.core.redis import redis_client
from app.modules.accounts.device_profiles import generate_device_profile
from app.modules.accounts.models import AccountStatus, TelegramAccount
from app.modules.accounts.proxy_utils import parse_proxy_url

logger = get_logger(__name__)

REDIS_PHONE_AUTH_PREFIX = "tg_phone_auth:"
REDIS_PHONE_AUTH_TTL = 600  # 10 minutes


async def request_phone_code(
    phone: str,
    title: str | None = None,
    api_id: int | None = None,
    api_hash: str | None = None,
    proxy_url: str | None = None,
) -> dict[str, Any]:
    """
    Step 1: Initiate MTProto connection and request Telegram confirmation code.
    Saves temporary session state to Redis for the verification step.
    """
    clean_phone = phone.strip().replace(" ", "").replace("-", "")
    if not clean_phone.startswith("+"):
        clean_phone = f"+{clean_phone}"

    resolved_api_id = api_id or settings.TELEGRAM_DEFAULT_API_ID
    resolved_api_hash = api_hash or settings.TELEGRAM_DEFAULT_API_HASH

    profile = generate_device_profile()
    proxy_dict = parse_proxy_url(proxy_url) if proxy_url else None

    client = TelegramClient(
        StringSession(),
        api_id=resolved_api_id,
        api_hash=resolved_api_hash,
        proxy=proxy_dict,
        device_model=profile.device_model,
        system_version=profile.system_version,
        app_version=profile.app_version,
        system_lang_code=profile.system_lang_code,
        lang_code=profile.lang_code,
        timeout=15,
    )

    try:
        await client.connect()
        sent_code = await client.send_code_request(clean_phone)
        saved_session = str(client.session.save())
        phone_code_hash = str(sent_code.phone_code_hash)
        timeout = getattr(sent_code, "timeout", 120) or 120
    except errors.FloodWaitError as exc:
        raise AppException(
            f"Telegram FloodWait: please wait {exc.seconds}s before requesting a code."
        ) from exc
    except errors.PhoneNumberInvalidError as exc:
        raise AppException("The provided phone number is invalid.") from exc
    except errors.PhoneNumberBannedError as exc:
        raise AppException("This phone number is banned from Telegram.") from exc
    except Exception as exc:
        logger.error("send_code_request_failed", phone=clean_phone, error=str(exc))
        raise AppException(f"Failed to request Telegram code: {exc}") from exc
    finally:
        await client.disconnect()

    state = {
        "phone": clean_phone,
        "phone_code_hash": phone_code_hash,
        "session_string": saved_session,
        "api_id": resolved_api_id,
        "api_hash": resolved_api_hash,
        "proxy_url": proxy_url,
        "title": title or f"Phone {clean_phone}",
        "device_model": profile.device_model,
        "system_version": profile.system_version,
        "app_version": profile.app_version,
        "system_lang_code": profile.system_lang_code,
        "lang_code": profile.lang_code,
    }

    await redis_client.set(
        f"{REDIS_PHONE_AUTH_PREFIX}{phone_code_hash}",
        json.dumps(state),
        ex=REDIS_PHONE_AUTH_TTL,
    )

    return {
        "phone_code_hash": phone_code_hash,
        "timeout_seconds": timeout,
        "phone": clean_phone,
    }


async def sign_in_with_phone(
    phone_code_hash: str,
    code: str,
    db_session: AsyncSession,
    phone: str | None = None,
    two_fa_password: str | None = None,
) -> dict[str, Any]:
    """
    Step 2/3: Verify confirmation code and optional 2FA password.
    If 2FA is needed and no password provided, returns {"status": "needs_2fa"}.
    Upon successful authorization, saves account to the database.
    """
    raw_state = await redis_client.get(f"{REDIS_PHONE_AUTH_PREFIX}{phone_code_hash}")
    if not raw_state:
        raise AppException(
            "Authorization session expired or not found. Please request a new code."
        )

    state: dict[str, Any] = json.loads(raw_state)
    target_phone = phone or state["phone"]
    clean_code = code.strip().replace(" ", "").replace("-", "")

    proxy_dict = parse_proxy_url(state["proxy_url"]) if state.get("proxy_url") else None

    client = TelegramClient(
        StringSession(state["session_string"]),
        api_id=state["api_id"],
        api_hash=state["api_hash"],
        proxy=proxy_dict,
        device_model=state["device_model"],
        system_version=state["system_version"],
        app_version=state["app_version"],
        system_lang_code=state["system_lang_code"],
        lang_code=state["lang_code"],
        timeout=15,
    )

    try:
        await client.connect()
        # If two_fa_password is provided directly
        if two_fa_password:
            try:
                await client.sign_in(password=two_fa_password)
            except errors.PasswordHashInvalidError as exc:
                raise AppException("Invalid 2FA cloud password.") from exc
        else:
            try:
                await client.sign_in(
                    phone=target_phone,
                    code=clean_code,
                    phone_code_hash=phone_code_hash,
                )
            except errors.SessionPasswordNeededError:
                # 2FA password required
                state["session_string"] = str(client.session.save())
                await redis_client.set(
                    f"{REDIS_PHONE_AUTH_PREFIX}{phone_code_hash}",
                    json.dumps(state),
                    ex=REDIS_PHONE_AUTH_TTL,
                )
                return {
                    "status": "needs_2fa",
                    "message": "Two-factor auth required. Send cloud password.",
                    "phone_code_hash": phone_code_hash,
                }
            except errors.PhoneCodeInvalidError as exc:
                raise AppException("Invalid Telegram confirmation code.") from exc
            except errors.PhoneCodeExpiredError as exc:
                raise AppException("The confirmation code has expired.") from exc

        # Successfully authorized
        me = await client.get_me()
        final_session_string = str(client.session.save())
    except AppException:
        raise
    except Exception as exc:
        logger.error("sign_in_failed", phone=target_phone, error=str(exc))
        raise AppException(f"Sign-in failed: {exc}") from exc
    finally:
        await client.disconnect()

    # Clean up Redis state
    await redis_client.delete(f"{REDIS_PHONE_AUTH_PREFIX}{phone_code_hash}")

    first = getattr(me, "first_name", "") or ""
    last = getattr(me, "last_name", "") or ""
    full_name = f"{first} {last}".strip()
    username = getattr(me, "username", None)
    title = state.get("title") or (
        f"@{username}" if username else full_name or target_phone
    )

    account = TelegramAccount(
        title=title,
        phone=target_phone,
        proxy_url=state.get("proxy_url"),
        api_id=state["api_id"],
        api_hash=state["api_hash"],
        session_string=final_session_string,
        device_model=state["device_model"],
        system_version=state["system_version"],
        app_version=state["app_version"],
        system_lang_code=state["system_lang_code"],
        lang_code=state["lang_code"],
        status=AccountStatus.ACTIVE,
        telegram_user_id=getattr(me, "id", None),
        first_name=getattr(me, "first_name", None),
        last_name=getattr(me, "last_name", None),
        username=username,
        is_premium=bool(getattr(me, "premium", False)),
        last_checked_at=datetime.now(UTC),
    )

    db_session.add(account)
    await db_session.flush()
    await db_session.refresh(account)

    handle = (
        f"@{account.username}" if account.username else str(account.telegram_user_id)
    )
    return {
        "status": "success",
        "account": account,
        "message": f"Successfully authorized account {handle}",
    }
