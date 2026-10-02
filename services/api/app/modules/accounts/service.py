"""
Business logic and persistence layer for Telegram accounts management.
Follows Unit of Work: NEVER calls session.commit(), relies on dependency flush.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.accounts.device_profiles import generate_device_profile
from app.modules.accounts.models import TelegramAccount
from app.modules.accounts.schemas import (
    TelegramAccountCheckResponse,
    TelegramAccountCreate,
    TelegramAccountUpdate,
)
from app.modules.accounts.telethon_checker import (
    calculate_flood_wait_until,
    check_telegram_account_status,
)
from app.shared.pagination import PageParams


async def create_account(
    session: AsyncSession,
    account_in: TelegramAccountCreate,
) -> TelegramAccount:
    """
    Create a new Telegram account record.
    Automatically generates hardware/OS profile if not fully specified.
    Optionally tests authorization via MTProto if verify_on_create is requested.
    """
    profile = generate_device_profile(
        lang_code=account_in.lang_code,
        system_lang_code=account_in.system_lang_code,
        device_model=account_in.device_model,
        system_version=account_in.system_version,
        app_version=account_in.app_version,
    )

    account_data = account_in.model_dump(exclude={"verify_on_create"})
    account_data["device_model"] = profile.device_model
    account_data["system_version"] = profile.system_version
    account_data["app_version"] = profile.app_version
    account_data["system_lang_code"] = profile.system_lang_code
    account_data["lang_code"] = profile.lang_code

    account = TelegramAccount(**account_data)

    if account_in.verify_on_create:
        check = await check_telegram_account_status(
            session_string=account.session_string,
            api_id=account.api_id,
            api_hash=account.api_hash,
            device_model=account.device_model,
            system_version=account.system_version,
            app_version=account.app_version,
            system_lang_code=account.system_lang_code,
            lang_code=account.lang_code,
            proxy_url=account.proxy_url,
        )
        account.status = check.status
        account.last_checked_at = datetime.now(UTC)
        account.last_error = check.error
        if check.is_authorized:
            account.telegram_user_id = check.telegram_user_id
            account.first_name = check.first_name
            account.last_name = check.last_name
            account.username = check.username
            if check.phone:
                account.phone = check.phone
            account.is_premium = check.is_premium
        if check.flood_wait_seconds:
            account.flood_wait_until = calculate_flood_wait_until(
                check.flood_wait_seconds
            )

    session.add(account)
    await session.flush()
    await session.refresh(account)
    return account


async def get_account(
    session: AsyncSession,
    account_id: UUID,
) -> TelegramAccount | None:
    """Retrieve a single Telegram account by UUID."""
    statement = select(TelegramAccount).where(TelegramAccount.id == account_id)
    result = await session.exec(statement)
    return result.first()


async def list_accounts_paginated(
    session: AsyncSession,
    params: PageParams,
) -> tuple[Sequence[TelegramAccount], int]:
    """Retrieve paginated Telegram accounts and total count."""
    count_statement = select(func.count()).select_from(TelegramAccount)
    total_result = await session.exec(count_statement)
    total = total_result.one() or 0

    statement = (
        select(TelegramAccount)
        .order_by(col(TelegramAccount.created_at).desc())
        .offset(params.offset)
        .limit(params.size)
    )
    result = await session.exec(statement)
    return result.all(), total


async def update_account(
    session: AsyncSession,
    db_account: TelegramAccount,
    account_in: TelegramAccountUpdate,
) -> TelegramAccount:
    """Partially update account metadata within active transaction."""
    update_data = account_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_account, key, value)

    session.add(db_account)
    await session.flush()
    await session.refresh(db_account)
    return db_account


async def delete_account(
    session: AsyncSession,
    db_account: TelegramAccount,
) -> None:
    """Delete a Telegram account within active transaction."""
    await session.delete(db_account)
    await session.flush()


async def verify_and_update_account(
    session: AsyncSession,
    db_account: TelegramAccount,
) -> tuple[TelegramAccount, TelegramAccountCheckResponse]:
    """
    Perform an MTProto connection check on the account, update its state in DB,
    and return the detailed response.
    """
    check = await check_telegram_account_status(
        session_string=db_account.session_string,
        api_id=db_account.api_id,
        api_hash=db_account.api_hash,
        device_model=db_account.device_model,
        system_version=db_account.system_version,
        app_version=db_account.app_version,
        system_lang_code=db_account.system_lang_code,
        lang_code=db_account.lang_code,
        proxy_url=db_account.proxy_url,
    )

    checked_at = datetime.now(UTC)
    db_account.status = check.status
    db_account.last_checked_at = checked_at
    db_account.last_error = check.error

    if check.is_authorized:
        db_account.telegram_user_id = check.telegram_user_id
        db_account.first_name = check.first_name
        db_account.last_name = check.last_name
        db_account.username = check.username
        if check.phone:
            db_account.phone = check.phone
        db_account.is_premium = check.is_premium

    if check.flood_wait_seconds:
        db_account.flood_wait_until = calculate_flood_wait_until(
            check.flood_wait_seconds
        )

    session.add(db_account)
    await session.flush()
    await session.refresh(db_account)

    response = TelegramAccountCheckResponse(
        account_id=db_account.id,
        status=check.status,
        is_authorized=check.is_authorized,
        telegram_user_id=check.telegram_user_id,
        first_name=check.first_name,
        last_name=check.last_name,
        username=check.username,
        phone=check.phone or db_account.phone,
        is_premium=check.is_premium,
        flood_wait_seconds=check.flood_wait_seconds,
        error=check.error,
        checked_at=checked_at,
    )

    return db_account, response


async def create_account_from_files(
    session: AsyncSession,
    session_bytes: bytes,
    json_bytes: bytes,
    title: str | None = None,
    proxy_url: str | None = None,
    verify: bool = True,
) -> TelegramAccount:
    """
    Create a new Telegram account from an uploaded .session file and JSON metadata.
    Extracts app_id, app_hash, device profile, and proxy directly from JSON.
    """
    from app.modules.accounts.session_converter import (
        convert_sqlite_session_bytes_to_string,
        parse_client_json,
    )

    meta = parse_client_json(json_bytes)
    session_string = convert_sqlite_session_bytes_to_string(session_bytes)

    effective_proxy = proxy_url or meta.get("proxy_url")
    effective_api_id = meta.get("api_id")
    effective_api_hash = meta.get("api_hash")

    account = TelegramAccount(
        title=title or meta.get("phone") or "Uploaded Session",
        phone=meta.get("phone"),
        proxy_url=effective_proxy,
        session_string=session_string,
        device_model=meta["device_model"],
        system_version=meta["system_version"],
        app_version=meta["app_version"],
        system_lang_code=meta["system_lang_code"],
        lang_code=meta["lang_code"],
        api_id=effective_api_id,
        api_hash=effective_api_hash,
    )

    if verify:
        check = await check_telegram_account_status(
            session_string=account.session_string,
            api_id=account.api_id,
            api_hash=account.api_hash,
            device_model=account.device_model,
            system_version=account.system_version,
            app_version=account.app_version,
            system_lang_code=account.system_lang_code,
            lang_code=account.lang_code,
            proxy_url=account.proxy_url,
        )
        account.status = check.status
        account.last_checked_at = datetime.now(UTC)
        account.last_error = check.error
        if check.is_authorized:
            account.telegram_user_id = check.telegram_user_id
            account.first_name = check.first_name
            account.last_name = check.last_name
            account.username = check.username
            if check.phone:
                account.phone = check.phone
            account.is_premium = check.is_premium
            if not title:
                account.title = (
                    f"@{check.username}"
                    if check.username
                    else f"Account {check.telegram_user_id}"
                )

        if check.flood_wait_seconds:
            account.flood_wait_until = calculate_flood_wait_until(
                check.flood_wait_seconds
            )

    session.add(account)
    await session.flush()
    await session.refresh(account)
    return account
