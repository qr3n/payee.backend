import time
from contextlib import suppress
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from aiogram.types import CallbackQuery, Message
from aiogram_dialog import Dialog, DialogManager, Window
from aiogram_dialog.widgets.input import TextInput
from aiogram_dialog.widgets.kbd import (
    Button,
    Cancel,
    Row,
    ScrollingGroup,
    Select,
    SwitchTo,
)
from aiogram_dialog.widgets.text import Const, Format

from bot.client.api import ApiClient
from bot.dialogs.states import ScenariosSG

logger = structlog.stdlib.get_logger(__name__)


def format_payment_status(status: str) -> str:
    """Return friendly status badge."""
    mapping = {
        "pending": "⏳ Ожидает оплаты (PENDING)",
        "paid": "✅ Оплачен (PAID)",
        "cancelled": "❌ Отменен (CANCELLED)",
        "expired": "⏱ Истек (EXPIRED)",
        "failed": "💥 Ошибка сценария (FAILED)",
    }
    return mapping.get(status.lower(), status)


def format_stage_timings(
    stage_timings: list[dict[str, Any]] | None,
    total_sec: float | str | None = None,
) -> str:
    """Format granular stage timings breakdown for Telegram message."""
    if not stage_timings:
        if total_sec:
            total_display = (
                f"{total_sec:.2f} сек."
                if isinstance(total_sec, (int, float))
                else (
                    str(total_sec) if "сек" in str(total_sec) else f"{total_sec} сек."
                )
            )
            return f"⏱ <b>Всего:</b> <code>{total_display}</code>"
        return "—"

    lines: list[str] = []
    for item in stage_timings:
        desc = item.get("description") or item.get("stage", "Этап")
        dur = item.get("duration_sec", 0.0)
        lines.append(f"• {desc}: <code>{dur:.2f} сек.</code>")

    if isinstance(total_sec, (int, float)):
        total_display = f"{total_sec:.2f} сек."
    elif total_sec:
        total_display = (
            str(total_sec) if "сек" in str(total_sec) else f"{total_sec} сек."
        )
    else:
        total_display = "—"

    lines.append(f"⏱ <b>Итого:</b> <code>{total_display}</code>")
    return "\n".join(lines)


def safe_alert_text(text: str, max_length: int = 180) -> str:
    """Ensure callback answer text stays within Telegram's 200 character limit."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


# ==============================================================================
# Data Getters
# ==============================================================================
async def get_scenarios_list(
    dialog_manager: DialogManager,
    api_client: ApiClient,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch all registered scenarios."""
    try:
        scenarios = await api_client.list_scenarios()
        items = [
            {
                "id": s.scenario_id,
                "name": s.name,
                "description": s.description,
                "display_name": f"⚡ {s.name}",
            }
            for s in scenarios
        ]
        return {
            "scenarios": items,
            "has_scenarios": len(items) > 0,
            "last_action_msg": dialog_manager.dialog_data.pop("last_action_msg", None),
        }
    except Exception as exc:
        return {
            "scenarios": [],
            "has_scenarios": False,
            "last_action_msg": f"❌ Ошибка загрузки сценариев: {exc}",
        }


