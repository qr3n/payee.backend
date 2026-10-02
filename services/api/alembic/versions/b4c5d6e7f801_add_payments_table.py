"""add_payments_table

Revision ID: b4c5d6e7f801
Revises: a3f821bc9012
Create Date: 2026-10-02 22:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4c5d6e7f801"
down_revision: str | Sequence[str] | None = "a3f821bc9012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sqlmodel.sql.sqltypes.UTCDateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sqlmodel.sql.sqltypes.UTCDateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "client_user_id",
            sqlmodel.sql.sqltypes.AutoString(length=128),
            nullable=False,
        ),
        sa.Column(
            "scenario_id",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column(
            "currency",
            sqlmodel.sql.sqltypes.AutoString(length=16),
            nullable=False,
        ),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("payment_link", sa.Text(), nullable=True),
        sa.Column(
            "expires_at",
            sqlmodel.sql.sqltypes.UTCDateTime(),
            nullable=False,
        ),
        sa.Column(
            "paid_at",
            sqlmodel.sql.sqltypes.UTCDateTime(),
            nullable=True,
        ),
        sa.Column(
            "cancelled_at",
            sqlmodel.sql.sqltypes.UTCDateTime(),
            nullable=True,
        ),
        sa.Column(
            "meta",
            sa.JSON(),
            server_default="{}",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["telegram_accounts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_payments_account_id"), "payments", ["account_id"], unique=False
    )
    op.create_index(
        op.f("ix_payments_client_user_id"),
        "payments",
        ["client_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_payments_created_at"), "payments", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_payments_expires_at"), "payments", ["expires_at"], unique=False
    )
    op.create_index(
        op.f("ix_payments_scenario_id"), "payments", ["scenario_id"], unique=False
    )
    op.create_index(op.f("ix_payments_status"), "payments", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_payments_status"), table_name="payments")
    op.drop_index(op.f("ix_payments_scenario_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_expires_at"), table_name="payments")
    op.drop_index(op.f("ix_payments_created_at"), table_name="payments")
    op.drop_index(op.f("ix_payments_client_user_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_account_id"), table_name="payments")
    op.drop_table("payments")
