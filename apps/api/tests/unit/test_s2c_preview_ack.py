"""H-6: redaction preview + live-run acknowledgment gate (Task S2-C)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.ai.llm import FixtureProvider, LLMClient, LLMResponse
from app.config import Settings
from app.models.llm_call import LLMCall
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin_resp


def _make_app(tmp_path, *, live: bool) -> tuple[TestClient, sessionmaker, dict]:
    url = f"sqlite:///{tmp_path / 'shield-preview.db'}"
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
    from app.routes.zt import _llm_dep

    def override_get_db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    provider = FixtureProvider()
    calls = {"n": 0}

    def _fake(_payload: dict) -> LLMResponse:
        calls["n"] += 1
        return LLMResponse('{"capabilities": [], "pillar_narratives": {}}')

    provider.register("zt_score", _fake)

    settings = None
    if live:
        settings = Settings(shield_llm_mode="live", shield_llm_provider="anthropic")

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[_llm_dep] = lambda: LLMClient(provider, settings=settings)
    return app, TestSession, calls


def _admin_zt(c: TestClient) -> tuple[dict, str, str]:
    admin = register_admin_resp(c, "admin@kentro.example")
    bearer = admin.json()["tokens"]["access_token"]
    cid = c.post(
        "/admin/clients",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"legal_name": "Acme"},
    ).json()["id"]
    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    svc = c.post("/zt/services", headers=h, json={"kind": "zero_trust_cisa", "title": "Acme ZT"})
    return h, svc.json()["id"], cid


@pytest.mark.unit
def test_preview_returns_redacted_payload_without_provider_call(tmp_path) -> None:
    app, TestSession, calls = _make_app(tmp_path, live=False)
    with TestClient(app) as c:
        h, svc_id, _cid = _admin_zt(c)
        r = c.post(f"/zt/services/{svc_id}/run-ai?preview=1", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["preview"] is True
        assert "redacted_payload" in body
        assert "redaction_summary" in body
    # Zero provider calls and zero llm_calls rows were written for a preview.
    assert calls["n"] == 0
    with TestSession() as db:
        assert db.execute(select(func.count()).select_from(LLMCall)).scalar_one() == 0


@pytest.mark.unit
def test_live_run_without_ack_returns_428(tmp_path) -> None:
    app, _TestSession, calls = _make_app(tmp_path, live=True)
    with TestClient(app) as c:
        h, svc_id, _cid = _admin_zt(c)
        r = c.post(f"/zt/services/{svc_id}/run-ai", headers=h)
        assert r.status_code == 428, r.text
        assert "acknowledg" in r.json()["error"]["message"].lower()
    assert calls["n"] == 0  # nothing ran


@pytest.mark.unit
def test_live_run_with_ack_proceeds(tmp_path) -> None:
    app, _TestSession, calls = _make_app(tmp_path, live=True)
    with TestClient(app) as c:
        h, svc_id, cid = _admin_zt(c)
        # Record the ack for this client, then the live run proceeds.
        ack = c.post("/admin/ai-preview-ack", headers=h, json={"client_id": cid})
        assert ack.status_code == 200, ack.text
        r = c.post(f"/zt/services/{svc_id}/run-ai", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["mode"] == "live"
    assert calls["n"] == 1


@pytest.mark.unit
def test_preview_allowed_in_live_mode_without_ack(tmp_path) -> None:
    """A preview is exactly how you review before acking, so it must not 428."""
    app, _TestSession, calls = _make_app(tmp_path, live=True)
    with TestClient(app) as c:
        h, svc_id, _cid = _admin_zt(c)
        r = c.post(f"/zt/services/{svc_id}/run-ai?preview=1", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["preview"] is True
    assert calls["n"] == 0
