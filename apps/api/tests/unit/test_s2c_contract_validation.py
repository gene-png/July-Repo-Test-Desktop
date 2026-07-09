"""A-6: run-ai/generate routes reject wrong-shape AI responses with 502."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.ai.contracts import validate_response
from app.ai.llm import FixtureProvider, LLMClient, LLMResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin_resp


@pytest.fixture()
def app_client(tmp_path) -> Iterator[tuple[TestClient, FixtureProvider]]:
    url = f"sqlite:///{tmp_path / 'shield-contractval.db'}"
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
    from app.routes import attack as attack_routes
    from app.routes import csf as csf_routes
    from app.routes import risk as risk_routes
    from app.routes import zt as zt_routes

    def override_get_db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    provider = FixtureProvider()
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    for mod in (csf_routes, zt_routes, attack_routes, risk_routes):
        app.dependency_overrides[mod._llm_dep] = lambda: LLMClient(provider)
    with TestClient(app) as c:
        yield c, provider


def _admin(c: TestClient) -> tuple[dict, str]:
    admin = register_admin_resp(c, "admin@kentro.example")
    bearer = admin.json()["tokens"]["access_token"]
    cid = c.post(
        "/admin/clients",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"legal_name": "Acme"},
    ).json()["id"]
    return {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}, cid


@pytest.mark.unit
def test_validate_response_unit_examples() -> None:
    assert validate_response("csf_score", {"scores": []}) == []
    assert validate_response("csf_score", {"scores": "nope"}) != []
    assert validate_response("csf_score", ["not", "a", "dict"]) != []
    assert validate_response("mitre_map", {"techniques": [{"technique_code": "T1", "status": "x"}]})
    assert validate_response("zt_score", {"capabilities": []}) == []
    assert validate_response("risk_synthesize", {"entries": []}) == []
    assert validate_response("risk_synthesize", {"entries": {}}) != []


@pytest.mark.unit
def test_csf_bad_shape_returns_502(app_client) -> None:
    c, provider = app_client
    h, _ = _admin(c)
    svc_id = c.post("/csf/services", headers=h, json={"kind": "nist_csf", "title": "CSF"}).json()[
        "id"
    ]
    c.post(f"/csf/services/{svc_id}/assessments", headers=h)
    # 'scores' should be a list; a bare object is a contract violation.
    provider.register_static("csf_score", LLMResponse('{"scores": {"oops": 1}}'))
    r = c.post(f"/csf/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 502, r.text
    assert "validation" in r.json()["error"]["message"].lower()


@pytest.mark.unit
def test_zt_bad_shape_returns_502(app_client) -> None:
    c, provider = app_client
    h, _ = _admin(c)
    svc_id = c.post(
        "/zt/services", headers=h, json={"kind": "zero_trust_cisa", "title": "ZT"}
    ).json()["id"]
    c.post(f"/zt/services/{svc_id}/assessments", headers=h)
    provider.register_static("zt_score", LLMResponse('{"capabilities": "not-a-list"}'))
    r = c.post(f"/zt/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 502, r.text


@pytest.mark.unit
def test_attack_bad_shape_returns_502(app_client) -> None:
    c, provider = app_client
    h, _ = _admin(c)
    svc_id = c.post(
        "/attack/services", headers=h, json={"kind": "attack_coverage", "title": "ATT&CK"}
    ).json()["id"]
    c.post(f"/attack/services/{svc_id}/assessments", headers=h)
    # A JSON list instead of the documented {"techniques": [...]} object.
    provider.register_static("mitre_map", LLMResponse('{"techniques": 42}'))
    r = c.post(f"/attack/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 502, r.text


@pytest.mark.unit
def test_risk_bad_shape_returns_502(app_client) -> None:
    c, provider = app_client
    h, cid = _admin(c)
    # Unlock the gate: an ATT&CK assessment + a ZT assessment for the client.
    a_svc = c.post(
        "/attack/services", headers=h, json={"kind": "attack_coverage", "title": "A"}
    ).json()["id"]
    c.post(f"/attack/services/{a_svc}/assessments", headers=h)
    z_svc = c.post(
        "/zt/services", headers=h, json={"kind": "zero_trust_cisa", "title": "Z"}
    ).json()["id"]
    c.post(f"/zt/services/{z_svc}/assessments", headers=h)

    provider.register_static("risk_synthesize", LLMResponse('{"entries": "nope"}'))
    r = c.post(f"/risk/clients/{cid}/register/generate", headers=h)
    assert r.status_code == 502, r.text
