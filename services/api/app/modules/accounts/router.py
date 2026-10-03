"""
FastAPI router endpoints for Telegram accounts management and verification.
"""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import get_db, verify_admin_key
from app.core.exceptions import AppException, NotFoundException
from app.modules.accounts import phone_auth_service
from app.modules.accounts import service as account_service
from app.modules.accounts.models import AccountStatus
from app.modules.accounts.schemas import (
    CheckAllAccountsResponse,
    PhoneCodeRequest,
    PhoneCodeResponse,
    PhoneSignInRequest,
    PhoneSignInResponse,
    TelegramAccountCheckResponse,
    TelegramAccountCreate,
    TelegramAccountRead,
    TelegramAccountUpdate,
)
from app.modules.payments.tasks import dispatch_account_scenarios_warmup
from app.shared.pagination import PageParams, PaginatedResponse

router = APIRouter(
    prefix="/accounts",
    tags=["Telegram Accounts"],
    dependencies=[Depends(verify_admin_key)],
)


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
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> TelegramAccountRead:
    """Create a new Telegram account session."""
    account = await account_service.create_account(session=db, account_in=account_in)
    if account.status == AccountStatus.ACTIVE:
        background_tasks.add_task(dispatch_account_scenarios_warmup, account.id)
    return TelegramAccountRead.model_validate(account)


@router.post(
    "/upload",
    response_model=TelegramAccountRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload .session and .json pair",
    description=(
        "Upload a Telethon .session file along with its client .json metadata. "
        "Automatically extracts app_id, app_hash, device profile, and proxy."
    ),
)
async def upload_account_session(
    session_file: UploadFile = File(..., description="Telethon .session file"),
    json_file: UploadFile = File(..., description="Client metadata .json file"),
    title: str | None = Form(default=None, description="Optional account label"),
    proxy_url: str | None = Form(default=None, description="Optional proxy override"),
    verify: bool = Form(default=True, description="Verify session via MTProto"),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
) -> TelegramAccountRead:
    """Import an account from .session + .json files."""
    if not session_file.filename or not session_file.filename.endswith(".session"):
        raise AppException("First file must have a .session extension")
    if not json_file.filename or not json_file.filename.endswith(".json"):
        raise AppException("Second file must have a .json extension")

    session_bytes = await session_file.read()
    json_bytes = await json_file.read()

    account = await account_service.create_account_from_files(
        session=db,
        session_bytes=session_bytes,
        json_bytes=json_bytes,
        title=title,
        proxy_url=proxy_url,
        verify=verify,
    )
    if account.status == AccountStatus.ACTIVE:
        background_tasks.add_task(dispatch_account_scenarios_warmup, account.id)
    return TelegramAccountRead.model_validate(account)


@router.post(
    "/auth/send-code",
    response_model=PhoneCodeResponse,
    summary="Request Telegram login code via phone",
    description="Initiates MTProto connection and sends confirmation code to Telegram.",
)
async def send_phone_code(
    payload: PhoneCodeRequest,
) -> PhoneCodeResponse:
    """Request confirmation code for phone number."""
    result = await phone_auth_service.request_phone_code(
        phone=payload.phone,
        title=payload.title,
        api_id=payload.api_id,
        api_hash=payload.api_hash,
        proxy_url=payload.proxy_url,
    )
    return PhoneCodeResponse(
        phone_code_hash=result["phone_code_hash"],
        timeout_seconds=result["timeout_seconds"],
        phone=result["phone"],
    )


@router.post(
    "/auth/sign-in",
    response_model=PhoneSignInResponse,
    summary="Complete phone login with code or 2FA password",
    description="Verifies Telegram code or cloud password and creates account in pool.",
)
async def sign_in_phone(
    payload: PhoneSignInRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> PhoneSignInResponse:
    """Sign in using phone code or 2FA password."""
    result = await phone_auth_service.sign_in_with_phone(
        phone_code_hash=payload.phone_code_hash,
        code=payload.code,
        db_session=db,
        phone=payload.phone,
        two_fa_password=payload.two_fa_password,
    )

    if result.get("status") == "needs_2fa":
        return PhoneSignInResponse(
            status="needs_2fa",
            message=result.get("message"),
            phone_code_hash=result.get("phone_code_hash"),
        )

    acc = result.get("account")
    if acc and acc.status == AccountStatus.ACTIVE:
        background_tasks.add_task(dispatch_account_scenarios_warmup, acc.id)
    account_dto = TelegramAccountRead.model_validate(acc) if acc else None
    return PhoneSignInResponse(
        status="success",
        account=account_dto,
        message=result.get("message"),
    )


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


@router.post(
    "/check-all",
    response_model=CheckAllAccountsResponse,
    summary="Batch verify all Telegram accounts",
    description=(
        "Scans all non-disabled Telegram accounts, checks their MTProto status, "
        "updates database records, evicts revoked sessions, and alerts admins."
    ),
)
async def check_all_accounts_endpoint(
    db: AsyncSession = Depends(get_db),
) -> CheckAllAccountsResponse:
    """Trigger batch verification of all Telegram accounts."""
    counts = await account_service.check_all_accounts(session=db)
    return CheckAllAccountsResponse(**counts)


@router.post(
    "/{account_id}/prepare",
    summary="Prepare account for all payment scenarios",
    description=(
        "Asynchronously joins required channels, runs /start, and prepares "
        "bot states for all scenarios."
    ),
)
async def prepare_account_endpoint(
    account_id: UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Trigger background scenario preparation for the specified account."""
    account = await account_service.get_account(session=db, account_id=account_id)
    if not account:
        raise NotFoundException(f"Telegram account with ID '{account_id}' not found.")

    background_tasks.add_task(dispatch_account_scenarios_warmup, account.id)
    return {
        "message": "Account scenarios preparation task dispatched in background",
        "account_id": str(account.id),
    }
