"""Auth routes: register, login, me, refresh, logout.

Master Spec §2 + §4.5:
  - Email + password only for v1 (MFA + email verification deferred behind
    feature flags).
  - 15-minute access JWT, refresh JWT, 30-minute idle, daily forced re-auth.
  - Account lockout: 10 failed attempts in 15 minutes
    (`SHIELD_ACCOUNT_LOCKOUT_*`).

DECISIONS.md D-004 (Q2): self-registration allowed. Self-registration ALWAYS
creates a regular `client` user (never an admin) - the privilege boundary is
that only an existing admin can mint another admin (POST /admin/users). The
standing platform admin is an explicit env-seeded service account, provisioned
at startup by `app.bootstrap.ensure_bootstrap_admin`, not via this route.

A registrant still becomes their organization's Primary POC when their signup
creates a brand-new org (first person on a work domain, or a personal mailbox).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.config import get_settings
from app.db.session import get_db
from app.dependencies import current_user
from app.middleware.ratelimit import rate_limit_ip
from app.models._common import utcnow
from app.models.client import Client
from app.models.client_domain import ClientDomain
from app.models.user import User, UserRole
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenPairResponse,
    UserResponse,
)
from app.security.email_domains import domain_of, is_generic_provider
from app.security.jwt import TokenError, issue_token, verify_token
from app.security.password import (
    PasswordPolicyError,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# H-2: per-IP token-bucket limiter on the credential endpoints.
_ip_rate_limited = Depends(rate_limit_ip())

# Precomputed Argon2 hash for the unknown-user code path. Keeps wrong-email
# response time comparable to wrong-password response time so an attacker
# can't enumerate accounts by timing (OWASP A07 hardening).
_DUMMY_HASH_FOR_TIMING = hash_password("dummy-password-for-timing-only-do-not-use")


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


def _normalize_email(raw: str) -> str:
    return raw.strip().lower()


def _issue_pair(user: User) -> TokenPairResponse:
    access_token, access_payload = issue_token(subject=user.id, role=user.role.value, typ="access")
    refresh_token, refresh_payload = issue_token(
        subject=user.id, role=user.role.value, typ="refresh"
    )
    return TokenPairResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires_at=access_payload.exp,
        refresh_expires_at=refresh_payload.exp,
    )


def _as_aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; coerce to UTC-aware for comparison."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _is_locked(user: User) -> bool:
    locked = _as_aware(user.locked_until_at)
    return locked is not None and locked > utcnow()


def _register_failed_attempt(db: Session, user: User) -> None:
    settings = get_settings()
    now = utcnow()
    window = timedelta(seconds=settings.shield_account_lockout_window_seconds)
    last_failed = _as_aware(user.last_failed_login_at)
    if last_failed is None or now - last_failed > window:
        user.failed_login_count = 1
    else:
        user.failed_login_count += 1
    user.last_failed_login_at = now
    if user.failed_login_count >= settings.shield_account_lockout_max_attempts:
        user.locked_until_at = now + window
        audit(
            db,
            action="user.locked",
            target_type="user",
            target_id=user.id,
            actor_user_id=user.id,
            details={"reason": "max_failed_login_attempts"},
        )


def _register_successful_login(db: Session, user: User) -> None:
    user.failed_login_count = 0
    user.last_failed_login_at = None
    user.locked_until_at = None
    user.last_login_at = utcnow()
    audit(
        db,
        action="user.login",
        target_type="user",
        target_id=user.id,
        actor_user_id=user.id,
    )


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Self-register (D-004)",
    dependencies=[_ip_rate_limited],
)
def register(
    body: RegisterRequest,
    db: Annotated[Session, Depends(get_db)],
) -> RegisterResponse:
    email = _normalize_email(body.email)

    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account already exists for that email.",
        )

    try:
        password_hash = hash_password(body.password)
    except PasswordPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # Onboarding: self-registration always creates a `client`-role user
    # (admins are minted only by an existing admin / the env-seeded service
    # account). Every registrant is auto-associated with an org by email domain:
    #   * Work domain (e.g. acme.com): the first person to use it creates the
    #     org (Client + client_domain row) and becomes its primary POC; later
    #     registrants on the same domain auto-join that same org.
    #   * Generic mailbox provider (gmail, outlook, ...): we never group
    #     strangers who merely share a public provider, so each such user gets
    #     their OWN private org and no shared client_domain row.
    role = UserRole.CLIENT

    user = User(
        email=email,
        password_hash=password_hash,
        role=role,
        display_name=body.display_name,
        title=body.title,
        phone=body.phone,
        timezone=body.timezone,
        last_login_at=utcnow(),
    )
    db.add(user)
    db.flush()  # assigns user.id, needed for org ownership below

    client_for_user: Client | None = None
    created_org = False
    domain = domain_of(email)
    if domain and not is_generic_provider(domain):
        approved = db.execute(
            select(ClientDomain).where(ClientDomain.domain == domain)
        ).scalar_one_or_none()
        if approved is not None:
            client_for_user = db.get(Client, approved.client_id)
            if client_for_user is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="The organization for that domain is no longer available.",
                )
        else:
            # No org for this work domain yet: create it and register the
            # domain so colleagues who follow auto-join. (A rare race where
            # two same-domain users register simultaneously trips the unique
            # constraint on client_domain.domain; the loser can retry.)
            client_for_user = Client(legal_name=domain, primary_poc_user_id=user.id)
            db.add(client_for_user)
            db.flush()
            db.add(
                ClientDomain(
                    client_id=client_for_user.id,
                    domain=domain,
                    created_by=user.id,
                )
            )
            created_org = True
    else:
        # Generic/personal mailbox or address with no domain: isolated org
        # for this user alone. No client_domain row -> nobody else joins it.
        client_for_user = Client(
            legal_name=body.display_name or email,
            primary_poc_user_id=user.id,
        )
        db.add(client_for_user)
        db.flush()
        created_org = True

    user.client_id = client_for_user.id if client_for_user is not None else None

    # The user is the org's Primary POC when their registration created a
    # brand-new organization.
    is_primary_poc = created_org

    audit(
        db,
        action="user.created",
        target_type="user",
        target_id=user.id,
        actor_user_id=user.id,
        details={
            "role": role.value,
            "source": "self_registration",
            "created_org": created_org,
            "client_id": str(client_for_user.id) if client_for_user else None,
        },
    )

    tokens = _issue_pair(user)
    db.commit()
    db.refresh(user)

    return RegisterResponse(
        user=UserResponse.model_validate(user, from_attributes=True),
        tokens=tokens,
        is_primary_poc=is_primary_poc,
    )


@router.post(
    "/login",
    response_model=TokenPairResponse,
    summary="Email + password login",
    dependencies=[_ip_rate_limited],
)
def login(
    body: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TokenPairResponse:
    email = _normalize_email(body.email)
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()

    # Defer the "no such user" branch to the same response shape + timing
    # the wrong-password branch produces, to avoid an account-existence
    # oracle (OWASP A07).
    if user is None:
        # Run a dummy verify against a real hash so wrong-email timing is
        # comparable to wrong-password timing (OWASP A07).
        verify_password(body.password, _DUMMY_HASH_FOR_TIMING)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    if _is_locked(user):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Account is temporarily locked. Try again later.",
        )

    matched, needs_rehash = verify_password(body.password, user.password_hash)
    if not matched:
        _register_failed_attempt(db, user)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    # Deactivated accounts must not be able to obtain tokens. Checked only after
    # the password verifies, so a wrong-password attempt can't probe account
    # state (OWASP A07).
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This account has been deactivated.",
        )

    if needs_rehash:
        user.password_hash = hash_password(body.password)

    _register_successful_login(db, user)
    tokens = _issue_pair(user)
    db.commit()
    return tokens


@router.post(
    "/refresh",
    response_model=TokenPairResponse,
    summary="Refresh access + refresh tokens",
    dependencies=[_ip_rate_limited],
)
def refresh(
    body: RefreshRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TokenPairResponse:
    try:
        payload = verify_token(body.refresh_token, expected_type="refresh")
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        ) from exc

    user = db.get(User, payload.sub)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is no longer active.",
        )
    return _issue_pair(user)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Logout (audited; token revocation list lands in v1.x)",
)
def logout(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    audit(
        db,
        action="user.logout",
        target_type="user",
        target_id=user.id,
        actor_user_id=user.id,
    )
    db.commit()


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Current authenticated user",
)
def me(user: Annotated[User, Depends(current_user)]) -> UserResponse:
    return UserResponse.model_validate(user, from_attributes=True)
