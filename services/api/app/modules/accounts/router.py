"""
FastAPI router endpoints for Telegram accounts management and verification.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import get_db
from app.core.exceptions import NotFoundException
from app.modules.accounts import service as account_service
from app.modules.accounts.schemas import (
    TelegramAccountCheckResponse,
    TelegramAccountCreate,
    TelegramAccountRead,
    TelegramAccountUpdate,
)
from app.shared.pagination import PageParams, PaginatedResponse

router = APIRouter(prefix="/accounts", tags=["Telegram Accounts"])


@router.post(
    "/",
    response_model=TelegramAccountRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add Telegram account",
    description=(
        "Registers a new Telegram account with automated device profile "
        "generation and optional proxy."
    ),
)
async def create_account(
    account_in: TelegramAccountCreate,
    db: AsyncSession = Depends(get_db),
) -> TelegramAccountRead:
    """Create a new Telegram account session."""
    account = await account_service.create_account(session=db, account_in=account_in)
    return TelegramAccountRead.model_validate(account)


@router.get(
    "/",
    response_model=PaginatedResponse[TelegramAccountRead],
    summary="List Telegram accounts",
    description="Retrieve paginated list of registered Telegram accounts.",
)
async def list_accounts(
    params: PageParams = Depends(),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[TelegramAccountRead]:
    """Retrieve paginated list of accounts."""
    accounts, total = await account_service.list_accounts_paginated(
        session=db, params=params
    )
    items = [TelegramAccountRead.model_validate(a) for a in accounts]
    return PaginatedResponse.create(items=items, total=total, params=params)


@router.get(
    "/{account_id}",
    response_model=TelegramAccountRead,
    summary="Get Telegram account details",
    description="Retrieve details for a specific Telegram account by UUID.",
)
async def get_account(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> TelegramAccountRead:
    """Retrieve account details."""
    account = await account_service.get_account(session=db, account_id=account_id)
    if not account:
        raise NotFoundException(f"Telegram account with ID '{account_id}' not found.")
    return TelegramAccountRead.model_validate(account)


@router.patch(
    "/{account_id}",
    response_model=TelegramAccountRead,
    summary="Update Telegram account",
    description="Partially update account metadata, proxy, or status.",
)
async def update_account(
    account_id: UUID,
    account_in: TelegramAccountUpdate,
    db: AsyncSession = Depends(get_db),
) -> TelegramAccountRead:
    """Partially update account."""
    account = await account_service.get_account(session=db, account_id=account_id)
    if not account:
        raise NotFoundException(f"Telegram account with ID '{account_id}' not found.")
    updated = await account_service.update_account(
        session=db, db_account=account, account_in=account_in
    )
    return TelegramAccountRead.model_validate(updated)


@router.delete(
    "/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Telegram account",
    description="Permanently delete a Telegram account session.",
)
async def delete_account(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete account session."""
    account = await account_service.get_account(session=db, account_id=account_id)
    if not account:
        raise NotFoundException(f"Telegram account with ID '{account_id}' not found.")
    await account_service.delete_account(session=db, db_account=account)


@router.post(
    "/{account_id}/check",
    response_model=TelegramAccountCheckResponse,
    summary="Check Telegram account status",
    description=(
        "Perform MTProto check via Telethon to verify session authorization "
        "and update profile info."
    ),
)
async def check_account_status(
    account_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> TelegramAccountCheckResponse:
    """Perform on-demand verification of account status."""
    account = await account_service.get_account(session=db, account_id=account_id)
    if not account:
        raise NotFoundException(f"Telegram account with ID '{account_id}' not found.")

    _, response = await account_service.verify_and_update_account(
        session=db, db_account=account
    )
    return response
