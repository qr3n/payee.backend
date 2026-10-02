"""
Helper utilities for resilient Telethon interactions with Telegram bots.
Handles message polling, button clicking, channel joining, and edit tracking.
"""

import asyncio
from collections.abc import Callable
from typing import Any

from telethon import TelegramClient, errors, events, functions, types
from telethon.tl.custom.messagebutton import MessageButton

from app.core.logging import get_logger

logger = get_logger(__name__)


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


async def click_button_fast(
    client: TelegramClient,
    button: MessageButton,
    wait_answer_timeout: float = 0.35,
) -> Any:
    """
    Click a button without stalling if the bot backend omits answerCallbackQuery.
    For inline callback buttons, dispatches GetBotCallbackAnswerRequest and waits
    at most `wait_answer_timeout` seconds before returning, preventing 15-30s
    MTProto timeouts.
    """
    raw_btn = getattr(button, "button", None)
    btn_type = getattr(raw_btn, "type", None)
    if isinstance(btn_type, types.InlineButtonTypeCallback):
        req = functions.messages.GetBotCallbackAnswerRequest(
            peer=button._chat,
            msg_id=button._msg_id,
            data=btn_type.data,
        )
        task = asyncio.create_task(client(req))
        try:
            return await asyncio.wait_for(
                asyncio.shield(task), timeout=wait_answer_timeout
            )
        except (TimeoutError, errors.BotResponseTimeoutError):
            logger.debug(
                "Bot callback answer timed out (safe to proceed)",
                btn_text=getattr(button, "text", ""),
            )
            return None
    return await button.click()


async def wait_for_bot_message(
    client: TelegramClient,
    peer: Any,
    predicate: Callable[[Any], bool],
    timeout: float = 20.0,
    fallback_interval: float = 2.5,
    min_id: int | None = None,
) -> Any:
    """
    Wait for a bot message matching predicate.
    Uses reactive MTProto event listening (events.NewMessage, events.MessageEdited)
    with a gentle fallback check every 2.5s to prevent GetHistoryRequest FloodWait.
    """
    # 1. Quick check if matching message is already in recent history
    try:
        recent = await client.get_messages(peer, limit=6, min_id=min_id or 0)
        for msg in recent:
            if not getattr(msg, "out", False) and predicate(msg):
                return msg
    except Exception as e:
        logger.debug("Initial message check failed", error=str(e))

    # 2. Event-driven listener
    loop = asyncio.get_running_loop()
    result_future: asyncio.Future[Any] = loop.create_future()

    async def on_event(event: Any) -> None:
        if not result_future.done():
            msg = getattr(event, "message", None)
            if msg and not getattr(msg, "out", False) and predicate(msg):
                result_future.set_result(msg)

    h_new = client.add_event_handler(on_event, events.NewMessage(chats=peer))
    h_edit = client.add_event_handler(on_event, events.MessageEdited(chats=peer))

    start_time = loop.time()
    try:
        while not result_future.done():
            remaining = timeout - (loop.time() - start_time)
            if remaining <= 0:
                raise TimeoutError(
                    f"Bot did not produce expected message within {timeout}s."
                )

            step_timeout = min(fallback_interval, remaining)
            try:
                return await asyncio.wait_for(
                    asyncio.shield(result_future), timeout=step_timeout
                )
            except TimeoutError:
                # Gentle fallback in case MTProto push was delayed
                try:
                    msgs = await client.get_messages(peer, limit=6, min_id=min_id or 0)
                    for msg in msgs:
                        if not getattr(msg, "out", False) and predicate(msg):
                            return msg
                except Exception:
                    pass

        return result_future.result()
    finally:
        client.remove_event_handler(h_new)
        client.remove_event_handler(h_edit)
