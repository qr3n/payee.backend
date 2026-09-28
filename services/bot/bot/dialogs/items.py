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
from bot.dialogs.states import ItemsSG


# ==============================================================================
# Data Getters
# ==============================================================================
async def get_items_list(
    dialog_manager: DialogManager, api_client: ApiClient, **_kwargs: Any
) -> dict[str, Any]:
    """Fetch paginated items list from the FastAPI backend."""
    try:
        paginated = await api_client.list_items(page=1, size=50)
        items = [
            {
                "id": str(item.id),
                "title": item.title,
                "description": item.description or "—",
            }
            for item in paginated.items
        ]
        return {
            "items": items,
            "total": paginated.total,
            "has_items": len(items) > 0,
            "last_action_msg": dialog_manager.dialog_data.pop("last_action_msg", None),
        }
    except Exception as exc:
        return {
            "items": [],
            "total": 0,
            "has_items": False,
            "last_action_msg": f"⚠️ Ошибка загрузки из API: {exc}",
        }


async def get_item_detail(
    dialog_manager: DialogManager, api_client: ApiClient, **_kwargs: Any
) -> dict[str, Any]:
    """Fetch details of a single item by ID from the FastAPI backend."""
    item_id = dialog_manager.dialog_data.get("selected_item_id")
    if not item_id:
        return {
            "id": "—",
            "title": "Не выбран",
            "description": "—",
            "status": "—",
            "created_at": "—",
            "task_msg": None,
        }

    try:
        item = await api_client.get_item(item_id)
        if not item:
            return {
                "id": str(item_id),
                "title": "Элемент не найден в базе",
                "description": "—",
                "status": "—",
                "created_at": "—",
                "task_msg": None,
            }

        return {
            "id": str(item.id),
            "title": item.title,
            "description": item.description or "Без описания",
            "status": "✅ Активен" if item.is_active else "❌ Неактивен",
            "created_at": item.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "task_msg": dialog_manager.dialog_data.pop("task_msg", None),
        }
    except Exception as exc:
        return {
            "id": str(item_id),
            "title": "Ошибка получения",
            "description": str(exc),
            "status": "—",
            "created_at": "—",
            "task_msg": None,
        }


# ==============================================================================
# Widget Event Callbacks
# ==============================================================================
async def on_item_click(
    _callback: CallbackQuery,
    _widget: Any,
    dialog_manager: DialogManager,
    item_id: str,
) -> None:
    """Handle item selection in the catalog."""
    dialog_manager.dialog_data["selected_item_id"] = item_id
    await dialog_manager.switch_to(ItemsSG.item_detail)


async def on_analyze_item(
    callback: CallbackQuery,
    _button: Button,
    dialog_manager: DialogManager,
) -> None:
    """Trigger background analysis of an item via Taskiq worker."""
    item_id = dialog_manager.dialog_data.get("selected_item_id")
    api_client: ApiClient = dialog_manager.middleware_data["api_client"]

    if not item_id:
        await callback.answer("Ошибка: Item не выбран!", show_alert=True)
        return

    try:
        result = await api_client.analyze_item(item_id)
        task_id = result.get("task_id", "отправлена")
        dialog_manager.dialog_data["task_msg"] = (
            f"🚀 Фоновая задача запущена! ID: {task_id}"
        )
        await callback.answer("Задача отправлена в Taskiq!")
    except Exception as exc:
        await callback.answer(f"Ошибка API: {exc}", show_alert=True)


async def on_title_entered(
    message: Message,
    _widget: Any,
    dialog_manager: DialogManager,
    text: str,
) -> None:
    """Store title and advance to description step."""
    clean_title = text.strip()
    if not clean_title:
        await message.answer("Название не может быть пустым. Попробуйте еще раз:")
        return

    dialog_manager.dialog_data["new_item_title"] = clean_title
    await dialog_manager.switch_to(ItemsSG.create_description)


