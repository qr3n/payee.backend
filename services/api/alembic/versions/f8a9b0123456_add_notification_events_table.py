"""add notification_events table

Revision ID: f8a9b0123456
Revises: e7f8a9b01234
Create Date: 2026-10-03 23:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8a9b0123456"
down_revision: str | Sequence[str] | None = "e7f8a9b01234"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("bot_username", sa.String(length=128), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("message_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="processed"
        ),
        sa.Column("payment_id", sa.Uuid(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id",
            "bot_username",
            "message_id",
            name="uq_notification_event_account_bot_msg",
        ),
    )
    op.create_index(
        op.f("ix_notification_events_account_id"),
        "notification_events",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notification_events_bot_username"),
        "notification_events",
        ["bot_username"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notification_events_message_id"),
        "notification_events",
        ["message_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notification_events_payment_id"),
        "notification_events",
        ["payment_id"],
        unique=False,
    )
    op.add_column(
        "payments",
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    )
    op.create_index(
        op.f("ix_payments_idempotency_key"),
        "payments",
        ["idempotency_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_payments_idempotency_key"), table_name="payments")
    op.drop_column("payments", "idempotency_key")
    op.drop_index(
        op.f("ix_notification_events_payment_id"), table_name="notification_events"
    )
    op.drop_index(
        op.f("ix_notification_events_message_id"), table_name="notification_events"
    )
    op.drop_index(
        op.f("ix_notification_events_bot_username"), table_name="notification_events"
    )
    op.drop_index(
        op.f("ix_notification_events_account_id"), table_name="notification_events"
    )
    op.drop_table("notification_events")
