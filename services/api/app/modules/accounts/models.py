from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Column, String, Text
from sqlmodel import Field, SQLModel

from app.shared.models import BaseUUIDModel


class AccountStatus(StrEnum):
    """Operational status of a Telegram account."""

    ACTIVE = "active"
    BANNED = "banned"
    REVOKED = "revoked"
    FLOOD_WAIT = "flood_wait"
    ERROR = "error"
    DISABLED = "disabled"


class TelegramAccountBase(SQLModel):
    """Shared properties for Telegram account entity."""

    title: str = Field(
        min_length=1,
        max_length=128,
        description="Friendly name/label for the account",
        schema_extra={"examples": ["Payment Worker #1"]},
    )
    phone: str | None = Field(
        default=None,
        max_length=32,
        index=True,
        description="Associated phone number in international format",
        schema_extra={"examples": ["+79991234567"]},
    )
    proxy_url: str | None = Field(
        default=None,
        max_length=512,
        description="Proxy URL (socks5://user:pass@host:port or http://...)",
        schema_extra={"examples": ["socks5://user:pass@192.168.1.100:1080"]},
    )
    device_model: str = Field(
        max_length=128,
        description="Device hardware model name",
        schema_extra={"examples": ["Samsung Galaxy S24 Ultra"]},
    )
    system_version: str = Field(
        max_length=64,
        description="Operating system / API level",
        schema_extra={"examples": ["Android 14 (SDK 34)"]},
    )
    app_version: str = Field(
        max_length=64,
        description="Telegram client app version",
        schema_extra={"examples": ["10.14.5"]},
    )
    system_lang_code: str = Field(
        default="ru-RU",
        max_length=16,
        description="System language locale code",
        schema_extra={"examples": ["ru-RU"]},
    )
    lang_code: str = Field(
        default="ru",
        max_length=16,
        description="Client language code",
        schema_extra={"examples": ["ru"]},
    )
    api_id: int | None = Field(
        default=None,
        description="Telegram API ID override (defaults to app config)",
        schema_extra={"examples": [2040]},
    )
    api_hash: str | None = Field(
        default=None,
        max_length=64,
        description="Telegram API Hash override (defaults to app config)",
        schema_extra={"examples": ["b18441a1ff607e10a989891a5462e627"]},
    )


class TelegramAccount(TelegramAccountBase, BaseUUIDModel, table=True):
    """Database entity representing a Telegram account session."""

    __tablename__ = "telegram_accounts"

    session_string: str = Field(
        sa_column=Column(Text, nullable=False),
        description="Telethon StringSession auth key string",
    )
    status: AccountStatus = Field(
        default=AccountStatus.ACTIVE,
        sa_column=Column(
            String(32),
            nullable=False,
            index=True,
            default=AccountStatus.ACTIVE.value,
        ),
        description="Current account operational status",
    )
    telegram_user_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True, index=True),
        description="Telegram 64-bit user identifier",
    )
    first_name: str | None = Field(
        default=None,
        max_length=128,
        description="Telegram account first name",
    )
    last_name: str | None = Field(
        default=None,
        max_length=128,
        description="Telegram account last name",
    )
    username: str | None = Field(
        default=None,
        max_length=128,
        index=True,
        description="Telegram username handle without @",
    )
    is_premium: bool | None = Field(
        default=None,
        description="Indicates whether the account has Telegram Premium subscription",
    )
    flood_wait_until: datetime | None = Field(
        default=None,
        description="UTC timestamp until which FloodWait is active",
    )
    last_checked_at: datetime | None = Field(
        default=None,
        description="UTC timestamp of the last verification check",
    )
    last_error: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="Details of the last encountered error",
    )
