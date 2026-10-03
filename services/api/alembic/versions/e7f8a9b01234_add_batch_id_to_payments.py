"""add batch_id to payments

Revision ID: e7f8a9b01234
Revises: d6e7f8012345
Create Date: 2026-10-03 16:17:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7f8a9b01234"
down_revision: str | Sequence[str] | None = "d6e7f8012345"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("batch_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_payments_batch_id"), "payments", ["batch_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_payments_batch_id"), table_name="payments")
    op.drop_column("payments", "batch_id")
