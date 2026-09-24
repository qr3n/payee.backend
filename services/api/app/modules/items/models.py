from sqlmodel import Field, SQLModel

from app.shared.models import BaseUUIDModel


class ItemBase(SQLModel):
    """Shared properties for Item domain entity."""

    title: str = Field(
        min_length=1,
        max_length=255,
        index=True,
        description="Title or name of the item",
        schema_extra={"examples": ["Project Alpha"]},
    )
    description: str | None = Field(
        default=None,
        max_length=1000,
        description="Detailed description of the item",
        schema_extra={"examples": ["A high-priority backend development project."]},
    )
    is_active: bool = Field(
        default=True,
        index=True,
        description="Operational status flag",
    )


class Item(ItemBase, BaseUUIDModel, table=True):
    """Database table model representing an Item."""

    __tablename__ = "items"
