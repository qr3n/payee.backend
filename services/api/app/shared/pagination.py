import base64
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, Field


def encode_cursor(dt: datetime, item_id: UUID) -> str:
    """Encode created_at timestamp and item UUID into an opaque URL-safe cursor."""
    raw = f"{dt.timestamp()}:{item_id}"
    return base64.urlsafe_b64encode(raw.encode("ascii")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Decode an opaque URL-safe cursor into a (datetime, UUID) tuple."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii")
        ts_str, id_str = raw.split(":", 1)
        dt = datetime.fromtimestamp(float(ts_str), tz=UTC)
        item_id = UUID(id_str)
        return dt, item_id
    except Exception as exc:
        raise ValueError(f"Invalid cursor format: {cursor}") from exc


class PageParams(BaseModel):
    """Reusable query parameters for offset-paginated endpoints."""

    page: int = Field(default=1, ge=1, description="Page number, 1-indexed")
    size: int = Field(default=20, ge=1, le=100, description="Items per page limit")

    @property
    def offset(self) -> int:
        """Calculate SQL offset based on page number and size."""
        return (self.page - 1) * self.size


class PaginatedResponse[T](BaseModel):
    """Generic envelope for offset-paginated collections."""

    items: Sequence[T] = Field(description="List of records for the current page")
    total: int = Field(description="Total count of records matching criteria")
    page: int = Field(description="Current page number")
    size: int = Field(description="Requested page size limit")
    pages: int = Field(description="Total number of available pages")

    @classmethod
    def create(
        cls,
        items: Sequence[T],
        total: int,
        params: PageParams,
    ) -> "PaginatedResponse[T]":
        """Factory method to compute total pages and build PaginatedResponse."""
        pages = math.ceil(total / params.size) if total > 0 else 0
        return cls(
            items=items,
            total=total,
            page=params.page,
            size=params.size,
            pages=pages,
        )


class CursorParams(BaseModel):
    """Reusable query parameters for cursor-based (keyset) pagination."""

    cursor: str | None = Field(
        default=None,
        description="Opaque base64 cursor token pointing to the last item",
    )
    size: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum items per page limit",
    )


class CursorPaginatedResponse[T](BaseModel):
    """Generic envelope for cursor-paginated collections."""

    items: Sequence[T] = Field(description="List of records for the current page")
    next_cursor: str | None = Field(
        default=None,
        description="Cursor token for fetching subsequent page, or null if at end",
    )
    has_more: bool = Field(
        default=False,
        description="Indicates whether more items are available",
    )
    size: int = Field(description="Requested page size limit")
