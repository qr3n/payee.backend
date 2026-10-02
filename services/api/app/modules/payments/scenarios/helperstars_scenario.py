"""
Scenario implementation for @HelperStars_Robot (Telegram Stars purchase via СБП).
Executes fast automated MTProto flow to generate invoice payment link.
"""

from telethon import TelegramClient

from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logging import get_logger
from app.modules.accounts.session_pool import telegram_session_pool
from app.modules.payments.scenarios.base import (
    BasePaymentScenario,
    ScenarioContext,
    ScenarioResult,
)
from app.modules.payments.scenarios.bot_dialog_helper import (
    click_button_fast,
    find_button_by_text,
    find_url_button,
    join_channel_safely,
    wait_for_bot_message,
)
from app.modules.payments.scenarios.stars_calculator import (
    calculate_stars_from_amount,
)

logger = get_logger(__name__)


class HelperStarsBotScenario(BasePaymentScenario):
    """
    Automated purchase scenario for @HelperStars_Robot.
    Flow:
    1. /start -> language selection ("Русский") if prompted.
    2. Channel subscription check ("HelperStars_Rezerv") if prompted.
    3. Main menu -> Click "Купить звёзды" (1st click).
    4. Submenu -> Click "Купить звёзды" (2nd click).
    5. Send target recipient username.
    6. Send calculated stars count.
    7. Click "Оплатить" in formed bill message.
    8. Select "СБП" from payment methods.
    9. Extract and return invoice 'Оплатить' URL.
    """

    scenario_id = "helperstars_bot"
    name = "HelperStars Bot (Telegram Stars via СБП)"
    description = (
        "Automated creation of Telegram Stars payment links through @HelperStars_Robot"
    )

    async def create_payment(self, ctx: ScenarioContext) -> ScenarioResult:
        bot_username = ctx.meta.get("bot_username") or settings.HELPERSTARS_BOT_USERNAME
        channel_username = (
            ctx.meta.get("channel_username") or settings.HELPERSTARS_CHANNEL_USERNAME
        )
        recipient = (
            ctx.meta.get("recipient_username") or settings.STARS_RECIPIENT_USERNAME
        )
        if not recipient.startswith("@"):
            recipient = f"@{recipient}"

        stars_count = calculate_stars_from_amount(
            amount=ctx.amount,
            currency=ctx.currency,
            rate=ctx.meta.get("rate"),
        )

        client: TelegramClient | None = None
        try:
            logger.info(
                "helperstars_scenario_started",
                account_id=str(ctx.account.id),
                bot=bot_username,
                recipient=recipient,
                stars_count=stars_count,
                amount=str(ctx.amount),
            )
            client = await telegram_session_pool.get_connected_client(ctx.account)

            # Step 1: Send /start
            logger.info("helperstars_step_1_sending_start", bot=bot_username)
            start_msg = await client.send_message(bot_username, "/start")

            # Check bot response: could be language selection, channel sub, or main menu
            first_reply = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    find_button_by_text(m, "русский") is not None
                    or find_button_by_text(m, "купить звёзды") is not None
                    or "подписаться" in (getattr(m, "text", "") or "").lower()
                    or "текущий баланс" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
                min_id=start_msg.id,
            )

            # 1a. Handle language selection if prompted
            btn_lang = find_button_by_text(first_reply, "русский")
            if btn_lang:
                logger.info("helperstars_step_1a_select_language")
                await click_button_fast(client, btn_lang)
                next_msg = await wait_for_bot_message(
                    client=client,
                    peer=bot_username,
                    predicate=lambda m: (
                        find_button_by_text(m, "купить звёзды") is not None
                        or "подписаться" in (getattr(m, "text", "") or "").lower()
                        or "текущий баланс" in (getattr(m, "text", "") or "").lower()
                    ),
                    timeout=15.0,
                )
            else:
                next_msg = first_reply

            # 1b. Handle channel subscription if prompted
            if "подписаться" in (getattr(next_msg, "text", "") or "").lower():
                logger.info(
                    "helperstars_step_1b_channel_sub_required",
                    channel=channel_username,
                )
                await join_channel_safely(client, channel_username)
                main_menu = await wait_for_bot_message(
                    client=client,
                    peer=bot_username,
                    predicate=lambda m: (
                        find_button_by_text(m, "купить звёзды") is not None
                        or "текущий баланс" in (getattr(m, "text", "") or "").lower()
                    ),
                    timeout=15.0,
                )
            else:
                main_menu = next_msg

            # Step 2: Main menu -> click "Купить звёзды" (1st click)
            btn_buy_1 = find_button_by_text(main_menu, "купить звёзды")
            if not btn_buy_1:
                raise AppException(
                    message="Could not find 'Купить звёзды' button in main menu.",
                    code="BOT_INTERACTION_ERROR",
                    status_code=502,
                )
            logger.info("helperstars_step_2_click_buy_stars_1")
            await click_button_fast(client, btn_buy_1)

            # Step 3: Submenu -> click "Купить звёзды" (2nd click)
            submenu_msg = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    "выберите, что именно" in (getattr(m, "text", "") or "").lower()
                    or find_button_by_text(m, "купить звёзды") is not None
                ),
                timeout=15.0,
            )

            btn_buy_2 = find_button_by_text(submenu_msg, "купить звёзды")
            if not btn_buy_2:
                raise AppException(
                    message="Could not find 'Купить звёзды' in category submenu.",
                    code="BOT_INTERACTION_ERROR",
                    status_code=502,
                )
            logger.info("helperstars_step_3_click_buy_stars_2")
            await click_button_fast(client, btn_buy_2)

            # Step 4: Wait for username prompt & send recipient username
            await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    "юзернейм" in (getattr(m, "text", "") or "").lower()
                    or "пользователя" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
            )

            logger.info("helperstars_step_4_sending_recipient", recipient=recipient)
            send_user_msg = await client.send_message(bot_username, recipient)

            # Step 5: Wait for stars count prompt & send calculated stars
            await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    "количество звёзд" in (getattr(m, "text", "") or "").lower()
                    or "минимум" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
                min_id=send_user_msg.id,
            )

            logger.info(
                "helperstars_step_5_sending_stars_count",
                stars_count=stars_count,
            )
            send_stars_msg = await client.send_message(bot_username, str(stars_count))

            # Step 6: Wait for bill formed message & click 'Оплатить'
            bill_msg = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    find_button_by_text(m, "оплатить") is not None
                    or "счёт сформирован" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
                min_id=send_stars_msg.id,
            )

            btn_confirm_pay = find_button_by_text(bill_msg, "оплатить")
            if not btn_confirm_pay:
                raise AppException(
                    message="Could not find 'Оплатить' button in formed bill.",
                    code="BOT_INTERACTION_ERROR",
                    status_code=502,
                )
            logger.info("helperstars_step_6_click_confirm_pay")
            await click_button_fast(client, btn_confirm_pay)

            # Step 7: Wait for insufficient funds / payment methods & click 'СБП'
            methods_msg = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    find_button_by_text(m, "сбп") is not None
                    or "способ оплаты" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
            )

            btn_sbp = find_button_by_text(methods_msg, "сбп")
            if not btn_sbp:
                raise AppException(
                    message="Could not find 'СБП' payment button.",
                    code="BOT_INTERACTION_ERROR",
                    status_code=502,
                )
            logger.info("helperstars_step_7_click_sbp")
            await click_button_fast(client, btn_sbp)

            # Step 8: Wait for invoice generated message with link button
            invoice_msg = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: find_url_button(m) is not None,
                timeout=20.0,
            )

            url_button_info = find_url_button(invoice_msg)
            if not url_button_info:
                raise AppException(
                    message="Invoice message received without payment link button.",
                    code="BOT_INTERACTION_ERROR",
                    status_code=502,
                )

            _, payment_link = url_button_info
            logger.info(
                "helperstars_step_8_invoice_link_extracted",
                payment_link=payment_link,
            )

            return ScenarioResult(
                payment_link=payment_link,
                meta={
                    "stars_count": stars_count,
                    "recipient_username": recipient,
                    "bot_username": bot_username,
                    "payment_method": "СБП",
                },
            )

        except AppException:
            raise
        except TimeoutError as e:
            logger.error("HelperStars bot interaction timed out", error=str(e))
            raise AppException(
                message=f"Timeout waiting for response from @{bot_username}: {e}",
                code="BOT_TIMEOUT",
                status_code=504,
            ) from e
        except Exception as e:
            logger.error("Unexpected error in HelperStars scenario", error=str(e))
            raise AppException(
                message=f"Failed to generate payment link via @{bot_username}: {e}",
                code="BOT_INTERACTION_FAILED",
                status_code=502,
            ) from e
        finally:
            await telegram_session_pool.touch(ctx.account.id)
