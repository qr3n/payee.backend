from collections.abc import Sequence
from uuid import UUID

from sqlmodel import and_, col, func, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.modules.items.models import Item
from app.modules.items.schemas import ItemCreate, ItemUpdate
from app.shared.pagination import (
    CursorParams,
    PageParams,
    decode_cursor,
    encode_cursor,
)


async def create_item(session: AsyncSession, item_in: ItemCreate) -> Item:
    """Create a new item in the database within the active transaction."""
    item = Item.model_validate(item_in)
    session.add(item)
    await session.flush()
    await session.refresh(item)
    return item


async def get_item(session: AsyncSession, item_id: UUID) -> Item | None:
    """Retrieve an item by its UUID."""
    statement = select(Item).where(Item.id == item_id)
    result = await session.exec(statement)
    return result.first()


async def list_items_paginated(
    session: AsyncSession, params: PageParams
) -> tuple[Sequence[Item], int]:
    """Retrieve items and total count according to pagination parameters."""
    count_statement = select(func.count()).select_from(Item)
    total_result = await session.exec(count_statement)
    total = total_result.one() or 0

    statement = (
        select(Item)
        .order_by(col(Item.created_at).desc())
        .offset(params.offset)
        .limit(params.size)
    )
    result = await session.exec(statement)
    return result.all(), total


async def list_items_cursor(
    session: AsyncSession, params: CursorParams
) -> tuple[Sequence[Item], str | None, bool]:
    """
    Retrieve items using keyset / cursor-based pagination.
    Orders monotonically by (created_at DESC, id DESC).
    """
    statement = select(Item)

    if params.cursor:
        cursor_dt, cursor_id = decode_cursor(params.cursor)
        statement = statement.where(
            or_(
                col(Item.created_at) < cursor_dt,
                and_(
                    col(Item.created_at) == cursor_dt,
                    col(Item.id) < cursor_id,
                ),
            )
        )

    statement = statement.order_by(
        col(Item.created_at).desc(), col(Item.id).desc()
    ).limit(params.size + 1)
    result = await session.exec(statement)
    items = list(result.all())

    has_more = len(items) > params.size
    page_items = items[: params.size]
    next_cursor = (
        encode_cursor(page_items[-1].created_at, page_items[-1].id)
        if has_more and page_items
        else None
    )

    return page_items, next_cursor, has_more


async def list_items(
    session: AsyncSession, skip: int = 0, limit: int = 100
) -> Sequence[Item]:
    """Retrieve a list of items with offset/limit pagination."""
    statement = (
        select(Item).offset(skip).limit(limit).order_by(col(Item.created_at).desc())
    )
    result = await session.exec(statement)
    return result.all()


async def update_item(
    session: AsyncSession, db_item: Item, item_in: ItemUpdate
) -> Item:
    """Partially update an existing item within the active transaction."""
    update_data = item_in.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_item, key, value)

    session.add(db_item)
    await session.flush()
    await session.refresh(db_item)
    return db_item


async def delete_item(session: AsyncSession, db_item: Item) -> None:
    """Delete an item from the database within the active transaction."""
    await session.delete(db_item)
    await session.flush()
