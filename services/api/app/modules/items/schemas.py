from datetime import datetime
from uuid import UUID

from pydantic import Field
from sqlmodel import SQLModel

from app.modules.items.models import ItemBase


class ItemCreate(ItemBase):
    """Schema for creating a new Item."""

    pass


class ItemUpdate(SQLModel):
    """Schema for updating an existing Item (partial updates supported)."""

    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="New title for the item",
        examples=["Updated Project Title"],
    )
    description: str | None = Field(
        default=None,
        max_length=1000,
        description="New description",
        examples=["Updated project details."],
    )
    is_active: bool | None = Field(
        default=None,
        description="New operational status",
        examples=[False],
    )


class ItemRead(ItemBase):
    """Schema for returning Item details in API responses."""

    id: UUID = Field(
        description="Unique identifier (UUIDv7, monotonic time-ordered)",
        examples=["01923d8c-7f51-789a-b456-c78901234567"],
    )
    created_at: datetime = Field(
        description="UTC creation timestamp",
    )
    updated_at: datetime = Field(
        description="UTC last update timestamp",
    )
