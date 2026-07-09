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

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import pyotp
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
from app.models.email_verification_token import EmailVerificationToken
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserRole
from app.notifications import email as email_sender
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    MfaCodeRequest,
    MfaEnrollResponse,
    MfaVerifyRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenPairResponse,
    UserResponse,
    VerifyEmailRequest,
)
from app.security.email_domains import domain_of, is_generic_provider
from app.security.jwt import TokenError, issue_token, verify_token
from app.security.password import (
    PasswordPolicyError,
    hash_password,
    verify_password,
)

# Issuer label baked into the otpauth:// provisioning URI, shown by the user's
# authenticator app next to the generated codes.
_TOTP_ISSUER = "SHIELD by Kentro"
_EMAIL_VERIFY_TTL = timedelta(hours=24)

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


def _issue_pair_with_record(
    user: User, db: Session, *, family_id: uuid.UUID | None = None
) -> tuple[TokenPairResponse, RefreshToken]:
    """Issue an access+refresh pair and persist the refresh-token record.

    D-017: refresh tokens are server-tracked for rotation and revocation.
    `family_id=None` starts a new session family (login/register); rotation
    passes the existing family through. The caller commits.
    """
    access_token, access_payload = issue_token(subject=user.id, role=user.role.value, typ="access")
    refresh_token, refresh_payload = issue_token(
        subject=user.id, role=user.role.value, typ="refresh"
    )
    record = RefreshToken(
        jti=refresh_payload.jti,
        user_id=user.id,
        family_id=family_id or uuid.uuid4(),
        issued_at=utcnow(),
        expires_at=refresh_payload.exp,
    )
    db.add(record)
    db.flush()
    pair = TokenPairResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires_at=access_payload.exp,
        refresh_expires_at=refresh_payload.exp,
    )
    return pair, record


def _issue_pair(
    user: User, db: Session, *, family_id: uuid.UUID | None = None
) -> TokenPairResponse:
    pair, _record = _issue_pair_with_record(user, db, family_id=family_id)
    return pair


