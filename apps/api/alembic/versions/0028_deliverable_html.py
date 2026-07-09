"""deliverables.html_artifact_id - HTML dashboard deliverable

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-01 00:00:00

The Tech Debt deliverable now includes a self-contained HTML dashboard
(app/tech_debt/exporters.py::render_html_dashboard) rendered alongside the
PDF/XLSX/DOCX. It is the primary, auto-built, in-browser view of the
consolidation analysis.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("deliverables") as batch:
        batch.add_column(
            sa.Column(
                "html_artifact_id",
                postgresql.UUID(as_uuid=True).with_variant(sa.String(36), "sqlite"),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("deliverables") as batch:
        batch.drop_column("html_artifact_id")
