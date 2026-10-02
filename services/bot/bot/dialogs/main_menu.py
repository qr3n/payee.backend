from typing import Any

from aiogram_dialog import Dialog, DialogManager, Window
from aiogram_dialog.widgets.kbd import Row, Start, SwitchTo
from aiogram_dialog.widgets.text import Const, Format

from bot.client.api import ApiClient
from bot.dialogs.states import AccountsSG, MainSG, ScenariosSG


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
        "🚀 <b>Панель управления платежным сервисом</b>\n\n"
        "Бот позволяет управлять пулом Telegram MTProto аккаунтов,\n"
        "проверять их статус и тестировать сценарии создания платежей.\n\n"
        "Выберите раздел для продолжения:"
    ),
    Row(
        Start(
            Const("📱 Управление сессиями"),
            id="to_accounts",
            state=AccountsSG.list_accounts,
        ),
        Start(
            Const("⚡ Тестирование сценариев"),
            id="to_scenarios",
            state=ScenariosSG.list_scenarios,
        ),
    ),
    Row(
        SwitchTo(
            Const("🩺 Статус системы"),
            id="to_status",
            state=MainSG.system_status,
        ),
        SwitchTo(
            Const("ℹ️ О сервисе"),
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
        "ℹ️ <b>Архитектура платежного сервиса</b>\n\n"
        "• <b>FastAPI + Granian + uvloop</b> — асинхронный REST API\n"
        "• <b>Telethon MTProto</b> — управление сессиями и ботами\n"
        "• <b>SQLModel + PostgreSQL 17</b> — персистентность данных\n"
        "• <b>Taskiq + Redis</b> — таймеры и истечение неоплаченных счетов\n"
        "• <b>aiogram 3 + aiogram-dialog</b> — интерактивная админ-панель"
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