def _revoke_family(db: Session, family_id: uuid.UUID) -> int:
    """Revoke every active token in a session family; returns the count."""
    now = utcnow()
    rows = (
        db.execute(
            select(RefreshToken).where(
                RefreshToken.family_id == family_id,
                RefreshToken.revoked_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.revoked_at = now
    db.flush()
    return len(rows)


def _refresh_reject(db: Session, family_id: uuid.UUID | None, message: str) -> HTTPException:
    """Revoke the family (when known) and build the 401 for a refused refresh."""
    if family_id is not None:
        _revoke_family(db, family_id)
        db.commit()
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=message)


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


# -- MFA (TOTP) + email-verification helpers ----------------------------------


def _verify_totp(secret: str, code: str) -> bool:
    """Constant-window TOTP check. valid_window=1 tolerates a +/-30s clock skew."""
    return pyotp.TOTP(secret).verify(code.strip().replace(" ", ""), valid_window=1)


def _sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _verification_link(raw_token: str) -> str:
    base = get_settings().shield_frontend_base_url.rstrip("/")
    path = f"/verify-email?token={raw_token}"
    return f"{base}{path}" if base else path


def _issue_email_verification(db: Session, user: User) -> str:
    """Invalidate any outstanding verification tokens for the user, mint a fresh
    one (storing only its sha256), and return the RAW token for emailing."""
    now = utcnow()
    outstanding = (
        db.execute(
            select(EmailVerificationToken).where(
                EmailVerificationToken.user_id == user.id,
                EmailVerificationToken.used_at.is_(None),
            )
        )
        .scalars()
        .all()
    )
    for row in outstanding:
        row.used_at = now  # consume so an old link can't be reused after a resend
    raw = secrets.token_urlsafe(32)
    db.add(
        EmailVerificationToken(
            user_id=user.id,
            token_hash=_sha256_hex(raw),
            expires_at=now + _EMAIL_VERIFY_TTL,
        )
    )
    db.flush()
    return raw


def _send_verification_email(user: User, raw_token: str) -> None:
    """Fire-and-forget; send_email never raises (registration must not depend on
    SMTP being up)."""
    link = _verification_link(raw_token)
    email_sender.send_email(
        to=user.email,
        subject="Verify your SHIELD email",
        body=(
            "Welcome to SHIELD by Kentro.\n\n"
            "Confirm your email address by opening this link:\n"
            f"{link}\n\n"
            "The link expires in 24 hours. If you did not create an account, "
            "you can ignore this message."
        ),
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

    # Always mint + send an email-verification token (cheap; the flag only gates
    # whether login later requires it). Best-effort delivery - never block signup.
    raw_verify_token = _issue_email_verification(db, user)

    tokens = _issue_pair(user, db)
    db.commit()
    db.refresh(user)

    _send_verification_email(user, raw_verify_token)

    return RegisterResponse(
        user=UserResponse.model_validate(user, from_attributes=True),
        tokens=tokens,
        is_primary_poc=is_primary_poc,
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Email + password login (may return an MFA challenge)",
    dependencies=[_ip_rate_limited],
)
def login(
    body: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
) -> LoginResponse:
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

    settings = get_settings()

    # Email-verification gate. Checked only AFTER the password verifies, so an
    # unauthenticated prober can never learn verification state (OWASP A07): a
    # wrong password still returns the generic 401 above.
    if settings.shield_auth_require_email_verify and user.email_verified_at is None:
        db.commit()  # persist any rehash
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email address before signing in.",
        )

    # Second factor. A user who has completed TOTP enrollment must present a code
    # before any token is issued: we hand back a short-lived challenge instead of
    # a session. Lockout counters are left untouched here - they reset only once
    # /auth/mfa/verify completes the login.
    if user.mfa_enrolled and user.mfa_secret:
        challenge_token, _payload = issue_token(
            subject=user.id, role=user.role.value, typ="mfa_challenge"
        )
        db.commit()  # persist any rehash
        return LoginResponse(mfa_required=True, challenge_token=challenge_token)

    _register_successful_login(db, user)
    tokens = _issue_pair(user, db)
    db.commit()
    # SHIELD_AUTH_REQUIRE_MFA nudge: the user can still log in, but the frontend
    # is told to prompt enrollment. This flag does not block anything server-side
    # today (documented in config.py / DECISIONS D-017 as a future hardening).
    setup_required = settings.shield_auth_require_mfa and not user.mfa_enrolled
    return LoginResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        access_expires_at=tokens.access_expires_at,
        refresh_expires_at=tokens.refresh_expires_at,
        mfa_setup_required=setup_required,
    )


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
    """Rotate the refresh token (D-017).

    Every successful refresh revokes the presented token and issues a new
    one in the same session family. Reusing an already-rotated (or revoked)
    token is treated as theft: the whole family is revoked. Idle-timeout and
    forced-re-auth limits are enforced here, on the server-side records.
    """
    try:
        payload = verify_token(body.refresh_token, expected_type="refresh")
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        ) from exc

    row = db.execute(
        select(RefreshToken).where(RefreshToken.jti == payload.jti)
    ).scalar_one_or_none()
    if row is None:
        # A validly-signed refresh JWT with no record: pre-rotation token or a
        # replayed artifact. Refuse; nothing to revoke without a family.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unknown refresh token; sign in again.",
        )
    settings = get_settings()
    now = utcnow()

    if row.revoked_at is not None:
        # Reuse of a rotated token inside the grace window is a benign race
        # (concurrent server-side renders refreshing simultaneously): refuse
        # this request but keep the family alive so the winner's token works.
        grace = settings.shield_refresh_reuse_grace_seconds
        revoked_at = _as_aware(row.revoked_at)
        recently_rotated = (
            row.replaced_by_jti is not None
            and revoked_at is not None
            and (now - revoked_at).total_seconds() <= grace
        )
        if recently_rotated:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token already rotated; retry with the newest token.",
            )
        # Outside the window (or never rotated): compromise signal, kill the family.
        raise _refresh_reject(
            db, row.family_id, "Refresh token reuse detected; session revoked, sign in again."
        )

    idle_limit = settings.shield_idle_timeout_seconds
    if idle_limit and idle_limit > 0:
        last_activity = _as_aware(row.last_used_at) or _as_aware(row.issued_at)
        if last_activity is not None and (now - last_activity).total_seconds() > idle_limit:
            raise _refresh_reject(db, row.family_id, "Session idle timeout; sign in again.")

    forced_limit = settings.shield_forced_reauth_seconds
    if forced_limit and forced_limit > 0:
        family_start = db.execute(
            select(RefreshToken.issued_at)
            .where(RefreshToken.family_id == row.family_id)
            .order_by(RefreshToken.issued_at.asc())
            .limit(1)
        ).scalar_one()
        family_start = _as_aware(family_start)
        if family_start is not None and (now - family_start).total_seconds() > forced_limit:
            raise _refresh_reject(db, row.family_id, "Session maximum age reached; sign in again.")

    user = db.get(User, payload.sub)
    if user is None or not user.is_active:
        raise _refresh_reject(db, row.family_id, "User is no longer active.")

    # Rotate: retire the presented token, mint its successor in the family.
    tokens, new_record = _issue_pair_with_record(user, db, family_id=row.family_id)
    row.revoked_at = now
    row.last_used_at = now
    row.replaced_by_jti = new_record.jti
    db.commit()
    return tokens


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Logout (audited; revokes the session's refresh-token family)",
)
def logout(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
    body: LogoutRequest | None = None,
) -> None:
    """D-017: when the client presents its refresh token, the whole session
    family is revoked server-side. Without one (legacy callers), every active
    family for the user is revoked - the safe interpretation of "log out".
    Access tokens stay stateless; their 15-minute TTL bounds the residue."""
    revoked = 0
    if body is not None and body.refresh_token:
        try:
            payload = verify_token(body.refresh_token, expected_type="refresh")
            row = db.execute(
                select(RefreshToken).where(RefreshToken.jti == payload.jti)
            ).scalar_one_or_none()
            if row is not None and row.user_id == user.id:
                revoked = _revoke_family(db, row.family_id)
        except TokenError:
            pass  # Bad token on logout is not an error; fall through to revoke-all.
    if revoked == 0:
        now = utcnow()
        rows = (
            db.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == user.id,
                    RefreshToken.revoked_at.is_(None),
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            r.revoked_at = now
        revoked = len(rows)
    audit(
        db,
        action="user.logout",
        target_type="user",
        target_id=user.id,
        actor_user_id=user.id,
        details={"refresh_tokens_revoked": revoked},
    )
    db.commit()


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Current authenticated user",
)
def me(user: Annotated[User, Depends(current_user)]) -> UserResponse:
    return UserResponse.model_validate(user, from_attributes=True)


