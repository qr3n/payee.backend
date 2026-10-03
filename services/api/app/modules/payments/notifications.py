"""
Incoming bot notification parsers and reactive payment confirmation.
Processes success messages from @StarShoppik_bot, @HelperStars_Robot, and @starslly_bot,
matching them to pending Payment records and confirming payments.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.payments.models import NotificationEvent, Payment, PaymentStatus
from app.modules.payments.schemas import PaymentCallback

logger = get_logger(__name__)


@dataclass(slots=True)
class ParsedPaymentNotification:
    """Structured information extracted from a bot confirmation message."""

    scenario_id: str
    is_success: bool
    order_id: str | None = None
    stars_count: int | None = None
    recipient: str | None = None
    amount_text: str | None = None
    raw_text: str = ""


def parse_starshoppik_message(text: str) -> ParsedPaymentNotification | None:
    """
    Parse notification message from @StarShoppik_bot.

    Examples:
    1. "✅ Ваш заказ выполнен! ... 📝 Заказ №001912"
    2. "⭐️ Оцените пожалуйста вашу покупку: ... 📝 Заказ №001912"
    """
    text_lower = text.lower()
    is_success = (
        "ваш заказ выполнен" in text_lower
        or "оцените пожалуйста" in text_lower
        or "заказ выполнен" in text_lower
    )
    if not is_success:
        return None

    order_match = re.search(r"Заказ №\s*#?([A-Za-z0-9]+)", text)
    order_id = order_match.group(1) if order_match else None

    stars_match = re.search(r"(\d+)\s*(?:Telegram\s+)?Stars", text, re.IGNORECASE)
    stars_count = int(stars_match.group(1)) if stars_match else None

    recip_match = re.search(r"отправлены на\s*(@?\w+)", text, re.IGNORECASE)
    recipient = recip_match.group(1) if recip_match else None
    if recipient and not recipient.startswith("@"):
        recipient = f"@{recipient}"

    return ParsedPaymentNotification(
        scenario_id="starshoppik_bot",
        is_success=True,
        order_id=order_id,
        stars_count=stars_count,
        recipient=recipient,
        raw_text=text,
    )


def parse_helperstars_message(text: str) -> ParsedPaymentNotification | None:
    """
    Parse notification message from @HelperStars_Robot.

    Examples:
    1. "⭐️ Заказ успешно подтверждён! ... — Заказ ID: 232384; — Размер: 50.00 шт.;"
    2. "✅ Счёт успешно оплачен. 💰 На баланс зачислено 0.82 USD"
    3. "✅ Заказ №232384 успешно выполнен"
    """
    text_lower = text.lower()
    is_success = (
        "заказ успешно подтверждён" in text_lower
        or "счёт успешно оплачен" in text_lower
        or "успешно выполнен" in text_lower
        or "заказ успешно подтвержден" in text_lower
    )
    if not is_success:
        return None

    order_match = re.search(
        r"—?\s*Заказ\s+(?:ID|№):\s*#?([A-Za-z0-9]+)", text, re.IGNORECASE
    ) or re.search(r"Заказ\s+№\s*#?([A-Za-z0-9]+)", text, re.IGNORECASE)
    order_id = order_match.group(1) if order_match else None

    stars_match = re.search(
        r"—?\s*Размер заказа:\s*([\d\.]+)\s*шт", text, re.IGNORECASE
    )
    stars_count = int(float(stars_match.group(1))) if stars_match else None

    recip_match = re.search(r"—?\s*Получатель:\s*(@?\w+)", text, re.IGNORECASE)
    recipient = recip_match.group(1) if recip_match else None
    if recipient and not recipient.startswith("@"):
        recipient = f"@{recipient}"

    return ParsedPaymentNotification(
        scenario_id="helperstars_bot",
        is_success=True,
        order_id=order_id,
        stars_count=stars_count,
        recipient=recipient,
        raw_text=text,
    )


def parse_starslly_message(text: str) -> ParsedPaymentNotification | None:
    """
    Parse notification message from @starslly_bot.

    Examples:
    1. "✅ Платеж успешно получен!\nВ ближайшее время звезды/TON будут зачислены..."
    2. "✅ Успешно! Звёзды были зачислены на твой баланс..."
    """
    text_lower = text.lower()
    is_success = (
        "платеж успешно получен" in text_lower
        or "платеж получен" in text_lower
        or "звёзды были зачислены" in text_lower
        or "звезды были зачислены" in text_lower
        or ("успешно" in text_lower and "баланс" in text_lower)
    )
    if not is_success:
        return None

    return ParsedPaymentNotification(
        scenario_id="starslly_bot",
        is_success=True,
        raw_text=text,
    )


def parse_bot_payment_message(
    bot_username: str, text: str
) -> ParsedPaymentNotification | None:
    """
    Route an incoming message to the matching bot parser based on sender username.
    """
    norm_bot = bot_username.lstrip("@").lower()
    norm_starshoppik = settings.STARSHOPPIK_BOT_USERNAME.lstrip("@").lower()
    norm_helperstars = settings.HELPERSTARS_BOT_USERNAME.lstrip("@").lower()
    norm_starslly = settings.STARSLY_BOT_USERNAME.lstrip("@").lower()

    if norm_bot == norm_starshoppik:
        return parse_starshoppik_message(text)
    if norm_bot == norm_helperstars:
        return parse_helperstars_message(text)
    if norm_bot == norm_starslly:
        return parse_starslly_message(text)

    return None


async def process_bot_notification(
    session: AsyncSession,
    account_id: UUID,
    sender_username: str,
    message_text: str,
    message_id: int | None = None,
    message_date: datetime | None = None,
) -> Payment | None:
    """
    Process incoming bot message, match to active PENDING payment in database,
    and transition payment to PAID. Prevents duplicate processing via NotificationEvent.
    """
    norm_bot = sender_username.lstrip("@").lower()
    existing_event: NotificationEvent | None = None

    # 1. Idempotency check: ignore already processed Telegram message
    if message_id is not None:
        existing_event_stmt = select(NotificationEvent).where(
            NotificationEvent.account_id == account_id,
            NotificationEvent.bot_username == norm_bot,
            NotificationEvent.message_id == message_id,
        )
        existing_event_res = await session.exec(existing_event_stmt)
        existing_event = existing_event_res.first()
        if existing_event:
            if existing_event.status == "processed":
                logger.info(
                    "bot_notification_already_processed_skipping",
                    account_id=str(account_id),
                    bot=norm_bot,
                    message_id=message_id,
                )
                return None
            if existing_event.status == "ignored":
                return None
            # If status == "unmatched", proceed to re-attempt matching
            # against active payments

    parsed = parse_bot_payment_message(sender_username, message_text)
    if not parsed or not parsed.is_success:
        if message_id is not None and not existing_event:
            ign_event = NotificationEvent(
                account_id=account_id,
                bot_username=norm_bot,
                message_id=message_id,
                message_date=message_date,
                status="ignored",
                raw_text=message_text,
            )
            session.add(ign_event)
            await session.flush()
        return None

    logger.info(
        "bot_payment_success_notification_received",
        account_id=str(account_id),
        scenario_id=parsed.scenario_id,
        order_id=parsed.order_id,
        stars_count=parsed.stars_count,
        recipient=parsed.recipient,
        message_id=message_id,
    )

    matched_payment: Payment | None = None

    if parsed.scenario_id == "starshoppik_bot":
        # 1. Primary match: order_id (Strict: scoped to account,
        # NEVER fallback if order_id is present)
        if parsed.order_id:
            stmt = (
                select(Payment)
                .where(
                    Payment.scenario_id == "starshoppik_bot",
                    Payment.account_id == account_id,
                    Payment.status == PaymentStatus.PENDING,
                )
                .order_by(col(Payment.created_at).desc())
            )
            result = await session.exec(stmt)
            for p in result.all():
                if p.meta.get("order_id") == parsed.order_id:
                    matched_payment = p
                    break
        elif parsed.stars_count:
            # Only match by stars_count if no order_id was provided by the bot
            stmt = (
                select(Payment)
                .where(
                    Payment.scenario_id == "starshoppik_bot",
                    Payment.account_id == account_id,
                    Payment.status == PaymentStatus.PENDING,
                )
                .order_by(col(Payment.created_at).desc())
            )
            result = await session.exec(stmt)
            for p in result.all():
                if p.meta.get("stars_count") == parsed.stars_count:
                    # Message must not predate payment creation (2s skew)
                    if message_date and message_date < (
                        p.created_at - timedelta(seconds=2)
                    ):
                        continue
                    matched_payment = p
                    break

    elif parsed.scenario_id == "helperstars_bot":
        # 1. Match by order_id if payment already has it in meta
        if parsed.order_id:
            stmt = (
                select(Payment)
                .where(
                    Payment.scenario_id == "helperstars_bot",
                    Payment.account_id == account_id,
                    Payment.status == PaymentStatus.PENDING,
                )
                .order_by(col(Payment.created_at).desc())
            )
            result = await session.exec(stmt)
            for p in result.all():
                if p.meta.get("order_id") == parsed.order_id:
                    matched_payment = p
                    break

        # 2. Match by unique allocated stars_count (+1..+10) ONLY for payments
        # that don't have a mismatched order_id
        if not matched_payment and parsed.stars_count:
            stmt = (
                select(Payment)
                .where(
                    Payment.scenario_id == "helperstars_bot",
                    Payment.account_id == account_id,
                    Payment.status == PaymentStatus.PENDING,
                )
                .order_by(col(Payment.created_at).desc())
            )
            result = await session.exec(stmt)
            for p in result.all():
                # If payment has an order_id that differs, do NOT match
                p_order = p.meta.get("order_id")
                if p_order and parsed.order_id and p_order != parsed.order_id:
                    continue
                if p.meta.get("stars_count") == parsed.stars_count:
                    if message_date and message_date < (
                        p.created_at - timedelta(seconds=2)
                    ):
                        continue
                    matched_payment = p
                    break

    elif parsed.scenario_id == "starslly_bot":
        # Find active pending payment on this account
        stmt = (
            select(Payment)
            .where(
                Payment.scenario_id == "starslly_bot",
                Payment.account_id == account_id,
                Payment.status == PaymentStatus.PENDING,
            )
            .order_by(col(Payment.created_at).desc())
        )
        result = await session.exec(stmt)
        candidate = result.first()
        if candidate:
            # Ensure message does not predate payment creation
            if message_date and message_date < (
                candidate.created_at - timedelta(seconds=2)
            ):
                logger.warning(
                    "starslly_notification_predates_payment",
                    account_id=str(account_id),
                    payment_id=str(candidate.id),
                    payment_created=candidate.created_at.isoformat(),
                    message_date=message_date.isoformat(),
                )
                matched_payment = None
            else:
                matched_payment = candidate

    if not matched_payment:
        logger.warning(
            "unmatched_bot_payment_notification",
            account_id=str(account_id),
            scenario_id=parsed.scenario_id,
            order_id=parsed.order_id,
            stars_count=parsed.stars_count,
            message_id=message_id,
        )
        if message_id is not None and not existing_event:
            unmatched_event = NotificationEvent(
                account_id=account_id,
                bot_username=norm_bot,
                message_id=message_id,
                message_date=message_date,
                status="unmatched",
                raw_text=message_text,
            )
            session.add(unmatched_event)
            await session.flush()
        return None

    # Update metadata with confirmed details
    callback_meta = {
        "confirmation_source": "telegram_bot_message",
        "confirmation_message": parsed.raw_text,
    }
    if parsed.order_id:
        callback_meta["order_id"] = parsed.order_id

    from app.modules.payments import service as payment_service

    updated_payment = await payment_service.mark_payment_status(
        session=session,
        db_payment=matched_payment,
        callback=PaymentCallback(
            status=PaymentStatus.PAID,
            external_transaction_id=parsed.order_id,
            meta=callback_meta,
        ),
    )

    if message_id is not None:
        if existing_event:
            existing_event.status = "processed"
            existing_event.payment_id = updated_payment.id
            session.add(existing_event)
        else:
            processed_event = NotificationEvent(
                account_id=account_id,
                bot_username=norm_bot,
                message_id=message_id,
                message_date=message_date,
                status="processed",
                payment_id=updated_payment.id,
                raw_text=message_text,
            )
            session.add(processed_event)
        await session.flush()

    logger.info(
        "payment_successfully_confirmed_via_bot",
        payment_id=str(updated_payment.id),
        scenario_id=updated_payment.scenario_id,
        account_id=str(account_id),
        order_id=parsed.order_id,
        message_id=message_id,
    )
    return updated_payment


async def check_all_pending_payments_notifications(
    session: AsyncSession,
) -> int:
    """
    Safety fallback scanner: checks recent messages from payment bots
    for all active PENDING payments in case an MTProto real-time event was missed.
    Passes message_id and timestamp to avoid false confirmations.
    """
    from app.modules.accounts.models import AccountStatus, TelegramAccount
    from app.modules.accounts.session_pool import telegram_session_pool
    from app.modules.payments.scenarios.registry import scenario_registry

    stmt = select(Payment).where(Payment.status == PaymentStatus.PENDING)
    result = await session.exec(stmt)
    pending_payments = list(result.all())

    if not pending_payments:
        return 0

    by_account: dict[UUID, list[Payment]] = {}
    for p in pending_payments:
        if p.account_id:
            by_account.setdefault(p.account_id, []).append(p)

    confirmed_count = 0

    for account_id, payments in by_account.items():
        account = await session.get(TelegramAccount, account_id)
        if not account or account.status != AccountStatus.ACTIVE:
            continue

        try:
            client = await telegram_session_pool.get_connected_client(account)
            scenario_ids = {p.scenario_id for p in payments}
            for sid in scenario_ids:
                scenario = scenario_registry.get(sid)
                if not scenario:
                    continue

                bot_username: str | None = None
                if sid == "starshoppik_bot":
                    bot_username = settings.STARSHOPPIK_BOT_USERNAME
                elif sid == "helperstars_bot":
                    bot_username = settings.HELPERSTARS_BOT_USERNAME
                elif sid == "starslly_bot":
                    bot_username = settings.STARSLY_BOT_USERNAME

                if not bot_username:
                    continue

                try:
                    messages = await client.get_messages(bot_username, limit=5)
                    for msg in messages:
                        if not msg or not getattr(msg, "text", ""):
                            continue
                        if not getattr(msg, "out", False):
                            raw_id = getattr(msg, "id", None)
                            msg_id = (
                                int(raw_id)
                                if isinstance(raw_id, int)
                                and not isinstance(raw_id, bool)
                                else None
                            )
                            raw_date = getattr(msg, "date", None)
                            msg_date = (
                                raw_date if isinstance(raw_date, datetime) else None
                            )
                            confirmed = await process_bot_notification(
                                session=session,
                                account_id=account_id,
                                sender_username=bot_username,
                                message_text=msg.text,
                                message_id=msg_id,
                                message_date=msg_date,
                            )
                            if confirmed:
                                confirmed_count += 1
                                await session.flush()
                except Exception as bot_err:
                    logger.debug(
                        "failed_fetching_bot_messages_fallback",
                        account_id=str(account_id),
                        bot=bot_username,
                        error=str(bot_err),
                    )
        except Exception as acc_err:
            logger.debug(
                "failed_checking_account_pending_notifications",
                account_id=str(account_id),
                error=str(acc_err),
            )

    return confirmed_count
