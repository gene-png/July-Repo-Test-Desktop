"""Email-verification tokens (D-017 auth package).

On registration we mint a random urlsafe token, email the raw value to the
user, and store ONLY its sha256 hex here (never the raw token, same posture as
a password hash). POST /auth/verify-email hashes the presented token, looks up
the unused, unexpired row, and stamps ``used_at`` + ``users.email_verified_at``.
Resending invalidates outstanding rows for the user.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._common import TimestampMixin, UUIDPKMixin


class EmailVerificationToken(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "email_verification_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # sha256 hex of the raw token; the raw value is only ever in the email.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
