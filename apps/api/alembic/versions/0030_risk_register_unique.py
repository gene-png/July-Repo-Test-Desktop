"""risk_registers: unique (client_id, version) (Task S2-A / E-3)

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-09 00:00:00

Guards against a double-generate racing two Risk Register rows onto the same
version number for one client. Uses batch_alter_table so the constraint applies
under SQLite (the test DB) as a plain CREATE UNIQUE INDEX, which SQLite supports
natively; on Postgres it emits an ALTER TABLE ... ADD CONSTRAINT.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "uq_risk_registers_client_version"


def upgrade() -> None:
    with op.batch_alter_table("risk_registers") as batch:
        batch.create_unique_constraint(_CONSTRAINT, ["client_id", "version"])


def downgrade() -> None:
    with op.batch_alter_table("risk_registers") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="unique")
