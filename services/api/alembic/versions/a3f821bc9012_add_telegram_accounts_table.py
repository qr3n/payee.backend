"""add_telegram_accounts_table

Revision ID: a3f821bc9012
Revises: e05aa5661b9c
Create Date: 2026-10-02 22:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f821bc9012"
down_revision: str | Sequence[str] | None = "e05aa5661b9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_accounts",
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
            "title", sqlmodel.sql.sqltypes.AutoString(length=128), nullable=False
        ),
        sa.Column("phone", sqlmodel.sql.sqltypes.AutoString(length=32), nullable=True),
        sa.Column(
            "proxy_url", sqlmodel.sql.sqltypes.AutoString(length=512), nullable=True
        ),
        sa.Column(
            "device_model",
            sqlmodel.sql.sqltypes.AutoString(length=128),
            nullable=False,
        ),
        sa.Column(
            "system_version",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column(
            "app_version",
            sqlmodel.sql.sqltypes.AutoString(length=64),
            nullable=False,
        ),
        sa.Column(
            "system_lang_code",
            sqlmodel.sql.sqltypes.AutoString(length=16),
            nullable=False,
        ),
        sa.Column(
            "lang_code",
            sqlmodel.sql.sqltypes.AutoString(length=16),
            nullable=False,
        ),
        sa.Column("api_id", sa.Integer(), nullable=True),
        sa.Column(
            "api_hash", sqlmodel.sql.sqltypes.AutoString(length=64), nullable=True
        ),
        sa.Column("session_string", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="active",
            nullable=False,
        ),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "first_name",
            sqlmodel.sql.sqltypes.AutoString(length=128),
            nullable=True,
        ),
        sa.Column(
            "last_name",
            sqlmodel.sql.sqltypes.AutoString(length=128),
            nullable=True,
        ),
        sa.Column(
            "username",
            sqlmodel.sql.sqltypes.AutoString(length=128),
            nullable=True,
        ),
        sa.Column("is_premium", sa.Boolean(), nullable=True),
        sa.Column(
            "flood_wait_until",
            sqlmodel.sql.sqltypes.UTCDateTime(),
            nullable=True,
        ),
        sa.Column(
            "last_checked_at",
            sqlmodel.sql.sqltypes.UTCDateTime(),
            nullable=True,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_telegram_accounts_created_at"),
        "telegram_accounts",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_telegram_accounts_phone"),
        "telegram_accounts",
        ["phone"],
        unique=False,
    )
    op.create_index(
        op.f("ix_telegram_accounts_status"),
        "telegram_accounts",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_telegram_accounts_telegram_user_id"),
        "telegram_accounts",
        ["telegram_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_telegram_accounts_username"),
        "telegram_accounts",
        ["username"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_telegram_accounts_username"), table_name="telegram_accounts")
    op.drop_index(
        op.f("ix_telegram_accounts_telegram_user_id"),
        table_name="telegram_accounts",
    )
    op.drop_index(op.f("ix_telegram_accounts_status"), table_name="telegram_accounts")
    op.drop_index(op.f("ix_telegram_accounts_phone"), table_name="telegram_accounts")
    op.drop_index(
        op.f("ix_telegram_accounts_created_at"), table_name="telegram_accounts"
    )
    op.drop_table("telegram_accounts")
