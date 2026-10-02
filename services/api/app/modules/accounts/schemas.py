"""
Pydantic DTO schemas for Telegram account management.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlmodel import SQLModel

from app.modules.accounts.models import AccountStatus
from app.modules.accounts.proxy_utils import parse_proxy_url


class TelegramAccountBaseSchema(SQLModel):
    """Base shared attributes for Telegram account schemas."""

    title: str = Field(
        min_length=1,
        max_length=128,
        description="Friendly identifier or label",
        examples=["Payment Worker #1"],
    )
    phone: str | None = Field(
        default=None,
        max_length=32,
        description="Associated international phone number",
        examples=["+79991234567"],
    )
    proxy_url: str | None = Field(
        default=None,
        max_length=512,
        description="Proxy URL (e.g. socks5://user:pass@host:port)",
        examples=["socks5://user:pass@192.168.1.100:1080"],
    )
    device_model: str | None = Field(
        default=None,
        max_length=128,
        description="Device model name (auto-generated if omitted)",
        examples=["Samsung Galaxy S24 Ultra"],
    )
    system_version: str | None = Field(
        default=None,
        max_length=64,
        description="OS / API version (auto-generated if omitted)",
        examples=["Android 14 (SDK 34)"],
    )
    app_version: str | None = Field(
        default=None,
        max_length=64,
        description="Telegram app version (auto-generated if omitted)",
        examples=["10.14.5"],
    )
    system_lang_code: str | None = Field(
        default=None,
        max_length=16,
        description="System language code (defaults based on lang_code)",
        examples=["ru-RU"],
    )
    lang_code: str = Field(
        default="ru",
        max_length=16,
        description="Client language code",
        examples=["ru"],
    )
    api_id: int | None = Field(
        default=None,
        description="Telegram API ID override",
        examples=[2040],
    )
    api_hash: str | None = Field(
        default=None,
        max_length=64,
        description="Telegram API Hash override",
        examples=["b18441a1ff607e10a989891a5462e627"],
    )

    @field_validator("proxy_url")
    @classmethod
    def validate_proxy(cls, v: str | None) -> str | None:
        if v is not None:
            parse_proxy_url(v)
        return v


class TelegramAccountCreate(TelegramAccountBaseSchema):
    """Schema for adding a new Telegram account session."""

    session_string: str = Field(
        min_length=10,
        description="Telethon StringSession auth key string",
        examples=["1ApWapzMBu7..."],
    )
    verify_on_create: bool = Field(
        default=False,
        description="Whether to immediately test MTProto connection to Telegram",
        examples=[True],
    )


class TelegramAccountUpdate(SQLModel):
    """Schema for updating an existing Telegram account (partial updates)."""

    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Updated label",
    )
    phone: str | None = Field(
        default=None,
        max_length=32,
        description="Updated phone number",
    )
    proxy_url: str | None = Field(
        default=None,
        max_length=512,
        description="Updated proxy URL",
    )
    device_model: str | None = Field(
        default=None,
        max_length=128,
        description="Updated device model",
    )
    system_version: str | None = Field(
        default=None,
        max_length=64,
        description="Updated OS version",
    )
    app_version: str | None = Field(
        default=None,
        max_length=64,
        description="Updated client version",
    )
    system_lang_code: str | None = Field(
        default=None,
        max_length=16,
        description="Updated system locale",
    )
    lang_code: str | None = Field(
        default=None,
        max_length=16,
        description="Updated client language",
    )
    api_id: int | None = Field(
        default=None,
        description="Updated API ID",
    )
    api_hash: str | None = Field(
        default=None,
        max_length=64,
        description="Updated API Hash",
    )
    status: AccountStatus | None = Field(
        default=None,
        description="Manually override status (e.g. disabled or active)",
    )
    session_string: str | None = Field(
        default=None,
        min_length=10,
        description="Replace StringSession string",
    )

    @field_validator("proxy_url")
    @classmethod
    def validate_proxy(cls, v: str | None) -> str | None:
        if v is not None:
            parse_proxy_url(v)
        return v


class TelegramAccountRead(SQLModel):
    """Safe schema for returning Telegram account details in responses."""

    id: UUID = Field(description="Account unique identifier (UUIDv7)")
    title: str = Field(description="Account label")
    phone: str | None = Field(description="Phone number")
    proxy_url: str | None = Field(description="Configured proxy URL")
    device_model: str = Field(description="Device model")
    system_version: str = Field(description="OS version")
    app_version: str = Field(description="App version")
    system_lang_code: str = Field(description="System locale")
    lang_code: str = Field(description="Language code")
    api_id: int | None = Field(description="Telegram API ID")
    status: AccountStatus = Field(description="Current status")
    telegram_user_id: int | None = Field(description="Telegram User ID")
    first_name: str | None = Field(description="First name")
    last_name: str | None = Field(description="Last name")
    username: str | None = Field(description="Username")
    is_premium: bool | None = Field(description="Premium status")
    flood_wait_until: datetime | None = Field(description="FloodWait expiry")
    last_checked_at: datetime | None = Field(description="Last check timestamp")
    last_error: str | None = Field(description="Last error description")
    created_at: datetime = Field(description="Creation timestamp")
    updated_at: datetime = Field(description="Last update timestamp")


class TelegramAccountCheckResponse(BaseModel):
    """Response returned when an account status check is executed."""

    account_id: UUID = Field(description="Account identifier")
    status: AccountStatus = Field(description="Evaluated status")
    is_authorized: bool = Field(description="Whether session is valid and authorized")
    telegram_user_id: int | None = Field(default=None, description="Telegram user ID")
    first_name: str | None = Field(default=None, description="First name")
    last_name: str | None = Field(default=None, description="Last name")
    username: str | None = Field(default=None, description="Telegram username")
    phone: str | None = Field(default=None, description="Phone number")
    is_premium: bool | None = Field(default=None, description="Telegram Premium flag")
    flood_wait_seconds: int | None = Field(
        default=None, description="Seconds to wait if flood wait"
    )
    error: str | None = Field(default=None, description="Error explanation if any")
    checked_at: datetime = Field(description="Check timestamp")