async def create_and_finish(
    dialog_manager: DialogManager,
    api_client: ApiClient,
    title: str,
    description: str | None,
) -> None:
    """Helper to call backend create_item and reset form state."""
    try:
        item = await api_client.create_item(title=title, description=description)
        dialog_manager.dialog_data["last_action_msg"] = (
            f"✅ Item '{item.title}' успешно создан!"
        )
    except Exception as exc:
        dialog_manager.dialog_data["last_action_msg"] = f"❌ Ошибка создания: {exc}"
    finally:
        dialog_manager.dialog_data.pop("new_item_title", None)
        await dialog_manager.switch_to(ItemsSG.list_items)


async def on_desc_entered(
    _message: Message,
    _widget: Any,
    dialog_manager: DialogManager,
    text: str,
) -> None:
    """Create item with provided description."""
    title = dialog_manager.dialog_data.get("new_item_title", "Без названия")
    api_client: ApiClient = dialog_manager.middleware_data["api_client"]
    await create_and_finish(dialog_manager, api_client, title, text.strip())


async def on_skip_desc(
    _callback: CallbackQuery,
    _button: Button,
    dialog_manager: DialogManager,
) -> None:
    """Create item without description."""
    title = dialog_manager.dialog_data.get("new_item_title", "Без названия")
    api_client: ApiClient = dialog_manager.middleware_data["api_client"]
    await create_and_finish(dialog_manager, api_client, title, None)


# ==============================================================================
# Windows Definition
# ==============================================================================
items_list_window = Window(
    Format("{last_action_msg}\n\n", when="last_action_msg"),
    Const("📦 <b>Каталог Items</b>\n\nВыберите элемент для просмотра подробностей:"),
    ScrollingGroup(
        Select(
            Format("🔹 {item[title]}"),
            id="s_items",
            item_id_getter=lambda item: item["id"],
            items="items",
            on_click=on_item_click,
        ),
        id="items_scroll",
        width=1,
        height=6,
        hide_on_single_page=True,
    ),
    Row(
        SwitchTo(
            Const("➕ Добавить Item"),
            id="to_create_item",
            state=ItemsSG.create_title,
        ),
        Cancel(
            Const("🔙 Главное меню"),
            id="cancel_to_menu",
        ),
    ),
    getter=get_items_list,
    state=ItemsSG.list_items,
)

item_detail_window = Window(
    Format("⚡ <i>{task_msg}</i>\n\n", when="task_msg"),
    Format(
        "📦 <b>Детали Item</b>\n\n"
        "<b>Название:</b> {title}\n"
        "<b>Описание:</b> {description}\n"
        "<b>Статус:</b> {status}\n"
        "<b>ID:</b> <code>{id}</code>\n"
        "<b>Создан:</b> {created_at}\n"
    ),
    Button(
        Const("🚀 Запустить анализ (Taskiq)"),
        id="btn_analyze",
        on_click=on_analyze_item,
    ),
    SwitchTo(
        Const("🔙 Назад к списку"),
        id="back_to_items_list",
        state=ItemsSG.list_items,
    ),
    getter=get_item_detail,
    state=ItemsSG.item_detail,
)

create_title_window = Window(
    Const(
        "➕ <b>Создание нового Item (Шаг 1 из 2)</b>\n\n"
        "Введите название для нового элемента (от 1 до 255 символов):"
    ),
    TextInput(
        id="input_title",
        on_success=on_title_entered,
    ),
    SwitchTo(
        Const("🔙 Отмена"),
        id="cancel_title_step",
        state=ItemsSG.list_items,
    ),
    state=ItemsSG.create_title,
)

create_description_window = Window(
    Format(
        "➕ <b>Создание нового Item (Шаг 2 из 2)</b>\n\n"
        "Название: <b>{dialog_data[new_item_title]}</b>\n\n"
        "Введите описание или нажмите «Пропустить»:"
    ),
    TextInput(
        id="input_description",
        on_success=on_desc_entered,
    ),
    Row(
        Button(
            Const("⏭ Пропустить описание"),
            id="btn_skip_desc",
            on_click=on_skip_desc,
        ),
        SwitchTo(
            Const("🔙 Отмена"),
            id="cancel_desc_step",
            state=ItemsSG.list_items,
        ),
    ),
    state=ItemsSG.create_description,
)

items_dialog = Dialog(
    items_list_window,
    item_detail_window,
    create_title_window,
    create_description_window,
)
