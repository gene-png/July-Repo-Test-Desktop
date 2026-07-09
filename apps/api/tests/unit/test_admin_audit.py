"""H-7 admin audit-log endpoint: filters, role gate, and CSV export."""

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

from tests.conftest import register_admin


@pytest.fixture()
def app_client(tmp_path) -> Iterator[TestClient]:
    db_path = tmp_path / "shield-audit.db"
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

    # A pre-approved org domain so a second (client-role) registrant can join.
    from app.models.client import Client as _Client
    from app.models.client_domain import ClientDomain as _ClientDomain

    seed = TestSession()
    tenant = _Client(legal_name="(pending intake)")
    seed.add(tenant)
    seed.flush()
    seed.add(_ClientDomain(client_id=tenant.id, domain="example.com"))
    seed.commit()
    seed.close()

    with TestClient(app) as c:
        yield c


def _auth(bearer: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {bearer}"}


def _register_client(client: TestClient, email: str) -> dict:
    r = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct horse battery staple!",
            "display_name": email.split("@")[0],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.unit
def test_audit_role_gate(app_client: TestClient) -> None:
    register_admin(app_client, "admin@example.com")
    client_bearer = _register_client(app_client, "client@example.com")["tokens"]["access_token"]
    r = app_client.get("/admin/audit", headers=_auth(client_bearer))
    assert r.status_code == 403, r.text


@pytest.mark.unit
def test_audit_requires_auth(app_client: TestClient) -> None:
    assert app_client.get("/admin/audit").status_code == 401


@pytest.mark.unit
def test_audit_filters(app_client: TestClient) -> None:
    admin = register_admin(app_client, "admin@example.com")
    admin_bearer = admin["tokens"]["access_token"]
    admin_id = admin["user"]["id"]

    # Generate a couple of audit rows via admin actions.
    created = app_client.post(
        "/admin/clients",
        headers=_auth(admin_bearer),
        json={"legal_name": "Atlas Defense Solutions"},
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]

    # A user.created row carries client_id inside details.
    made_user = app_client.post(
        "/admin/users",
        headers=_auth(admin_bearer),
        json={
            "email": "poc@atlas.example",
            "password": "correct horse battery staple!",
            "display_name": "Atlas POC",
            "role": "client",
            "client_id": cid,
        },
    )
    assert made_user.status_code == 201, made_user.text

    # No filter: newest-first, includes both actions.
    r = app_client.get("/admin/audit", headers=_auth(admin_bearer))
    assert r.status_code == 200, r.text
    body = r.json()
    actions = [row["action"] for row in body["rows"]]
    assert "client.created" in actions
    assert "user.created" in actions
    assert body["total"] >= 2
    # Ordered newest-first: user.created happened after client.created.
    assert actions.index("user.created") < actions.index("client.created")

    # action filter.
    r = app_client.get(
        "/admin/audit", headers=_auth(admin_bearer), params={"action": "user.created"}
    )
    rows = r.json()["rows"]
    assert rows and all(row["action"] == "user.created" for row in rows)

    # actor_user_id filter (the admin performed every action).
    r = app_client.get(
        "/admin/audit", headers=_auth(admin_bearer), params={"actor_user_id": admin_id}
    )
    assert r.json()["rows"] and all(row["actor_user_id"] == admin_id for row in r.json()["rows"])

    # client_id filter surfaces the user.created row (details.client_id).
    r = app_client.get("/admin/audit", headers=_auth(admin_bearer), params={"client_id": cid})
    rows = r.json()["rows"]
    assert rows, "expected at least the user.created row for this client"
    assert all(row["client_id"] == cid for row in rows)
    assert any(row["action"] == "user.created" for row in rows)

    # A non-matching action returns an empty page but a well-formed envelope.
    r = app_client.get(
        "/admin/audit", headers=_auth(admin_bearer), params={"action": "does.not.exist"}
    )
    assert r.status_code == 200
    assert r.json()["rows"] == []
    assert r.json()["total"] == 0


@pytest.mark.unit
def test_audit_pagination(app_client: TestClient) -> None:
    admin_bearer = register_admin(app_client, "admin@example.com")["tokens"]["access_token"]
    for i in range(3):
        app_client.post(
            "/admin/clients",
            headers=_auth(admin_bearer),
            json={"legal_name": f"Org {i}"},
        )
    r = app_client.get(
        "/admin/audit", headers=_auth(admin_bearer), params={"limit": 2, "offset": 0}
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["rows"]) == 2
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert body["total"] >= 3
    # limit is clamped to the max.
    over = app_client.get("/admin/audit", headers=_auth(admin_bearer), params={"limit": 9999})
    assert over.status_code == 422


@pytest.mark.unit
def test_audit_csv_content_type(app_client: TestClient) -> None:
    admin_bearer = register_admin(app_client, "admin@example.com")["tokens"]["access_token"]
    app_client.post(
        "/admin/clients",
        headers=_auth(admin_bearer),
        json={"legal_name": "CSV Org"},
    )
    r = app_client.get("/admin/audit", headers=_auth(admin_bearer), params={"format": "csv"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers.get("content-disposition", "")
    body = r.text
    assert "action" in body.splitlines()[0]
    assert "client.created" in body
