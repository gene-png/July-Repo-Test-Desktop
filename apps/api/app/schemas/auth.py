"""Auth-route request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.user import UserRole


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(min_length=1, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    """Optional logout body (D-017): presenting the refresh token lets the
    server revoke exactly that session family; omitting it revokes all of
    the user's active refresh tokens."""

    refresh_token: str | None = None


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth 2.0 token_type field, not a credential
    access_expires_at: datetime
    refresh_expires_at: datetime


class LoginResponse(BaseModel):
    """Two documented shapes behind one model (keeps OpenAPI honest):

    * Completed login - the token fields are populated exactly as the old
      TokenPairResponse (unchanged for MFA-off callers), ``mfa_required`` false.
    * TOTP challenge - ``mfa_required`` is true and ``challenge_token`` carries
      a 5-minute JWT; the token fields stay null until the caller posts the code
      to /auth/mfa/verify.

    ``mfa_setup_required`` is an advisory nudge set when SHIELD_AUTH_REQUIRE_MFA
    is on but the user has no factor yet (does not block login server-side).
    """

    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"  # noqa: S105 - OAuth 2.0 token_type field, not a credential
    access_expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None

    mfa_required: bool = False
    challenge_token: str | None = None
    mfa_setup_required: bool = False


class MfaEnrollResponse(BaseModel):
    """Returned by /auth/mfa/enroll. The secret is shown ONCE for manual entry;
    otpauth_uri feeds an authenticator-app QR. MFA is not active until the user
    proves possession via /auth/mfa/activate."""

    secret: str
    otpauth_uri: str


class MfaCodeRequest(BaseModel):
    """A 6-digit TOTP code (activate / disable / verify)."""

    code: str = Field(min_length=6, max_length=10)


class MfaVerifyRequest(BaseModel):
    challenge_token: str
    code: str = Field(min_length=6, max_length=10)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class UserResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    role: UserRole
    display_name: str | None
    title: str | None
    phone: str | None
    timezone: str
    is_active: bool
    mfa_enrolled: bool
    email_verified_at: datetime | None
    last_login_at: datetime | None
    created_at: datetime
    # Nullable for platform admin/reviewer; set for client-role users.
    client_id: uuid.UUID | None = None


class RegisterResponse(BaseModel):
    user: UserResponse
    tokens: TokenPairResponse
    is_primary_poc: bool
