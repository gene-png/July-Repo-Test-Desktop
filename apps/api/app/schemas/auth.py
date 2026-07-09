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
