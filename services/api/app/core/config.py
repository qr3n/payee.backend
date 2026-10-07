from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Project Info
    PROJECT_NAME: str = "FastAPI Service"
    VERSION: str = "0.1.0"
    DESCRIPTION: str = "FastAPI Service with Granian, uvloop, and SQLModel"
    API_V1_STR: str = "/api/v1"

    # Environment
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # Server Configuration (used by Granian / container)
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    WORKERS: int = 1

    # CORS Origins (comma-separated string or list)
    BACKEND_CORS_ORIGINS: list[str] = [
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
        "http://api.localhost",
        "http://app.localhost",
    ]

    # PostgreSQL Database Configuration
    POSTGRES_SERVER: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: SecretStr = SecretStr("postgres")
    POSTGRES_DB: str = "app"
    DATABASE_URL: str | None = None

    # Database Pool Settings
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_PRE_PING: bool = True
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800

    # Redis Cache & Message Broker Configuration
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: SecretStr | None = None
    REDIS_DB: int = 0
    REDIS_URL: str | None = None

    # Redis Connection Pool Settings
    REDIS_MAX_CONNECTIONS: int = 20
    REDIS_SOCKET_TIMEOUT: float = 5.0
    REDIS_SOCKET_CONNECT_TIMEOUT: float = 5.0

    # Telegram MTProto Client Defaults
    TELEGRAM_DEFAULT_API_ID: int = 2040
    TELEGRAM_DEFAULT_API_HASH: str = "b18441a1ff607e10a989891a5462e627"
    TELEGRAM_SESSION_IDLE_TTL: int = 1800  # Keep warm session in memory for 30 minutes

    # Starslly Bot Payment Scenario Defaults
    STARSLY_BOT_USERNAME: str = "starslly_bot"
    STARSLY_CHANNEL_USERNAME: str = "thelab"
    STARS_RECIPIENT_USERNAME: str = "@qr3nnn"
    STARS_CALCULATION_RATE: float = 1.0

    # StarShoppik Bot Payment Scenario Defaults
    STARSHOPPIK_BOT_USERNAME: str = "StarShoppik_bot"
    STARSHOPPIK_CHANNEL_USERNAME: str = "Star_Shopikk"
    STARSHOPPIK_START_PARAM: str = "ref1287935345"

    # HelperStars Bot Payment Scenario Defaults
    HELPERSTARS_BOT_USERNAME: str = "HelperStars_Robot"
    HELPERSTARS_CHANNEL_USERNAME: str = "HelperStars_Rezerv"
    HELPERSTARS_START_PARAM: str = "1287935345"

    # Telegram Bot Admin & Notifications
    ADMIN_API_KEY: SecretStr | None = None
    TELEGRAM_BOT_TOKEN: SecretStr | None = None
    ADMIN_CHAT_IDS: list[int] = []
    ACCOUNT_CHECK_INTERVAL_SECONDS: int = 300
    ACCOUNT_CHECK_BACKGROUND_ENABLED: bool = True

    # Outbound Payment Webhook Configuration
    PAYMENT_WEBHOOK_SECRET: SecretStr | None = None
    PAYMENT_WEBHOOK_TIMEOUT_SECONDS: float = 10.0
    PAYMENT_WEBHOOK_MAX_RETRIES: int = 3

    @property
    def redis_uri(self) -> str:
        """Constructs an async Redis connection string."""
        if self.REDIS_URL:
            return self.REDIS_URL
        password = self.REDIS_PASSWORD.get_secret_value() if self.REDIS_PASSWORD else ""
        auth = f":{password}@" if password else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def async_database_uri(self) -> str:
        """Constructs an asyncpg database connection string."""
        if self.DATABASE_URL:
            if self.DATABASE_URL.startswith("postgresql://"):
                return self.DATABASE_URL.replace(
                    "postgresql://", "postgresql+asyncpg://", 1
                )
            return self.DATABASE_URL
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD.get_secret_value(),
            host=self.POSTGRES_SERVER,
            port=self.POSTGRES_PORT,
            database=self.POSTGRES_DB,
        ).render_as_string(hide_password=False)

    @field_validator("ADMIN_CHAT_IDS", mode="before")
    @classmethod
    def assemble_admin_chat_ids(cls, v: object) -> list[int]:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("[") and v.endswith("]"):
                import json

                try:
                    return [int(x) for x in json.loads(v)]
                except Exception:
                    pass
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, (list, tuple, set)):
            return [int(x) for x in v]
        if isinstance(v, int):
            return [v]
        return []

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",") if i.strip()]
        elif isinstance(v, list):
            return [str(i).strip() for i in v if str(i).strip()]
        return [
            "http://localhost",
            "http://localhost:3000",
            "http://localhost:5173",
            "http://localhost:8080",
            "http://api.localhost",
            "http://app.localhost",
        ]


settings = Settings()


def get_settings() -> Settings:
    """Singleton getter for application settings."""
    return settings
