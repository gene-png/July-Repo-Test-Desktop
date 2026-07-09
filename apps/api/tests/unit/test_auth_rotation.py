"""D-017 auth package: refresh rotation, revocation, and session limits."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture()
def app_client(tmp_path) -> Iterator[TestClient]:
    db_path = tmp_path / "shield-rotation.db"
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


def _register(client: TestClient, email: str = "rotate@example.com") -> dict:
    r = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct horse battery staple!",
            "display_name": "Rotation User",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _settings_override(**kwargs):
    """Patch live settings values for the duration of a test."""
    from app.config import get_settings

    s = get_settings()
    originals = {k: getattr(s, k) for k in kwargs}
    for k, v in kwargs.items():
        object.__setattr__(s, k, v)
    return s, originals


def _restore(s, originals) -> None:
    for k, v in originals.items():
        object.__setattr__(s, k, v)


@pytest.mark.unit
def test_rotation_retires_the_presented_token(app_client: TestClient) -> None:
    body = _register(app_client)
    old = body["tokens"]["refresh_token"]

    first = app_client.post("/auth/refresh", json={"refresh_token": old})
    assert first.status_code == 200
    new = first.json()["refresh_token"]
    assert new != old

    # The new token works; the old one is refused (strict mode: reuse).
    ok = app_client.post("/auth/refresh", json={"refresh_token": new})
    assert ok.status_code == 200

    s, originals = _settings_override(shield_refresh_reuse_grace_seconds=0)
    try:
        reused = app_client.post("/auth/refresh", json={"refresh_token": old})
        assert reused.status_code == 401
        assert "reuse" in reused.json()["error"]["message"].lower()
    finally:
        _restore(s, originals)


@pytest.mark.unit
def test_reuse_detection_revokes_the_whole_family(app_client: TestClient) -> None:
    body = _register(app_client)
    t0 = body["tokens"]["refresh_token"]

    t1 = app_client.post("/auth/refresh", json={"refresh_token": t0}).json()["refresh_token"]
    t2 = app_client.post("/auth/refresh", json={"refresh_token": t1}).json()["refresh_token"]

    s, originals = _settings_override(shield_refresh_reuse_grace_seconds=0)
    try:
        # Replay the first token in strict mode: theft signal. The whole family
        # dies, including the newest (t2), not just the replayed one.
        assert app_client.post("/auth/refresh", json={"refresh_token": t0}).status_code == 401
        r = app_client.post("/auth/refresh", json={"refresh_token": t2})
        assert r.status_code == 401
    finally:
        _restore(s, originals)


@pytest.mark.unit
def test_grace_window_tolerates_benign_rotation_race(app_client: TestClient) -> None:
    """Reusing a JUST-rotated token 401s but leaves the family alive (the
    concurrent-NextAuth-refresh race), with the default 30s grace window."""
    body = _register(app_client)
    t0 = body["tokens"]["refresh_token"]

    t1 = app_client.post("/auth/refresh", json={"refresh_token": t0}).json()["refresh_token"]

    # Replay t0 immediately: refused, but softly (no family revocation).
    replay = app_client.post("/auth/refresh", json={"refresh_token": t0})
    assert replay.status_code == 401
    assert "already rotated" in replay.json()["error"]["message"].lower()

    # The winner's token still works: the family survived the race.
    assert app_client.post("/auth/refresh", json={"refresh_token": t1}).status_code == 200


@pytest.mark.unit
def test_family_isolation_between_logins(app_client: TestClient) -> None:
    body = _register(app_client)
    email = body["user"]["email"]
    session_a = body["tokens"]["refresh_token"]

    login = app_client.post(
        "/auth/login",
        json={"email": email, "password": "correct horse battery staple!"},
    )
    assert login.status_code == 200
    session_b = login.json()["refresh_token"]

    # Kill family A via reuse; family B keeps working.
    a1 = app_client.post("/auth/refresh", json={"refresh_token": session_a}).json()["refresh_token"]
    assert a1
    assert app_client.post("/auth/refresh", json={"refresh_token": session_a}).status_code == 401
    assert app_client.post("/auth/refresh", json={"refresh_token": session_b}).status_code == 200


@pytest.mark.unit
def test_logout_with_refresh_token_revokes_its_family(app_client: TestClient) -> None:
    body = _register(app_client)
    access = body["tokens"]["access_token"]
    refresh = body["tokens"]["refresh_token"]

    r = app_client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {access}"},
        json={"refresh_token": refresh},
    )
    assert r.status_code == 204
    assert app_client.post("/auth/refresh", json={"refresh_token": refresh}).status_code == 401


@pytest.mark.unit
def test_logout_without_body_revokes_all_families(app_client: TestClient) -> None:
    body = _register(app_client)
    email = body["user"]["email"]
    access = body["tokens"]["access_token"]
    session_a = body["tokens"]["refresh_token"]
    session_b = app_client.post(
        "/auth/login",
        json={"email": email, "password": "correct horse battery staple!"},
    ).json()["refresh_token"]

    r = app_client.post("/auth/logout", headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 204
    assert app_client.post("/auth/refresh", json={"refresh_token": session_a}).status_code == 401
    assert app_client.post("/auth/refresh", json={"refresh_token": session_b}).status_code == 401


@pytest.mark.unit
def test_idle_timeout_revokes_family(app_client: TestClient) -> None:
    body = _register(app_client)
    refresh = body["tokens"]["refresh_token"]

    s, originals = _settings_override(shield_idle_timeout_seconds=1)
    try:
        time.sleep(1.2)
        r = app_client.post("/auth/refresh", json={"refresh_token": refresh})
        assert r.status_code == 401
        assert "idle" in r.json()["error"]["message"].lower()
    finally:
        _restore(s, originals)


@pytest.mark.unit
def test_forced_reauth_caps_family_age(app_client: TestClient) -> None:
    body = _register(app_client)
    refresh = body["tokens"]["refresh_token"]

    # A quick rotation keeps the token fresh for idle purposes, but the
    # family started at registration; a 1s max age then rejects the next hop.
    r1 = app_client.post("/auth/refresh", json={"refresh_token": refresh})
    assert r1.status_code == 200
    fresh = r1.json()["refresh_token"]

    s, originals = _settings_override(shield_forced_reauth_seconds=1, shield_idle_timeout_seconds=0)
    try:
        time.sleep(1.2)
        r = app_client.post("/auth/refresh", json={"refresh_token": fresh})
        assert r.status_code == 401
        assert "maximum age" in r.json()["error"]["message"].lower()
    finally:
        _restore(s, originals)


@pytest.mark.unit
def test_rotation_rows_are_linked(app_client: TestClient) -> None:
    from app.models.refresh_token import RefreshToken

    body = _register(app_client)
    refresh = body["tokens"]["refresh_token"]
    assert app_client.post("/auth/refresh", json={"refresh_token": refresh}).status_code == 200

    engine = create_engine(os.environ["DATABASE_URL"], future=True)
    with Session(engine, future=True) as db:
        rows = db.execute(select(RefreshToken)).scalars().all()
        assert len(rows) == 2
        old = next(r for r in rows if r.revoked_at is not None)
        new = next(r for r in rows if r.revoked_at is None)
        assert old.replaced_by_jti == new.jti
        assert old.family_id == new.family_id
        assert old.last_used_at is not None
    engine.dispose()
