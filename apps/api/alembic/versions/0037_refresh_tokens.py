"""Refresh-token rotation and revocation records (D-017 auth package).

Revision ID: 0037_refresh_tokens
Revises: 0036_risk_governance
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037_refresh_tokens"
down_revision: str | Sequence[str] | None = "0036_risk_governance"
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
    op.create_table(
        "refresh_tokens",
        _uuid_col("id", primary_key=True, nullable=False),
        _uuid_col("jti", nullable=False),
        _uuid_col("user_id", nullable=False),
        _uuid_col("family_id", nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        _uuid_col("replaced_by_jti"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_refresh_tokens_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_refresh_tokens_jti", "refresh_tokens", ["jti"], unique=True)
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])
    op.create_index(
        "ix_refresh_tokens_family_active", "refresh_tokens", ["family_id", "revoked_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_family_active", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_family_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_jti", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
