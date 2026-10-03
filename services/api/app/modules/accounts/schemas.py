"""
Pydantic DTO schemas for Telegram account management.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlmodel import SQLModel

from app.modules.accounts.models import AccountStatus
from app.modules.accounts.proxy_utils import mask_proxy_url, parse_proxy_url


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

    @field_validator("title", "lang_code", "status", "session_string", mode="before")
    @classmethod
    def validate_non_nullable(cls, v: Any, info: Any) -> Any:
        if v is None:
            raise ValueError(f"Field '{info.field_name}' cannot be null.")
        return v

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

    @field_validator("proxy_url", mode="before")
    @classmethod
    def mask_proxy(cls, v: str | None) -> str | None:
        return mask_proxy_url(v)


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


class PhoneCodeRequest(BaseModel):
    """Payload to request Telegram confirmation code for a phone number."""

    phone: str = Field(
        min_length=7,
        max_length=32,
        description="Phone number in international format (+7...)",
        examples=["+79991234567"],
    )
    title: str | None = Field(
        default=None,
        max_length=128,
        description="Optional friendly account label",
    )
    api_id: int | None = Field(
        default=None,
        description="Optional Telegram API ID",
    )
    api_hash: str | None = Field(
        default=None,
        max_length=64,
        description="Optional Telegram API Hash",
    )
    proxy_url: str | None = Field(
        default=None,
        max_length=512,
        description="Optional proxy URL",
    )


class PhoneCodeResponse(BaseModel):
    """Response returned after Telegram sends login code."""

    phone_code_hash: str = Field(description="Hash identifying this auth session")
    timeout_seconds: int = Field(default=120, description="Seconds until code expires")
    phone: str = Field(description="Sanitized phone number")


class PhoneSignInRequest(BaseModel):
    """Payload to complete sign-in using phone code or 2FA cloud password."""

    phone_code_hash: str = Field(description="Hash from send-code step")
    code: str = Field(
        default="",
        description="Telegram confirmation code received via SMS or Telegram",
    )
    phone: str | None = Field(
        default=None,
        description="Optional phone number",
    )
    two_fa_password: str | None = Field(
        default=None,
        description="Cloud 2FA password if required",
    )


class PhoneSignInResponse(BaseModel):
    """Response returned upon sign-in attempt."""

    status: str = Field(
        description="Result status: 'success' or 'needs_2fa'",
        examples=["success", "needs_2fa"],
    )
    account: TelegramAccountRead | None = Field(
        default=None,
        description="Authorized account details on success",
    )
    message: str | None = Field(
        default=None,
        description="Informative message or instructions",
    )
    phone_code_hash: str | None = Field(
        default=None,
        description="Phone code hash if 2FA is needed",
    )


class CheckAllAccountsResponse(BaseModel):
    """Aggregated results of batch account health verification."""

    total: int = Field(description="Total non-disabled accounts scanned")
    active: int = Field(
        default=0, description="Number of currently active/authorized accounts"
    )
    revoked: int = Field(
        default=0, description="Number of revoked or unauthorized sessions"
    )
    banned: int = Field(default=0, description="Number of banned accounts")
    flood_wait: int = Field(
        default=0, description="Number of accounts under flood wait"
    )
    error: int = Field(
        default=0, description="Number of accounts that produced an error"
    )
