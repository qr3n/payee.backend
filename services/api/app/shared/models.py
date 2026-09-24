from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Field, SQLModel
from uuid6 import uuid7


class BaseUUIDModel(SQLModel):
    """
    Base SQLModel class providing UUIDv7 primary key, UTC timestamps, and indexing.
    Inherited by domain entities across all modules.
    """

    id: UUID = Field(
        default_factory=uuid7,
        primary_key=True,
        nullable=False,
        description="Unique identifier (UUIDv7, monotonic time-ordered)",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column_kwargs={"server_default": func.now()},
        index=True,
        nullable=False,
        description="UTC creation timestamp",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column_kwargs={
            "server_default": func.now(),
            "onupdate": lambda: datetime.now(UTC),
        },
        nullable=False,
        description="UTC last update timestamp",
    )
