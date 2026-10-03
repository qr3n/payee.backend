"""
Unit tests for HelperStarsBotScenario.
Tests full 9-step flow, language selection, double click on 'Купить звёзды',
SBP button click, and error cases.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AppException
from app.modules.accounts.models import AccountStatus, TelegramAccount
from app.modules.payments.scenarios.base import ScenarioContext
from app.modules.payments.scenarios.helperstars_scenario import (
    HelperStarsBotScenario,
)
from tests.test_accounts_service import VALID_SESSION_STRING


@pytest.fixture
def test_account() -> TelegramAccount:
    return TelegramAccount(
        title="HelperStars Worker",
        session_string=VALID_SESSION_STRING,
        device_model="PC 64bit",
        system_version="Windows 11",
        app_version="5.2.2 x64",
        status=AccountStatus.ACTIVE,
    )


@pytest.fixture
def scenario_context(test_account: TelegramAccount) -> ScenarioContext:
    return ScenarioContext(
        client_user_id="user_buyer_helperstars",
        amount=Decimal("142.24"),
        currency="RUB",
        account=test_account,
        meta={"recipient_username": "@qr3nnn"},
    )


@pytest.mark.asyncio
async def test_helperstars_bot_scenario_full_flow(
    scenario_context: ScenarioContext,
) -> None:
    # 1. Language prompt
    btn_lang = MagicMock()
    btn_lang.text = "🇷🇺 Русский"
    btn_lang.click = AsyncMock()

    msg_lang = MagicMock()
    msg_lang.out = False
    msg_lang.id = 501
    msg_lang.text = "Select language"
    msg_lang.buttons = [[btn_lang]]

    # 2. Main menu
    btn_buy_1 = MagicMock()
    btn_buy_1.text = "⭐️ Купить звёзды"
    btn_buy_1.click = AsyncMock()

    msg_main_menu = MagicMock()
    msg_main_menu.out = False
    msg_main_menu.id = 502
    msg_main_menu.text = (
        "💼 Текущий баланс: 0.00 USD (~0.00 RUB).\nВыберите действие 👇"
    )
    msg_main_menu.buttons = [[btn_buy_1]]

    # 3. Submenu
    btn_buy_2 = MagicMock()
    btn_buy_2.text = "⭐️ Купить звёзды"
    btn_buy_2.click = AsyncMock()

    msg_submenu = MagicMock()
    msg_submenu.out = False
    msg_submenu.id = 503
    msg_submenu.text = "⭐️ Выберите, что именно вы хотите купить 👇"
    msg_submenu.buttons = [[btn_buy_2]]

    # 4. Username prompt
    msg_user_prompt = MagicMock()
    msg_user_prompt.out = False
    msg_user_prompt.id = 504
    msg_user_prompt.text = (
        "⭐️ Покупка звёзд\n"
        "🔎 Введите юзернейм пользователя, которому будем дарить звёзды:"
    )
    msg_user_prompt.buttons = []

    # 5. Count prompt
    msg_count_prompt = MagicMock()
    msg_count_prompt.out = False
    msg_count_prompt.id = 505
    msg_count_prompt.text = (
        "⭐️ Покупка звёзд\n"
        "👤 Получатель: @qr3nnn.\n"
        "• Минимум: 50 звёзд\n"
        "🔎 Введите количество звёзд для покупки:"
    )
    msg_count_prompt.buttons = []

    # 6. Bill formed
    btn_confirm = MagicMock()
    btn_confirm.text = "✅ Оплатить"
    btn_confirm.click = AsyncMock()

    msg_bill = MagicMock()
    msg_bill.out = False
    msg_bill.id = 506
    msg_bill.text = (
        "♻️ Счёт сформирован.\n"
        "🕙 Счёт действителен в течение 3 минут.\n"
        "Подтвердите покупку 👇"
    )
    msg_bill.buttons = [[btn_confirm]]

    # 7. Payment methods
    btn_sbp = MagicMock()
    btn_sbp.text = "СБП"
    btn_sbp.click = AsyncMock()

    msg_methods = MagicMock()
    msg_methods.out = False
    msg_methods.id = 507
    msg_methods.text = (
        "❌ Недостаточно средств для оплаты заказа.\n👉 Выберите способ оплаты:"
    )
    msg_methods.buttons = [[btn_sbp]]

    # 8. Invoice with URL button
    btn_url = MagicMock()
    btn_url.text = "Оплатить"
    btn_url.url = "https://cardlink.link/transfer/TEST12345"

    msg_invoice = MagicMock()
    msg_invoice.out = False
    msg_invoice.id = 508
    msg_invoice.text = (
        "✅ Счёт сгенерирован\n❓ Для оплаты перейдите по ссылке и ожидайте пополнения"
    )
    msg_invoice.buttons = [[btn_url]]

    responses = [
        [msg_lang],
        [msg_main_menu],
        [msg_submenu],
        [msg_user_prompt],
        [msg_count_prompt],
        [msg_bill],
        [msg_methods],
        [msg_invoice],
    ]

    with (
        patch(
            "app.modules.payments.scenarios.helperstars_scenario.telegram_session_pool.get_connected_client"
        ) as mock_get_client,
        patch(
            "app.modules.payments.scenarios.helperstars_scenario.telegram_session_pool.touch",
            new=AsyncMock(),
        ) as mock_touch,
        patch(
            "app.modules.payments.scenarios.helperstars_scenario.join_channel_safely",
            new=AsyncMock(return_value=True),
        ),
    ):
        mock_client = AsyncMock()
        mock_client.is_user_authorized = AsyncMock(return_value=True)
        mock_client.is_connected = MagicMock(return_value=True)
        mock_client.get_messages = AsyncMock(side_effect=responses)

        start_msg = MagicMock()
        start_msg.id = 500
        mock_client.send_message = AsyncMock(return_value=start_msg)

        mock_get_client.return_value = mock_client

        scenario = HelperStarsBotScenario()
        result = await scenario.create_payment(scenario_context)

        assert result.payment_link == "https://cardlink.link/transfer/TEST12345"
        assert result.meta["stars_count"] == 142
        assert result.meta["base_stars_count"] == 142
        assert result.meta["stars_delta"] == 0
        assert result.meta["recipient_username"] == "@qr3nnn"
        assert result.meta["payment_method"] == "СБП"

        # Assert flow buttons were clicked
        btn_lang.click.assert_awaited_once()
        btn_buy_1.click.assert_awaited_once()
        btn_buy_2.click.assert_awaited_once()
        btn_confirm.click.assert_awaited_once()
        btn_sbp.click.assert_awaited_once()
        mock_touch.assert_awaited_once_with(scenario_context.account.id)


@pytest.mark.asyncio
async def test_helperstars_missing_sbp_error(
    scenario_context: ScenarioContext,
) -> None:
    msg_main = MagicMock()
    msg_main.out = False
    msg_main.id = 601
    msg_main.text = "Текущий баланс"
    btn_buy_1 = MagicMock()
    btn_buy_1.text = "⭐️ Купить звёзды"
    btn_buy_1.click = AsyncMock()
    msg_main.buttons = [[btn_buy_1]]

    msg_sub = MagicMock()
    msg_sub.out = False
    msg_sub.id = 602
    msg_sub.text = "Выберите, что именно"
    btn_buy_2 = MagicMock()
    btn_buy_2.text = "⭐️ Купить звёзды"
    btn_buy_2.click = AsyncMock()
    msg_sub.buttons = [[btn_buy_2]]

    msg_user = MagicMock()
    msg_user.out = False
    msg_user.id = 603
    msg_user.text = "Введите юзернейм"
    msg_user.buttons = []

    msg_cnt = MagicMock()
    msg_cnt.out = False
    msg_cnt.id = 604
    msg_cnt.text = "Введите количество звёзд"
    msg_cnt.buttons = []

    msg_bill = MagicMock()
    msg_bill.out = False
    msg_bill.id = 605
    msg_bill.text = "Счёт сформирован"
    btn_confirm = MagicMock()
    btn_confirm.text = "Оплатить"
    btn_confirm.click = AsyncMock()
    msg_bill.buttons = [[btn_confirm]]

    # Methods without SBP
    msg_methods_no_sbp = MagicMock()
    msg_methods_no_sbp.out = False
    msg_methods_no_sbp.id = 606
    msg_methods_no_sbp.text = (
        "Недостаточно средств для оплаты заказа.\n👉 Выберите способ оплаты:"
    )
    btn_other = MagicMock()
    btn_other.text = "CryptoBot"
    msg_methods_no_sbp.buttons = [[btn_other]]

    responses = [
        [msg_main],
        [msg_sub],
        [msg_user],
        [msg_cnt],
        [msg_bill],
        [msg_methods_no_sbp],
    ]

    with (
        patch(
            "app.modules.payments.scenarios.helperstars_scenario.telegram_session_pool.get_connected_client"
        ) as mock_get_client,
        patch(
            "app.modules.payments.scenarios.helperstars_scenario.telegram_session_pool.touch",
            new=AsyncMock(),
        ),
    ):
        mock_client = AsyncMock()
        mock_client.is_user_authorized = AsyncMock(return_value=True)
        mock_client.is_connected = MagicMock(return_value=True)
        mock_client.get_messages = AsyncMock(side_effect=responses)

        start_msg = MagicMock()
        start_msg.id = 600
        mock_client.send_message = AsyncMock(return_value=start_msg)

        mock_get_client.return_value = mock_client

        scenario = HelperStarsBotScenario()
        with pytest.raises(AppException) as exc_info:
            await scenario.create_payment(scenario_context)

        assert exc_info.value.code == "BOT_INTERACTION_ERROR"


@pytest.mark.asyncio
async def test_helperstars_scenario_prepare() -> None:
    """Test HelperStarsBotScenario prepare hook."""
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

    btn_lang = MagicMock()
    btn_lang.text = "Русский"

    msg_reply = MagicMock()
    msg_reply.id = 51
    msg_reply.out = False
    msg_reply.text = "Select language"
    msg_reply.buttons = [[btn_lang]]

    mock_client.get_messages = AsyncMock(return_value=[msg_reply])

    with (
        patch(
            "app.modules.payments.scenarios.helperstars_scenario.join_channel_safely",
            new=AsyncMock(return_value=True),
        ) as mock_join,
        patch(
            "app.modules.payments.scenarios.helperstars_scenario.click_button_fast",
            new=AsyncMock(),
        ) as mock_click,
    ):
        scenario = HelperStarsBotScenario()
        await scenario.prepare(account=account, client=mock_client)

        mock_join.assert_awaited_once()
        mock_client.send_message.assert_awaited_once()
        mock_click.assert_awaited_once_with(mock_client, btn_lang)
