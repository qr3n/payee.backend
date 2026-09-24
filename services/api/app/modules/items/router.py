from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.deps import get_db
from app.core.exceptions import NotFoundException
from app.core.rate_limit import RateLimiter
from app.modules.items import service as item_service
from app.modules.items.schemas import ItemCreate, ItemRead, ItemUpdate
from app.modules.items.tasks import process_item_analysis
from app.shared.pagination import (
    CursorPaginatedResponse,
    CursorParams,
    PageParams,
    PaginatedResponse,
)

router = APIRouter(prefix="/items", tags=["Items"])


@router.post(
    "/",
    response_model=ItemRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new item",
    description="Creates a new item record in PostgreSQL using SQLModel.",
)
async def create_item(
    item_in: ItemCreate,
    db: AsyncSession = Depends(get_db),
) -> ItemRead:
    """Create a new item."""
    item = await item_service.create_item(session=db, item_in=item_in)
    return ItemRead.model_validate(item)


@router.get(
    "/",
    response_model=PaginatedResponse[ItemRead],
    summary="List items",
    description="Retrieve a paginated list of items ordered by creation time.",
)
async def list_items(
    params: PageParams = Depends(),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[ItemRead]:
    """Retrieve paginated items."""
    items, total = await item_service.list_items_paginated(session=db, params=params)
    item_reads = [ItemRead.model_validate(item) for item in items]
    return PaginatedResponse.create(items=item_reads, total=total, params=params)


@router.get(
    "/cursor",
    response_model=CursorPaginatedResponse[ItemRead],
    summary="List items with cursor",
    description="Retrieve items using high-performance keyset/cursor pagination.",
)
async def list_items_by_cursor(
    params: CursorParams = Depends(),
    db: AsyncSession = Depends(get_db),
) -> CursorPaginatedResponse[ItemRead]:
    """Retrieve items by cursor."""
    items, next_cursor, has_more = await item_service.list_items_cursor(
        session=db, params=params
    )
    item_reads = [ItemRead.model_validate(item) for item in items]
    return CursorPaginatedResponse[ItemRead](
        items=item_reads,
        next_cursor=next_cursor,
        has_more=has_more,
        size=params.size,
    )


@router.get(
    "/{item_id}",
    response_model=ItemRead,
    summary="Get item by ID",
    description="Retrieve details of a specific item by its UUID.",
)
async def get_item(
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ItemRead:
    """Retrieve a single item."""
    item = await item_service.get_item(session=db, item_id=item_id)
    if not item:
        raise NotFoundException("Item not found", code="ITEM_NOT_FOUND")
    return ItemRead.model_validate(item)


@router.patch(
    "/{item_id}",
    response_model=ItemRead,
    summary="Update item",
    description="Partially update an existing item.",
)
async def update_item(
    item_id: UUID,
    item_in: ItemUpdate,
    db: AsyncSession = Depends(get_db),
) -> ItemRead:
    """Update an item."""
    item = await item_service.get_item(session=db, item_id=item_id)
    if not item:
        raise NotFoundException("Item not found", code="ITEM_NOT_FOUND")
    updated_item = await item_service.update_item(
        session=db, db_item=item, item_in=item_in
    )
    return ItemRead.model_validate(updated_item)


@router.delete(
    "/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete item",
    description="Permanently delete an item by its UUID.",
)
async def delete_item(
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete an item."""
    item = await item_service.get_item(session=db, item_id=item_id)
    if not item:
        raise NotFoundException("Item not found", code="ITEM_NOT_FOUND")
    await item_service.delete_item(session=db, db_item=item)


@router.post(
    "/{item_id}/analyze",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue background analysis for item",
    description=(
        "Dispatches a background worker task using Taskiq to process item analysis."
    ),
    dependencies=[Depends(RateLimiter(max_requests=10, window_seconds=60))],
)
async def analyze_item(
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Enqueue background item analysis task."""
    item = await item_service.get_item(session=db, item_id=item_id)
    if not item:
        raise NotFoundException("Item not found", code="ITEM_NOT_FOUND")

    task = await process_item_analysis.kiq(item_id=item_id)
    return {
        "status": "queued",
        "task_id": str(task.task_id),
        "item_id": str(item_id),
    }
