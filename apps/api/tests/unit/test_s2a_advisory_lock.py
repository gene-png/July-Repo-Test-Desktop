"""E-3 advisory lock: SQL emission (mock PG), 409 contention, SQLite no-op, and a
route-level 409-loser path with a monkeypatched lock helper (Task S2-A).

Honest scope note: the SQLite test suite cannot exercise real advisory-lock
contention (pg_try_advisory_xact_lock does not exist on SQLite, where the helper
is a deliberate no-op). These tests verify the SQL the helper emits on Postgres,
the 409 mapping when the lock is not granted, and that the route surfaces that
409 - but two-transaction contention behavior on a live Postgres is out of reach
here and must be covered by an integration environment.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic import command
from alembic.config import Config
from app.ai.llm import FixtureProvider, LLMClient, LLMResponse
from app.csf.catalog import SUBCATEGORIES
from app.db.session import _advisory_lock_key, assessment_advisory_lock
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin_resp


def _pg_db(acquired: bool) -> MagicMock:
    db = MagicMock()
    db.get_bind.return_value.dialect.name = "postgresql"
    result = MagicMock()
    result.scalar.return_value = acquired
    db.execute.return_value = result
    return db


@pytest.mark.unit
def test_lock_emits_pg_try_advisory_xact_lock_sql() -> None:
    aid = uuid.uuid4()
    db = _pg_db(acquired=True)
    assessment_advisory_lock(db, aid)
    call = db.execute.call_args
    sql_text = str(call.args[0])
    assert "pg_try_advisory_xact_lock" in sql_text
    assert call.args[1]["key"] == _advisory_lock_key(aid)


@pytest.mark.unit
def test_lock_contention_raises_409() -> None:
    db = _pg_db(acquired=False)
    with pytest.raises(HTTPException) as ei:
        assessment_advisory_lock(db, uuid.uuid4())
    assert ei.value.status_code == 409
    assert "already in progress" in ei.value.detail


@pytest.mark.unit
def test_lock_is_noop_on_sqlite() -> None:
    db = MagicMock()
    db.get_bind.return_value.dialect.name = "sqlite"
    assessment_advisory_lock(db, uuid.uuid4())
    db.execute.assert_not_called()


@pytest.mark.unit
def test_lock_key_is_stable_signed_64bit() -> None:
    aid = uuid.uuid4()
    key = _advisory_lock_key(aid)
    assert isinstance(key, int)
    assert -(2**63) <= key < 2**63
    assert _advisory_lock_key(aid) == key  # deterministic


# ---------------------------------------------------------------------------
# Route-level 409-loser path: monkeypatch the lock helper to simulate contention.
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_client(tmp_path) -> Iterator[tuple[TestClient, FixtureProvider]]:
    url = f"sqlite:///{tmp_path / 'shield-lock.db'}"
    os.environ["DATABASE_URL"] = url
    api_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from app.db.session import get_db
    from app.main import create_app
    from app.models.client import Client as _Client
    from app.models.client_domain import ClientDomain as _ClientDomain
    from app.routes.csf import _llm_dep

    def override_get_db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    provider = FixtureProvider()
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[_llm_dep] = lambda: LLMClient(provider)
    _seed = TestSession()
    tenant = _Client(legal_name="Test Tenant")
    _seed.add(tenant)
    _seed.flush()
    _seed.add(_ClientDomain(client_id=tenant.id, domain="example.com"))
    _seed.commit()
    cid = str(tenant.id)
    with TestClient(app, headers={"X-Client-Id": cid}) as c:
        yield c, provider


def _bootstrap(c: TestClient) -> tuple[dict, str]:
    r = register_admin_resp(c, "admin@example.com")
    h = {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}
    svc_id = c.post("/csf/services", headers=h, json={"kind": "nist_csf", "title": "CSF"}).json()[
        "id"
    ]
    c.post(f"/csf/services/{svc_id}/assessments", headers=h)
    c.post(f"/csf/services/{svc_id}/profiles/seed", headers=h, json={"tiers": ["high"]})
    return h, svc_id


@pytest.mark.unit
def test_run_ai_concurrent_loser_returns_409(app_client, monkeypatch) -> None:
    """A run-ai whose advisory lock is already held (simulated) returns 409."""
    c, provider = app_client
    h, svc_id = _bootstrap(c)
    provider.register_static("csf_score", LLMResponse('{"scores": []}'))

    def _contended(_db, _subject) -> None:
        raise HTTPException(status_code=409, detail="a run is already in progress")

    monkeypatch.setattr("app.routes.csf.assessment_advisory_lock", _contended)
    r = c.post(f"/csf/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 409, r.text
    assert "already in progress" in r.json()["error"]["message"]


@pytest.mark.unit
def test_run_ai_winner_proceeds_when_lock_granted(app_client) -> None:
    """With the real (SQLite no-op) lock the run proceeds normally - a control."""
    c, provider = app_client
    h, svc_id = _bootstrap(c)
    code = SUBCATEGORIES[0].code
    provider.register_static(
        "csf_score",
        LLMResponse(
            '{"scores": [{"tier": "high", "subcategory_code": "' + code + '",' ' "governance": 2}]}'
        ),
    )
    r = c.post(f"/csf/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 200, r.text
