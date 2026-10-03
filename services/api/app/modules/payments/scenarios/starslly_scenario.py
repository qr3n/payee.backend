"""
Scenario implementation for @starslly_bot (Telegram Stars purchase via QR/СБП).
Executes complete automated MTProto flow to generate invoice payment link,
supporting two-phase execution (pre-warmed fast-path with graceful full-path fallback).
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
from app.modules.payments.scenarios.state import (
    clear_scenario_prepared,
    is_scenario_prepared,
    set_scenario_prepared,
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

        timer = StageTimer()
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
            timer.record_stage("connect_session", "Подключение сессии из пула")

            # Check if chat is pre-warmed / waiting for amount
            is_prep = await is_scenario_prepared(ctx.account.id, self.scenario_id)
            if is_prep:
                try:
                    logger.info(
                        "starslly_attempting_fast_path",
                        account_id=str(ctx.account.id),
                        stars_count=stars_count,
                    )
                    res = await self._create_payment_fast(
                        client=client,
                        ctx=ctx,
                        timer=timer,
                        bot_username=bot_username,
                        recipient=recipient,
                        stars_count=stars_count,
                    )
                    await clear_scenario_prepared(ctx.account.id, self.scenario_id)
                    return res
                except Exception as exc:
                    logger.warning(
                        "starslly_fast_path_failed_falling_back",
                        account_id=str(ctx.account.id),
                        error=str(exc),
                    )
                    timer.record_stage(
                        "fast_path_fallback",
                        "Сессия в боте устарела — переход на полный цикл",
                    )
                    await clear_scenario_prepared(ctx.account.id, self.scenario_id)

            # Full path
            res = await self._create_payment_full(
                client=client,
                ctx=ctx,
                timer=timer,
                bot_username=bot_username,
                channel_username=channel_username,
                recipient=recipient,
                stars_count=stars_count,
            )
            await clear_scenario_prepared(ctx.account.id, self.scenario_id)
            return res

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

    async def _create_payment_fast(
        self,
        client: TelegramClient,
        ctx: ScenarioContext,
        timer: StageTimer,
        bot_username: str,
        recipient: str,
        stars_count: int,
    ) -> ScenarioResult:
        """Fast-path: bot is already waiting for stars count."""
        _ = ctx
        logger.info(
            "starslly_fast_path_sending_stars_count",
            stars_count=stars_count,
        )
        send_stars_msg = await client.send_message(bot_username, str(stars_count))

        method_prompt = await wait_for_bot_message(
            client=client,
            peer=bot_username,
            predicate=lambda m: (
                find_button_by_text(m, "QR") is not None
                or find_button_by_text(m, "СБП") is not None
                or "способ оплаты" in (getattr(m, "text", "") or "").lower()
            ),
            timeout=5.0,
            min_id=send_stars_msg.id,
        )
        timer.record_stage(
            "send_stars_count",
            f"Ввод суммы ({stars_count} звёзд) [быстрый путь]",
        )

        sbp_button = (
            find_button_by_text(method_prompt, "Запасной")
            or find_button_by_text(method_prompt, "Резерв")
            or find_button_by_text(method_prompt, "QR/СБП")
            or find_button_by_text(method_prompt, "СБП")
        )
        if not sbp_button:
            raise AppException(
                message="Could not find 'QR/СБП' payment button in bot response.",
                code="BOT_INTERACTION_ERROR",
                status_code=502,
            )
        await click_button_fast(client, sbp_button)

        invoice_msg = await wait_for_bot_message(
            client=client,
            peer=bot_username,
            predicate=lambda m: (
                find_url_button(m) is not None
                or "поддержк" in (getattr(m, "text", "") or "").lower()
                or "проблем" in (getattr(m, "text", "") or "").lower()
                or "ошибк" in (getattr(m, "text", "") or "").lower()
            ),
            timeout=15.0,
        )
        timer.record_stage("select_sbp_method", "Выбор способа оплаты QR/СБП")

        url_button_info = find_url_button(invoice_msg)
        if not url_button_info:
            err_text = getattr(invoice_msg, "text", "") or "No payment link"
            raise AppException(
                message=f"Bot returned error instead of invoice link: {err_text}",
                code="BOT_INTERACTION_ERROR",
                status_code=502,
            )

        _, payment_link = url_button_info
        timer.record_stage("extract_payment_link", "Получение ссылки на оплату")

        return ScenarioResult(
            payment_link=payment_link,
            meta={
                "stars_count": stars_count,
                "recipient_username": recipient,
                "bot_username": bot_username,
                "payment_method": "QR/СБП",
                "is_fast_path": True,
                "stage_timings": timer.stages,
                "scenario_duration_sec": timer.total_duration_sec,
            },
        )

    async def _create_payment_full(
        self,
        client: TelegramClient,
        ctx: ScenarioContext,
        timer: StageTimer,
        bot_username: str,
        channel_username: str,
        recipient: str,
        stars_count: int,
    ) -> ScenarioResult:
        """Full-path: /start -> menus -> recipient -> stars -> payment link."""
        _ = ctx
        logger.info("starslly_step_1_sending_start", bot=bot_username)
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
        timer.record_stage("send_start", "Команда /start и ответ бота")

        check_sub_btn = find_button_by_text(first_reply, "Проверить подписку")
        if check_sub_btn:
            logger.info(
                "starslly_step_2_channel_sub_required", channel=channel_username
            )
            await join_channel_safely(client, channel_username)
            await click_button_fast(client, check_sub_btn)
            timer.record_stage("verify_channel", "Подписка на канал и проверка")

        buy_stars_msg = await client.send_message(bot_username, "⭐️ Купить Звезды")
        stars_prompt = await wait_for_bot_message(
            client=client,
            peer=bot_username,
            predicate=lambda m: (
                find_button_by_text(m, "Купить другу") is not None
                or "покупка для" in (getattr(m, "text", "") or "").lower()
                or "юзернейм" in (getattr(m, "text", "") or "").lower()
                or "username" in (getattr(m, "text", "") or "").lower()
            ),
            timeout=15.0,
            min_id=buy_stars_msg.id,
        )
        timer.record_stage("open_stars_menu", "Команда «⭐️ Купить Звезды»")

        gift_friend_btn = find_button_by_text(stars_prompt, "Купить другу")
        if gift_friend_btn:
            await click_button_fast(client, gift_friend_btn)
            await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    "юзернейм" in (getattr(m, "text", "") or "").lower()
                    or "/cancel" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
            )
            timer.record_stage("select_gift_friend", "Кнопка «Купить другу»")

        send_user_msg = await client.send_message(bot_username, recipient)

        await wait_for_bot_message(
            client=client,
            peer=bot_username,
            predicate=lambda m: (
                "количество звезд" in (getattr(m, "text", "") or "").lower()
                or "покупка для" in (getattr(m, "text", "") or "").lower()
            ),
            timeout=15.0,
            min_id=send_user_msg.id,
        )
        timer.record_stage("send_recipient", f"Ввод получателя ({recipient})")

        send_stars_msg = await client.send_message(bot_username, str(stars_count))

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
        timer.record_stage("send_stars_count", f"Ввод суммы ({stars_count} звёзд)")

        sbp_button = (
            find_button_by_text(method_prompt, "Запасной")
            or find_button_by_text(method_prompt, "Резерв")
            or find_button_by_text(method_prompt, "QR/СБП")
            or find_button_by_text(method_prompt, "СБП")
        )
        if not sbp_button:
            raise AppException(
                message="Could not find 'QR/СБП' payment button in bot response.",
                code="BOT_INTERACTION_ERROR",
                status_code=502,
            )
        await click_button_fast(client, sbp_button)

        invoice_msg = await wait_for_bot_message(
            client=client,
            peer=bot_username,
            predicate=lambda m: (
                find_url_button(m) is not None
                or "поддержк" in (getattr(m, "text", "") or "").lower()
                or "проблем" in (getattr(m, "text", "") or "").lower()
                or "ошибк" in (getattr(m, "text", "") or "").lower()
            ),
            timeout=20.0,
        )
        timer.record_stage("select_sbp_method", "Выбор способа оплаты QR/СБП")

        url_button_info = find_url_button(invoice_msg)
        if not url_button_info:
            err_text = getattr(invoice_msg, "text", "") or "No payment link"
            raise AppException(
                message=f"Bot returned error instead of invoice link: {err_text}",
                code="BOT_INTERACTION_ERROR",
                status_code=502,
            )

        _, payment_link = url_button_info
        timer.record_stage("extract_payment_link", "Получение ссылки на оплату")

        return ScenarioResult(
            payment_link=payment_link,
            meta={
                "stars_count": stars_count,
                "recipient_username": recipient,
                "bot_username": bot_username,
                "payment_method": "QR/СБП",
                "is_fast_path": False,
                "stage_timings": timer.stages,
                "scenario_duration_sec": timer.total_duration_sec,
            },
        )

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
        4. Send '⭐️ Купить Звезды'.
        5. Click 'Купить другу'.
        6. Send recipient username.
        7. Wait until bot prompt asks for stars count.
        8. Mark chat as prepared in Redis.
        """
        bot_username = settings.STARSLY_BOT_USERNAME
        channel_username = settings.STARSLY_CHANNEL_USERNAME
        recipient = settings.STARS_RECIPIENT_USERNAME
        if not recipient.startswith("@"):
            recipient = f"@{recipient}"

        try:
            logger.info(
                "starslly_prepare_started",
                account_id=str(account.id),
                bot=bot_username,
                recipient=recipient,
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

            buy_stars_msg = await client.send_message(bot_username, "⭐️ Купить Звезды")
            stars_prompt = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    find_button_by_text(m, "Купить другу") is not None
                    or "покупка для" in (getattr(m, "text", "") or "").lower()
                    or "юзернейм" in (getattr(m, "text", "") or "").lower()
                    or "username" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
                min_id=buy_stars_msg.id,
            )

            gift_friend_btn = find_button_by_text(stars_prompt, "Купить другу")
            if gift_friend_btn:
                await click_button_fast(client, gift_friend_btn)
                await wait_for_bot_message(
                    client=client,
                    peer=bot_username,
                    predicate=lambda m: (
                        "юзернейм" in (getattr(m, "text", "") or "").lower()
                        or "/cancel" in (getattr(m, "text", "") or "").lower()
                    ),
                    timeout=15.0,
                )

            send_user_msg = await client.send_message(bot_username, recipient)

            await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    "количество звезд" in (getattr(m, "text", "") or "").lower()
                    or "покупка для" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
                min_id=send_user_msg.id,
            )

            await set_scenario_prepared(account.id, self.scenario_id)
            logger.info(
                "starslly_prepare_completed_ready_for_amount",
                account_id=str(account.id),
            )
        except Exception as exc:
            await clear_scenario_prepared(account.id, self.scenario_id)
            logger.warning(
                "starslly_prepare_failed",
                account_id=str(account.id),
                error=str(exc),
            )
