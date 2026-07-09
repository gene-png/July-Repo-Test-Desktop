"""H-2 rate limiting: 429 + Retry-After for per-IP auth and per-user AI routes.

Limits are read from app.state (seeded from config in create_app), so each test
sets its own small limit on its own app instance. The bucket store is forced to
the in-memory implementation so no Redis is contacted.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.ai.llm import FixtureProvider, LLMClient, LLMResponse
from app.csf.catalog import SUBCATEGORIES
from app.middleware.ratelimit import InMemoryBucketStore
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin_resp


@pytest.fixture()
def app_client(tmp_path) -> Iterator[tuple[TestClient, FixtureProvider]]:
    url = f"sqlite:///{tmp_path / 'shield-rl.db'}"
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
    # Force the in-memory store so no Redis connection is attempted.
    app.state.rate_limit_store = InMemoryBucketStore()
    _seed = TestSession()
    tenant = _Client(legal_name="Test Tenant")
    _seed.add(tenant)
    _seed.flush()
    _seed.add(_ClientDomain(client_id=tenant.id, domain="example.com"))
    _seed.commit()
    cid = str(tenant.id)
    with TestClient(app, headers={"X-Client-Id": cid}) as c:
        yield c, provider


@pytest.mark.unit
def test_auth_login_rate_limited_per_ip(app_client) -> None:
    c, _ = app_client
    c.app.state.rate_limit_auth_per_min = 3
    body = {"email": "nobody@example.com", "password": "whatever-not-real"}
    codes = [c.post("/auth/login", json=body).status_code for _ in range(4)]
    # First three are allowed (401 unknown user); the fourth trips the limiter.
    assert codes[:3] == [401, 401, 401]
    assert codes[3] == 429
    r = c.post("/auth/login", json=body)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1


@pytest.mark.unit
def test_auth_limiter_disabled_when_zero(app_client) -> None:
    c, _ = app_client
    c.app.state.rate_limit_auth_per_min = 0
    body = {"email": "nobody@example.com", "password": "whatever-not-real"}
    codes = [c.post("/auth/login", json=body).status_code for _ in range(8)]
    assert all(code == 401 for code in codes)  # 0 disables the class


@pytest.mark.unit
def test_run_ai_rate_limited_per_user(app_client) -> None:
    c, provider = app_client
    c.app.state.rate_limit_ai_per_min = 2
    r = register_admin_resp(c, "admin@example.com")
    h = {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}
    svc_id = c.post("/csf/services", headers=h, json={"kind": "nist_csf", "title": "CSF"}).json()[
        "id"
    ]
    c.post(f"/csf/services/{svc_id}/assessments", headers=h)
    c.post(f"/csf/services/{svc_id}/profiles/seed", headers=h, json={"tiers": ["high"]})
    code = SUBCATEGORIES[0].code
    provider.register_static(
        "csf_score",
        LLMResponse(
            '{"scores": [{"tier": "high", "subcategory_code": "' + code + '", "governance": 1}]}'
        ),
    )
    first = c.post(f"/csf/services/{svc_id}/run-ai", headers=h)
    second = c.post(f"/csf/services/{svc_id}/run-ai", headers=h)
    third = c.post(f"/csf/services/{svc_id}/run-ai", headers=h)
    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert int(third.headers["Retry-After"]) >= 1
