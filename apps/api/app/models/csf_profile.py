"""CSF full-Playbook tiered Working Profile scores (Work Order D4).

One row per (assessment, FIPS tier, subcategory): the five dimension scores plus
in-scope / rationale / narrative / evidence / per-subcategory target. Added
alongside the simplified `CsfAnswer` (which still backs the client
self-assessment); the admin's full-Playbook scoring lives here. The deterministic
math (total/level/evidence-cap/weighted-floor roll-up) is `app/csf/playbook.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._common import TimestampMixin, UUIDPKMixin


class CsfDimensionScore(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "csf_dimension_scores"
    __table_args__ = (
        UniqueConstraint(
            "assessment_id",
            "tier",
            "subcategory_code",
            name="uq_csf_dimension_scores_assessment_tier_subcat",
        ),
    )

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("csf_assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    tier: Mapped[str] = mapped_column(String(16), nullable=False)  # high/moderate/low
    subcategory_code: Mapped[str] = mapped_column(String(16), nullable=False)

    # Five dimensions, each 0..2.
    governance: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    policy: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    implementation: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    monitoring: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    improvement: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)

    # Set the moment a row is actually scored (human PATCH or AI apply); stays
    # null for seeded-but-untouched rows so the playbook export gate (B-3) can
    # distinguish "0 because unscored" from "0 because scored zero".
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    in_scope: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    what_we_found: Mapped[str | None] = mapped_column(Text)
    evidence_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    has_evidence: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    target_level: Mapped[int | None] = mapped_column(SmallInteger)

    # Work Order C2: locked rows are untouched by a Run-AI rerun.
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
