"""csf_dimension_scores: nullable scored_at stamp (Task S1-B / B-3)

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-09 00:00:00

Distinguishes a seeded-but-untouched dimension-score row (scored_at null) from a
row deliberately scored zero. The playbook export gate requires every in-scope
row to be scored before rendering; exporters render "Unscored" for null rows.
Uses batch_alter_table so the additive column applies under SQLite (test DB).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | Sequence[str] | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("csf_dimension_scores") as batch:
        batch.add_column(sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("csf_dimension_scores") as batch:
        batch.drop_column("scored_at")
