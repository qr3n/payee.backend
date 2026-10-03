import base64
from typing import Any

from aiogram.enums import ContentType
from aiogram.types import CallbackQuery, Message
from aiogram_dialog import Dialog, DialogManager, Window
from aiogram_dialog.widgets.input import MessageInput, TextInput
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


def safe_alert_text(text: str, max_length: int = 180) -> str:
    """Ensure callback answer text stays within Telegram's 200 character limit."""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


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
            api_info = f" [ID:{acc.api_id}]" if acc.api_id else ""
            accounts_data.append(
                {
                    "id": str(acc.id),
                    "title": acc.title,
                    "status_badge": badge,
                    "display_name": f"{badge} | {acc.title}{user_label}{api_info}",
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
            "api_id": "—",
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
                "api_id": "—",
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
            "api_id": str(acc.api_id or "Стандартный"),
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
            "api_id": "—",
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
        await callback.answer(safe_alert_text(f"Ошибка: {exc}"), show_alert=True)


async def on_prepare_account(
    callback: CallbackQuery,
    _button: Button,
    dialog_manager: DialogManager,
) -> None:
    """Trigger background scenario preparation."""
    account_id = dialog_manager.dialog_data.get("selected_account_id")
    api_client: ApiClient = dialog_manager.middleware_data["api_client"]

    if not account_id:
        await callback.answer("Ошибка: аккаунт не выбран!", show_alert=True)
        return

    try:
        await api_client.prepare_account(account_id)
        dialog_manager.dialog_data["detail_msg"] = (
            "⚡️ Запущена фоновая подготовка для всех сценариев "
            "(вступление в каналы, /start и т.д.)"
        )
        await callback.answer("Подготовка запущена в фоне!", show_alert=False)
    except Exception as exc:
        dialog_manager.dialog_data["detail_msg"] = (
            f"❌ Ошибка запуска подготовки: {exc}"
        )
        await callback.answer(safe_alert_text(f"Ошибка: {exc}"), show_alert=True)


async def on_check_all_accounts(
    callback: CallbackQuery,
    _button: Button,
    dialog_manager: DialogManager,
) -> None:
    """Trigger batch MTProto verification across all accounts."""
    api_client: ApiClient = dialog_manager.middleware_data["api_client"]
    await callback.answer("⏳ Запущена проверка всех сессий...", show_alert=False)
    try:
        res = await api_client.check_all_accounts()
        dialog_manager.dialog_data["last_action_msg"] = (
            f"📊 <b>Проверка завершена:</b> "
            f"Всего: {res.total} | 🟢 {res.active} | "
            f"🔴 {res.revoked} | ⛔️ {res.banned} | "
            f"🟡 {res.flood_wait} | ❌ {res.error}"
        )
    except Exception as exc:
        dialog_manager.dialog_data["last_action_msg"] = (
            f"❌ Ошибка проверки сессий: {exc}"
        )


async def on_release_all_accounts(
    callback: CallbackQuery,
    _button: Button,
    dialog_manager: DialogManager,
) -> None:
    """Cancel all active pending payments and release all reserved accounts."""
    api_client: ApiClient = dialog_manager.middleware_data["api_client"]
    try:
        res = await api_client.release_all_accounts()
        dialog_manager.dialog_data["last_action_msg"] = (
            f"🔓 <b>Все аккаунты освобождены:</b> {res.message}"
        )
        await callback.answer(
            f"Освобождено: {res.released_accounts_count} аккаунтов",
            show_alert=False,
        )
    except Exception as exc:
        dialog_manager.dialog_data["last_action_msg"] = (
            f"❌ Ошибка освобождения аккаунтов: {exc}"
        )
        await callback.answer(safe_alert_text(f"Ошибка: {exc}"), show_alert=True)


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
        await callback.answer(
            safe_alert_text(f"Ошибка удаления: {exc}"), show_alert=True
        )


# ==============================================================================
# Phone Auth Flow Callbacks
# ==============================================================================
async def on_phone_entered(
    message: Message,
    _widget: Any,
    dialog_manager: DialogManager,
    text: str,
) -> None:
    """Step 1: Send phone number to API and request code."""
    clean_phone = text.strip().replace(" ", "").replace("-", "")
    api_client: ApiClient = dialog_manager.middleware_data["api_client"]

    try:
        resp = await api_client.send_phone_code(phone=clean_phone)
        dialog_manager.dialog_data["auth_phone"] = clean_phone
        dialog_manager.dialog_data["auth_phone_code_hash"] = resp.phone_code_hash
        await dialog_manager.switch_to(AccountsSG.enter_code)
    except Exception as exc:
        await message.answer(f"❌ Ошибка отправки кода:\n{exc}\n\nПопробуйте снова:")


async def on_code_entered(
    message: Message,
    _widget: Any,
    dialog_manager: DialogManager,
    text: str,
) -> None:
    """Step 2: Submit confirmation code."""
    code = text.strip()
    phone_code_hash = dialog_manager.dialog_data.get("auth_phone_code_hash", "")
    phone = dialog_manager.dialog_data.get("auth_phone")
    api_client: ApiClient = dialog_manager.middleware_data["api_client"]

    try:
        res = await api_client.sign_in_phone(
            phone_code_hash=phone_code_hash,
            code=code,
            phone=phone,
        )
        if res.status == "needs_2fa":
            await dialog_manager.switch_to(AccountsSG.enter_2fa_password)
            return

        dialog_manager.dialog_data["last_action_msg"] = (
            f"✅ {res.message or 'Аккаунт успешно добавлен!'}\n"
            "<i>⚡️ В фоне запущена подготовка для всех сценариев.</i>"
        )
        await dialog_manager.switch_to(AccountsSG.list_accounts)
    except Exception as exc:
        await message.answer(
            f"❌ Ошибка подтверждения кода:\n{exc}\n\nВведите код повторно:"
        )


async def on_2fa_entered(
    message: Message,
    _widget: Any,
    dialog_manager: DialogManager,
    text: str,
) -> None:
    """Step 3: Submit 2FA password."""
    password = text.strip()
    phone_code_hash = dialog_manager.dialog_data.get("auth_phone_code_hash", "")
    phone = dialog_manager.dialog_data.get("auth_phone")
    api_client: ApiClient = dialog_manager.middleware_data["api_client"]

    try:
        res = await api_client.sign_in_phone(
            phone_code_hash=phone_code_hash,
            code="",
            phone=phone,
            two_fa_password=password,
        )
        dialog_manager.dialog_data["last_action_msg"] = (
            f"✅ {res.message or 'Аккаунт успешно добавлен с 2FA!'}\n"
            "<i>⚡️ В фоне запущена подготовка для всех сценариев.</i>"
        )
        await dialog_manager.switch_to(AccountsSG.list_accounts)
    except Exception as exc:
        await message.answer(
            f"❌ Ошибка 2FA пароля:\n{exc}\n\nВведите пароль повторно:"
        )


# ==============================================================================
# File Upload Flow Callbacks (.session + .json)
# ==============================================================================
async def on_session_file_received(
    message: Message,
    _widget: MessageInput,
    dialog_manager: DialogManager,
) -> None:
    """Handle receiving .session document."""
    doc = message.document
    if not doc or not doc.file_name or not doc.file_name.endswith(".session"):
        await message.answer(
            "⚠️ Пожалуйста, отправьте файл документа с расширением .session\n"
            "(например <code>worker.session</code>):"
        )
        return

    bot = message.bot
    if not bot:
        await message.answer("Ошибка: бот недоступен")
        return

    buffer = await bot.download(doc)
    if not buffer:
        await message.answer("Ошибка скачивания файла")
        return

    buffer.seek(0)
    bytes_data = buffer.read()
    dialog_manager.dialog_data["uploaded_session_b64"] = base64.b64encode(
        bytes_data
    ).decode("ascii")
    dialog_manager.dialog_data["uploaded_session_filename"] = doc.file_name
    await dialog_manager.switch_to(AccountsSG.upload_json_file)


async def on_json_file_received(
    message: Message,
    _widget: MessageInput,
    dialog_manager: DialogManager,
) -> None:
    """Handle receiving .json document and finalize upload."""
    doc = message.document
    if not doc or not doc.file_name or not doc.file_name.endswith(".json"):
        await message.answer(
            "⚠️ Пожалуйста, отправьте файл с расширением .json\n"
            "(например <code>metadata.json</code>):"
        )
        return

    bot = message.bot
    if not bot:
        await message.answer("Ошибка: бот недоступен")
        return

    buffer = await bot.download(doc)
    if not buffer:
        await message.answer("Ошибка скачивания файла")
        return

    buffer.seek(0)
    json_bytes = buffer.read()
    session_b64 = dialog_manager.dialog_data.get("uploaded_session_b64", "")
    session_filename = dialog_manager.dialog_data.get(
        "uploaded_session_filename", "account.session"
    )

    if not session_b64:
        await message.answer("Ошибка: файл .session не найден. Начните сначала.")
        await dialog_manager.switch_to(AccountsSG.list_accounts)
        return

    session_bytes = base64.b64decode(session_b64)
    api_client: ApiClient = dialog_manager.middleware_data["api_client"]

    try:
        created = await api_client.upload_account_session(
            session_bytes=session_bytes,
            session_filename=session_filename,
            json_bytes=json_bytes,
            json_filename=doc.file_name,
            verify=True,
        )
        status_text = format_status_badge(created.status)
        user_info = f" (@{created.username})" if created.username else ""
        dialog_manager.dialog_data["last_action_msg"] = (
            f"✅ Сессия '{created.title}' (API ID: {created.api_id}) "
            f"успешно добавлена! Статус: {status_text}{user_info}\n"
            "<i>⚡️ В фоне запущена подготовка для всех сценариев.</i>"
        )
    except Exception as exc:
        dialog_manager.dialog_data["last_action_msg"] = (
            f"❌ Ошибка добавления файлов:\n{exc}"
        )
    finally:
        dialog_manager.dialog_data.pop("uploaded_session_b64", None)
        dialog_manager.dialog_data.pop("uploaded_session_filename", None)
        await dialog_manager.switch_to(AccountsSG.list_accounts)


# ==============================================================================
# StringSession Manual Callbacks
# ==============================================================================
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
        Button(
            Const("🔄 Проверить все"),
            id="btn_check_all_accounts",
            on_click=on_check_all_accounts,
            when="has_accounts",
        ),
        Button(
            Const("🔓 Освободить все"),
            id="btn_release_all_accounts",
            on_click=on_release_all_accounts,
            when="has_accounts",
        ),
    ),
    Row(
        SwitchTo(
            Const("➕ Добавить сессию"),
            id="to_choose_method",
            state=AccountsSG.choose_add_method,
        ),
    ),
    Cancel(
        Const("🔙 Главное меню"),
        id="cancel_to_menu",
    ),
    getter=get_accounts_list,
    state=AccountsSG.list_accounts,
)

