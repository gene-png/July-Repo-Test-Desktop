"""risk governance: entry lock/soft-delete + register approval (Task S3-B / F-3)

Revision ID: 0036_risk_governance
Revises: 0034
Create Date: 2026-07-09 00:00:00

Adds entry-level governance columns (locked, deleted_at) to risk_entries and
approval columns (approved_at, approved_by) to risk_registers. Uses
batch_alter_table so the ALTER applies under SQLite (the test DB) as well as
Postgres.

Chaining note: authored to sit after 0033_llm_calls_client_id. A concurrent
agent (S3-A) may introduce 0034/0035; this revision is numbered 0036 to leave
room. down_revision points at the real head observed at authoring time; the lead
re-chains if S3-A's migrations landed between 0033 and this one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036_risk_governance"
# Re-chained by S3-A: 0034 (csf_action_items) landed between 0033 and this
# revision, so this now sits after 0034 to keep a single linear head.
down_revision: str | Sequence[str] | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("risk_entries") as batch:
        batch.add_column(
            sa.Column(
                "locked",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table("risk_registers") as batch:
        batch.add_column(sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
        # FK to users declared on the ORM model; kept as a plain UUID column in
        # the migration to match 0023's artifact columns (batch-mode named FKs
        # are brittle under SQLite, the test DB).
        batch.add_column(
            sa.Column(
                "approved_by",
                postgresql.UUID(as_uuid=True).with_variant(sa.String(36), "sqlite"),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("risk_registers") as batch:
        batch.drop_column("approved_by")
        batch.drop_column("approved_at")

    with op.batch_alter_table("risk_entries") as batch:
        batch.drop_column("deleted_at")
        batch.drop_column("locked")
