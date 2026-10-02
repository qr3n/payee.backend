from typing import Any

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
from bot.dialogs.states import AccountsSG


def format_status_badge(status: str) -> str:
    """Return status emoji and text."""
    mapping = {
        "active": "🟢 Активен",
        "pending": "⚪ Не проверен",
        "banned": "🔴 Забанен",
        "revoked": "🔴 Отозван",
        "flood_wait": "🟡 Flood Wait",
        "error": "❌ Ошибка",
    }
    return mapping.get(status.lower(), f"⚪ {status}")


# ==============================================================================
# Data Getters
# ==============================================================================
async def get_accounts_list(
    dialog_manager: DialogManager,
    api_client: ApiClient,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch all telegram accounts from backend API."""
    try:
        paginated = await api_client.list_accounts(page=1, size=100)
        accounts_data = []
        for acc in paginated.items:
            badge = format_status_badge(acc.status)
            user_label = f" (@{acc.username})" if acc.username else ""
            accounts_data.append(
                {
                    "id": str(acc.id),
                    "title": acc.title,
                    "status_badge": badge,
                    "display_name": f"{badge} | {acc.title}{user_label}",
                }
            )

        return {
            "accounts": accounts_data,
            "total": paginated.total,
            "has_accounts": len(accounts_data) > 0,
            "last_action_msg": dialog_manager.dialog_data.pop("last_action_msg", None),
        }
    except Exception as exc:
        return {
            "accounts": [],
            "total": 0,
            "has_accounts": False,
            "last_action_msg": f"❌ Ошибка загрузки аккаунтов: {exc}",
        }


async def get_account_detail(
    dialog_manager: DialogManager,
    api_client: ApiClient,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch details of the selected telegram account."""
    account_id = dialog_manager.dialog_data.get("selected_account_id")
    if not account_id:
        return {
            "id": "—",
            "title": "Не выбран",
            "status": "—",
            "phone": "—",
            "username": "—",
            "telegram_user_id": "—",
            "full_name": "—",
            "device": "—",
            "proxy": "—",
            "is_premium": "—",
            "last_checked": "—",
            "last_error": None,
            "detail_msg": None,
        }

    try:
        acc = await api_client.get_account(account_id)
        if not acc:
            return {
                "id": str(account_id),
                "title": "Аккаунт не найден",
                "status": "—",
                "phone": "—",
                "username": "—",
                "telegram_user_id": "—",
                "full_name": "—",
                "device": "—",
                "proxy": "—",
                "is_premium": "—",
                "last_checked": "—",
                "last_error": None,
                "detail_msg": "Аккаунт был удален или не существует",
            }

        full_name = f"{acc.first_name or ''} {acc.last_name or ''}".strip() or "—"
        device = f"{acc.device_model} ({acc.system_version}, {acc.app_version})"

        return {
            "id": str(acc.id),
            "title": acc.title,
            "status": format_status_badge(acc.status),
            "phone": acc.phone or "—",
            "username": f"@{acc.username}" if acc.username else "—",
            "telegram_user_id": str(acc.telegram_user_id or "—"),
            "full_name": full_name,
            "device": device,
            "proxy": acc.proxy_url or "Прямое подключение (без прокси)",
            "is_premium": "⭐️ Да" if acc.is_premium else "Нет",
            "last_checked": (
                acc.last_checked_at.strftime("%Y-%m-%d %H:%M:%S UTC")
                if acc.last_checked_at
                else "Никогда"
            ),
            "last_error": acc.last_error,
            "detail_msg": dialog_manager.dialog_data.pop("detail_msg", None),
        }
    except Exception as exc:
        return {
            "id": str(account_id),
            "title": "Ошибка получения",
            "status": "—",
            "phone": "—",
            "username": "—",
            "telegram_user_id": "—",
            "full_name": "—",
            "device": "—",
            "proxy": "—",
            "is_premium": "—",
            "last_checked": "—",
            "last_error": str(exc),
            "detail_msg": f"❌ Ошибка API: {exc}",
        }


# ==============================================================================
# Callbacks
# ==============================================================================
async def on_account_click(
    _callback: CallbackQuery,
    _widget: Any,
    dialog_manager: DialogManager,
    item_id: str,
) -> None:
    """Handle account selection in the list."""
    dialog_manager.dialog_data["selected_account_id"] = item_id
    await dialog_manager.switch_to(AccountsSG.account_detail)


async def on_check_account(
    callback: CallbackQuery,
    _button: Button,
    dialog_manager: DialogManager,
) -> None:
    """Trigger MTProto session check."""
    account_id = dialog_manager.dialog_data.get("selected_account_id")
    api_client: ApiClient = dialog_manager.middleware_data["api_client"]

    if not account_id:
        await callback.answer("Ошибка: аккаунт не выбран!", show_alert=True)
        return

    try:
        result = await api_client.check_account(account_id)
        if result.is_authorized:
            dialog_manager.dialog_data["detail_msg"] = (
                f"✅ Сессия активна! Telegram ID: {result.telegram_user_id} "
                f"(@{result.username})"
            )
        else:
            dialog_manager.dialog_data["detail_msg"] = (
                f"⚠️ Статус: {result.status}. Ошибка: {result.error}"
            )
        await callback.answer("Проверка выполнена!")
    except Exception as exc:
        dialog_manager.dialog_data["detail_msg"] = f"❌ Ошибка проверки: {exc}"
        await callback.answer(f"Ошибка: {exc}", show_alert=True)


async def on_delete_account(
    callback: CallbackQuery,
    _button: Button,
    dialog_manager: DialogManager,
) -> None:
    """Delete selected account from the pool."""
    account_id = dialog_manager.dialog_data.get("selected_account_id")
    api_client: ApiClient = dialog_manager.middleware_data["api_client"]

    if not account_id:
        await callback.answer("Ошибка: аккаунт не выбран!", show_alert=True)
        return

    try:
        await api_client.delete_account(account_id)
        dialog_manager.dialog_data["last_action_msg"] = (
            "✅ Сессия успешно удалена из пула."
        )
        await dialog_manager.switch_to(AccountsSG.list_accounts)
    except Exception as exc:
        await callback.answer(f"Ошибка удаления: {exc}", show_alert=True)


async def on_title_entered(
    message: Message,
    _widget: Any,
    dialog_manager: DialogManager,
    text: str,
) -> None:
    """Save account title and move to session string step."""
    clean_title = text.strip()
    if not clean_title:
        await message.answer("Название не может быть пустым. Попробуйте снова:")
        return

    dialog_manager.dialog_data["new_account_title"] = clean_title
    await dialog_manager.switch_to(AccountsSG.add_session)


async def on_session_entered(
    message: Message,
    _widget: Any,
    dialog_manager: DialogManager,
    text: str,
) -> None:
    """Save session string and move to proxy step."""
    clean_session = text.strip()
    if len(clean_session) < 10:
        await message.answer(
            "Некорректная строка сессии (слишком короткая). Попробуйте снова:"
        )
        return

    dialog_manager.dialog_data["new_account_session"] = clean_session
    await dialog_manager.switch_to(AccountsSG.add_proxy)


async def create_account_and_finish(
    dialog_manager: DialogManager,
    api_client: ApiClient,
    proxy_url: str | None,
) -> None:
    """Register account via backend API."""
    title = dialog_manager.dialog_data.get("new_account_title", "Без названия")
    session_string = dialog_manager.dialog_data.get("new_account_session", "")

    try:
        created = await api_client.create_account(
            title=title,
            session_string=session_string,
            proxy_url=proxy_url,
            verify_on_create=True,
        )
        status_text = format_status_badge(created.status)
        user_info = f" (@{created.username})" if created.username else ""
        dialog_manager.dialog_data["last_action_msg"] = (
            f"✅ Аккаунт '{created.title}' добавлен! Статус: {status_text}{user_info}"
        )
    except Exception as exc:
        dialog_manager.dialog_data["last_action_msg"] = f"❌ Ошибка добавления: {exc}"
    finally:
        dialog_manager.dialog_data.pop("new_account_title", None)
        dialog_manager.dialog_data.pop("new_account_session", None)
        await dialog_manager.switch_to(AccountsSG.list_accounts)


async def on_proxy_entered(
    _message: Message,
    _widget: Any,
    dialog_manager: DialogManager,
    text: str,
) -> None:
    """Save proxy URL and register account."""
    api_client: ApiClient = dialog_manager.middleware_data["api_client"]
    clean_proxy = text.strip()
    await create_account_and_finish(dialog_manager, api_client, clean_proxy)


async def on_skip_proxy(
    _callback: CallbackQuery,
    _button: Button,
    dialog_manager: DialogManager,
) -> None:
    """Register account without proxy."""
    api_client: ApiClient = dialog_manager.middleware_data["api_client"]
    await create_account_and_finish(dialog_manager, api_client, None)


# ==============================================================================
# Windows Definition
# ==============================================================================
accounts_list_window = Window(
    Format("⚡ <i>{last_action_msg}</i>\n\n", when="last_action_msg"),
    Const("📱 <b>Пул Telegram-аккаунтов</b>\n"),
    Format(
        "Всего аккаунтов в пуле: <b>{total}</b>\n\nВыберите аккаунт для управления:",
        when="has_accounts",
    ),
    Const(
        "<i>В пуле пока нет добавленных аккаунтов. Добавьте первую сессию ниже:</i>",
        when=lambda data, *_: not data.get("has_accounts"),
    ),
    ScrollingGroup(
        Select(
            Format("{item[display_name]}"),
            id="s_accounts",
            item_id_getter=lambda item: item["id"],
            items="accounts",
            on_click=on_account_click,
        ),
        id="accounts_scroll",
        width=1,
        height=6,
        hide_on_single_page=True,
    ),
    Row(
        SwitchTo(
            Const("➕ Добавить сессию"),
            id="to_add_account",
            state=AccountsSG.add_title,
        ),
        Cancel(
            Const("🔙 Главное меню"),
            id="cancel_to_menu",
        ),
    ),
    getter=get_accounts_list,
    state=AccountsSG.list_accounts,
)

account_detail_window = Window(
    Format("🔔 <b>{detail_msg}</b>\n\n", when="detail_msg"),
    Format(
        "📱 <b>Информация об аккаунте</b>\n\n"
        "<b>Название:</b> {title}\n"
        "<b>Статус:</b> {status}\n"
        "<b>ID:</b> <code>{id}</code>\n"
        "<b>Telegram User ID:</b> <code>{telegram_user_id}</code>\n"
        "<b>Юзернейм:</b> {username}\n"
        "<b>Имя:</b> {full_name}\n"
        "<b>Телефон:</b> {phone}\n"
        "<b>Telegram Premium:</b> {is_premium}\n"
        "<b>Устройство:</b> {device}\n"
        "<b>Прокси:</b> <code>{proxy}</code>\n"
        "<b>Последняя проверка:</b> {last_checked}\n"
    ),
    Format("\n⚠️ <b>Последняя ошибка:</b> {last_error}", when="last_error"),
    Row(
        Button(
            Const("🔄 Проверить статус"),
            id="btn_check_account",
            on_click=on_check_account,
        ),
        Button(
            Const("🗑 Удалить сессию"),
            id="btn_delete_account",
            on_click=on_delete_account,
        ),
    ),
    SwitchTo(
        Const("🔙 Назад к списку"),
        id="back_to_accounts_list",
        state=AccountsSG.list_accounts,
    ),
    getter=get_account_detail,
    state=AccountsSG.account_detail,
)

add_title_window = Window(
    Const(
        "➕ <b>Добавление Telegram сессии (Шаг 1 из 3)</b>\n\n"
        "Введите название или пометку для аккаунта (например: <code>Worker 1</code>):"
    ),
    TextInput(
        id="input_acc_title",
        on_success=on_title_entered,
    ),
    SwitchTo(
        Const("🔙 Отмена"),
        id="cancel_add_title",
        state=AccountsSG.list_accounts,
    ),
    state=AccountsSG.add_title,
)

add_session_window = Window(
    Format(
        "🔑 <b>Добавление Telegram сессии (Шаг 2 из 3)</b>\n\n"
        "Название: <b>{dialog_data[new_account_title]}</b>\n\n"
        "Отправьте строку Telethon StringSession (начинается с <code>1...</code>):"
    ),
    TextInput(
        id="input_acc_session",
        on_success=on_session_entered,
    ),
    SwitchTo(
        Const("🔙 Отмена"),
        id="cancel_add_session",
        state=AccountsSG.list_accounts,
    ),
    state=AccountsSG.add_session,
)

add_proxy_window = Window(
    Format(
        "🌐 <b>Добавление Telegram сессии (Шаг 3 из 3)</b>\n\n"
        "Название: <b>{dialog_data[new_account_title]}</b>\n\n"
        "Введите URL прокси (например: <code>socks5://user:pass@1.2.3.4:1080</code>)\n"
        "или нажмите «Пропустить прокси»:"
    ),
    TextInput(
        id="input_acc_proxy",
        on_success=on_proxy_entered,
    ),
    Row(
        Button(
            Const("⏭ Пропустить прокси"),
            id="btn_skip_proxy",
            on_click=on_skip_proxy,
        ),
        SwitchTo(
            Const("🔙 Отмена"),
            id="cancel_add_proxy",
            state=AccountsSG.list_accounts,
        ),
    ),
    state=AccountsSG.add_proxy,
)

accounts_dialog = Dialog(
    accounts_list_window,
    account_detail_window,
    add_title_window,
    add_session_window,
    add_proxy_window,
)