choose_add_method_window = Window(
    Const(
        "➕ <b>Выберите способ добавления Telegram-сессии:</b>\n\n"
        "1. <b>Вход по номеру телефона</b>\n"
        "   Ввод телефона ➔ код из Telegram/SMS ➔ облачный 2FA пароль.\n\n"
        "2. <b>Загрузка файлов (.session + .json)</b>\n"
        "   Файл SQLite Telethon + JSON с app_id, app_hash и параметрами.\n\n"
        "3. <b>Ввести StringSession вручную</b>\n"
        "   Готовая строка base64 сессии Telethon."
    ),
    SwitchTo(
        Const("📲 1. Вход по номеру телефона"),
        id="btn_method_phone",
        state=AccountsSG.enter_phone,
    ),
    SwitchTo(
        Const("📁 2. Загрузить .session + .json"),
        id="btn_method_files",
        state=AccountsSG.upload_session_file,
    ),
    SwitchTo(
        Const("🔑 3. Ввести StringSession вручную"),
        id="btn_method_string",
        state=AccountsSG.add_title,
    ),
    SwitchTo(
        Const("🔙 Назад к списку"),
        id="back_to_list_from_choose",
        state=AccountsSG.list_accounts,
    ),
    state=AccountsSG.choose_add_method,
)

