"""H-5: llm_calls.client_id threading + GET /admin/ai-usage aggregation."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.ai.llm import FixtureProvider, LLMClient, LLMResponse
from app.models.llm_call import LLMCall
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin_resp


@pytest.fixture()
def app_client(tmp_path) -> Iterator[tuple[TestClient, FixtureProvider, sessionmaker]]:
    url = f"sqlite:///{tmp_path / 'shield-usage.db'}"
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
    tenant = _Client(legal_name="Usage Tenant")
    _seed.add(tenant)
    _seed.flush()
    _seed.add(_ClientDomain(client_id=tenant.id, domain="example.com"))
    _seed.commit()
    cid = str(tenant.id)
    with TestClient(app, headers={"X-Client-Id": cid}) as c:
        yield c, provider, TestSession


@pytest.mark.unit
def test_client_id_lands_on_llm_calls_and_usage_math(app_client) -> None:
    c, provider, TestSession = app_client
    r = register_admin_resp(c, "admin@example.com")
    h = {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}
    svc_id = c.post("/csf/services", headers=h, json={"kind": "nist_csf", "title": "CSF"}).json()[
        "id"
    ]
    c.post(f"/csf/services/{svc_id}/assessments", headers=h)
    # csf_score overrides model=claude-haiku-4-5, which is in the price table.
    provider.register_static(
        "csf_score", LLMResponse('{"scores": []}', input_tokens=1000, output_tokens=2000)
    )
    assert c.post(f"/csf/services/{svc_id}/run-ai", headers=h).status_code == 200

    # H-5: the tenant id was recorded on the llm_calls row.
    with TestSession() as db:
        row = db.execute(select(LLMCall)).scalar_one()
        assert row.client_id is not None
        assert str(row.client_id) == c.headers["X-Client-Id"]
        assert row.model == "claude-haiku-4-5"

    # ai-usage aggregates the row into one (client, month) bucket with a cost
    # from the haiku price table: 1000/1e6*0.8 + 2000/1e6*4.0 = 0.0088.
    u = c.get("/admin/ai-usage", headers=h)
    assert u.status_code == 200, u.text
    rows = u.json()["rows"]
    assert len(rows) == 1
    bucket = rows[0]
    assert bucket["calls"] == 1
    assert bucket["input_tokens"] == 1000
    assert bucket["output_tokens"] == 2000
    assert bucket["estimated_cost_usd"] == pytest.approx(0.0088)
    assert bucket["client_id"] == c.headers["X-Client-Id"]


@pytest.mark.unit
def test_ai_usage_csv_export(app_client) -> None:
    c, provider, _TestSession = app_client
    r = register_admin_resp(c, "admin@example.com")
    h = {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}
    svc_id = c.post("/csf/services", headers=h, json={"kind": "nist_csf", "title": "CSF"}).json()[
        "id"
    ]
    c.post(f"/csf/services/{svc_id}/assessments", headers=h)
    provider.register_static(
        "csf_score", LLMResponse('{"scores": []}', input_tokens=10, output_tokens=20)
    )
    c.post(f"/csf/services/{svc_id}/run-ai", headers=h)

    u = c.get("/admin/ai-usage?format=csv", headers=h)
    assert u.status_code == 200, u.text
    assert u.headers["content-type"].startswith("text/csv")
    lines = u.text.strip().splitlines()
    assert lines[0] == "client_id,month,calls,input_tokens,output_tokens,estimated_cost_usd"
    assert len(lines) == 2  # header + one bucket
    assert c.headers["X-Client-Id"] in lines[1]


@pytest.mark.unit
def test_ai_status_banner_copy_is_simulated_truth(app_client) -> None:
    """E-5: fixture-mode ai-status detail is the corrected 'simulated' copy."""
    c, _provider, _TestSession = app_client
    r = register_admin_resp(c, "admin@example.com")
    h = {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}
    body = c.get("/admin/ai-status", headers=h).json()
    assert body["mode"] == "fixture"
    assert body["detail"] == (
        "AI suggestions are simulated (deterministic fixtures) for demo "
        "and testing; set SHIELD_LLM_MODE=live for real analysis."
    )


@pytest.mark.unit
def test_ai_usage_admin_only(app_client) -> None:
    c, _provider, _TestSession = app_client
    # A client-role token must not reach the usage report.
    reg = c.post(
        "/auth/register",
        json={
            "email": "user@example.com",
            "password": "correct horse battery staple!",
            "display_name": "User",
        },
    )
    bearer = reg.json()["tokens"]["access_token"]
    u = c.get("/admin/ai-usage", headers={"Authorization": f"Bearer {bearer}"})
    assert u.status_code == 403, u.text
