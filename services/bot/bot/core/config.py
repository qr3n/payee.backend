from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    """Configuration settings for the Telegram Bot service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Bot identity & mode
    TELEGRAM_BOT_TOKEN: SecretStr = Field(
        default=SecretStr("1234567890:mock_token_for_dev_and_tests"),
        description="Telegram bot token obtained from @BotFather",
    )
    TELEGRAM_BOT_MODE: Literal["polling", "webhook"] = Field(
        default="polling",
        description="Operating mode: 'polling' for local dev, 'webhook' for production",
    )
    TELEGRAM_DROP_PENDING_UPDATES: bool = Field(
        default=True,
        description="Whether to drop unhandled updates on startup",
    )

    # Webhook server configuration (used in webhook mode)
    TELEGRAM_WEBHOOK_URL: str | None = Field(
        default=None,
        description="Public HTTPS URL for the webhook (e.g., https://bot.example.com/webhook)",
    )
    TELEGRAM_WEBHOOK_SECRET: SecretStr | None = Field(
        default=None,
        description="Secret token passed in X-Telegram-Bot-Api-Secret-Token header",
    )
    WEB_SERVER_HOST: str = "0.0.0.0"
    WEB_SERVER_PORT: int = 8080
    WEBHOOK_PATH: str = "/webhook"

    # Backend API connection (Single Source of Truth)
    API_BASE_URL: str = Field(
        default="http://api:8000",
        description="Base URL of the FastAPI backend service",
    )
    API_TIMEOUT: float = Field(
        default=10.0,
        description="HTTP request timeout in seconds for backend calls",
    )

    # Redis FSM Storage Configuration (Isolated for Dialog/FSM state)
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: SecretStr | None = None
    REDIS_FSM_DB: int = 1
    REDIS_URL: str | None = None
    REDIS_FSM_PREFIX: str = "fsm"
    REDIS_STATE_TTL: int | None = 3600 * 24  # 24 hours
    REDIS_DATA_TTL: int | None = 3600 * 24  # 24 hours

    # Environment & Diagnostics
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    @property
    def redis_fsm_uri(self) -> str:
        """Construct the Redis connection string for FSM/dialog storage."""
        if self.REDIS_URL:
            return self.REDIS_URL
        password = self.REDIS_PASSWORD.get_secret_value() if self.REDIS_PASSWORD else ""
        auth = f":{password}@" if password else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_FSM_DB}"


@lru_cache(maxsize=1)
def get_settings() -> BotSettings:
    """Singleton getter for application settings."""
    return BotSettings()