# --- Phone Flow Windows ---
enter_phone_window = Window(
    Const(
        "📱 <b>Вход по номеру телефона (Шаг 1 из 2)</b>\n\n"
        "Введите номер телефона (+7...):\n"
        "Например: <code>+79991234567</code>"
    ),
    TextInput(
        id="input_auth_phone",
        on_success=on_phone_entered,
    ),
    SwitchTo(
        Const("🔙 Отмена"),
        id="cancel_phone_flow",
        state=AccountsSG.list_accounts,
    ),
    state=AccountsSG.enter_phone,
)

enter_code_window = Window(
    Format(
        "🔑 <b>Ввод кода подтверждения</b>\n\n"
        "Код отправлен на <b>{dialog_data[auth_phone]}</b> в Telegram или SMS.\n\n"
        "Введите полученный код:"
    ),
    TextInput(
        id="input_auth_code",
        on_success=on_code_entered,
    ),
    SwitchTo(
        Const("🔙 Отмена"),
        id="cancel_code_flow",
        state=AccountsSG.list_accounts,
    ),
    state=AccountsSG.enter_code,
)

enter_2fa_password_window = Window(
    Const(
        "🔐 <b>Двухфакторная аутентификация (2FA)</b>\n\n"
        "На аккаунте установлен облачный пароль.\n"
        "Введите пароль 2FA для завершения входа:"
    ),
    TextInput(
        id="input_auth_2fa",
        on_success=on_2fa_entered,
    ),
    SwitchTo(
        Const("🔙 Отмена"),
        id="cancel_2fa_flow",
        state=AccountsSG.list_accounts,
    ),
    state=AccountsSG.enter_2fa_password,
)

