"""Webhook package for Telegram bot production deployment."""

from bot.webhook.server import create_webhook_app

__all__ = ["create_webhook_app"]
