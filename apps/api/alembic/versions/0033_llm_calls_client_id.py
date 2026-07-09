"""llm_calls.client_id - per-tenant AI usage accounting (Task S2-C / H-5)

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-09 00:00:03

Additive, batch-safe. Denormalized (no FK) tenant id so a usage row survives a
client purge; indexed for the per-client ai-usage aggregation.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True).with_variant(sa.String(36), "sqlite")


def upgrade() -> None:
    with op.batch_alter_table("llm_calls") as batch:
        batch.add_column(sa.Column("client_id", _UUID, nullable=True))
    op.create_index("ix_llm_calls_client_id", "llm_calls", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_llm_calls_client_id", table_name="llm_calls")
    with op.batch_alter_table("llm_calls") as batch:
        batch.drop_column("client_id")
