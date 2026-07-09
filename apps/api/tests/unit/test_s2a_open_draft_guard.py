"""E-3 open-draft guard on create-assessment for CSF / ZT / ATT&CK (Task S2-A).

A create against a service that already has an assessment in a pre-approval
working status returns that assessment (200) instead of minting a new version.
Working statuses per service: CSF DRAFT+SUBMITTED, ZT DRAFT+SUBMITTED, ATT&CK
DRAFT. Once the current assessment is approved, a create mints the next version.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin_resp


@pytest.fixture()
def app_client(tmp_path) -> Iterator[TestClient]:
    url = f"sqlite:///{tmp_path / 'shield-guard.db'}"
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

    def override_get_db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    _seed = TestSession()
    tenant = _Client(legal_name="Test Tenant")
    _seed.add(tenant)
    _seed.flush()
    _seed.add(_ClientDomain(client_id=tenant.id, domain="example.com"))
    _seed.commit()
    cid = str(tenant.id)
    with TestClient(app, headers={"X-Client-Id": cid}) as c:
        yield c


def _admin_headers(c: TestClient) -> dict:
    r = register_admin_resp(c, "admin@example.com")
    return {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}


@pytest.mark.unit
def test_csf_create_returns_open_draft(app_client) -> None:
    c = app_client
    h = _admin_headers(c)
    svc = c.post("/csf/services", headers=h, json={"kind": "nist_csf", "title": "CSF"}).json()["id"]
    first = c.post(f"/csf/services/{svc}/assessments", headers=h)
    assert first.status_code == 201
    again = c.post(f"/csf/services/{svc}/assessments", headers=h)
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["version"] == 1
    # After approval, a create mints v2.
    assert c.post(f"/csf/assessments/{first.json()['id']}/approve", headers=h).status_code == 200
    v2 = c.post(f"/csf/services/{svc}/assessments", headers=h)
    assert v2.status_code == 201
    assert v2.json()["version"] == 2


@pytest.mark.unit
def test_zt_create_returns_open_draft(app_client) -> None:
    c = app_client
    h = _admin_headers(c)
    svc = c.post("/zt/services", headers=h, json={"kind": "zero_trust_cisa", "title": "ZT"}).json()[
        "id"
    ]
    first = c.post(f"/zt/services/{svc}/assessments", headers=h)
    assert first.status_code == 201
    again = c.post(f"/zt/services/{svc}/assessments", headers=h)
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["version"] == 1


@pytest.mark.unit
def test_attack_create_returns_open_draft(app_client) -> None:
    c = app_client
    h = _admin_headers(c)
    svc = c.post(
        "/attack/services", headers=h, json={"kind": "attack_coverage", "title": "ATTACK"}
    ).json()["id"]
    first = c.post(f"/attack/services/{svc}/assessments", headers=h)
    assert first.status_code == 201
    again = c.post(f"/attack/services/{svc}/assessments", headers=h)
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["version"] == 1
