import contextlib
import uuid
from typing import Any

from aiogram.types import CallbackQuery, Message
from aiogram_dialog import Dialog, DialogManager, Window
from aiogram_dialog.widgets.input import TextInput
from aiogram_dialog.widgets.kbd import Button, Cancel, Row
from aiogram_dialog.widgets.text import Const, Format

from bot.client.api import ApiClient
from bot.dialogs.states import AISG


async def get_ai_chat_data(
    dialog_manager: DialogManager,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Provide rendering context for the AI chat window."""
    conv_id = dialog_manager.dialog_data.get("conversation_id")
    if not conv_id:
        conv_id = str(uuid.uuid4())
        dialog_manager.dialog_data["conversation_id"] = conv_id

    search_enabled = dialog_manager.dialog_data.get("search_enabled", False)
    last_prompt = dialog_manager.dialog_data.get("last_prompt")
    last_response = dialog_manager.dialog_data.get("last_response")
    citations = dialog_manager.dialog_data.get("citations", [])
    error = dialog_manager.dialog_data.get("error")

    # Format dialogue preview
    if last_prompt and last_response:
        dialogue_text = (
            f"👤 <b>Вы:</b> {last_prompt}\n\n🤖 <b>DeepSeek:</b>\n{last_response}"
        )
    else:
        dialogue_text = "<i>Контекст диалога чист. Отправьте сообщение для старта.</i>"

    # Format citations if present
    citations_text = ""
    if citations:
        sources = []
        for i, c in enumerate(citations, 1):
            title = c.get("title") or "Источник"
            url = c.get("url") or "#"
            sources.append(f'  {i}. <a href="{url}">{title}</a>')
        citations_text = "\n\n🔗 <b>Источники из сети:</b>\n" + "\n".join(sources)

    error_text = f"\n\n⚠️ <b>Ошибка:</b> {error}" if error else ""

    return {
        "conv_id_short": conv_id[:8],
        "search_status": "🟢 ВКЛ" if search_enabled else "⚪️ ВЫКЛ",
        "search_btn_text": "🌐 Поиск: ВКЛ" if search_enabled else "🌐 Поиск: ВЫКЛ",
        "dialogue_text": dialogue_text,
        "citations_text": citations_text,
        "error_text": error_text,
    }


async def on_prompt_entered(
    message: Message,
    _widget: Any,
    dialog_manager: DialogManager,
    text: str,
) -> None:
    """Handle user text messages and query the AI service via API client."""
    conv_id = dialog_manager.dialog_data.get("conversation_id") or str(uuid.uuid4())
    search_enabled = dialog_manager.dialog_data.get("search_enabled", False)
    api_client: ApiClient = dialog_manager.middleware_data["api_client"]

    # Delete the user's prompt message from chat to keep dialog tidy
    with contextlib.suppress(Exception):
        await message.delete()

    try:
        response = await api_client.ask_ai(
            prompt=text,
            conversation_id=conv_id,
            search_enabled=search_enabled,
        )
        dialog_manager.dialog_data["conversation_id"] = response.conversation_id
        dialog_manager.dialog_data["last_prompt"] = text
        dialog_manager.dialog_data["last_response"] = response.response
        dialog_manager.dialog_data["citations"] = [
            c.model_dump() for c in response.citations
        ]
        dialog_manager.dialog_data.pop("error", None)
    except Exception as exc:
        dialog_manager.dialog_data["error"] = str(exc)


async def on_toggle_search(
    callback: CallbackQuery,
    _button: Button,
    dialog_manager: DialogManager,
) -> None:
    """Toggle web search mode on/off."""
    current = dialog_manager.dialog_data.get("search_enabled", False)
    new_state = not current
    dialog_manager.dialog_data["search_enabled"] = new_state
    await callback.answer(f"Поиск в интернете: {'ВКЛ' if new_state else 'ВЫКЛ'}")


async def on_reset_conversation(
    callback: CallbackQuery,
    _button: Button,
    dialog_manager: DialogManager,
) -> None:
    """Reset the server-side conversation context and clear dialog data."""
    conv_id = dialog_manager.dialog_data.get("conversation_id")
    api_client: ApiClient = dialog_manager.middleware_data["api_client"]

    if conv_id:
        with contextlib.suppress(Exception):
            await api_client.reset_ai_conversation(conv_id)

    new_id = str(uuid.uuid4())
    dialog_manager.dialog_data["conversation_id"] = new_id
    dialog_manager.dialog_data.pop("last_prompt", None)
    dialog_manager.dialog_data.pop("last_response", None)
    dialog_manager.dialog_data.pop("citations", None)
    dialog_manager.dialog_data.pop("error", None)
    await callback.answer("Контекст диалога очищен!")


ai_chat_window = Window(
    Format(
        "🤖 <b>DeepSeek AI Ассистент</b>\n\n"
        "Сессия: <code>{conv_id_short}</code> | Поиск: <b>{search_status}</b>\n\n"
        "{dialogue_text}{citations_text}{error_text}\n\n"
        "<i>💬 Отправьте текст в чат, чтобы задать вопрос DeepSeek:</i>"
    ),
    TextInput(
        id="input_ai_prompt",
        on_success=on_prompt_entered,
    ),
    Row(
        Button(
            Format("{search_btn_text}"),
            id="btn_toggle_search",
            on_click=on_toggle_search,
        ),
        Button(
            Const("🔄 Сбросить память"),
            id="btn_reset_ai",
            on_click=on_reset_conversation,
        ),
    ),
    Cancel(
        Const("🔙 В главное меню"),
        id="back_to_menu_from_ai",
    ),
    getter=get_ai_chat_data,
    state=AISG.chat,
)

ai_dialog = Dialog(ai_chat_window)
