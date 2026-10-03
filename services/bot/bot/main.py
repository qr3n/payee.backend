import asyncio
import sys

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram_dialog import setup_dialogs
from aiohttp import web

from bot.client.api import ApiClient
from bot.core.config import BotSettings, get_settings
from bot.core.logging import setup_logging
from bot.core.storage import create_storage
from bot.handlers.router import get_root_router
from bot.middlewares.acl import AdminAclMiddleware
from bot.middlewares.client import ApiClientMiddleware
from bot.middlewares.logging import LoggingMiddleware
from bot.webhook.server import create_webhook_app

logger = structlog.stdlib.get_logger(__name__)


def build_dispatcher(
    settings: BotSettings, api_client: ApiClient
) -> tuple[Dispatcher, Bot]:
    """Construct and configure the Bot and Dispatcher instances."""
    token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    if not token or token.startswith("1234567890:mock"):
        logger.warning(
            "running_with_placeholder_token",
            message=(
                "Using placeholder TELEGRAM_BOT_TOKEN. "
                "Please provide a valid token from @BotFather in production."
            ),
        )

    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    storage = create_storage(settings)
    dp = Dispatcher(storage=storage)

    # Register Middlewares
    dp.update.outer_middleware(LoggingMiddleware())
    dp.update.outer_middleware(AdminAclMiddleware(settings.ADMIN_CHAT_IDS))
    dp.update.outer_middleware(ApiClientMiddleware(api_client))

    # Register Root Router (Commands + Dialogs)
    dp.include_router(get_root_router())

    # Crucial: Register aiogram-dialog managers and handlers
    setup_dialogs(dp)

    return dp, bot


async def run_polling(
    bot: Bot, dp: Dispatcher, settings: BotSettings, api_client: ApiClient
) -> None:
    """Run bot in long-polling mode (ideal for local development)."""
    logger.info("starting_bot_in_polling_mode")
    try:
        if settings.TELEGRAM_DROP_PENDING_UPDATES:
            logger.info("dropping_pending_updates")
            await bot.delete_webhook(drop_pending_updates=True)

        await dp.start_polling(bot)
    finally:
        logger.info("cleaning_up_polling_resources")
        await api_client.close()
        await bot.session.close()


def run_webhook(
    bot: Bot, dp: Dispatcher, settings: BotSettings, api_client: ApiClient
) -> None:
    """Run bot in webhook mode behind Traefik reverse proxy (ideal for production)."""
    logger.info(
        "starting_bot_in_webhook_mode",
        host=settings.WEB_SERVER_HOST,
        port=settings.WEB_SERVER_PORT,
        path=settings.WEBHOOK_PATH,
    )
    app = create_webhook_app(
        bot=bot,
        dp=dp,
        settings=settings,
        api_client=api_client,
    )
    web.run_app(
        app,
        host=settings.WEB_SERVER_HOST,
        port=settings.WEB_SERVER_PORT,
    )


def main() -> None:
    """Application main entry point."""
    settings = get_settings()
    setup_logging(
        json_format=(settings.ENVIRONMENT == "production"),
        log_level=settings.LOG_LEVEL,
    )

    api_client = ApiClient(
        base_url=settings.API_BASE_URL,
        timeout=settings.API_TIMEOUT,
        api_key=settings.API_KEY.get_secret_value() if settings.API_KEY else None,
    )

    dp, bot = build_dispatcher(settings, api_client)

    if settings.TELEGRAM_BOT_MODE == "webhook":
        run_webhook(bot, dp, settings, api_client)
    else:
        try:
            asyncio.run(run_polling(bot, dp, settings, api_client))
        except (KeyboardInterrupt, SystemExit):
            logger.info("bot_polling_stopped_by_user")
            sys.exit(0)


if __name__ == "__main__":
    main()
