from typing import Any

from aiogram_dialog import Dialog, DialogManager, Window
from aiogram_dialog.widgets.kbd import Row, Start, SwitchTo
from aiogram_dialog.widgets.text import Const, Format

from bot.client.api import ApiClient
from bot.dialogs.states import ItemsSG, MainSG


async def get_system_status(
    dialog_manager: DialogManager,  # noqa: ARG001
    api_client: ApiClient,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch health and readiness from the FastAPI backend."""
    try:
        health = await api_client.get_health()
        readiness = await api_client.get_readiness()
        return {
            "service": health.service,
            "version": health.version,
            "environment": health.environment,
            "status": "🟢 Работает" if health.status == "ok" else f"🟡 {health.status}",
            "db_status": "✅ Подключена" if readiness.database else "❌ Ошибка",
            "redis_status": "✅ Подключен" if readiness.redis else "❌ Ошибка",
            "error": None,
        }
    except Exception as exc:
        return {
            "service": "FastAPI Backend",
            "version": "неизвестно",
            "environment": "неизвестно",
            "status": "🔴 Недоступен",
            "db_status": "—",
            "redis_status": "—",
            "error": str(exc),
        }


main_menu_window = Window(
    Const(
        "🚀 <b>Главное меню Telegram Bot</b>\n\n"
        "Бот построен на <b>aiogram 3</b> и <b>aiogram-dialog</b>.\n"
        "Архитектура: бот выступает в роли клиента (BFF/фронтенда),\n"
        "а FastAPI бэкенд является единым источником правды (SSOT).\n\n"
        "Выберите раздел для продолжения:"
    ),
    Start(
        Const("📦 Управление Items"),
        id="to_items",
        state=ItemsSG.list_items,
    ),
    Row(
        SwitchTo(
            Const("🩺 Статус системы"),
            id="to_status",
            state=MainSG.system_status,
        ),
        SwitchTo(
            Const("ℹ️ О проекте"),
            id="to_about",
            state=MainSG.about,
        ),
    ),
    state=MainSG.menu,
)

system_status_window = Window(
    Format(
        "🩺 <b>Статус Backend API</b>\n\n"
        "Сервис: <b>{service}</b>\n"
        "Версия: <b>{version}</b>\n"
        "Окружение: <b>{environment}</b>\n"
        "API Health: <b>{status}</b>\n"
        "PostgreSQL: <b>{db_status}</b>\n"
        "Redis: <b>{redis_status}</b>\n"
    ),
    SwitchTo(
        Const("🔙 Назад в меню"),
        id="back_to_menu_from_status",
        state=MainSG.menu,
    ),
    getter=get_system_status,
    state=MainSG.system_status,
)

about_window = Window(
    Const(
        "ℹ️ <b>Архитектура проекта</b>\n\n"
        "• <b>FastAPI + Granian + uvloop</b> — асинхронный REST API\n"
        "• <b>SQLModel + PostgreSQL 17</b> — персистентность данных\n"
        "• <b>Taskiq + Redis</b> — фоновые задачи (Workers)\n"
        "• <b>Redis Storage</b> — изолированное хранилище FSM\n"
        "• <b>Traefik v3</b> — Reverse Proxy (Webhook / Polling)\n"
        "• <b>BFF Pattern</b> — Бот общается с API по HTTP"
    ),
    SwitchTo(
        Const("🔙 Назад в меню"),
        id="back_to_menu_from_about",
        state=MainSG.menu,
    ),
    state=MainSG.about,
)

main_dialog = Dialog(
    main_menu_window,
    system_status_window,
    about_window,
)
