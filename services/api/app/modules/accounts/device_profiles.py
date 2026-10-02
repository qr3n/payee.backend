"""
Device profiles generator for realistic Telegram MTProto client emulation.
Provides consistent hardware/OS presets for Android, iOS, and Desktop clients.
"""

import random

from pydantic import BaseModel, Field


class DeviceProfile(BaseModel):
    """Consistent device and client parameters for Telethon sessions."""

    device_model: str = Field(description="Hardware model name")
    system_version: str = Field(description="OS version or Android API level")
    app_version: str = Field(description="Telegram client version")
    system_lang_code: str = Field(default="ru-RU", description="System language locale")
    lang_code: str = Field(default="ru", description="Short language code")


_DEVICE_PRESETS: list[dict[str, str]] = [
    {
        "device_model": "Samsung Galaxy S24 Ultra",
        "system_version": "Android 14 (SDK 34)",
        "app_version": "10.14.5",
    },
    {
        "device_model": "Samsung Galaxy S23+",
        "system_version": "Android 14 (SDK 34)",
        "app_version": "10.14.5",
    },
    {
        "device_model": "Samsung Galaxy A54 5G",
        "system_version": "Android 13 (SDK 33)",
        "app_version": "10.13.2",
    },
    {
        "device_model": "Google Pixel 8 Pro",
        "system_version": "Android 14 (SDK 34)",
        "app_version": "10.14.5",
    },
    {
        "device_model": "Google Pixel 7a",
        "system_version": "Android 14 (SDK 34)",
        "app_version": "10.13.0",
    },
    {
        "device_model": "Xiaomi 14 Pro",
        "system_version": "Android 14 (HyperOS)",
        "app_version": "10.14.5",
    },
    {
        "device_model": "Xiaomi Redmi Note 13 Pro",
        "system_version": "Android 13",
        "app_version": "10.12.0",
    },
    {
        "device_model": "iPhone 15 Pro",
        "system_version": "iOS 17.5.1",
        "app_version": "10.14.0",
    },
    {
        "device_model": "iPhone 14",
        "system_version": "iOS 16.6.1",
        "app_version": "10.12.1",
    },
    {
        "device_model": "PC 64bit",
        "system_version": "Windows 11",
        "app_version": "5.2.2 x64",
    },
]


def generate_device_profile(
    lang_code: str = "ru",
    system_lang_code: str | None = None,
    device_model: str | None = None,
    system_version: str | None = None,
    app_version: str | None = None,
) -> DeviceProfile:
    """
    Generate or complete a device profile.
    If specific fields are omitted, a consistent preset is selected.
    """
    preset = random.choice(_DEVICE_PRESETS)

    resolved_system_lang = system_lang_code or (
        "ru-RU" if lang_code == "ru" else f"{lang_code}-{lang_code.upper()}"
    )

    return DeviceProfile(
        device_model=device_model or preset["device_model"],
        system_version=system_version or preset["system_version"],
        app_version=app_version or preset["app_version"],
        system_lang_code=resolved_system_lang,
        lang_code=lang_code,
    )
