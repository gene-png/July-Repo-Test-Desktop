"""E-1 whole-call timeout: a blocking provider yields 504 and applies nothing.

The provider stub blocks past the client's configured deadline; invoke's worker
thread is joined with that deadline, raises LLMTimeoutError, and the shared
handler maps it to 504. Because the route applies results only AFTER the provider
returns, the timed-out run leaves the assessment untouched, and the llm_calls
audit row is marked FAILED on its independent session.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.ai.llm import FixtureProvider, LLMClient, LLMResponse
from app.config import Settings
from app.csf.catalog import SUBCATEGORIES
from app.models.llm_call import LLMCall, LLMCallStatus
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin_resp


@pytest.fixture()
def app_client(tmp_path) -> Iterator[tuple[TestClient, FixtureProvider, sessionmaker]]:
    url = f"sqlite:///{tmp_path / 'shield-timeout.db'}"
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
    # A 1s deadline; the provider stub blocks ~5s, so the deadline always fires.
    fast_deadline = Settings(shield_llm_mode="fixture", shield_llm_timeout_seconds=1)
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[_llm_dep] = lambda: LLMClient(provider, settings=fast_deadline)
    _seed = TestSession()
    tenant = _Client(legal_name="Test Tenant")
    _seed.add(tenant)
    _seed.flush()
    _seed.add(_ClientDomain(client_id=tenant.id, domain="example.com"))
    _seed.commit()
    cid = str(tenant.id)
    with TestClient(app, headers={"X-Client-Id": cid}) as c:
        yield c, provider, TestSession


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
def test_run_ai_timeout_returns_504_and_changes_nothing(app_client) -> None:
    c, provider, TestSession = app_client
    h, svc_id = _bootstrap(c)
    code = SUBCATEGORIES[0].code

    def blocking(_payload: dict) -> LLMResponse:
        time.sleep(5)  # far past the 1s deadline; the worker thread is abandoned
        return LLMResponse(
            '{"scores": [{"tier": "high", "subcategory_code": "' + code + '", "governance": 2}]}'
        )

    provider.register("csf_score", blocking)

    r = c.post(f"/csf/services/{svc_id}/run-ai", headers=h)
    assert r.status_code == 504, r.text
    assert r.json()["error"]["message"] == "the AI call timed out; nothing was changed"

    # No state change: the seeded row is still at its default governance score.
    rows = c.get(f"/csf/services/{svc_id}/profile/high", headers=h).json()["rows"]
    row = next(x for x in rows if x["subcategory_code"] == code)
    assert row["governance"] == 0

    # The audit row was marked FAILED on its independent session.
    with TestSession() as fresh:
        call = fresh.execute(select(LLMCall)).scalar_one()
        assert call.status == LLMCallStatus.FAILED
        assert "LLMTimeoutError" in call.error_message
