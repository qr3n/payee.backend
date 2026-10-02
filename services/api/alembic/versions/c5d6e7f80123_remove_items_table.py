"""remove_items_table

Revision ID: c5d6e7f80123
Revises: b4c5d6e7f801
Create Date: 2026-10-02 22:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5d6e7f80123"
down_revision: str | Sequence[str] | None = "b4c5d6e7f801"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop items table if it exists
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "items" in inspector.get_table_names():
        op.drop_table("items")


def downgrade() -> None:
    pass
