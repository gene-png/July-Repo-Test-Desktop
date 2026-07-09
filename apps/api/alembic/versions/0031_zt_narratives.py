"""zt_assessments.narratives - persisted AI narrative output (Task S2-C / E-4)

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-09 00:00:01

Additive, batch-safe. Stores the zt_score run's pillar narratives + executive /
roadmap summaries as JSON so the assessment GET can echo them back.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("zt_assessments") as batch:
        batch.add_column(sa.Column("narratives", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("zt_assessments") as batch:
        batch.drop_column("narratives")