# --- File Upload Flow Windows ---
upload_session_file_window = Window(
    Const(
        "📁 <b>Загрузка файлов (Шаг 1 из 2)</b>\n\n"
        "Отправьте файл <code>.session</code> (например: <code>worker.session</code>) "
        "как документ в чат:"
    ),
    MessageInput(
        on_session_file_received,
        content_types=[ContentType.DOCUMENT],
    ),
    SwitchTo(
        Const("🔙 Отмена"),
        id="cancel_file_upload_1",
        state=AccountsSG.list_accounts,
    ),
    state=AccountsSG.upload_session_file,
)

upload_json_file_window = Window(
    Format(
        "📄 <b>Загрузка метаданных (Шаг 2 из 2)</b>\n\n"
        "Файл сессии: <b>{dialog_data[uploaded_session_filename]}</b>\n\n"
        "Теперь отправьте файл <code>.json</code> с метаданными клиента как документ:"
    ),
    MessageInput(
        on_json_file_received,
        content_types=[ContentType.DOCUMENT],
    ),
    SwitchTo(
        Const("🔙 Отмена"),
        id="cancel_file_upload_2",
        state=AccountsSG.list_accounts,
    ),
    state=AccountsSG.upload_json_file,
)

account_detail_window = Window(
    Format("🔔 <b>{detail_msg}</b>\n\n", when="detail_msg"),
    Format(
        "📱 <b>Информация об аккаунте</b>\n\n"
        "<b>Название:</b> {title}\n"
        "<b>Статус:</b> {status}\n"
        "<b>API ID:</b> <code>{api_id}</code>\n"
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
            Const("🔄 Проверить"),
            id="btn_check_account",
            on_click=on_check_account,
        ),
        Button(
            Const("⚡️ Подготовить"),
            id="btn_prepare_account",
            on_click=on_prepare_account,
        ),
        Button(
            Const("🗑 Удалить"),
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

# --- Manual StringSession Windows ---
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
    choose_add_method_window,
    enter_phone_window,
    enter_code_window,
    enter_2fa_password_window,
    upload_session_file_window,
    upload_json_file_window,
    account_detail_window,
    add_title_window,
    add_session_window,
    add_proxy_window,
)
