"""
Unit and integration tests for StarsllyBotScenario and helpers.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.modules.accounts.models import AccountStatus, TelegramAccount
from app.modules.payments.scenarios.base import ScenarioContext
from app.modules.payments.scenarios.bot_dialog_helper import (
    find_button_by_text,
    find_url_button,
    wait_for_bot_message,
)
from app.modules.payments.scenarios.stars_calculator import (
    calculate_stars_from_amount,
)
from app.modules.payments.scenarios.starslly_scenario import (
    StarsllyBotScenario,
)
from tests.test_accounts_service import VALID_SESSION_STRING


def test_calculate_stars_from_amount() -> None:
    """Test stars calculation, rates, and boundaries."""
    # 1:1 standard
    assert calculate_stars_from_amount(Decimal("300")) == 300
    assert calculate_stars_from_amount(Decimal("1500.50")) == 1500

    # Min boundary (< 50) raises AppException
    with pytest.raises(AppException) as exc_min:
        calculate_stars_from_amount(Decimal("10"))
    assert exc_min.value.code == "AMOUNT_OUT_OF_RANGE"

    with pytest.raises(AppException) as exc_zero:
        calculate_stars_from_amount(Decimal("0"))
    assert exc_zero.value.code == "INVALID_AMOUNT"

    # Max boundary (> 30000) raises AppException
    with pytest.raises(AppException) as exc_max:
        calculate_stars_from_amount(Decimal("50000"))
    assert exc_max.value.code == "AMOUNT_OUT_OF_RANGE"

    # Valid boundaries
    assert calculate_stars_from_amount(Decimal("50")) == 50
    assert calculate_stars_from_amount(Decimal("30000")) == 30000

    # Custom rate
    assert calculate_stars_from_amount(Decimal("100"), rate=2.0) == 200


def test_find_button_by_text() -> None:
    """Test locating inline button by text substring."""
    btn1 = MagicMock()
    btn1.text = "Купить другу"
    btn2 = MagicMock()
    btn2.text = "Назад"

    msg = MagicMock()
    msg.buttons = [[btn1, btn2]]

    assert find_button_by_text(msg, "друг") == btn1
    assert find_button_by_text(msg, "НАЗАД") == btn2
    assert find_button_by_text(msg, "Оплатить") is None


def test_find_url_button() -> None:
    """Test locating link button with URL."""
    btn_callback = MagicMock()
    btn_callback.text = "Обычная кнопка"
    btn_callback.url = None

    btn_link = MagicMock()
    btn_link.text = "Оплатить"
    btn_link.url = "https://pay.example.com/invoice/999"

    msg = MagicMock()
    msg.buttons = [[btn_callback], [btn_link]]

    result = find_url_button(msg)
    assert result is not None
    button, url = result
    assert button == btn_link
    assert url == "https://pay.example.com/invoice/999"


@pytest.mark.asyncio
async def test_wait_for_bot_message_success() -> None:
    """Test wait_for_bot_message polling returns matching message."""
    mock_client = AsyncMock()

    msg1 = MagicMock()
    msg1.out = True  # sent by user, should be ignored

    msg2 = MagicMock()
    msg2.out = False
    msg2.text = "Заказ создан!"

    mock_client.get_messages.return_value = [msg1, msg2]

    result = await wait_for_bot_message(
        client=mock_client,
        peer="some_bot",
        predicate=lambda m: "заказ" in (getattr(m, "text", "") or "").lower(),
        timeout=2.0,
    )
    assert result == msg2


@pytest.mark.asyncio
async def test_starslly_bot_scenario_complete_flow() -> None:
    """
    Test complete 9-step flow of StarsllyBotScenario using mocked Telethon client.
    """
    account = TelegramAccount(
        title="Stars Worker",
        session_string=VALID_SESSION_STRING,
        device_model="PC 64bit",
        system_version="Windows 11",
        app_version="5.2.2 x64",
        status=AccountStatus.ACTIVE,
    )

    ctx = ScenarioContext(
        client_user_id="user_buyer_1",
        amount=Decimal("300.00"),
        currency="RUB",
        account=account,
        meta={"recipient_username": "@target_friend"},
    )

    # Prepare button and message mocks for each step
    btn_verify = MagicMock()
    btn_verify.text = "Проверить подписку"
    btn_verify.click = AsyncMock()

    msg_sub = MagicMock()
    msg_sub.out = False
    msg_sub.id = 101
    msg_sub.text = "Подпишитесь на канал"
    msg_sub.buttons = [[btn_verify]]

    btn_gift = MagicMock()
    btn_gift.text = "Купить другу"
    btn_gift.click = AsyncMock()

    msg_buy = MagicMock()
    msg_buy.out = False
    msg_buy.id = 103
    msg_buy.text = "Введите нужное количество звезд\nПокупка для: @me"
    msg_buy.buttons = [[btn_gift]]

    msg_ask_user = MagicMock()
    msg_ask_user.out = False
    msg_ask_user.id = 104
    msg_ask_user.text = "Введите юзернейм аккаунта, на который будут отправлены звезды."
    msg_ask_user.buttons = []

    msg_ask_stars = MagicMock()
    msg_ask_stars.out = False
    msg_ask_stars.id = 106
    msg_ask_stars.text = "Введите нужное количество звезд\nПокупка для: @target_friend"
    msg_ask_stars.buttons = []

    btn_sbp = MagicMock()
    btn_sbp.text = "QR/СБП"
    btn_sbp.click = AsyncMock()

    msg_payment_method = MagicMock()
    msg_payment_method.out = False
    msg_payment_method.id = 108
    msg_payment_method.text = "Стоимость: 495 ₽\nВыберите способ оплаты:"
    msg_payment_method.buttons = [[btn_sbp]]

    btn_pay_link = MagicMock()
    btn_pay_link.text = "Оплатить"
    btn_pay_link.url = "https://sbp.nspk.ru/pay/invoice_token_12345"

    msg_final_invoice = MagicMock()
    msg_final_invoice.out = False
    msg_final_invoice.id = 110
    msg_final_invoice.text = "Заказ создан!\nСумма: 495 ₽"
    msg_final_invoice.buttons = [[btn_pay_link]]

    # Step responses sequence
    responses = [
        [msg_sub],
        [msg_buy],
        [msg_ask_user],
        [msg_ask_stars],
        [msg_payment_method],
        [msg_final_invoice],
    ]

    with (
        patch(
            "app.modules.payments.scenarios.starslly_scenario.telegram_session_pool.get_connected_client"
        ) as mock_get_client,
        patch(
            "app.modules.payments.scenarios.starslly_scenario.telegram_session_pool.touch",
            new=AsyncMock(),
        ) as mock_touch,
        patch(
            "app.modules.payments.scenarios.starslly_scenario.join_channel_safely",
            new=AsyncMock(return_value=True),
        ),
    ):
        mock_client = AsyncMock()
        mock_client.is_user_authorized = AsyncMock(return_value=True)
        mock_client.is_connected = MagicMock(return_value=True)

        # Wire responses to get_messages
        mock_client.get_messages = AsyncMock(side_effect=responses)

        mock_start = MagicMock()
        mock_start.id = 100
        mock_client.send_message = AsyncMock(return_value=mock_start)

        mock_get_client.return_value = mock_client

        scenario = StarsllyBotScenario()
        result = await scenario.create_payment(ctx)

        assert result.payment_link == "https://sbp.nspk.ru/pay/invoice_token_12345"
        assert result.meta["stars_count"] == 300
        assert result.meta["base_stars_count"] == 300
        assert result.meta["stars_delta"] == 0
        assert result.meta["recipient_username"] == "@target_friend"

        # Verify button clicks
        btn_verify.click.assert_awaited_once()
        btn_gift.click.assert_awaited_once()
        btn_sbp.click.assert_awaited_once()
        mock_touch.assert_awaited_once_with(account.id)


@pytest.mark.asyncio
async def test_starslly_scenario_prepare() -> None:
    """Test StarsllyBotScenario prepare hook."""
    account = TelegramAccount(
        title="Prepare Test",
        phone="+1234567890",
        session_string=VALID_SESSION_STRING,
        device_model="PC 64bit",
        system_version="Windows 11",
        app_version="5.2.2 x64",
        status=AccountStatus.ACTIVE,
    )
    mock_client = AsyncMock()
    mock_start = MagicMock()
    mock_start.id = 50
    mock_client.send_message = AsyncMock(return_value=mock_start)

    btn_verify = MagicMock()
    btn_verify.text = "Проверить подписку"

    msg_reply = MagicMock()
    msg_reply.id = 51
    msg_reply.out = False
    msg_reply.text = "Подпишитесь"
    msg_reply.buttons = [[btn_verify]]

    btn_friend = MagicMock()
    btn_friend.text = "Купить другу"

    msg_menu = MagicMock()
    msg_menu.id = 52
    msg_menu.out = False
    msg_menu.text = "Купить звезды"
    msg_menu.buttons = [[btn_friend]]

    msg_username = MagicMock()
    msg_username.id = 53
    msg_username.out = False
    msg_username.text = "Введите юзернейм"
    msg_username.buttons = []

    msg_count = MagicMock()
    msg_count.id = 54
    msg_count.out = False
    msg_count.text = "Введите количество звезд (покупка для @test)"
    msg_count.buttons = []

    mock_client.get_messages = AsyncMock(
        side_effect=[[msg_reply], [msg_menu], [msg_username], [msg_count]]
    )

    with (
        patch(
            "app.modules.payments.scenarios.starslly_scenario.join_channel_safely",
            new=AsyncMock(return_value=True),
        ) as mock_join,
        patch(
            "app.modules.payments.scenarios.starslly_scenario.click_button_fast",
            new=AsyncMock(),
        ) as mock_click,
    ):
        scenario = StarsllyBotScenario()
        await scenario.prepare(account=account, client=mock_client)

        mock_join.assert_awaited_once()
        assert mock_client.send_message.await_count >= 1
        assert mock_click.await_count >= 1
