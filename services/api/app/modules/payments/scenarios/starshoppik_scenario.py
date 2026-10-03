"""
Scenario implementation for @StarShoppik_bot (Telegram Stars purchase via СБП).
Executes complete automated MTProto flow to generate invoice payment link,
supporting two-phase execution (pre-warmed fast-path with graceful full-path fallback).
"""

import re

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
    calculate_stars_from_amount,
)
from app.modules.payments.scenarios.state import (
    clear_scenario_prepared,
    is_scenario_prepared,
    set_scenario_prepared,
)

logger = get_logger(__name__)


class StarShoppikBotScenario(BasePaymentScenario):
    """
    Automated purchase scenario for @StarShoppik_bot.
    Flow:
    1. /start -> handle channel subscription if prompted.
    2. Click 'Купить Stars'.
    3. Click 'Купить другу'.
    4. Click 'Ввести своё количество'.
    5. Send desired amount of stars.
    6. Send target recipient username (@username).
    7. Select 'СБП' payment method.
    8. Extract and return invoice URL from button.
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

            from app.modules.accounts.session_pool import compute_account_fingerprint

            acc_fp = compute_account_fingerprint(ctx.account)

            # Check if chat is pre-warmed / waiting for amount
            is_prep = await is_scenario_prepared(
                ctx.account.id,
                self.scenario_id,
                expected_recipient=recipient,
                expected_bot_username=bot_username,
                expected_fingerprint=acc_fp,
            )
            if is_prep:
                try:
                    logger.info(
                        "starshoppik_attempting_fast_path",
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
                        "starshoppik_fast_path_failed_falling_back",
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
            "starshoppik_fast_path_sending_stars_count",
            stars_count=stars_count,
        )
        send_stars_msg = await client.send_message(bot_username, str(stars_count))

        await wait_for_bot_message(
            client=client,
            peer=bot_username,
            predicate=lambda m: (
                "получателя" in (getattr(m, "text", "") or "").lower()
                or "@username" in (getattr(m, "text", "") or "").lower()
            ),
            timeout=5.0,
            min_id=send_stars_msg.id,
        )
        timer.record_stage(
            "send_stars_count",
            f"Ввод суммы ({stars_count} звёзд) [быстрый путь]",
        )

        logger.info("starshoppik_fast_path_sending_recipient", recipient=recipient)
        send_user_msg = await client.send_message(bot_username, recipient)

        method_prompt = await wait_for_bot_message(
            client=client,
            peer=bot_username,
            predicate=lambda m: (
                find_button_by_text(m, "сбп") is not None
                or "способ оплаты" in (getattr(m, "text", "") or "").lower()
            ),
            timeout=5.0,
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

        msg_text = getattr(invoice_msg, "text", "") or ""
        order_match = re.search(r"Заказ №\s*#?(\w+)", msg_text) or re.search(
            r"#(\d+)", msg_text
        )
        order_id = order_match.group(1) if order_match else None

        return ScenarioResult(
            payment_link=payment_link,
            meta={
                "stars_count": stars_count,
                "order_id": order_id,
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
        """Full-path: /start -> sub -> menus -> amount -> recipient -> sbp -> link."""
        _ = ctx
        logger.info("starshoppik_step_1_sending_start", bot=bot_username)
        start_cmd = (
            f"/start {settings.STARSHOPPIK_START_PARAM}"
            if settings.STARSHOPPIK_START_PARAM
            else "/start"
        )
        start_msg = await client.send_message(bot_username, start_cmd)

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

        sub_btn = find_button_by_text(first_reply, "подписался")
        if sub_btn:
            logger.info(
                "starshoppik_channel_sub_required",
                channel=channel_username,
            )
            await join_channel_safely(client, channel_username)
            await click_button_fast(client, sub_btn)

            main_menu = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: find_button_by_text(m, "купить stars") is not None,
                timeout=15.0,
            )
            timer.record_stage("verify_channel", "Подписка и подтверждение канала")
        else:
            main_menu = first_reply

        buy_stars_btn = find_button_by_text(main_menu, "купить stars")
        if not buy_stars_btn:
            raise AppException(
                message="Could not find 'Купить Stars' button in bot menu.",
                code="BOT_INTERACTION_ERROR",
                status_code=502,
            )
        await click_button_fast(client, buy_stars_btn)

        target_menu = await wait_for_bot_message(
            client=client,
            peer=bot_username,
            predicate=lambda m: (
                find_button_by_text(m, "другу") is not None
                or "получателя" in (getattr(m, "text", "") or "").lower()
            ),
            timeout=15.0,
        )
        timer.record_stage("open_stars_menu", "Переход в меню «Купить Stars»")

        buy_friend_btn = find_button_by_text(target_menu, "другу")
        if not buy_friend_btn:
            raise AppException(
                message="Could not find 'Купить другу' button in bot menu.",
                code="BOT_INTERACTION_ERROR",
                status_code=502,
            )
        await click_button_fast(client, buy_friend_btn)

        amount_menu = await wait_for_bot_message(
            client=client,
            peer=bot_username,
            predicate=lambda m: (
                find_button_by_text(m, "своё") is not None
                or "количество" in (getattr(m, "text", "") or "").lower()
            ),
            timeout=15.0,
        )
        timer.record_stage("select_gift_friend", "Выбор «Купить другу»")

        custom_amount_btn = find_button_by_text(
            amount_menu, "ввести своё"
        ) or find_button_by_text(amount_menu, "своё")
        if not custom_amount_btn:
            raise AppException(
                message="Could not find 'Ввести своё количество' button in bot menu.",
                code="BOT_INTERACTION_ERROR",
                status_code=502,
            )
        await click_button_fast(client, custom_amount_btn)
        timer.record_stage("select_custom_amount", "Выбор «Ввести своё количество»")

        send_stars_msg = await client.send_message(bot_username, str(stars_count))

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
        timer.record_stage("send_stars_count", f"Ввод суммы ({stars_count} звёзд)")

        send_user_msg = await client.send_message(bot_username, recipient)

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

        msg_text = getattr(invoice_msg, "text", "") or ""
        order_match = re.search(r"Заказ №\s*#?(\w+)", msg_text) or re.search(
            r"#(\d+)", msg_text
        )
        order_id = order_match.group(1) if order_match else None

        return ScenarioResult(
            payment_link=payment_link,
            meta={
                "stars_count": stars_count,
                "order_id": order_id,
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
        Background warmup for @StarShoppik_bot:
        1. Join channel if required.
        2. Send /start to bot.
        3. If 'подписался' button appears, click it.
        4. Click 'Купить Stars'.
        5. Click 'Купить другу'.
        6. Click 'Ввести своё количество'.
        7. Wait until prompt asks to enter amount.
        8. Mark chat as prepared in Redis.
        """
        bot_username = settings.STARSHOPPIK_BOT_USERNAME
        channel_username = settings.STARSHOPPIK_CHANNEL_USERNAME
        try:
            logger.info("starshoppik_prepare_started", account_id=str(account.id))
            await join_channel_safely(client, channel_username)

            start_cmd = (
                f"/start {settings.STARSHOPPIK_START_PARAM}"
                if settings.STARSHOPPIK_START_PARAM
                else "/start"
            )
            start_msg = await client.send_message(bot_username, start_cmd)
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

                main_menu = await wait_for_bot_message(
                    client=client,
                    peer=bot_username,
                    predicate=lambda m: (
                        find_button_by_text(m, "купить stars") is not None
                    ),
                    timeout=15.0,
                )
            else:
                main_menu = first_reply

            buy_stars_btn = find_button_by_text(main_menu, "купить stars")
            if not buy_stars_btn:
                raise AppException("Could not find 'Купить Stars' button in menu")
            await click_button_fast(client, buy_stars_btn)

            target_menu = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    find_button_by_text(m, "купить другу") is not None
                    or "выберите получателя" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
            )
            buy_friend_btn = find_button_by_text(target_menu, "купить другу")
            if not buy_friend_btn:
                raise AppException("Could not find 'Купить другу' button in menu")
            await click_button_fast(client, buy_friend_btn)

            amount_menu = await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    find_button_by_text(m, "ввести своё") is not None
                    or find_button_by_text(m, "своё") is not None
                    or "количество telegram stars"
                    in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
            )
            custom_amount_btn = find_button_by_text(
                amount_menu, "ввести своё"
            ) or find_button_by_text(amount_menu, "своё")
            if not custom_amount_btn:
                raise AppException("Could not find 'Ввести своё количество' button")
            await click_button_fast(client, custom_amount_btn)

            await wait_for_bot_message(
                client=client,
                peer=bot_username,
                predicate=lambda m: (
                    "введите своё количество" in (getattr(m, "text", "") or "").lower()
                    or "минимум: 50" in (getattr(m, "text", "") or "").lower()
                    or "введите количество" in (getattr(m, "text", "") or "").lower()
                ),
                timeout=15.0,
            )

            from app.modules.accounts.session_pool import compute_account_fingerprint

            acc_fp = compute_account_fingerprint(account)
            await set_scenario_prepared(
                account.id,
                self.scenario_id,
                context={
                    "recipient": None,
                    "bot_username": bot_username,
                    "fingerprint": acc_fp,
                },
            )
            logger.info("starshoppik_prepare_completed", account_id=str(account.id))
            return PreparationResult(status="ok")
        except Exception as exc:
            await clear_scenario_prepared(account.id, self.scenario_id)
            logger.warning(
                "starshoppik_prepare_failed",
                account_id=str(account.id),
                error=str(exc),
            )
            return PreparationResult(status="failed", reason=str(exc))
