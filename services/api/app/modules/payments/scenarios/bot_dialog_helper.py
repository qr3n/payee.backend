"""
Helper utilities for resilient Telethon interactions with Telegram bots.
Handles message polling, button clicking, channel joining, and edit tracking.
"""

import asyncio
from collections.abc import Callable
from typing import Any

from telethon import TelegramClient, errors, functions
from telethon.sessions import StringSession
from telethon.tl.custom.messagebutton import MessageButton

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.accounts.models import TelegramAccount
from app.modules.accounts.proxy_utils import parse_proxy_url

logger = get_logger(__name__)


def create_telethon_client(account: TelegramAccount) -> TelegramClient:
    """Instantiate a TelegramClient using account session, proxy, and device profile."""
    resolved_api_id = account.api_id or settings.TELEGRAM_DEFAULT_API_ID
    resolved_api_hash = account.api_hash or settings.TELEGRAM_DEFAULT_API_HASH
    proxy_dict = parse_proxy_url(account.proxy_url) if account.proxy_url else None

    return TelegramClient(
        StringSession(account.session_string),
        api_id=resolved_api_id,
        api_hash=resolved_api_hash,
        proxy=proxy_dict,
        device_model=account.device_model,
        system_version=account.system_version,
        app_version=account.app_version,
        system_lang_code=account.system_lang_code,
        lang_code=account.lang_code,
        timeout=15,
        connection_retries=3,
        auto_reconnect=True,
    )


async def join_channel_safely(client: TelegramClient, channel_username: str) -> bool:
    """
    Join a public Telegram channel by username.
    Gracefully ignores if account is already a member.
    """
    clean_username = channel_username.removeprefix("https://t.me/").removeprefix("@")
    try:
        await client(functions.channels.JoinChannelRequest(channel=clean_username))
        logger.info("Successfully joined channel", channel=clean_username)
        return True
    except errors.UserAlreadyParticipantError:
        logger.debug("Account already participant of channel", channel=clean_username)
        return True
    except Exception as e:
        logger.warning("Could not join channel", channel=clean_username, error=str(e))
        return False


def find_button_by_text(message: Any, text_substring: str) -> MessageButton | None:
    """
    Search message keyboard for an inline or reply button
    containing the given text substring (case-insensitive).
    """
    buttons = getattr(message, "buttons", None)
    if not buttons:
        return None

    query = text_substring.lower()
    for row in buttons:
        for button in row:
            btn_text = getattr(button, "text", "")
            if query in btn_text.lower():
                return button
    return None


def find_url_button(
    message: Any, text_substring: str | None = None
) -> tuple[MessageButton, str] | None:
    """
    Search message keyboard for a button containing a link/URL.
    Optionally filters by button title substring.
    Returns (button, url) or None.
    """
    buttons = getattr(message, "buttons", None)
    if not buttons:
        return None

    query = text_substring.lower() if text_substring else None
    for row in buttons:
        for button in row:
            btn_url = getattr(button, "url", None)
            if not btn_url:
                continue
            btn_text = getattr(button, "text", "")
            if query is None or query in btn_text.lower():
                return button, btn_url
    return None


async def wait_for_bot_message(
    client: TelegramClient,
    peer: Any,
    predicate: Callable[[Any], bool],
    timeout: float = 20.0,
    poll_interval: float = 1.0,
    min_id: int | None = None,
) -> Any:
    """
    Poll recent messages from the bot until a message satisfies the predicate.
    Robust against message edits and intermediate loading notifications.
    """
    loop_start = asyncio.get_running_loop().time()

    while (asyncio.get_running_loop().time() - loop_start) < timeout:
        messages = await client.get_messages(peer, limit=6, min_id=min_id or 0)
        for msg in messages:
            # Skip messages sent by the user account itself
            if getattr(msg, "out", False):
                continue
            if predicate(msg):
                return msg

        await asyncio.sleep(poll_interval)

    raise TimeoutError(
        f"Bot did not produce expected message within {timeout}s timeout."
    )
