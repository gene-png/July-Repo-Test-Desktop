"""MFA (TOTP) secret columns + email_verification_tokens table (D-017).

Revision ID: 0038_mfa_email_verify
Revises: 0037_refresh_tokens

Adds users.mfa_secret / users.mfa_enrolled_at (TOTP enrollment) and a table of
hashed email-verification tokens. SQLite-safe: the users ALTER runs inside a
batch block.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0038_mfa_email_verify"
down_revision: str | Sequence[str] | None = "0037_refresh_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_col(name: str, *, primary_key: bool = False, nullable: bool = True) -> sa.Column:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True).with_variant(sa.String(36), "sqlite"),
        primary_key=primary_key,
        nullable=nullable,
    )


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("mfa_secret", sa.String(64), nullable=True))
        batch.add_column(sa.Column("mfa_enrolled_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "email_verification_tokens",
        _uuid_col("id", primary_key=True, nullable=False),
        _uuid_col("user_id", nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_email_verification_tokens_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_email_verification_tokens_token_hash",
        "email_verification_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_email_verification_tokens_user_id",
        "email_verification_tokens",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_email_verification_tokens_user_id", table_name="email_verification_tokens")
    op.drop_index("ix_email_verification_tokens_token_hash", table_name="email_verification_tokens")
    op.drop_table("email_verification_tokens")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("mfa_enrolled_at")
        batch.drop_column("mfa_secret")
