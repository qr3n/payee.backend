"""
Admin notification service for Telegram account status changes and anomalies.
Sends rich HTML alerts to administrators via Telegram Bot API.
"""

import html
from contextlib import suppress
from datetime import UTC, datetime

import httpx
from redis.asyncio import Redis

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.modules.accounts.models import AccountStatus, TelegramAccount

logger = get_logger(__name__)


def format_account_alert(
    account: TelegramAccount,
    old_status: AccountStatus,
    new_status: AccountStatus,
    error: str | None = None,
) -> str:
    """Format an informative HTML alert for Telegram administrators."""
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    phone_display = html.escape(account.phone or "без номера")
    title_display = html.escape(account.title)
    tg_id_display = str(account.telegram_user_id) if account.telegram_user_id else "—"
    username_display = f"@{html.escape(account.username)}" if account.username else "—"
    err_display = html.escape(error or account.last_error or "Причина не указана")

    if new_status == AccountStatus.REVOKED:
        return (
            "🚨 <b>Внимание: Telegram-сессия отозвана!</b>\n\n"
            f"📱 <b>Аккаунт:</b> {title_display} (<code>{phone_display}</code>)\n"
            f"🆔 <b>ID в системе:</b> <code>{account.id}</code>\n"
            f"👤 <b>TG ID:</b> {tg_id_display} ({username_display})\n"
            f"⚠️ <b>Статус:</b> 🔴 <b>Отозвана (REVOKED)</b>\n"
            f"❌ <b>Причина:</b> <code>{err_display}</code>\n"
            f"🕒 <b>Время проверки:</b> {now_str}\n\n"
            "⚠️ <i>Аккаунт временно исключен из пула платежей. "
            "Замените файл .session или авторизуйтесь заново в боте админки.</i>"
        )

    if new_status == AccountStatus.BANNED:
        return (
            "⛔️ <b>Внимание: Telegram-аккаунт заблокирован!</b>\n\n"
            f"📱 <b>Аккаунт:</b> {title_display} (<code>{phone_display}</code>)\n"
            f"🆔 <b>ID в системе:</b> <code>{account.id}</code>\n"
            f"👤 <b>TG ID:</b> {tg_id_display} ({username_display})\n"
            f"⚠️ <b>Статус:</b> 🔴 <b>Заблокирован (BANNED)</b>\n"
            f"❌ <b>Причина:</b> <code>{err_display}</code>\n"
            f"🕒 <b>Время проверки:</b> {now_str}\n\n"
            "⚠️ <i>Аккаунт удален из рабочего пула. Требуется замена.</i>"
        )

    if new_status == AccountStatus.FLOOD_WAIT:
        wait_until_str = (
            account.flood_wait_until.strftime("%Y-%m-%d %H:%M:%S UTC")
            if account.flood_wait_until
            else "до истечения таймаута"
        )
        return (
            "⏳ <b>Внимание: Telegram-сессия получила Flood Wait!</b>\n\n"
            f"📱 <b>Аккаунт:</b> {title_display} (<code>{phone_display}</code>)\n"
            f"🆔 <b>ID в системе:</b> <code>{account.id}</code>\n"
            f"👤 <b>TG ID:</b> {tg_id_display} ({username_display})\n"
            f"⚠️ <b>Статус:</b> 🟡 <b>Flood Wait</b>\n"
            f"🕒 <b>Действует до:</b> {wait_until_str}\n"
            f"❌ <b>Детали:</b> <code>{err_display}</code>\n\n"
            "<i>Аккаунт временно не используется для сценариев "
            "до истечения ограничения.</i>"
        )

    if new_status == AccountStatus.ERROR:
        return (
            "❌ <b>Ошибка подключения к Telegram-сессии!</b>\n\n"
            f"📱 <b>Аккаунт:</b> {title_display} (<code>{phone_display}</code>)\n"
            f"🆔 <b>ID в системе:</b> <code>{account.id}</code>\n"
            f"👤 <b>TG ID:</b> {tg_id_display} ({username_display})\n"
            f"⚠️ <b>Статус:</b> ❌ <b>Ошибка (ERROR)</b>\n"
            f"❌ <b>Причина:</b> <code>{err_display}</code>\n"
            f"🕒 <b>Время проверки:</b> {now_str}"
        )

    if new_status == AccountStatus.ACTIVE and old_status != AccountStatus.ACTIVE:
        return (
            "✅ <b>Telegram-сессия успешно восстановлена!</b>\n\n"
            f"📱 <b>Аккаунт:</b> {title_display} (<code>{phone_display}</code>)\n"
            f"🆔 <b>ID в системе:</b> <code>{account.id}</code>\n"
            f"👤 <b>TG ID:</b> {tg_id_display} ({username_display})\n"
            "🟢 <b>Статус:</b> <b>Активен (ACTIVE)</b>\n"
            f"🕒 <b>Время:</b> {now_str}\n\n"
            "<i>Сессия снова доступна для создания платежей и выполнения сценариев.</i>"
        )

    return ""


