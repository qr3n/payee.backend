"""make_payment_account_id_nullable_set_null

Revision ID: d6e7f8012345
Revises: c5d6e7f80123
Create Date: 2026-10-03 12:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6e7f8012345"
down_revision: str | Sequence[str] | None = "c5d6e7f80123"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Make account_id nullable so payment history survives account deletion
    op.alter_column(
        "payments",
        "account_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    # Recreate foreign key constraint with ON DELETE SET NULL
    op.drop_constraint(
        "payments_account_id_fkey",
        "payments",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "payments_account_id_fkey",
        "payments",
        "telegram_accounts",
        ["account_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "payments_account_id_fkey",
        "payments",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "payments_account_id_fkey",
        "payments",
        "telegram_accounts",
        ["account_id"],
        ["id"],
    )
    op.alter_column(
        "payments",
        "account_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
