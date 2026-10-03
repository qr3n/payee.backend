"""
Scenario implementation for @starslly_bot (Telegram Stars purchase via QR/СБП).
Executes complete automated MTProto flow to generate invoice payment link.
"""

from telethon import TelegramClient

from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logging import get_logger
from app.modules.accounts.models import TelegramAccount
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


class StarsllyBotScenario(BasePaymentScenario):
    """
    Automated purchase scenario for @starslly_bot.
    Flow:
    1. /start -> handle channel subscription if prompted.
    2. Request '⭐️ Купить Звезды'.
    3. Click 'Купить другу' inline button.
    4. Provide target recipient username.
    5. Provide calculated stars count.
    6. Select 'QR/СБП' payment method.
    7. Extract and return invoice 'Оплатить' URL.
    """

    scenario_id = "starslly_bot"
    name = "Starslly Bot (Telegram Stars via QR/СБП)"
    description = (
        "Automated creation of Telegram Stars payment links through @starslly_bot"
    )

    async def create_payment(self, ctx: ScenarioContext) -> ScenarioResult:
        bot_username = ctx.meta.get("bot_username") or settings.STARSLY_BOT_USERNAME
        channel_username = (
            ctx.meta.get("channel_username") or settings.STARSLY_CHANNEL_USERNAME
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
                "starslly_scenario_started",
                account_id=str(ctx.account.id),
                bot=bot_username,
                recipient=recipient,
                stars_count=stars_count,
                amount=str(ctx.amount),
            )
            client = await telegram_session_pool.get_connected_client(ctx.account)

            # Step 1: Send /start
            logger.info("starslly_step_1_sending_start", bot=bot_username)
            start_msg = await client.send_message(bot_username, "/start")

            # Check bot response
            first_reply = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    find_button_by_text(m, "Проверить подписку") is not None
                    or find_button_by_text(m, "Купить Звезды") is not None
                    or "купить звезды" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
                min_id=start_msg.id,
            )

            # Step 2 & 3: Channel subscription check if prompted
            check_sub_btn = find_button_by_text(first_reply, "Проверить подписку")
            if check_sub_btn:
                logger.info(
                    "starslly_step_2_channel_sub_required",
                    channel=channel_username,
                )
                await join_channel_safely(client, channel_username)
                await click_button_fast(client, check_sub_btn)
                logger.info("starslly_step_3_sub_verified_clicked")

            # Step 4: Open stars menu
            logger.info("starslly_step_4_open_stars_menu")
            buy_stars_msg = await client.send_message(bot_username, "⭐️ Купить Звезды")

            # Step 5: Wait for stars prompt and click "Купить другу"
            stars_prompt = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    find_button_by_text(m, "Купить другу") is not None
                    or "покупка для" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
                min_id=buy_stars_msg.id,
            )

            gift_friend_btn = find_button_by_text(stars_prompt, "Купить другу")
            if not gift_friend_btn:
                raise AppException(
                    message="Could not find 'Купить другу' button in bot response.",
                    code="BOT_INTERACTION_ERROR",
                    status_code=502,
                )
            logger.info("starslly_step_5_click_gift_friend")
            await click_button_fast(client, gift_friend_btn)

            # Step 6: Wait for username prompt & send recipient username
            await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    "юзернейм" in (getattr(m, "text", "") or "").lower()
                    or "/cancel" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
            )

            logger.info("starslly_step_6_sending_recipient", recipient=recipient)
            send_user_msg = await client.send_message(bot_username, recipient)

            # Step 7: Wait for stars count prompt & send calculated stars
            await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    "количество звезд" in (getattr(m, "text", "") or "").lower()
                    and "покупка для" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
                min_id=send_user_msg.id,
            )

            logger.info(
                "starslly_step_7_sending_stars_count",
                stars_count=stars_count,
            )
            send_stars_msg = await client.send_message(bot_username, str(stars_count))

            # Step 8: Wait for payment method selection & click 'QR/СБП'
            method_prompt = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    find_button_by_text(m, "QR") is not None
                    or find_button_by_text(m, "СБП") is not None
                    or "способ оплаты" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
                min_id=send_stars_msg.id,
            )

            sbp_button = find_button_by_text(
                method_prompt, "QR/СБП"
            ) or find_button_by_text(method_prompt, "СБП")
            if not sbp_button:
                raise AppException(
                    message="Could not find 'QR/СБП' payment button in bot response.",
                    code="BOT_INTERACTION_ERROR",
                    status_code=502,
                )
            logger.info("starslly_step_8_click_sbp_button")
            await click_button_fast(client, sbp_button)

            # Step 9: Wait for invoice order message with 'Оплатить' link button
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
                "starslly_step_9_invoice_link_extracted",
                payment_link=payment_link,
            )

            return ScenarioResult(
                payment_link=payment_link,
                meta={
                    "stars_count": stars_count,
                    "recipient_username": recipient,
                    "bot_username": bot_username,
                    "payment_method": "QR/СБП",
                },
            )

        except AppException:
            raise
        except TimeoutError as e:
            logger.error("Starslly bot interaction timed out", error=str(e))
            raise AppException(
                message=f"Timeout waiting for response from @{bot_username}: {e}",
                code="BOT_TIMEOUT",
                status_code=504,
            ) from e
        except Exception as e:
            logger.error("Unexpected error in Starslly scenario", error=str(e))
            raise AppException(
                message=f"Failed to generate payment link via @{bot_username}: {e}",
                code="BOT_INTERACTION_FAILED",
                status_code=502,
            ) from e
        finally:
            await telegram_session_pool.touch(ctx.account.id)

    async def prepare(
        self,
        account: TelegramAccount,
        client: TelegramClient,
    ) -> None:
        """
        Background warmup for @starslly_bot:
        1. Join channel if required.
        2. Send /start to bot.
        3. If 'Проверить подписку' button appears, click it.
        """
        bot_username = settings.STARSLY_BOT_USERNAME
        channel_username = settings.STARSLY_CHANNEL_USERNAME
        try:
            logger.info(
                "starslly_prepare_started",
                account_id=str(account.id),
                bot=bot_username,
                channel=channel_username,
            )
            await join_channel_safely(client, channel_username)

            start_msg = await client.send_message(bot_username, "/start")
            first_reply = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    find_button_by_text(m, "Проверить подписку") is not None
                    or find_button_by_text(m, "Купить Звезды") is not None
                    or "купить звезды" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
                min_id=start_msg.id,
            )

            check_sub_btn = find_button_by_text(first_reply, "Проверить подписку")
            if check_sub_btn:
                logger.info("starslly_prepare_click_check_sub")
                await click_button_fast(client, check_sub_btn)

            logger.info("starslly_prepare_completed", account_id=str(account.id))
        except Exception as exc:
            logger.warning(
                "starslly_prepare_failed",
                account_id=str(account.id),
                error=str(exc),
            )
