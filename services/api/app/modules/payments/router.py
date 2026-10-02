"""
FastAPI router endpoints for creating, managing, and reacting to payments.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import get_db
from app.core.exceptions import NotFoundException
from app.modules.payments import service as payment_service
from app.modules.payments.models import PaymentStatus
from app.modules.payments.scenarios import scenario_registry
from app.modules.payments.schemas import (
    PaymentCallback,
    PaymentCreate,
    PaymentRead,
    ScenarioRead,
)
from app.shared.pagination import PageParams, PaginatedResponse

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.post(
    "/",
    response_model=PaymentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create payment invoice",
    description=(
        "Reserves a free Telegram account for 30m and executes the requested "
        "bot scenario to generate an invoice link. Cancels previous pending "
        "payment from the same user and reuses their account."
    ),
)
async def create_payment(
    payment_in: PaymentCreate,
    db: AsyncSession = Depends(get_db),
) -> PaymentRead:
    """Create a new payment transaction."""
    payment = await payment_service.create_payment(session=db, payment_in=payment_in)
    return PaymentRead.model_validate(payment)


@router.get(
    "/",
    response_model=PaginatedResponse[PaymentRead],
    summary="List payments",
    description="Retrieve paginated list of payment transactions.",
)
async def list_payments(
    params: PageParams = Depends(),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[PaymentRead]:
    """Retrieve paginated payments."""
    payments, total = await payment_service.list_payments_paginated(
        session=db, params=params
    )
    items = [PaymentRead.model_validate(p) for p in payments]
    return PaginatedResponse.create(items=items, total=total, params=params)


@router.get(
    "/scenarios",
    response_model=list[ScenarioRead],
    summary="List available payment scenarios",
    description="Retrieve all registered bot payment scenarios and strategies.",
)
async def list_scenarios() -> list[ScenarioRead]:
    """List registered payment scenarios."""
    scenarios = scenario_registry.list()
    return [
        ScenarioRead(
            scenario_id=s.scenario_id,
            name=s.name,
            description=s.description,
        )
        for s in scenarios
    ]


@router.get(
    "/{payment_id}",
    response_model=PaymentRead,
    summary="Get payment details",
    description="Retrieve full details and status for a specific payment.",
)
async def get_payment(
    payment_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> PaymentRead:
    """Retrieve payment details."""
    payment = await payment_service.get_payment(session=db, payment_id=payment_id)
    if not payment:
        raise NotFoundException(f"Payment with ID '{payment_id}' not found.")
    return PaymentRead.model_validate(payment)


@router.post(
    "/{payment_id}/paid",
    response_model=PaymentRead,
    summary="Mark payment as paid",
    description=(
        "Reactive payment confirmation callback. Immediately frees the "
        "reserved Telegram account back to the pool."
    ),
)
async def mark_payment_paid(
    payment_id: UUID,
    callback: PaymentCallback | None = None,
    db: AsyncSession = Depends(get_db),
) -> PaymentRead:
    """Mark payment as PAID and reactively unlock account."""
    payment = await payment_service.get_payment(session=db, payment_id=payment_id)
    if not payment:
        raise NotFoundException(f"Payment with ID '{payment_id}' not found.")

    cb = callback or PaymentCallback(status=PaymentStatus.PAID)
    cb.status = PaymentStatus.PAID

    updated = await payment_service.mark_payment_status(
        session=db, db_payment=payment, callback=cb
    )
    return PaymentRead.model_validate(updated)


@router.post(
    "/{payment_id}/cancel",
    response_model=PaymentRead,
    summary="Cancel payment",
    description="Cancels a pending payment and immediately frees its Telegram account.",
)
async def cancel_payment(
    payment_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> PaymentRead:
    """Cancel payment and release account."""
    payment = await payment_service.get_payment(session=db, payment_id=payment_id)
    if not payment:
        raise NotFoundException(f"Payment with ID '{payment_id}' not found.")

    cancelled = await payment_service.cancel_payment(session=db, db_payment=payment)
    return PaymentRead.model_validate(cancelled)


@router.post(
    "/{payment_id}/callback",
    response_model=PaymentRead,
    summary="Generic payment status callback",
    description=(
        "Updates payment status via webhook and reactively handles account locking."
    ),
)
async def handle_payment_callback(
    payment_id: UUID,
    callback: PaymentCallback,
    db: AsyncSession = Depends(get_db),
) -> PaymentRead:
    """Update payment status via external webhook."""
    payment = await payment_service.get_payment(session=db, payment_id=payment_id)
    if not payment:
        raise NotFoundException(f"Payment with ID '{payment_id}' not found.")

    updated = await payment_service.mark_payment_status(
        session=db, db_payment=payment, callback=callback
    )
    return PaymentRead.model_validate(updated)
