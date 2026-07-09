"""Server-side refresh-token records (D-017 auth package).

Access tokens stay stateless (15-minute TTL is the mitigation); refresh
tokens are tracked here so they can be rotated on every use and revoked
server-side. A ``family_id`` groups the chain that starts at one login:
rotation moves the family forward one row at a time, and any attempt to
reuse an already-rotated token revokes the entire family (theft
detection, RFC 6819 style). Idle-timeout and forced-re-auth enforcement
read ``last_used_at`` and the family's first ``issued_at``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._common import TimestampMixin, UUIDPKMixin


class RefreshToken(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "refresh_tokens"

    # The JWT's jti claim; the lookup key on refresh.
    jti: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, index=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # One login session = one family; rotation preserves it.
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Stamped when this token is successfully exchanged (rotation).
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # jti of the token this one was rotated into (audit trail).
    replaced_by_jti: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    __table_args__ = (Index("ix_refresh_tokens_family_active", "family_id", "revoked_at"),)
