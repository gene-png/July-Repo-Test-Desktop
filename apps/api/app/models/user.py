"""User model.

Two roles: admin (Kentro consultant, cross-client) and client (external
company user, tied to one client by email domain). The earlier read-only
reviewer role was collapsed into admin (Work Order A3): multiple admins
all see and do the work, no separate reviewer gate. MFA enrollment column
is present in v1 but the flag default-false until SHIELD_AUTH_REQUIRE_MFA
flips on (spec §2 locked decisions).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._common import TimestampMixin, UUIDPKMixin


class UserRole(enum.StrEnum):
    ADMIN = "admin"
    CLIENT = "client"


class User(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, name="user_role", native_enum=False, length=16),
        nullable=False,
    )

    display_name: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)

    client_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("client.id", ondelete="RESTRICT")
    )

    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    # Set when an admin deactivates the account. Starts the retention clock:
    # deactivated accounts with no login for SHIELD_USER_PURGE_IDLE_DAYS are
    # purged (app.maintenance.retention.purge_stale_users).
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when the retention job has crypto-shredded this account's PII. The row
    # is kept (not row-deleted) because the append-only audit_entries trail
    # references actor_user_id; purging scrubs PII instead of deleting the row.
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mfa_enrolled: Mapped[bool] = mapped_column(default=False, nullable=False)
    # Base32 TOTP seed (RFC 6238). Set at /auth/mfa/enroll before the user has
    # proven possession; mfa_enrolled only flips true once /auth/mfa/activate
    # verifies a live code. Cleared on /auth/mfa/disable. Never surfaced by any
    # response schema.
    mfa_secret: Mapped[str | None] = mapped_column(String(64))
    mfa_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Lockout bookkeeping (Master Spec §4.5: 10 failed attempts in 15 min).
    failed_login_count: Mapped[int] = mapped_column(default=0, nullable=False)
    last_failed_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_until_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
