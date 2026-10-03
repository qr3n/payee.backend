"""
Scenario implementation for @HelperStars_Robot (Telegram Stars purchase via СБП).
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
    PreparationResult,
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
    allocate_unique_stars_for_scenario,
    calculate_stars_from_amount,
)
from app.modules.payments.scenarios.state import (
    clear_scenario_prepared,
    is_scenario_prepared,
    release_scenario_stars_reservation,
    set_scenario_prepared,
)

logger = get_logger(__name__)


class HelperStarsBotScenario(BasePaymentScenario):
    """
    Automated purchase scenario for @HelperStars_Robot.
    Flow:
    1. /start -> handle language selection & channel subscription if prompted.
    2. Click 'Купить звёзды' in main menu.
    3. Click 'Купить звёзды' in category menu.
    4. Send target recipient username (@username).
    5. Send calculated stars count.
    6. Click 'Подтвердить' creation of bill.
    7. Select 'СБП' payment method.
    8. Extract and return invoice URL from button.
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

        base_stars = calculate_stars_from_amount(
            amount=ctx.amount,
            currency=ctx.currency,
            rate=ctx.meta.get("rate"),
        )
        stars_count, stars_delta = await allocate_unique_stars_for_scenario(
            scenario_id=self.scenario_id,
            base_stars=base_stars,
        )

        timer = StageTimer()
        client: TelegramClient | None = None
        try:
            logger.info(
                "helperstars_scenario_started",
                account_id=str(ctx.account.id),
                bot=bot_username,
                recipient=recipient,
                base_stars=base_stars,
                stars_count=stars_count,
                stars_delta=stars_delta,
                amount=str(ctx.amount),
            )
            client = await telegram_session_pool.get_connected_client(ctx.account)
            timer.record_stage("connect_session", "Подключение сессии из пула")

            # Check if chat is pre-warmed / waiting for amount
            is_prep = await is_scenario_prepared(
                ctx.account.id, self.scenario_id, expected_recipient=recipient
            )
            if is_prep:
                try:
                    logger.info(
                        "helperstars_attempting_fast_path",
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
                    res.meta["base_stars_count"] = base_stars
                    res.meta["stars_delta"] = stars_delta
                    return res
                except Exception as exc:
                    logger.warning(
                        "helperstars_fast_path_failed_falling_back",
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
            res.meta["base_stars_count"] = base_stars
            res.meta["stars_delta"] = stars_delta
            return res

        except AppException:
            await release_scenario_stars_reservation(self.scenario_id, stars_count)
            raise
        except TimeoutError as e:
            await release_scenario_stars_reservation(self.scenario_id, stars_count)
            logger.error("HelperStars bot interaction timed out", error=str(e))
            raise AppException(
                message=f"Timeout waiting for response from @{bot_username}: {e}",
                code="BOT_TIMEOUT",
                status_code=504,
            ) from e
        except Exception as e:
            await release_scenario_stars_reservation(self.scenario_id, stars_count)
            logger.error("Unexpected error in HelperStars scenario", error=str(e))
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
            "helperstars_fast_path_sending_stars_count",
            stars_count=stars_count,
        )
        send_stars_msg = await client.send_message(bot_username, str(stars_count))

        bill_msg = await wait_for_bot_message(
            client=client,
            peer=bot_username,
            predicate=lambda m: (
                find_button_by_text(m, "оплатить") is not None
                or find_button_by_text(m, "подтвердить") is not None
                or "деталями заказа" in (getattr(m, "text", "") or "").lower()
                or "счёт" in (getattr(m, "text", "") or "").lower()
            ),
            timeout=2.5,
            min_id=send_stars_msg.id,
        )
        timer.record_stage(
            "send_stars_count",
            f"Ввод суммы ({stars_count} звёзд) [быстрый путь]",
        )

        confirm_btn = find_button_by_text(bill_msg, "оплатить") or find_button_by_text(
            bill_msg, "подтвердить"
        )
        if not confirm_btn:
            raise AppException(
                message="Could not find payment button in formed bill.",
                code="BOT_INTERACTION_ERROR",
                status_code=502,
            )
        await click_button_fast(client, confirm_btn)

        methods_msg = await wait_for_bot_message(
            client=client,
            peer=bot_username,
            predicate=lambda m: (
                find_button_by_text(m, "сбп") is not None
                or "способ оплаты" in (getattr(m, "text", "") or "").lower()
            ),
            timeout=5.0,
        )
        timer.record_stage("confirm_bill", "Подтверждение создания счёта")

        sbp_button = find_button_by_text(methods_msg, "сбп")
        if not sbp_button:
            raise AppException(
                message="Could not find 'СБП' payment button in bot response.",
                code="BOT_INTERACTION_ERROR",
                status_code=502,
            )
        await click_button_fast(client, sbp_button)

        invoice_msg = await wait_for_bot_message(
            client=client,
            peer=bot_username,
            predicate=lambda m: find_url_button(m) is not None,
            timeout=10.0,
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
        timer.record_stage("extract_payment_link", "Получение ссылки на оплату")

        return ScenarioResult(
            payment_link=payment_link,
            meta={
                "stars_count": stars_count,
                "recipient_username": recipient,
                "bot_username": bot_username,
                "payment_method": "СБП",
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
        """
        Full-path: /start -> language -> channel -> menus -> recipient
        -> stars -> sbp -> link.
        """
        _ = ctx
        logger.info("helperstars_step_1_sending_start", bot=bot_username)
        start_cmd = (
            f"/start {settings.HELPERSTARS_START_PARAM}"
            if settings.HELPERSTARS_START_PARAM
            else "/start"
        )
        start_msg = await client.send_message(bot_username, start_cmd)

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
        timer.record_stage("send_start", "Команда /start и ответ бота")

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
            timer.record_stage("select_language", "Выбор языка (Русский)")
        else:
            next_msg = first_reply

        btn_channel = find_button_by_text(
            next_msg, "helperstars"
        ) or find_button_by_text(next_msg, "подписаться")
        if (
            btn_channel
            or "подписаться" in (getattr(next_msg, "text", "") or "").lower()
        ):
            logger.info("helperstars_step_1b_channel_sub_required")
            await join_channel_safely(client, channel_username)
            main_menu = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    find_button_by_text(m, "купить звёзды") is not None
                    or "текущий баланс" in (getattr(m, "text", "") or "").lower()
                    or "выберите действие" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
            )
            timer.record_stage("verify_channel", "Подписка на информационный канал")
        else:
            main_menu = next_msg

        buy_stars_btn = find_button_by_text(main_menu, "купить звёзды")
        if not buy_stars_btn:
            raise AppException(
                message="Could not find 'Купить звёзды' button in main menu.",
                code="BOT_INTERACTION_ERROR",
                status_code=502,
            )
        await click_button_fast(client, buy_stars_btn)

        cat_menu = await wait_for_bot_message(
            client=client,
            peer=bot_username,
            predicate=lambda m: (
                find_button_by_text(m, "купить звёзды") is not None
                or "введите юзернейм" in (getattr(m, "text", "") or "").lower()
            ),
            timeout=15.0,
        )
        timer.record_stage("open_stars_menu", "Переход в меню «Купить звёзды»")

        cat_btn = find_button_by_text(cat_menu, "купить звёзды")
        if cat_btn:
            await click_button_fast(client, cat_btn)

        await wait_for_bot_message(
            client=client,
            peer=bot_username,
            predicate=lambda m: (
                "введите юзернейм" in (getattr(m, "text", "") or "").lower()
                or "дарить звёзды" in (getattr(m, "text", "") or "").lower()
            ),
            timeout=15.0,
        )
        timer.record_stage("confirm_stars_category", "Подтверждение категории звёзд")

        send_user_msg = await client.send_message(bot_username, recipient)

        await wait_for_bot_message(
            client=client,
            peer=bot_username,
            predicate=lambda m: (
                "введите количество звёзд" in (getattr(m, "text", "") or "").lower()
                or "получатель:" in (getattr(m, "text", "") or "").lower()
            ),
            timeout=15.0,
            min_id=send_user_msg.id,
        )
        timer.record_stage("send_recipient", f"Ввод получателя ({recipient})")

        send_stars_msg = await client.send_message(bot_username, str(stars_count))

        bill_msg = await wait_for_bot_message(
            client=client,
            peer=bot_username,
            predicate=lambda m: (
                find_button_by_text(m, "оплатить") is not None
                or find_button_by_text(m, "подтвердить") is not None
                or "деталями заказа" in (getattr(m, "text", "") or "").lower()
                or "счёт" in (getattr(m, "text", "") or "").lower()
            ),
            timeout=15.0,
            min_id=send_stars_msg.id,
        )
        timer.record_stage("send_stars_count", f"Ввод количества ({stars_count} звёзд)")

        confirm_btn = find_button_by_text(bill_msg, "оплатить") or find_button_by_text(
            bill_msg, "подтвердить"
        )
        if not confirm_btn:
            raise AppException(
                message="Could not find payment button in formed bill.",
                code="BOT_INTERACTION_ERROR",
                status_code=502,
            )
        await click_button_fast(client, confirm_btn)

        methods_msg = await wait_for_bot_message(
            client=client,
            peer=bot_username,
            predicate=lambda m: (
                find_button_by_text(m, "сбп") is not None
                or "способ оплаты" in (getattr(m, "text", "") or "").lower()
            ),
            timeout=15.0,
        )
        timer.record_stage("confirm_bill", "Подтверждение создания счёта")

        sbp_button = find_button_by_text(methods_msg, "сбп")
        if not sbp_button:
            raise AppException(
                message="Could not find 'СБП' payment button in bot response.",
                code="BOT_INTERACTION_ERROR",
                status_code=502,
            )
        await click_button_fast(client, sbp_button)

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
        timer.record_stage("extract_payment_link", "Получение ссылки на оплату")

        return ScenarioResult(
            payment_link=payment_link,
            meta={
                "stars_count": stars_count,
                "recipient_username": recipient,
                "bot_username": bot_username,
                "payment_method": "СБП",
                "is_fast_path": False,
                "stage_timings": timer.stages,
                "scenario_duration_sec": timer.total_duration_sec,
            },
        )

    async def prepare(
        self,
        account: TelegramAccount,
        client: TelegramClient,
    ) -> PreparationResult:
        """
        Background warmup for @HelperStars_Robot:
        1. Join channel if required.
        2. Send /start to bot.
        3. If language selection appears, click 'Русский'.
        4. If channel subscription prompt appears, handle it.
        5. Click 'Купить звёзды' in main menu.
        6. Click category 'Купить звёзды'.
        7. Send recipient username.
        8. Wait until prompt asks to enter amount of stars.
        9. Mark chat as prepared in Redis.
        """
        bot_username = settings.HELPERSTARS_BOT_USERNAME
        channel_username = settings.HELPERSTARS_CHANNEL_USERNAME
        recipient = settings.STARS_RECIPIENT_USERNAME
        if not recipient.startswith("@"):
            recipient = f"@{recipient}"

        try:
            logger.info("helperstars_prepare_started", account_id=str(account.id))
            await join_channel_safely(client, channel_username)

            start_cmd = (
                f"/start {settings.HELPERSTARS_START_PARAM}"
                if settings.HELPERSTARS_START_PARAM
                else "/start"
            )
            start_msg = await client.send_message(bot_username, start_cmd)
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

            btn_lang = find_button_by_text(first_reply, "русский")
            if btn_lang:
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

            btn_channel = find_button_by_text(
                next_msg, "helperstars"
            ) or find_button_by_text(next_msg, "подписаться")
            if (
                btn_channel
                or "подписаться" in (getattr(next_msg, "text", "") or "").lower()
            ):
                await join_channel_safely(client, channel_username)
                main_menu = await wait_for_bot_message(
                    client=client,
                    peer=bot_username,
                    predicate=lambda m: (
                        find_button_by_text(m, "купить звёзды") is not None
                        or "текущий баланс" in (getattr(m, "text", "") or "").lower()
                        or "выберите действие" in (getattr(m, "text", "") or "").lower()
                    ),
                    timeout=15.0,
                )
            else:
                main_menu = next_msg

            buy_stars_btn = find_button_by_text(main_menu, "купить звёзды")
            if not buy_stars_btn:
                raise AppException(
                    "Could not find 'Купить звёзды' button in main menu."
                )
            await click_button_fast(client, buy_stars_btn)

            cat_menu = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    find_button_by_text(m, "купить звёзды") is not None
                    or "введите юзернейм" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
            )

            cat_btn = find_button_by_text(cat_menu, "купить звёзды")
            if cat_btn:
                await click_button_fast(client, cat_btn)

            await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    "введите юзернейм" in (getattr(m, "text", "") or "").lower()
                    or "дарить звёзды" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
            )

            send_user_msg = await client.send_message(bot_username, recipient)

            await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    "введите количество звёзд" in (getattr(m, "text", "") or "").lower()
                    or "получатель:" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
                min_id=send_user_msg.id,
            )

            await set_scenario_prepared(
                account.id,
                self.scenario_id,
                context={"recipient": recipient, "bot_username": bot_username},
            )
            logger.info("helperstars_prepare_completed", account_id=str(account.id))
            return PreparationResult(status="ok")
        except Exception as exc:
            await clear_scenario_prepared(account.id, self.scenario_id)
            logger.warning(
                "helperstars_prepare_failed",
                account_id=str(account.id),
                error=str(exc),
            )
            return PreparationResult(status="failed", reason=str(exc))
