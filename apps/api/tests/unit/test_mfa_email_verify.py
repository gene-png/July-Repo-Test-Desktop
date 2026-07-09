"""MFA (TOTP) + email-verification (D-017 auth package).

Fixture pattern mirrors test_auth_rotation.py: a per-test SQLite DB migrated to
head, with the FastAPI app's get_db overridden onto it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pyotp
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

PASSWORD = "correct horse battery staple!"


@pytest.fixture()
def sent_emails(monkeypatch) -> list[dict]:
    """Capture outbound email instead of touching SMTP."""
    captured: list[dict] = []

    def _fake_send_email(*, to: str, subject: str, body: str) -> bool:
        captured.append({"to": to, "subject": subject, "body": body})
        return True

    monkeypatch.setattr("app.notifications.email.send_email", _fake_send_email)
    return captured


@pytest.fixture()
def app_client(tmp_path) -> Iterator[TestClient]:
    db_path = tmp_path / "shield-mfa.db"
    url = f"sqlite:///{db_path}"
    os.environ["DATABASE_URL"] = url

    api_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")

    test_engine = create_engine(url, future=True)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False, future=True)

    from app.db.session import get_db
    from app.main import create_app

    def override_get_db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        yield c


def _settings_override(**kwargs):
    from app.config import get_settings

    s = get_settings()
    originals = {k: getattr(s, k) for k in kwargs}
    for k, v in kwargs.items():
        object.__setattr__(s, k, v)
    return s, originals


def _restore(s, originals) -> None:
    for k, v in originals.items():
        object.__setattr__(s, k, v)


def _register(client: TestClient, email: str = "mfa@example.com") -> dict:
    r = client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "MFA User"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _bearer(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _enroll_and_activate(client: TestClient, access_token: str) -> str:
    """Enroll + activate TOTP; returns the base32 secret."""
    r = client.post("/auth/mfa/enroll", headers=_bearer(access_token))
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    assert r.json()["otpauth_uri"].startswith("otpauth://totp/")

    code = pyotp.TOTP(secret).now()
    act = client.post("/auth/mfa/activate", json={"code": code}, headers=_bearer(access_token))
    assert act.status_code == 200, act.text
    assert act.json()["mfa_enrolled"] is True
    return secret


def _extract_token(body: str) -> str:
    m = re.search(r"/verify-email\?token=([A-Za-z0-9_-]+)", body)
    assert m, f"no verify token in email body: {body!r}"
    return m.group(1)


# -- MFA ----------------------------------------------------------------------


@pytest.mark.unit
def test_enroll_activate_login_challenge_then_verify(app_client: TestClient) -> None:
    body = _register(app_client)
    access = body["tokens"]["access_token"]
    secret = _enroll_and_activate(app_client, access)

    # Login now returns a challenge, not a session.
    login = app_client.post(
        "/auth/login", json={"email": body["user"]["email"], "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    payload = login.json()
    assert payload["mfa_required"] is True
    assert payload["challenge_token"]
    assert payload["access_token"] is None

    # Exchange challenge + code for a real pair.
    code = pyotp.TOTP(secret).now()
    verify = app_client.post(
        "/auth/mfa/verify",
        json={"challenge_token": payload["challenge_token"], "code": code},
    )
    assert verify.status_code == 200, verify.text
    assert verify.json()["access_token"]
    assert verify.json()["refresh_token"]


@pytest.mark.unit
def test_wrong_code_401_and_counts_toward_lockout(app_client: TestClient) -> None:
    body = _register(app_client)
    access = body["tokens"]["access_token"]
    _enroll_and_activate(app_client, access)

    login = app_client.post(
        "/auth/login", json={"email": body["user"]["email"], "password": PASSWORD}
    )
    challenge = login.json()["challenge_token"]

    s, originals = _settings_override(shield_account_lockout_max_attempts=1)
    try:
        bad = app_client.post(
            "/auth/mfa/verify", json={"challenge_token": challenge, "code": "000000"}
        )
        assert bad.status_code == 401
        # That failed attempt tripped the lockout (max_attempts=1): the next
        # verify - even a correct one - is refused with 423.
        again = app_client.post(
            "/auth/mfa/verify", json={"challenge_token": challenge, "code": "000000"}
        )
        assert again.status_code == 423
    finally:
        _restore(s, originals)


@pytest.mark.unit
def test_disable_requires_valid_code(app_client: TestClient) -> None:
    body = _register(app_client)
    access = body["tokens"]["access_token"]
    secret = _enroll_and_activate(app_client, access)

    bad = app_client.post("/auth/mfa/disable", json={"code": "000000"}, headers=_bearer(access))
    assert bad.status_code == 400

    good = app_client.post(
        "/auth/mfa/disable",
        json={"code": pyotp.TOTP(secret).now()},
        headers=_bearer(access),
    )
    assert good.status_code == 200
    assert good.json()["mfa_enrolled"] is False

    # MFA gone: login returns a normal pair again.
    login = app_client.post(
        "/auth/login", json={"email": body["user"]["email"], "password": PASSWORD}
    )
    assert login.json()["mfa_required"] is False
    assert login.json()["access_token"]


@pytest.mark.unit
def test_challenge_and_access_tokens_are_not_interchangeable(app_client: TestClient) -> None:
    body = _register(app_client)
    access = body["tokens"]["access_token"]
    _enroll_and_activate(app_client, access)

    login = app_client.post(
        "/auth/login", json={"email": body["user"]["email"], "password": PASSWORD}
    )
    challenge = login.json()["challenge_token"]

    # A challenge token must not authenticate a protected route.
    me = app_client.get("/auth/me", headers=_bearer(challenge))
    assert me.status_code == 401

    # An access token must not satisfy the MFA challenge step.
    reused = app_client.post("/auth/mfa/verify", json={"challenge_token": access, "code": "000000"})
    assert reused.status_code == 401


@pytest.mark.unit
def test_require_mfa_flag_nudges_but_does_not_block(app_client: TestClient) -> None:
    body = _register(app_client, email="nudge@example.com")
    s, originals = _settings_override(shield_auth_require_mfa=True)
    try:
        login = app_client.post(
            "/auth/login", json={"email": body["user"]["email"], "password": PASSWORD}
        )
        assert login.status_code == 200
        # Still logs in (not blocked), but is told to set up MFA.
        assert login.json()["access_token"]
        assert login.json()["mfa_setup_required"] is True
    finally:
        _restore(s, originals)


# -- Email verification -------------------------------------------------------


@pytest.mark.unit
def test_email_verify_happy_path(app_client: TestClient, sent_emails: list[dict]) -> None:
    body = _register(app_client, email="verify@example.com")
    assert sent_emails, "registration should have sent a verification email"
    token = _extract_token(sent_emails[-1]["body"])

    r = app_client.post("/auth/verify-email", json={"token": token})
    assert r.status_code == 200, r.text
    assert r.json()["verified"] is True

    # Re-using the same token now fails (single use).
    again = app_client.post("/auth/verify-email", json={"token": token})
    assert again.status_code == 400
    _ = body


@pytest.mark.unit
def test_expired_verification_token_400(app_client: TestClient, sent_emails: list[dict]) -> None:
    _register(app_client, email="expired@example.com")
    token = _extract_token(sent_emails[-1]["body"])

    from app.models._common import utcnow
    from app.models.email_verification_token import EmailVerificationToken

    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    with Session(engine, future=True) as db:
        row = db.execute(select(EmailVerificationToken)).scalars().one()
        row.expires_at = utcnow() - timedelta(hours=1)
        db.commit()
    engine.dispose()

    r = app_client.post("/auth/verify-email", json={"token": token})
    assert r.status_code == 400


@pytest.mark.unit
def test_login_403_when_flag_on_and_unverified(
    app_client: TestClient, sent_emails: list[dict]
) -> None:
    body = _register(app_client, email="gated@example.com")
    s, originals = _settings_override(shield_auth_require_email_verify=True)
    try:
        blocked = app_client.post(
            "/auth/login", json={"email": body["user"]["email"], "password": PASSWORD}
        )
        assert blocked.status_code == 403
        assert "verify" in blocked.json()["error"]["message"].lower()

        # Verify, then login succeeds.
        token = _extract_token(sent_emails[-1]["body"])
        assert app_client.post("/auth/verify-email", json={"token": token}).status_code == 200
        ok = app_client.post(
            "/auth/login", json={"email": body["user"]["email"], "password": PASSWORD}
        )
        assert ok.status_code == 200
        assert ok.json()["access_token"]
    finally:
        _restore(s, originals)


@pytest.mark.unit
def test_login_normal_when_flag_off(app_client: TestClient) -> None:
    body = _register(app_client, email="open@example.com")
    # Default: SHIELD_AUTH_REQUIRE_EMAIL_VERIFY is off - unverified user logs in.
    login = app_client.post(
        "/auth/login", json={"email": body["user"]["email"], "password": PASSWORD}
    )
    assert login.status_code == 200
    assert login.json()["access_token"]
    assert login.json()["mfa_required"] is False


@pytest.mark.unit
def test_resend_invalidates_old_token(app_client: TestClient, sent_emails: list[dict]) -> None:
    body = _register(app_client, email="resend@example.com")
    access = body["tokens"]["access_token"]
    old_token = _extract_token(sent_emails[-1]["body"])

    resent = app_client.post("/auth/resend-verification", headers=_bearer(access))
    assert resent.status_code == 202
    new_token = _extract_token(sent_emails[-1]["body"])
    assert new_token != old_token

    # Old link is now dead; the new one works.
    assert app_client.post("/auth/verify-email", json={"token": old_token}).status_code == 400
    assert app_client.post("/auth/verify-email", json={"token": new_token}).status_code == 200
