"""
Unit tests for StarShoppikBotScenario.
Tests full 8-step flow, subscription handling, emoji/percentage button matching,
and error handling.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.modules.accounts.models import AccountStatus, TelegramAccount
from app.modules.payments.scenarios.base import ScenarioContext
from app.modules.payments.scenarios.starshoppik_scenario import (
    StarShoppikBotScenario,
)
from tests.test_accounts_service import VALID_SESSION_STRING


@pytest.fixture
def test_account() -> TelegramAccount:
    return TelegramAccount(
        title="Shoppik Worker",
        session_string=VALID_SESSION_STRING,
        device_model="PC 64bit",
        system_version="Windows 11",
        app_version="5.2.2 x64",
        status=AccountStatus.ACTIVE,
    )


@pytest.fixture
def scenario_context(test_account: TelegramAccount) -> ScenarioContext:
    return ScenarioContext(
        client_user_id="user_buyer_shoppik",
        amount=Decimal("141.00"),
        currency="RUB",
        account=test_account,
        meta={"recipient_username": "@qr3nnn"},
    )


@pytest.mark.asyncio
async def test_starshoppik_bot_scenario_full_flow(
    scenario_context: ScenarioContext,
) -> None:
    # 1. Sub prompt
    btn_sub = MagicMock()
    btn_sub.text = "Я подписался"
    btn_sub.click = AsyncMock()

    msg_sub = MagicMock()
    msg_sub.out = False
    msg_sub.id = 201
    msg_sub.text = "Подпишитесь на наш канал"
    msg_sub.buttons = [[btn_sub]]

    # 2. Main menu
    btn_buy_stars = MagicMock()
    btn_buy_stars.text = "Купить Stars"
    btn_buy_stars.click = AsyncMock()

    msg_main_menu = MagicMock()
    msg_main_menu.out = False
    msg_main_menu.id = 202
    msg_main_menu.text = "Добро пожаловать в Star Shop!"
    msg_main_menu.buttons = [[btn_buy_stars]]

    # 3. Recipient selection
    btn_gift = MagicMock()
    btn_gift.text = "Купить другу"
    btn_gift.click = AsyncMock()

    msg_recipient = MagicMock()
    msg_recipient.out = False
    msg_recipient.id = 203
    msg_recipient.text = "⭐️ Покупка звёзд\n👉 Выберите получателя звёзд:"
    msg_recipient.buttons = [[btn_gift]]

    # 4. Count selection
    btn_custom_count = MagicMock()
    btn_custom_count.text = "Ввести своё количество"
    btn_custom_count.click = AsyncMock()

    msg_count = MagicMock()
    msg_count.out = False
    msg_count.id = 204
    msg_count.text = (
        "⭐️ Покупка звёзд\n🔢 Выберите количество Telegram Stars или введите своё:"
    )
    msg_count.buttons = [[btn_custom_count]]

    # 5. Username prompt
    msg_username = MagicMock()
    msg_username.out = False
    msg_username.id = 205
    msg_username.text = "⭐️ Покупка звёзд\n👤 Введите @username получателя:"
    msg_username.buttons = []

    # 6. Payment methods
    btn_sbp = MagicMock()
    btn_sbp.text = "СБП • 5%"
    btn_sbp.click = AsyncMock()

    msg_methods = MagicMock()
    msg_methods.out = False
    msg_methods.id = 206
    msg_methods.text = (
        "🛒 Выберите способ оплаты\n━━━━━━━━━━━━━━━━━━━━━━\n📦 Товар: 141 Stars"
    )
    msg_methods.buttons = [[btn_sbp]]

    # 7. Final invoice
    btn_pay_link = MagicMock()
    btn_pay_link.text = "Перейти к оплате"
    btn_pay_link.url = "https://gate.antilopay.com/payment/APAY12345"

    msg_invoice = MagicMock()
    msg_invoice.out = False
    msg_invoice.text = (
        "🛒 Оплата заказа\n"
        "📝 Заказ № #001908\n"
        "Способ: СБП 🏦\n"
        "👇 Нажмите «Перейти к оплате»"
    )
    msg_invoice.buttons = [[btn_pay_link]]

    responses = [
        [msg_sub],
        [msg_main_menu],
        [msg_recipient],
        [msg_count],
        [msg_username],
        [msg_methods],
        [msg_invoice],
    ]

    with (
        patch(
            "app.modules.payments.scenarios.starshoppik_scenario.telegram_session_pool.get_connected_client"
        ) as mock_get_client,
        patch(
            "app.modules.payments.scenarios.starshoppik_scenario.telegram_session_pool.touch",
            new=AsyncMock(),
        ) as mock_touch,
        patch(
            "app.modules.payments.scenarios.starshoppik_scenario.join_channel_safely",
            new=AsyncMock(return_value=True),
        ) as mock_join,
    ):
        mock_client = AsyncMock()
        mock_client.is_user_authorized = AsyncMock(return_value=True)
        mock_client.is_connected = MagicMock(return_value=True)
        mock_client.get_messages = AsyncMock(side_effect=responses)

        start_msg = MagicMock()
        start_msg.id = 200
        mock_client.send_message = AsyncMock(return_value=start_msg)

        mock_get_client.return_value = mock_client

        scenario = StarShoppikBotScenario()
        result = await scenario.create_payment(scenario_context)

        assert result.payment_link == "https://gate.antilopay.com/payment/APAY12345"
        assert result.meta["stars_count"] == 141
        assert result.meta["order_id"] == "001908"
        assert result.meta["recipient_username"] == "@qr3nnn"
        assert result.meta["payment_method"] == "СБП"

        # Assert actions occurred
        mock_join.assert_awaited_once()
        btn_sub.click.assert_awaited_once()
        btn_buy_stars.click.assert_awaited_once()
        btn_gift.click.assert_awaited_once()
        btn_custom_count.click.assert_awaited_once()
        btn_sbp.click.assert_awaited_once()
        mock_touch.assert_awaited_once_with(scenario_context.account.id)


@pytest.mark.asyncio
async def test_starshoppik_missing_sbp_button_error(
    scenario_context: ScenarioContext,
) -> None:
    msg_main = MagicMock()
    msg_main.out = False
    msg_main.id = 301
    msg_main.text = "Добро пожаловать в Star Shop!"
    btn_buy = MagicMock()
    btn_buy.text = "Купить Stars"
    btn_buy.click = AsyncMock()
    msg_main.buttons = [[btn_buy]]

    msg_rec = MagicMock()
    msg_rec.out = False
    msg_rec.id = 302
    msg_rec.text = "Выберите получателя"
    btn_friend = MagicMock()
    btn_friend.text = "Купить другу"
    btn_friend.click = AsyncMock()
    msg_rec.buttons = [[btn_friend]]

    msg_cnt = MagicMock()
    msg_cnt.out = False
    msg_cnt.id = 303
    msg_cnt.text = "Количество"
    btn_custom = MagicMock()
    btn_custom.text = "Ввести своё количество"
    btn_custom.click = AsyncMock()
    msg_cnt.buttons = [[btn_custom]]

    msg_user = MagicMock()
    msg_user.out = False
    msg_user.id = 304
    msg_user.text = "Введите username"
    msg_user.buttons = []

    # Missing SBP button
    msg_methods_no_sbp = MagicMock()
    msg_methods_no_sbp.out = False
    msg_methods_no_sbp.id = 305
    msg_methods_no_sbp.text = "Выберите способ оплаты"
    msg_methods_no_sbp.buttons = []

    responses = [
        [msg_main],
        [msg_rec],
        [msg_cnt],
        [msg_user],
        [msg_methods_no_sbp],
    ]

    with (
        patch(
            "app.modules.payments.scenarios.starshoppik_scenario.telegram_session_pool.get_connected_client"
        ) as mock_get_client,
        patch(
            "app.modules.payments.scenarios.starshoppik_scenario.telegram_session_pool.touch",
            new=AsyncMock(),
        ),
    ):
        mock_client = AsyncMock()
        mock_client.is_user_authorized = AsyncMock(return_value=True)
        mock_client.is_connected = MagicMock(return_value=True)
        mock_client.get_messages = AsyncMock(side_effect=responses)

        start_msg = MagicMock()
        start_msg.id = 300
        mock_client.send_message = AsyncMock(return_value=start_msg)

        mock_get_client.return_value = mock_client

        scenario = StarShoppikBotScenario()
        with pytest.raises(AppException) as exc_info:
            await scenario.create_payment(scenario_context)

        assert exc_info.value.code == "BOT_INTERACTION_ERROR"


@pytest.mark.asyncio
async def test_starshoppik_scenario_prepare() -> None:
    """Test StarShoppikBotScenario prepare hook."""
    account = TelegramAccount(
        title="Prepare Test",
        phone="+1234567890",
        session_string="1BVtsOH...",
        device_model="PC 64bit",
        system_version="Windows 11",
        app_version="5.2.2 x64",
        status=AccountStatus.ACTIVE,
    )
    mock_client = AsyncMock()
    mock_start = MagicMock()
    mock_start.id = 50
    mock_client.send_message = AsyncMock(return_value=mock_start)

    btn_sub = MagicMock()
    btn_sub.text = "Я подписался"

    msg_reply = MagicMock()
    msg_reply.id = 51
    msg_reply.out = False
    msg_reply.text = "Подпишитесь"
    msg_reply.buttons = [[btn_sub]]

    mock_client.get_messages = AsyncMock(return_value=[msg_reply])

    with (
        patch(
            "app.modules.payments.scenarios.starshoppik_scenario.join_channel_safely",
            new=AsyncMock(return_value=True),
        ) as mock_join,
        patch(
            "app.modules.payments.scenarios.starshoppik_scenario.click_button_fast",
            new=AsyncMock(),
        ) as mock_click,
    ):
        scenario = StarShoppikBotScenario()
        await scenario.prepare(account=account, client=mock_client)

        mock_join.assert_awaited_once()
        mock_client.send_message.assert_awaited_once()
        mock_click.assert_awaited_once_with(mock_client, btn_sub)


@pytest.mark.asyncio
async def test_starshoppik_bot_scenario_account_blocked(
    scenario_context: ScenarioContext,
) -> None:
    """Test that bot block message immediately raises ACCOUNT_BLOCKED_IN_BOT."""
    mock_client = AsyncMock()
    mock_start = MagicMock()
    mock_start.id = 100
    mock_client.send_message = AsyncMock(return_value=mock_start)

    msg_blocked = MagicMock()
    msg_blocked.id = 101
    msg_blocked.out = False
    msg_blocked.text = "🚫 Вы заблокированы в этом боте."
    msg_blocked.buttons = []

    mock_client.get_messages = AsyncMock(return_value=[msg_blocked])

    scenario = StarShoppikBotScenario()
    with pytest.raises(AppException) as exc_info:
        await scenario._create_payment_full(
            client=mock_client,
            ctx=scenario_context,
            timer=MagicMock(),
            bot_username="StarShoppik_bot",
            channel_username="StarShoppik_Channel",
            recipient="@qr3nnn",
            stars_count=50,
        )

    assert exc_info.value.code == "ACCOUNT_BLOCKED_IN_BOT"
    assert "заблокирован" in str(exc_info.value).lower()
