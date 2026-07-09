"""E-1 connection release: a blocked provider call must NOT hold the DB pool.

The engine is deliberately built with a single-connection pool (pool_size=1,
max_overflow=0). While one run-ai request is blocked inside the provider, a
second request must still acquire the one connection and complete a DB query
within a bound - which is only possible because the run-ai route released its
request session (db.close()) before the provider call and the audit row is
written on an independent session that commits and releases immediately.

This is a genuine concurrency test of E-1: without the release, the blocked
request would hold the single connection and the second request would hang until
the block lifts.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.ai.llm import FixtureProvider, LLMClient, LLMResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from tests.conftest import register_admin_resp


@pytest.fixture()
def app_client(tmp_path) -> Iterator[tuple[TestClient, FixtureProvider]]:
    url = f"sqlite:///{tmp_path / 'shield-pool.db'}"
    os.environ["DATABASE_URL"] = url
    api_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    # ONE shared connection: if a blocked request holds it, a concurrent request
    # cannot proceed.
    engine = create_engine(
        url,
        future=True,
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
        connect_args={"check_same_thread": False},
    )
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
    _seed.close()  # release the single pooled connection before serving requests
    with TestClient(app, headers={"X-Client-Id": cid}) as c:
        yield c, provider


@pytest.mark.unit
def test_concurrent_request_proceeds_while_provider_blocks(app_client) -> None:
    c, provider = app_client
    r = register_admin_resp(c, "admin@example.com")
    h = {"Authorization": f"Bearer {r.json()['tokens']['access_token']}"}
    svc_id = c.post("/csf/services", headers=h, json={"kind": "nist_csf", "title": "CSF"}).json()[
        "id"
    ]
    c.post(f"/csf/services/{svc_id}/assessments", headers=h)
    c.post(f"/csf/services/{svc_id}/profiles/seed", headers=h, json={"tiers": ["high"]})

    entered = threading.Event()
    release = threading.Event()

    def blocking(_payload: dict) -> LLMResponse:
        entered.set()
        release.wait(timeout=15)
        return LLMResponse('{"scores": []}')

    provider.register("csf_score", blocking)

    result: dict = {}

    def run_ai() -> None:
        result["resp"] = c.post(f"/csf/services/{svc_id}/run-ai", headers=h)

    worker = threading.Thread(target=run_ai, name="blocked-run-ai")
    worker.start()
    try:
        assert entered.wait(10), "provider never entered - run-ai did not reach the call"
        # The one connection must be free while run-ai is blocked in the provider.
        t0 = time.monotonic()
        rb = c.get(f"/csf/services/{svc_id}/assessments/latest", headers=h)
        elapsed = time.monotonic() - t0
        assert rb.status_code == 200, rb.text
        assert elapsed < 8, f"second request blocked for {elapsed:.1f}s (pool was held)"
    finally:
        release.set()
        worker.join(15)
    assert result["resp"].status_code == 200, result["resp"].text
