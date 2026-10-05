"""add_uq_payments_client_idempotency

Revision ID: a1b2c3d4e5f6
Revises: f8a9b0123456
Create Date: 2026-10-05 19:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f8a9b0123456"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_payments_client_idempotency",
        "payments",
        ["client_user_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_payments_client_idempotency",
        "payments",
        type_="unique",
    )
