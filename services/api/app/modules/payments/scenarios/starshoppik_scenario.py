"""
Scenario implementation for @StarShoppik_bot (Telegram Stars purchase via СБП).
Executes fast automated MTProto flow to generate invoice payment link.
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
from app.modules.payments.scenarios.stage_timer import StageTimer
from app.modules.payments.scenarios.stars_calculator import (
    calculate_stars_from_amount,
)

logger = get_logger(__name__)


class StarShoppikBotScenario(BasePaymentScenario):
    """
    Automated purchase scenario for @StarShoppik_bot.
    Flow:
    1. /start -> handle channel subscription if prompted ("Я подписался").
    2. Click 'Купить Stars' in main menu.
    3. Click 'Купить другу' in recipient selection.
    4. Click 'Ввести своё количество'.
    5. Send calculated stars count.
    6. Send recipient username.
    7. Select 'СБП' payment method (supports emoji and fee percentages).
    8. Extract and return invoice 'Перейти к оплате' URL.
    """

    scenario_id = "starshoppik_bot"
    name = "StarShoppik Bot (Telegram Stars via СБП)"
    description = (
        "Automated creation of Telegram Stars payment links through @StarShoppik_bot"
    )

    async def create_payment(self, ctx: ScenarioContext) -> ScenarioResult:
        bot_username = ctx.meta.get("bot_username") or settings.STARSHOPPIK_BOT_USERNAME
        channel_username = (
            ctx.meta.get("channel_username") or settings.STARSHOPPIK_CHANNEL_USERNAME
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

        timer = StageTimer()
        client: TelegramClient | None = None
        try:
            logger.info(
                "starshoppik_scenario_started",
                account_id=str(ctx.account.id),
                bot=bot_username,
                recipient=recipient,
                stars_count=stars_count,
                amount=str(ctx.amount),
            )
            client = await telegram_session_pool.get_connected_client(ctx.account)
            timer.record_stage("connect_session", "Подключение сессии из пула")

            # Step 1: Send /start
            logger.info("starshoppik_step_1_sending_start", bot=bot_username)
            start_msg = await client.send_message(bot_username, "/start")

            # Check bot response: either channel sub prompt or main menu
            first_reply = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    find_button_by_text(m, "подписался") is not None
                    or find_button_by_text(m, "купить stars") is not None
                    or "star shop" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
                min_id=start_msg.id,
            )
            timer.record_stage("send_start", "Команда /start и ответ бота")

            # Step 1b: Channel subscription if required
            sub_btn = find_button_by_text(first_reply, "подписался")
            if sub_btn:
                logger.info(
                    "starshoppik_channel_sub_required",
                    channel=channel_username,
                )
                await join_channel_safely(client, channel_username)
                await click_button_fast(client, sub_btn)
                logger.info("starshoppik_sub_verified_clicked")

                # Wait for main menu after subscription confirmation
                main_menu = await wait_for_bot_message(
                    client=client,
                    peer=bot_username,
                    predicate=lambda m: (
                        find_button_by_text(m, "купить stars") is not None
                    ),
                    timeout=15.0,
                )
                timer.record_stage(
                    "verify_channel", "Подписка на канал и подтверждение"
                )
            else:
                main_menu = first_reply

            # Step 2: Click 'Купить Stars'
            buy_stars_btn = find_button_by_text(main_menu, "купить stars")
            if not buy_stars_btn:
                raise AppException(
                    message="Could not find 'Купить Stars' button in main menu.",
                    code="BOT_INTERACTION_ERROR",
                    status_code=502,
                )
            logger.info("starshoppik_step_2_click_buy_stars")
            await click_button_fast(client, buy_stars_btn)

            # Step 3: Wait for recipient prompt & click 'Купить другу'
            recipient_prompt = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    find_button_by_text(m, "другу") is not None
                    or "получателя" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
            )
            timer.record_stage("open_stars_menu", "Кнопка «Купить Stars»")

            gift_friend_btn = find_button_by_text(recipient_prompt, "другу")
            if not gift_friend_btn:
                raise AppException(
                    message="Could not find 'Купить другу' button in bot response.",
                    code="BOT_INTERACTION_ERROR",
                    status_code=502,
                )
            logger.info("starshoppik_step_3_click_gift_friend")
            await click_button_fast(client, gift_friend_btn)

            # Step 4: Wait for count prompt & click 'Ввести своё количество'
            count_prompt = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    find_button_by_text(m, "своё") is not None
                    or "количество" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
            )
            timer.record_stage("select_gift_friend", "Кнопка «Купить другу»")

            custom_count_btn = find_button_by_text(count_prompt, "своё")
            if not custom_count_btn:
                raise AppException(
                    message="Could not find 'Ввести своё количество' button.",
                    code="BOT_INTERACTION_ERROR",
                    status_code=502,
                )
            logger.info("starshoppik_step_4_click_custom_count")
            await click_button_fast(client, custom_count_btn)

            # Step 5: Send desired stars count
            logger.info(
                "starshoppik_step_5_sending_stars_count",
                stars_count=stars_count,
            )
            send_stars_msg = await client.send_message(bot_username, str(stars_count))

            # Step 6: Wait for username prompt & send recipient username
            await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    "username" in (getattr(m, "text", "") or "").lower()
                    or "получателя" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
                min_id=send_stars_msg.id,
            )
            timer.record_stage(
                "select_custom_amount", f"Ввод суммы ({stars_count} звёзд)"
            )

            logger.info("starshoppik_step_6_sending_recipient", recipient=recipient)
            send_user_msg = await client.send_message(bot_username, recipient)

            # Step 7: Wait for payment method prompt & click 'СБП'
            method_prompt = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    find_button_by_text(m, "сбп") is not None
                    or "способ оплаты" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
                min_id=send_user_msg.id,
            )
            timer.record_stage("send_recipient", f"Ввод получателя ({recipient})")

            sbp_button = find_button_by_text(method_prompt, "сбп")
            if not sbp_button:
                raise AppException(
                    message="Could not find 'СБП' payment button in bot response.",
                    code="BOT_INTERACTION_ERROR",
                    status_code=502,
                )
            logger.info(
                "starshoppik_step_7_click_sbp",
                button_text=getattr(sbp_button, "text", ""),
            )
            await click_button_fast(client, sbp_button)

            # Step 8: Wait for invoice order message with payment URL
            invoice_msg = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: find_url_button(m) is not None,
                timeout=20.0,
            )
            timer.record_stage("select_sbp_method", "Выбор способа оплаты СБП")

            url_button_info = find_url_button(invoice_msg)
            if not url_button_info:
                raise AppException(
                    message="Invoice message received without payment link button.",
                    code="BOT_INTERACTION_ERROR",
                    status_code=502,
                )

            _, payment_link = url_button_info
            logger.info(
                "starshoppik_step_8_invoice_link_extracted",
                payment_link=payment_link,
            )
            timer.record_stage("extract_payment_link", "Получение ссылки на оплату")

            return ScenarioResult(
                payment_link=payment_link,
                meta={
                    "stars_count": stars_count,
                    "recipient_username": recipient,
                    "bot_username": bot_username,
                    "payment_method": "СБП",
                    "stage_timings": timer.stages,
                    "scenario_duration_sec": timer.total_duration_sec,
                },
            )

        except AppException:
            raise
        except TimeoutError as e:
            logger.error("StarShoppik bot interaction timed out", error=str(e))
            raise AppException(
                message=f"Timeout waiting for response from @{bot_username}: {e}",
                code="BOT_TIMEOUT",
                status_code=504,
            ) from e
        except Exception as e:
            logger.error("Unexpected error in StarShoppik scenario", error=str(e))
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
        Background warmup for @StarShoppik_bot:
        1. Join channel if required.
        2. Send /start to bot.
        3. If 'подписался' button appears, click it.
        """
        bot_username = settings.STARSHOPPIK_BOT_USERNAME
        channel_username = settings.STARSHOPPIK_CHANNEL_USERNAME
        try:
            logger.info(
                "starshoppik_prepare_started",
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
                    find_button_by_text(m, "подписался") is not None
                    or find_button_by_text(m, "купить stars") is not None
                    or "star shop" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
                min_id=start_msg.id,
            )

            sub_btn = find_button_by_text(first_reply, "подписался")
            if sub_btn:
                logger.info("starshoppik_prepare_click_sub_verified")
                await click_button_fast(client, sub_btn)

            logger.info("starshoppik_prepare_completed", account_id=str(account.id))
        except Exception as exc:
            logger.warning(
                "starshoppik_prepare_failed",
                account_id=str(account.id),
                error=str(exc),
            )
