"""
Unit tests for incoming bot payment notifications parsing and matching.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.accounts.models import AccountStatus, TelegramAccount
from app.modules.payments.models import Payment, PaymentStatus
from app.modules.payments.notifications import (
    parse_bot_payment_message,
    parse_helperstars_message,
    parse_starshoppik_message,
    parse_starslly_message,
    process_bot_notification,
)


def test_parse_starshoppik_message_variant_1() -> None:
    text = (
        "✅ Ваш заказ выполнен!\n\n"
        "⭐️ 50 Telegram Stars отправлены на @qr3nnn!\n"
        "📝 Заказ №001912"
    )
    parsed = parse_starshoppik_message(text)
    assert parsed is not None
    assert parsed.is_success is True
    assert parsed.scenario_id == "starshoppik_bot"
    assert parsed.order_id == "001912"
    assert parsed.stars_count == 50
    assert parsed.recipient == "@qr3nnn"


def test_parse_starshoppik_message_variant_2() -> None:
    text = "⭐️ Оцените пожалуйста вашу покупку:\n\n📦 50 Stars\n📝 Заказ №001912"
    parsed = parse_starshoppik_message(text)
    assert parsed is not None
    assert parsed.is_success is True
    assert parsed.order_id == "001912"
    assert parsed.stars_count == 50


def test_parse_helperstars_message_variant_1() -> None:
    text = (
        "⭐️ Заказ успешно подтверждён и обрабатывается!\n\n"
        "ℹ️ Информация о заказе:\n\n"
        "— Заказ ID: 232384;\n"
        "— Тип заказа: Звёзды;\n"
        "— Получатель: @qr3nnn;\n"
        "— Размер заказа: 50.00 шт.;\n"
        "— Цена: 0.82 USD (71.15 RUB).\n\n"
        "⏳ Вы получите уведомление, когда заказ будет выполнен."
    )
    parsed = parse_helperstars_message(text)
    assert parsed is not None
    assert parsed.is_success is True
    assert parsed.scenario_id == "helperstars_bot"
    assert parsed.order_id == "232384"
    assert parsed.stars_count == 50
    assert parsed.recipient == "@qr3nnn"


def test_parse_helperstars_message_variant_2() -> None:
    text = "✅ Счёт успешно оплачен.\n\n💰 На баланс зачислено 0.82 USD"
    parsed = parse_helperstars_message(text)
    assert parsed is not None
    assert parsed.is_success is True
    assert parsed.scenario_id == "helperstars_bot"


def test_parse_helperstars_message_variant_3() -> None:
    text = (
        "✅ Заказ №232384 успешно выполнен, товар будет доставлен "
        "в течение 1 минуты.\n\n"
        "❓Есть вопросы или столкнулись с проблемой? "
        "Обратитесь в поддержку — @Helper_All_Support."
    )
    parsed = parse_helperstars_message(text)
    assert parsed is not None
    assert parsed.is_success is True
    assert parsed.scenario_id == "helperstars_bot"
    assert parsed.order_id == "232384"


def test_parse_starslly_message_variant_1() -> None:
    text = (
        "✅ Платеж успешно получен!\n\n"
        "В ближайшее время звезды/TON будут зачислены на баланс аккаунта. "
        "(Обычно до 15 минут)\n\n"
        "⚠️ Важно: тщательно проверяйте сторонние сервисы..."
    )
    parsed = parse_starslly_message(text)
    assert parsed is not None
    assert parsed.is_success is True
    assert parsed.scenario_id == "starslly_bot"


def test_parse_starslly_message_variant_2() -> None:
    text = (
        "✅ Успешно! Звёзды были зачислены на твой баланс.\n\n"
        "Держи скидку на следующую покупку — звёзды по 1.55₽. Не упусти возможность!"
    )
    parsed = parse_starslly_message(text)
    assert parsed is not None
    assert parsed.is_success is True
    assert parsed.scenario_id == "starslly_bot"


def test_parse_bot_payment_message_dispatch() -> None:
    res = parse_bot_payment_message(
        "StarShoppik_bot", "✅ Ваш заказ выполнен! 📝 Заказ №123"
    )
    assert res is not None
    assert res.scenario_id == "starshoppik_bot"

    res_helper = parse_bot_payment_message(
        "@HelperStars_Robot", "✅ Счёт успешно оплачен."
    )
    assert res_helper is not None
    assert res_helper.scenario_id == "helperstars_bot"

    res_starsly = parse_bot_payment_message(
        "starslly_bot", "✅ Платеж успешно получен!"
    )
    assert res_starsly is not None
    assert res_starsly.scenario_id == "starslly_bot"

    res_unknown = parse_bot_payment_message("random_bot", "✅ Платеж успешно получен!")
    assert res_unknown is None


VALID_SESSION_STRING = (
    "1ApWapzMBu2q0u5O-vNqG-w8dJj8f4W9H7c4D2B8A1Z3E5G7I9K1M3O5Q7S9U1W3Y5a7"
    "c9e1g3i5k7m9o1q3s5u7w9y1A3C5E7G9I1K3M5O7Q9S1U3W5Y7a9c1e3g5i7k9m1o3q"
)


async def _create_test_account(session: AsyncSession, title: str) -> TelegramAccount:
    """Helper to populate an active TelegramAccount in test DB."""
    account = TelegramAccount(
        title=title,
        session_string=VALID_SESSION_STRING,
        device_model="Test Phone",
        system_version="Android 14",
        app_version="10.14.0",
        lang_code="ru",
        system_lang_code="ru-RU",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    await session.flush()
    await session.refresh(account)
    return account


@pytest.mark.asyncio
async def test_process_bot_notification_starshoppik(
    db_session: AsyncSession,
) -> None:
    sample_account = await _create_test_account(db_session, "Account SP")
    now = datetime.now(UTC)
    payment = Payment(
        client_user_id="user_sp1",
        scenario_id="starshoppik_bot",
        amount=Decimal("75.60"),
        account_id=sample_account.id,
        status=PaymentStatus.PENDING,
        expires_at=now + timedelta(minutes=30),
        meta={"order_id": "001912", "stars_count": 50},
    )
    db_session.add(payment)
    await db_session.flush()

    text = (
        "✅ Ваш заказ выполнен!\n\n"
        "⭐️ 50 Telegram Stars отправлены на @qr3nnn!\n"
        "📝 Заказ №001912"
    )
    with patch(
        "app.modules.payments.service.release_scenario_stars_reservation",
        new_callable=AsyncMock,
    ):
        updated = await process_bot_notification(
            session=db_session,
            account_id=sample_account.id,
            sender_username="StarShoppik_bot",
            message_text=text,
        )

    assert updated is not None
    assert updated.status == PaymentStatus.PAID
    assert updated.paid_at is not None
    assert updated.meta.get("confirmation_source") == "telegram_bot_message"


@pytest.mark.asyncio
async def test_process_bot_notification_helperstars_by_stars_count(
    db_session: AsyncSession,
) -> None:
    sample_account = await _create_test_account(db_session, "Account HS")
    now = datetime.now(UTC)
    payment = Payment(
        client_user_id="user_hs1",
        scenario_id="helperstars_bot",
        amount=Decimal("71.15"),
        account_id=sample_account.id,
        status=PaymentStatus.PENDING,
        expires_at=now + timedelta(minutes=30),
        meta={"stars_count": 53, "recipient_username": "@qr3nnn"},
    )
    db_session.add(payment)
    await db_session.flush()

    text = (
        "⭐️ Заказ успешно подтверждён и обрабатывается!\n\n"
        "— Заказ ID: 232384;\n"
        "— Получатель: @qr3nnn;\n"
        "— Размер заказа: 53.00 шт.;"
    )
    with patch(
        "app.modules.payments.service.release_scenario_stars_reservation",
        new_callable=AsyncMock,
    ):
        updated = await process_bot_notification(
            session=db_session,
            account_id=sample_account.id,
            sender_username="HelperStars_Robot",
            message_text=text,
        )

    assert updated is not None
    assert updated.status == PaymentStatus.PAID
    assert updated.meta.get("order_id") == "232384"


@pytest.mark.asyncio
async def test_process_bot_notification_starslly_exclusive_slot(
    db_session: AsyncSession,
) -> None:
    sample_account = await _create_test_account(db_session, "Account SL")
    now = datetime.now(UTC)
    payment = Payment(
        client_user_id="user_sl1",
        scenario_id="starslly_bot",
        amount=Decimal("80.00"),
        account_id=sample_account.id,
        status=PaymentStatus.PENDING,
        expires_at=now + timedelta(minutes=30),
        meta={"stars_count": 50, "slot_lock_token": "token_sl_test"},
    )
    db_session.add(payment)
    await db_session.flush()

    text = "✅ Платеж успешно получен!\nВ ближайшее время звезды будут зачислены."
    with patch(
        "app.modules.payments.service.release_scenario_pending_lock",
        new_callable=AsyncMock,
    ) as mock_release_slot:
        updated = await process_bot_notification(
            session=db_session,
            account_id=sample_account.id,
            sender_username="starslly_bot",
            message_text=text,
        )

    assert updated is not None
    assert updated.status == PaymentStatus.PAID
    mock_release_slot.assert_awaited_once_with(
        "starslly_bot", sample_account.id, owner_token="token_sl_test"
    )


@pytest.mark.asyncio
async def test_check_all_pending_payments_notifications(
    db_session: AsyncSession,
) -> None:
    sample_account = await _create_test_account(db_session, "Account Fallback")
    now = datetime.now(UTC)
    payment = Payment(
        client_user_id="user_fb1",
        scenario_id="starshoppik_bot",
        amount=Decimal("75.60"),
        account_id=sample_account.id,
        status=PaymentStatus.PENDING,
        expires_at=now + timedelta(minutes=30),
        meta={"order_id": "001920", "stars_count": 50},
    )
    db_session.add(payment)
    await db_session.flush()

    mock_msg = AsyncMock()
    mock_msg.out = False
    mock_msg.text = (
        "✅ Ваш заказ выполнен!\n\n"
        "⭐️ 50 Telegram Stars отправлены на @qr3nnn!\n"
        "📝 Заказ №001920"
    )

    mock_client = AsyncMock()
    mock_client.get_messages = AsyncMock(return_value=[mock_msg])

    with (
        patch(
            "app.modules.accounts.session_pool.telegram_session_pool.get_connected_client",
            new_callable=AsyncMock,
            return_value=mock_client,
        ),
        patch(
            "app.modules.payments.service.release_scenario_stars_reservation",
            new_callable=AsyncMock,
        ),
    ):
        from app.modules.payments.notifications import (
            check_all_pending_payments_notifications,
        )

        confirmed = await check_all_pending_payments_notifications(session=db_session)

    assert confirmed == 1
    await db_session.refresh(payment)
    assert payment.status == PaymentStatus.PAID


@pytest.mark.asyncio
async def test_notification_event_deduplication(
    db_session: AsyncSession,
) -> None:
    sample_account = await _create_test_account(db_session, "Account Dedup")
    now = datetime.now(UTC)
    payment = Payment(
        client_user_id="user_dedup",
        scenario_id="starshoppik_bot",
        amount=Decimal("75.60"),
        account_id=sample_account.id,
        status=PaymentStatus.PENDING,
        expires_at=now + timedelta(minutes=30),
        meta={"order_id": "009999", "stars_count": 50},
    )
    db_session.add(payment)
    await db_session.flush()

    text = "✅ Ваш заказ выполнен!\n⭐️ 50 Stars отправлены\n📝 Заказ №009999"
    msg_id = 888123

    with patch(
        "app.modules.payments.service.release_scenario_stars_reservation",
        new_callable=AsyncMock,
    ):
        # First processing -> success
        first = await process_bot_notification(
            session=db_session,
            account_id=sample_account.id,
            sender_username="StarShoppik_bot",
            message_text=text,
            message_id=msg_id,
            message_date=now,
        )
        assert first is not None
        assert first.status == PaymentStatus.PAID

        # Second processing with same message_id -> deduplicated (None returned)
        second = await process_bot_notification(
            session=db_session,
            account_id=sample_account.id,
            sender_username="StarShoppik_bot",
            message_text=text,
            message_id=msg_id,
            message_date=now,
        )
        assert second is None


@pytest.mark.asyncio
async def test_old_notification_does_not_confirm_new_payment(
    db_session: AsyncSession,
) -> None:
    sample_account = await _create_test_account(db_session, "Account OldMsg")
    now = datetime.now(UTC)
    # Payment created now
    payment = Payment(
        client_user_id="user_new",
        scenario_id="starslly_bot",
        amount=Decimal("100.00"),
        account_id=sample_account.id,
        status=PaymentStatus.PENDING,
        expires_at=now + timedelta(minutes=30),
        meta={"stars_count": 100},
    )
    db_session.add(payment)
    await db_session.flush()

    # Message arrived 1 hour AGO (from previous transaction)
    old_date = now - timedelta(hours=1)
    text = "✅ Платеж успешно получен!\nВ ближайшее время звезды/TON будут зачислены"

    res = await process_bot_notification(
        session=db_session,
        account_id=sample_account.id,
        sender_username="starslly_bot",
        message_text=text,
        message_id=777123,
        message_date=old_date,
    )
    # Must NOT match the new payment!
    assert res is None
    await db_session.refresh(payment)
    assert payment.status == PaymentStatus.PENDING


@pytest.mark.asyncio
async def test_mismatched_order_id_does_not_fallback_to_another_payment(
    db_session: AsyncSession,
) -> None:
    sample_account = await _create_test_account(db_session, "Account Mismatch")
    now = datetime.now(UTC)
    # Payment has order_id 111111 and stars_count 50
    payment = Payment(
        client_user_id="user_target",
        scenario_id="starshoppik_bot",
        amount=Decimal("50.00"),
        account_id=sample_account.id,
        status=PaymentStatus.PENDING,
        expires_at=now + timedelta(minutes=30),
        meta={"order_id": "111111", "stars_count": 50},
    )
    db_session.add(payment)
    await db_session.flush()

    # Message is for order_id 222222 with 50 stars
    text = "✅ Ваш заказ выполнен!\n⭐️ 50 Stars\n📝 Заказ №222222"

    res = await process_bot_notification(
        session=db_session,
        account_id=sample_account.id,
        sender_username="StarShoppik_bot",
        message_text=text,
        message_id=666123,
        message_date=now,
    )
    # Must NOT confirm payment 111111 even though stars count is 50!
    assert res is None
    await db_session.refresh(payment)
    assert payment.status == PaymentStatus.PENDING


@pytest.mark.asyncio
async def test_unmatched_notification_re_matched_when_payment_arrives(
    db_session: AsyncSession,
) -> None:
    """
    Test that an unmatched event is subsequently matched and upgraded
    to processed.
    """
    from sqlmodel import select

    from app.modules.payments.models import NotificationEvent

    sample_account = await _create_test_account(db_session, "Account Unmatched")
    now = datetime.now(UTC)
    text = "✅ Ваш заказ выполнен!\n⭐️ 50 Stars\n📝 Заказ №999888"

    # 1. Message arrives before payment is inserted into DB
    res1 = await process_bot_notification(
        session=db_session,
        account_id=sample_account.id,
        sender_username="StarShoppik_bot",
        message_text=text,
        message_id=999123,
        message_date=now,
    )
    assert res1 is None

    # Verify event stored as 'unmatched'
    stmt = select(NotificationEvent).where(
        NotificationEvent.message_id == 999123,
        NotificationEvent.account_id == sample_account.id,
    )
    event = (await db_session.exec(stmt)).first()
    assert event is not None
    assert event.status == "unmatched"

    # 2. Payment arrives
    payment = Payment(
        client_user_id="user_late",
        scenario_id="starshoppik_bot",
        amount=Decimal("50.00"),
        account_id=sample_account.id,
        status=PaymentStatus.PENDING,
        expires_at=now + timedelta(minutes=30),
        meta={"order_id": "999888", "stars_count": 50},
    )
    db_session.add(payment)
    await db_session.flush()

    # 3. Notification scanner or re-check re-evaluates the same message
    res2 = await process_bot_notification(
        session=db_session,
        account_id=sample_account.id,
        sender_username="StarShoppik_bot",
        message_text=text,
        message_id=999123,
        message_date=now,
    )
    assert res2 is not None
    assert res2.id == payment.id
    assert res2.status == PaymentStatus.PAID

    # Event status must now be upgraded to 'processed'
    await db_session.refresh(event)
    assert event.status == "processed"
    assert event.payment_id == payment.id
