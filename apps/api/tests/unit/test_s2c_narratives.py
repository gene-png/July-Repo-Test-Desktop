"""E-4: run-ai persists AI narratives; GET assessment echoes them."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.ai.llm import FixtureProvider, LLMClient, LLMResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin_resp


@pytest.fixture()
def app_client(tmp_path) -> Iterator[tuple[TestClient, FixtureProvider]]:
    url = f"sqlite:///{tmp_path / 'shield-narr.db'}"
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
    for mod in (zt_routes, attack_routes):
        app.dependency_overrides[mod._llm_dep] = lambda: LLMClient(provider)
    with TestClient(app) as c:
        yield c, provider


def _admin(c: TestClient) -> dict:
    admin = register_admin_resp(c, "admin@kentro.example")
    bearer = admin.json()["tokens"]["access_token"]
    cid = c.post(
        "/admin/clients",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"legal_name": "Acme"},
    ).json()["id"]
    return {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}


@pytest.mark.unit
def test_zt_run_ai_persists_narratives_and_get_returns_them(app_client) -> None:
    c, provider = app_client
    h = _admin(c)
    svc_id = c.post(
        "/zt/services", headers=h, json={"kind": "zero_trust_cisa", "title": "ZT"}
    ).json()["id"]
    a = c.post(f"/zt/services/{svc_id}/assessments", headers=h)
    code = a.json()["answers"][0]["capability_code"]
    provider.register_static(
        "zt_score",
        LLMResponse(
            '{"capabilities": [{"code": "' + code + '", "current": 2, "target": 4}],'
            ' "pillar_narratives": {"ID": "Identity is partial."},'
            ' "executive_summary": "Exec draft", "roadmap_summary": "12-month plan"}'
        ),
    )
    r = c.post(f"/zt/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 200, r.text

    # E-4: the assessment GET (admin) now carries the persisted narratives.
    latest = c.get(f"/zt/services/{svc_id}/assessments/latest", headers=h).json()
    narr = latest["narratives"]
    assert narr is not None
    assert narr["pillar_narratives"]["ID"] == "Identity is partial."
    assert narr["executive_summary"] == "Exec draft"
    assert narr["roadmap_summary"] == "12-month plan"


@pytest.mark.unit
def test_attack_run_ai_persists_ai_summaries_and_get_returns_them(app_client) -> None:
    c, provider = app_client
    h = _admin(c)
    svc_id = c.post(
        "/attack/services", headers=h, json={"kind": "attack_coverage", "title": "ATT&CK"}
    ).json()["id"]
    a = c.post(f"/attack/services/{svc_id}/assessments", headers=h)
    code = a.json()["coverage"][0]["technique_code"]
    provider.register_static(
        "mitre_map",
        LLMResponse(
            '{"techniques": [{"technique_code": "' + code + '", "status": "gap"}],'
            ' "executive_summary": "Coverage is thin.",'
            ' "top_blind_spots": ["Credential Access", "Lateral Movement"]}'
        ),
    )
    r = c.post(f"/attack/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 200, r.text

    latest = c.get(f"/attack/services/{svc_id}/assessments/latest", headers=h).json()
    summ = latest["ai_summaries"]
    assert summ is not None
    assert summ["executive_summary"] == "Coverage is thin."
    assert summ["top_blind_spots"] == ["Credential Access", "Lateral Movement"]


@pytest.mark.unit
def test_attack_run_ai_auto_creates_assessment(app_client) -> None:
    """F-2: run-ai with no existing assessment auto-creates a seeded draft."""
    c, provider = app_client
    h = _admin(c)
    svc_id = c.post(
        "/attack/services", headers=h, json={"kind": "attack_coverage", "title": "ATT&CK"}
    ).json()["id"]
    # No create-assessment call first.
    provider.register_static("mitre_map", LLMResponse('{"techniques": []}'))
    r = c.post(f"/attack/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["coverage"], "auto-created assessment should have a coverage grid"


@pytest.mark.unit
def test_zt_run_ai_auto_creates_assessment(app_client) -> None:
    c, provider = app_client
    h = _admin(c)
    svc_id = c.post(
        "/zt/services", headers=h, json={"kind": "zero_trust_cisa", "title": "ZT"}
    ).json()["id"]
    provider.register_static("zt_score", LLMResponse('{"capabilities": []}'))
    r = c.post(f"/zt/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["answers"], "auto-created assessment should have an answer grid"
