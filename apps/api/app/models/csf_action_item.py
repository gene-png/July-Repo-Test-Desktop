"""NIST CSF 2.0 action-plan items (Task S3-A / H-8).

An admin-managed remediation task attached to a CSF assessment, typically
created from a gap row. Client-invisible: like the other CSF admin surfaces
(G-1), clients never see or manage these. Rendered into the playbook XLSX
"Action Plan" sheet and the finalize DOCX/PDF.

Per Master Spec §11.1 `client_id` is denormalized on every business row.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String, Text
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._common import TimestampMixin, UUIDPKMixin


class CsfActionStatus(enum.StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class CsfActionItem(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "csf_action_items"

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("csf_assessments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # NIST subcategory code the item remediates (e.g. "GV.OC-01"). Validated at
    # the API edge against app.csf.catalog.all_codes().
    subcategory_code: Mapped[str] = mapped_column(String(16), nullable=False)

    owner: Mapped[str | None] = mapped_column(String(255))
    due_date: Mapped[date | None] = mapped_column(Date)
    milestone: Mapped[str | None] = mapped_column(Text)
    status: Mapped[CsfActionStatus] = mapped_column(
        SAEnum(
            CsfActionStatus,
            name="csf_action_status",
            native_enum=False,
            length=16,
        ),
        default=CsfActionStatus.OPEN,
        nullable=False,
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