# -----------------------------------------------------------------------------
# MFA (TOTP)
# -----------------------------------------------------------------------------


@router.post(
    "/mfa/verify",
    response_model=TokenPairResponse,
    summary="Complete an MFA login with a TOTP code",
    dependencies=[_ip_rate_limited],
)
def mfa_verify(
    body: MfaVerifyRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TokenPairResponse:
    """Second step of an MFA login: exchange the challenge token + TOTP code for
    a normal session pair. A wrong code counts toward the lockout counters, same
    as a wrong password."""
    try:
        payload = verify_token(body.challenge_token, expected_type="mfa_challenge")
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired MFA challenge; sign in again.",
        ) from exc

    user = db.get(User, payload.sub)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is no longer active.",
        )
    if _is_locked(user):
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Account is temporarily locked. Try again later.",
        )
    if not (user.mfa_enrolled and user.mfa_secret):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is not enabled for this account.",
        )
    if not _verify_totp(user.mfa_secret, body.code):
        _register_failed_attempt(db, user)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication code.",
        )

    _register_successful_login(db, user)
    tokens = _issue_pair(user, db)  # fresh session family
    db.commit()
    return tokens


@router.post(
    "/mfa/enroll",
    response_model=MfaEnrollResponse,
    summary="Begin TOTP enrollment (returns the secret + otpauth URI)",
    dependencies=[_ip_rate_limited],
)
def mfa_enroll(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> MfaEnrollResponse:
    """Generate a TOTP secret and stash it on the user. MFA is NOT active yet -
    the user must prove possession via /auth/mfa/activate. Re-enrolling while
    already active is refused so a stray call can't silently swap a working
    factor for one the user hasn't scanned."""
    if user.mfa_enrolled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MFA is already enabled. Disable it first to re-enroll.",
        )
    secret = pyotp.random_base32()
    user.mfa_secret = secret
    audit(
        db,
        action="user.mfa_enroll_started",
        target_type="user",
        target_id=user.id,
        actor_user_id=user.id,
    )
    db.commit()
    otpauth_uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=_TOTP_ISSUER)
    return MfaEnrollResponse(secret=secret, otpauth_uri=otpauth_uri)