async def get_enter_amount_data(
    dialog_manager: DialogManager,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Context for entering test amount."""
    scenario_id = dialog_manager.dialog_data.get("selected_scenario_id", "starslly_bot")
    scenario_name = dialog_manager.dialog_data.get(
        "selected_scenario_name", scenario_id
    )
    return {
        "scenario_id": scenario_id,
        "scenario_name": scenario_name,
        "error_msg": dialog_manager.dialog_data.pop("amount_error", None),
    }


async def get_payment_result(
    dialog_manager: DialogManager,
    api_client: ApiClient,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch payment transaction details."""
    payment_id = dialog_manager.dialog_data.get("last_payment_id")
    if not payment_id:
        return {
            "id": "—",
            "scenario_id": "—",
            "amount": "—",
            "status": "—",
            "payment_link": "—",
            "has_link": False,
            "expires_at": "—",
            "account_id": "—",
            "meta_info": "—",
            "generation_time": "—",
            "timings_breakdown": "—",
            "action_msg": dialog_manager.dialog_data.pop("payment_action_msg", None),
        }

    try:
        payment = await api_client.get_payment(payment_id)
        if not payment:
            return {
                "id": str(payment_id),
                "scenario_id": "—",
                "amount": "—",
                "status": "Платеж не найден",
                "payment_link": "—",
                "has_link": False,
                "expires_at": "—",
                "account_id": "—",
                "meta_info": "—",
                "generation_time": "—",
                "timings_breakdown": "—",
                "action_msg": "Платеж не найден в базе данных",
            }

        stars = payment.meta.get("stars_count") or payment.meta.get("calculated_stars")
        meta_info = f"Звезд: {stars} ⭐️" if stars else "—"

        gen_time = dialog_manager.dialog_data.get("generation_time")
        if not gen_time and payment.meta.get("generation_time_sec") is not None:
            gen_time = f"{payment.meta.get('generation_time_sec')} сек."
        if not gen_time:
            gen_time = "—"

        stage_timings = payment.meta.get("stage_timings") or []
        timings_breakdown = format_stage_timings(
            stage_timings,
            total_sec=payment.meta.get("generation_time_sec") or gen_time,
        )

        return {
            "id": str(payment.id),
            "scenario_id": payment.scenario_id,
            "amount": f"{payment.amount} {payment.currency}",
            "status": format_payment_status(payment.status),
            "payment_link": payment.payment_link or "Не сгенерирована",
            "has_link": bool(payment.payment_link),
            "expires_at": payment.expires_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "account_id": str(payment.account_id),
            "meta_info": meta_info,
            "generation_time": gen_time,
            "timings_breakdown": timings_breakdown,
            "action_msg": dialog_manager.dialog_data.pop("payment_action_msg", None),
        }
    except Exception as exc:
        return {
            "id": str(payment_id),
            "scenario_id": "—",
            "amount": "—",
            "status": "Ошибка загрузки",
            "payment_link": "—",
            "has_link": False,
            "expires_at": "—",
            "account_id": "—",
            "meta_info": "—",
            "generation_time": "—",
            "timings_breakdown": "—",
            "action_msg": f"❌ Ошибка: {exc}",
        }


# ==============================================================================
# Callbacks
# ==============================================================================
async def on_scenario_click(
    _callback: CallbackQuery,
    _widget: Any,
    dialog_manager: DialogManager,
    item_id: str,
) -> None:
    """Select scenario and proceed to amount input."""
    dialog_manager.dialog_data["selected_scenario_id"] = item_id
    dialog_manager.dialog_data["selected_scenario_name"] = item_id
    await dialog_manager.switch_to(ScenariosSG.enter_amount)


async def execute_payment_creation(
    dialog_manager: DialogManager,
    api_client: ApiClient,
    amount: Decimal,
    user_id: int,
) -> None:
    """Helper to call backend create_payment API and measure execution time."""
    scenario_id = dialog_manager.dialog_data.get("selected_scenario_id", "starslly_bot")
    client_user_id = f"tg_admin_{user_id}"

    try:
        logger.info(
            "bot_launching_scenario_payment",
            scenario_id=scenario_id,
            amount=str(amount),
            client_user_id=client_user_id,
        )
        t_start = time.perf_counter()
        payment = await api_client.create_payment(
            client_user_id=client_user_id,
            amount=amount,
            scenario_id=scenario_id,
            currency="RUB",
        )
        duration_sec = round(time.perf_counter() - t_start, 2)
        gen_time_str = f"{duration_sec} сек."

        logger.info(
            "bot_scenario_payment_created",
            payment_id=str(payment.id),
            status=payment.status,
            link=payment.payment_link,
            duration_sec=duration_sec,
        )
        dialog_manager.dialog_data["last_payment_id"] = str(payment.id)
        dialog_manager.dialog_data["generation_time"] = gen_time_str
        dialog_manager.dialog_data["payment_action_msg"] = (
            f"✅ Ссылка сгенерирована за <b>{gen_time_str}</b>!"
        )
        await dialog_manager.switch_to(ScenariosSG.payment_result)
    except Exception as exc:
        logger.error(
            "bot_scenario_execution_error",
            scenario_id=scenario_id,
            amount=str(amount),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        err_detail = str(exc).strip()
        if not err_detail or "readtimeout" in err_detail.lower():
            err_detail = (
                "Превышено время ожидания ответа (ReadTimeout). "
                "Сценарий выполняется в фоне или бот отвечает медленно."
            )
        dialog_manager.dialog_data["last_action_msg"] = (
            f"❌ Ошибка запуска сценария:\n{err_detail}"
        )
        await dialog_manager.switch_to(ScenariosSG.list_scenarios)


async def on_amount_entered(
    message: Message,
    _widget: Any,
    dialog_manager: DialogManager,
    text: str,
) -> None:
    """Handle custom amount input."""
    try:
        val = Decimal(text.strip().replace(",", "."))
        if val < 50:
            raise ValueError("Минимальная сумма заказа — 50 звёзд")
    except (InvalidOperation, ValueError) as err:
        dialog_manager.dialog_data["amount_error"] = (
            f"Некорректная сумма: {err}. Введите число от 50:"
        )
        return

    api_client: ApiClient = dialog_manager.middleware_data["api_client"]
    sender_id = message.from_user.id if message.from_user else 0
    await execute_payment_creation(dialog_manager, api_client, val, sender_id)


async def on_quick_amount(
    callback: CallbackQuery,
    button: Button,
    dialog_manager: DialogManager,
) -> None:
    if not button.widget_id:
        return
    with suppress(Exception):
        await callback.answer("⏳ Создаем платёж...", show_alert=False)
    amount_str = button.widget_id.split("_")[-1]
    amount = Decimal(amount_str)
    api_client: ApiClient = dialog_manager.middleware_data["api_client"]
    sender_id = callback.from_user.id if callback.from_user else 0
    await execute_payment_creation(dialog_manager, api_client, amount, sender_id)


async def on_refresh_payment(
    callback: CallbackQuery,
    _button: Button,
    dialog_manager: DialogManager,
) -> None:
    """Refresh payment status."""
    dialog_manager.dialog_data["payment_action_msg"] = "🔄 Данные обновлены"
    await callback.answer("Обновлено")


async def on_mark_paid(
    callback: CallbackQuery,
    _button: Button,
    dialog_manager: DialogManager,
) -> None:
    """Simulate user paying the invoice."""
    payment_id = dialog_manager.dialog_data.get("last_payment_id")
    api_client: ApiClient = dialog_manager.middleware_data["api_client"]

    if not payment_id:
        await callback.answer("Платеж не найден", show_alert=True)
        return

    try:
        await api_client.mark_payment_paid(payment_id)
        dialog_manager.dialog_data["payment_action_msg"] = (
            "✅ Статус платежа успешно изменен на PAID! Аккаунт освобожден в пуле."
        )
        await callback.answer("Платеж оплачен!")
    except Exception as exc:
        dialog_manager.dialog_data["payment_action_msg"] = f"❌ Ошибка: {exc}"
        await callback.answer(safe_alert_text(f"Ошибка: {exc}"), show_alert=True)


async def on_cancel_payment(
    callback: CallbackQuery,
    _button: Button,
    dialog_manager: DialogManager,
) -> None:
    """Simulate invoice cancellation."""
    payment_id = dialog_manager.dialog_data.get("last_payment_id")
    api_client: ApiClient = dialog_manager.middleware_data["api_client"]

    if not payment_id:
        await callback.answer("Платеж не найден", show_alert=True)
        return

    try:
        await api_client.cancel_payment(payment_id)
        dialog_manager.dialog_data["payment_action_msg"] = (
            "❌ Платеж отменен! Аккаунт мгновенно разблокирован в пуле."
        )
        await callback.answer("Платеж отменен!")
    except Exception as exc:
        dialog_manager.dialog_data["payment_action_msg"] = f"❌ Ошибка: {exc}"
        await callback.answer(safe_alert_text(f"Ошибка: {exc}"), show_alert=True)


# ==============================================================================
# Windows Definition
# ==============================================================================
scenarios_list_window = Window(
    Format("⚡ <i>{last_action_msg}</i>\n\n", when="last_action_msg"),
    Const("🧪 <b>Тестирование сценариев оплаты</b>\n"),
    Const("Выберите сценарий для генерации тестовой ссылки на оплату:"),
    ScrollingGroup(
        Select(
            Format("{item[display_name]}"),
            id="s_scenarios",
            item_id_getter=lambda item: item["id"],
            items="scenarios",
            on_click=on_scenario_click,
        ),
        id="scenarios_scroll",
        width=1,
        height=6,
        hide_on_single_page=True,
    ),
    Cancel(
        Const("🔙 Главное меню"),
        id="cancel_to_menu",
    ),
    getter=get_scenarios_list,
    state=ScenariosSG.list_scenarios,
)

enter_amount_window = Window(
    Format("⚠️ <i>{error_msg}</i>\n\n", when="error_msg"),
    Format(
        "🚀 <b>Сценарий: {scenario_name}</b>\n\n"
        "Выберите быструю сумму или отправьте число сообщением (в рублях ₽):"
    ),
    Row(
        Button(Const("100 ₽"), id="btn_amt_100", on_click=on_quick_amount),
        Button(Const("495 ₽"), id="btn_amt_495", on_click=on_quick_amount),
        Button(Const("1000 ₽"), id="btn_amt_1000", on_click=on_quick_amount),
    ),
    TextInput(
        id="input_amount",
        on_success=on_amount_entered,
    ),
    SwitchTo(
        Const("🔙 Назад к списку"),
        id="back_to_scenarios",
        state=ScenariosSG.list_scenarios,
    ),
    getter=get_enter_amount_data,
    state=ScenariosSG.enter_amount,
)

payment_result_window = Window(
    Format("🔔 <b>{action_msg}</b>\n\n", when="action_msg"),
    Format(
        "💳 <b>Информация о платеже</b>\n\n"
        "<b>ID платежа:</b> <code>{id}</code>\n"
        "<b>Сценарий:</b> <code>{scenario_id}</code>\n"
        "<b>Сумма:</b> <b>{amount}</b>\n"
        "<b>Статус:</b> {status}\n"
        "<b>Расчет:</b> {meta_info}\n"
        "<b>Истекает:</b> {expires_at}\n"
        "<b>Аккаунт в пуле:</b> <code>{account_id}</code>\n\n"
        "📊 <b>Детализация времени:</b>\n"
        "{timings_breakdown}\n\n"
        "🔗 <b>Ссылка на оплату:</b>\n{payment_link}\n"
    ),
    Row(
        Button(
            Const("🔄 Обновить статус"),
            id="btn_refresh_payment",
            on_click=on_refresh_payment,
        ),
    ),
    Row(
        Button(
            Const("✅ Симулировать оплату"),
            id="btn_paid_sim",
            on_click=on_mark_paid,
        ),
        Button(
            Const("❌ Отменить платеж"),
            id="btn_cancel_sim",
            on_click=on_cancel_payment,
        ),
    ),
    SwitchTo(
        Const("🔙 К сценариям"),
        id="to_scenarios_from_res",
        state=ScenariosSG.list_scenarios,
    ),
    getter=get_payment_result,
    state=ScenariosSG.payment_result,
)

scenarios_dialog = Dialog(
    scenarios_list_window,
    enter_amount_window,
    payment_result_window,
)
