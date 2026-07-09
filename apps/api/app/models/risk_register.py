"""Risk Register models (Work Order E).

A point-in-time synthesis deliverable, not a service. Each generate creates a
new versioned RiskRegister with one RiskEntry per finding. SHIELD keeps only
version history; the client owns governance after handoff (so decision-maker,
approval date, expiry, next review, status are NOT modeled here — they print as
blank columns in the export).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._common import TimestampMixin, UUIDPKMixin

_JSON_LIST = JSON().with_variant(JSONB, "postgresql")


class RiskRegister(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "risk_registers"
    __table_args__ = (
        # E-3: one register per (client, version); guards against a double
        # generate racing two rows onto the same version number.
        UniqueConstraint("client_id", "version", name="uq_risk_registers_client_version"),
    )

    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    generated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Newest current; older versions kept (superseded).
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("risk_registers.id", ondelete="SET NULL")
    )

    # F-3: admin approval freezes the version — entries reject further edits and
    # export is only permitted once the latest register is approved. A later
    # generate creates the next (draft) version, preserving the supersession
    # chain.
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    # Exported artifacts (XLSX + PDF + Word), set on export.
    xlsx_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    pdf_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    docx_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )


class RiskEntry(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "risk_entries"

    register_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("risk_registers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    axis: Mapped[str | None] = mapped_column(String(16))  # detection/prevention/response

    # Where this entry came from (traceability — required, no orphan risks).
    source: Mapped[str | None] = mapped_column(
        String(32)
    )  # coverage_finding | questionnaire_response
    source_id: Mapped[str | None] = mapped_column(String(64))

    # Links the AI may only draw from the client's assessments (validated).
    linked_techniques: Mapped[list | None] = mapped_column(_JSON_LIST)
    linked_controls: Mapped[list | None] = mapped_column(_JSON_LIST)

    likelihood: Mapped[str | None] = mapped_column(String(16))
    impact: Mapped[str | None] = mapped_column(String(16))
    tier: Mapped[str | None] = mapped_column(String(16))  # code-derived, never AI-set

    compensating_controls: Mapped[str | None] = mapped_column(Text)
    residual_risk: Mapped[str | None] = mapped_column(Text)
    recommended_action: Mapped[str | None] = mapped_column(String(16))
    rationale: Mapped[str | None] = mapped_column(Text)

    # Provenance (first-class + visible).
    origin: Mapped[str] = mapped_column(String(24), default="ai_generated", nullable=False)
    trust: Mapped[str | None] = mapped_column(String(32))

    # F-3: a locked entry is preserved verbatim by regenerate (copied into the
    # new version instead of being redrafted from synthesis).
    locked: Mapped[bool] = mapped_column(default=False, nullable=False)
    # F-3: soft delete — excluded from serialization and exports, never redrafted.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