@router.post(
    "/mfa/activate",
    response_model=UserResponse,
    summary="Activate TOTP by confirming a live code",
    dependencies=[_ip_rate_limited],
)
def mfa_activate(
    body: MfaCodeRequest,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UserResponse:
    if not user.mfa_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Start enrollment first (POST /auth/mfa/enroll).",
        )
    if not _verify_totp(user.mfa_secret, body.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid authentication code.",
        )
    user.mfa_enrolled = True
    user.mfa_enrolled_at = utcnow()
    audit(
        db,
        action="user.mfa_enrolled",
        target_type="user",
        target_id=user.id,
        actor_user_id=user.id,
    )
    db.commit()
    db.refresh(user)
    return UserResponse.model_validate(user, from_attributes=True)


@router.post(
    "/mfa/disable",
    response_model=UserResponse,
    summary="Disable TOTP (requires a valid current code)",
    dependencies=[_ip_rate_limited],
)
def mfa_disable(
    body: MfaCodeRequest,
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UserResponse:
    if not (user.mfa_enrolled and user.mfa_secret):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is not enabled for this account.",
        )
    if not _verify_totp(user.mfa_secret, body.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid authentication code.",
        )
    user.mfa_enrolled = False
    user.mfa_secret = None
    user.mfa_enrolled_at = None
    audit(
        db,
        action="user.mfa_disabled",
        target_type="user",
        target_id=user.id,
        actor_user_id=user.id,
    )
    db.commit()
    db.refresh(user)
    return UserResponse.model_validate(user, from_attributes=True)


# -----------------------------------------------------------------------------
# Email verification
# -----------------------------------------------------------------------------


@router.post(
    "/verify-email",
    summary="Confirm an email address via its verification token",
    dependencies=[_ip_rate_limited],
)
def verify_email(
    body: VerifyEmailRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, bool]:
    """Consume an email-verification token. Idempotent-ish: a token can only be
    used once; a used/expired/unknown token all return the same generic 400 so
    the endpoint can't be used to probe which tokens exist."""
    token_hash = _sha256_hex(body.token)
    row = db.execute(
        select(EmailVerificationToken).where(EmailVerificationToken.token_hash == token_hash)
    ).scalar_one_or_none()
    now = utcnow()
    expires_at = _as_aware(row.expires_at) if row is not None else None
    if row is None or row.used_at is not None or (expires_at is not None and expires_at < now):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This verification link is invalid or has expired.",
        )
    row.used_at = now
    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This verification link is invalid or has expired.",
        )
    if user.email_verified_at is None:
        user.email_verified_at = now
    audit(
        db,
        action="user.email_verified",
        target_type="user",
        target_id=user.id,
        actor_user_id=user.id,
    )
    db.commit()
    return {"verified": True}


@router.post(
    "/resend-verification",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Resend the email-verification link (invalidates prior links)",
    dependencies=[_ip_rate_limited],
)
def resend_verification(
    user: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, bool]:
    if user.email_verified_at is not None:
        return {"already_verified": True}
    raw_token = _issue_email_verification(db, user)
    audit(
        db,
        action="user.email_verification_resent",
        target_type="user",
        target_id=user.id,
        actor_user_id=user.id,
    )
    db.commit()
    _send_verification_email(user, raw_token)
    return {"sent": True}
