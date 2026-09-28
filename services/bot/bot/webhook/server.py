import structlog
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from bot.client.api import ApiClient
from bot.core.config import BotSettings

logger = structlog.stdlib.get_logger(__name__)


def create_webhook_app(
    bot: Bot,
    dp: Dispatcher,
    settings: BotSettings,
    api_client: ApiClient,
) -> web.Application:
    """Create and configure an aiohttp Application for processing Telegram webhooks."""
    app = web.Application()

    # Health check route for Docker, Traefik, and container orchestrators
    async def health_handler(_request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "service": "telegram-bot",
                "mode": "webhook",
                "environment": settings.ENVIRONMENT,
            }
        )

    app.router.add_get("/health", health_handler)

    # Register startup and shutdown lifecycle hooks
    async def on_startup(_application: web.Application) -> None:
        if not settings.TELEGRAM_WEBHOOK_URL:
            raise ValueError(
                "TELEGRAM_WEBHOOK_URL is required when running in webhook mode"
            )

        secret_token = (
            settings.TELEGRAM_WEBHOOK_SECRET.get_secret_value()
            if settings.TELEGRAM_WEBHOOK_SECRET
            else None
        )

        logger.info(
            "setting_telegram_webhook",
            webhook_url=settings.TELEGRAM_WEBHOOK_URL,
            drop_pending=settings.TELEGRAM_DROP_PENDING_UPDATES,
        )

        await bot.set_webhook(
            url=settings.TELEGRAM_WEBHOOK_URL,
            secret_token=secret_token,
            drop_pending_updates=settings.TELEGRAM_DROP_PENDING_UPDATES,
        )

    async def on_shutdown(_application: web.Application) -> None:
        logger.info("shutting_down_bot_webhook")
        await api_client.close()
        if (
            hasattr(bot, "session")
            and bot.session is not None
            and hasattr(bot.session, "close")
        ):
            await bot.session.close()

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    # Attach aiogram SimpleRequestHandler with secret token validation
    secret_token_val = (
        settings.TELEGRAM_WEBHOOK_SECRET.get_secret_value()
        if settings.TELEGRAM_WEBHOOK_SECRET
        else None
    )
    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=secret_token_val,
    )
    webhook_handler.register(app, path=settings.WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)
    return app