async def get_target_admin_chat_ids(redis: Redis | None = None) -> list[int]:
    """
    Retrieve recipient chat IDs from configuration and dynamic Redis admin registry.
    """
    chat_ids = set(settings.ADMIN_CHAT_IDS)
    if redis is not None:
        try:
            cached_ids = await redis.smembers("admin_chat_ids")
            for cid in cached_ids:
                try:
                    chat_ids.add(int(cid))
                except (ValueError, TypeError):
                    continue
        except Exception as exc:
            logger.debug(
                "Failed retrieving dynamic admin_chat_ids from redis", error=str(exc)
            )
    return sorted(chat_ids)


async def send_admin_notification(text: str) -> int:
    """
    Send an HTML-formatted message to all configured administrators.
    Returns the number of successfully delivered messages.
    """
    if not text:
        return 0

    token = (
        settings.TELEGRAM_BOT_TOKEN.get_secret_value()
        if settings.TELEGRAM_BOT_TOKEN
        else None
    )
    if not token:
        logger.debug("admin_notification_skipped", reason="no_telegram_bot_token")
        return 0

    redis: Redis | None = None
    with suppress(Exception):
        redis = get_redis()

    target_chat_ids = await get_target_admin_chat_ids(redis)
    if not target_chat_ids:
        logger.debug("admin_notification_skipped", reason="no_admin_chat_ids")
        return 0

    sent_count = 0
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    async with httpx.AsyncClient(timeout=10.0) as client:
        for chat_id in target_chat_ids:
            try:
                resp = await client.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
                if resp.status_code == 200:
                    sent_count += 1
                else:
                    logger.warning(
                        "admin_notification_send_failed",
                        chat_id=chat_id,
                        status_code=resp.status_code,
                        response=resp.text[:200],
                    )
            except Exception as exc:
                logger.error(
                    "admin_notification_exception",
                    chat_id=chat_id,
                    error=str(exc),
                )

    return sent_count


async def notify_status_change_if_needed(
    account: TelegramAccount,
    old_status: AccountStatus,
    new_status: AccountStatus,
    error: str | None = None,
) -> bool:
    """
    Check if a notification should be dispatched based on status transitions
    and anti-spam deduplication in Redis.
    """
    redis: Redis | None = None
    with suppress(Exception):
        redis = get_redis()

    state_key = f"account_alert_state:{account.id}"
    last_alerted_status: str | None = None

    if redis is not None:
        try:
            val = await redis.get(state_key)
            last_alerted_status = val.decode("utf-8") if isinstance(val, bytes) else val
        except Exception as exc:
            logger.debug("Failed reading alert state from redis", error=str(exc))

    # Recovery: from error/revoked/banned/flood_wait back to ACTIVE
    if new_status == AccountStatus.ACTIVE:
        if last_alerted_status and last_alerted_status != AccountStatus.ACTIVE.value:
            text = format_account_alert(account, old_status, new_status, error)
            sent = 0
            if text:
                sent = await send_admin_notification(text)
            if (sent > 0 or not text) and redis is not None:
                with suppress(Exception):
                    await redis.delete(state_key)
            return sent > 0
        return False

    # Issue detected: REVOKED, BANNED, FLOOD_WAIT, ERROR
    if new_status in (
        AccountStatus.REVOKED,
        AccountStatus.BANNED,
        AccountStatus.FLOOD_WAIT,
        AccountStatus.ERROR,
    ):
        # Do not spam if already alerted about this exact status
        if last_alerted_status == new_status.value:
            return False

        text = format_account_alert(account, old_status, new_status, error)
        sent = 0
        if text:
            sent = await send_admin_notification(text)

        if sent > 0 and redis is not None:
            with suppress(Exception):
                # Keep alert state for 7 days
                await redis.set(state_key, new_status.value, ex=7 * 86400)
        return sent > 0

    return False
