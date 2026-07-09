"""Deliverable - the finalized PDF / XLSX pair for a service.

  deliverables    id, service_id, title, summary, version,
                  pdf_artifact_id, xlsx_artifact_id, finalized_at,
                  finalized_by, superseded_by

Deliverables are admin-only (Work Order A1): there is no client release
path and no `released_to_client_at` column. `version`/`superseded_by`
keep internal history; an admin downloads and shares outside the app.

Filenames follow Master Spec §15.5: `{Company}_{Service}{MMDDYY}.{ext}`.
The slugifier lives in app.deliverables.filename (Phase 3 stage 8).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._common import TimestampMixin, UUIDPKMixin


class Deliverable(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "deliverables"

    service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    pdf_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    xlsx_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    # Word (.docx) deliverable (Work Order C4), alongside PDF + XLSX.
    docx_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    # Self-contained HTML dashboard - the primary, auto-built in-browser view.
    html_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )

    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("deliverables.id", ondelete="SET NULL")
    )
