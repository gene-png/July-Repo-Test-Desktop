"""Gated live smoke test (Task S2-C / A-6).

Makes the smallest possible REAL provider call per registered AI job with tiny
payloads and asserts the response parses and validates against its documented
contract. Skipped by default: it runs ONLY when both SHIELD_LIVE_SMOKE=1 and
ANTHROPIC_API_KEY are set, so it never fires in CI/offline runs. Its purpose is
an on-demand check that the live prompts still yield contract-conformant JSON.
"""

from __future__ import annotations

import os
import uuid

import pytest
from app.ai.contracts import validate_response
from app.ai.engine import run_job
from app.ai.llm import LLMClient
from app.config import Settings

_LIVE = os.environ.get("SHIELD_LIVE_SMOKE") == "1" and bool(os.environ.get("ANTHROPIC_API_KEY"))

pytestmark = pytest.mark.skipif(
    not _LIVE,
    reason="live smoke test: set SHIELD_LIVE_SMOKE=1 and ANTHROPIC_API_KEY to run",
)


# The smallest plausible input payload per job. Kept tiny so a real call is cheap.
_JOB_INPUTS: dict[str, dict] = {
    "csf_score": {
        "tiers": ["high"],
        "subcategories": [
            {
                "tier": "high",
                "subcategory_code": "GV.OC-01",
                "in_scope": True,
                "has_evidence": False,
                "rationale": None,
                "questionnaire_tier": 2,
                "questionnaire_notes": None,
            }
        ],
    },
    "zt_score": {
        "framework": "cisa_ztmm_2_0",
        "capabilities": ["CISA.ID.01"],
        "answers": {"CISA.ID.01": {"notes": None, "current": 2}},
    },
    "mitre_map": {"capability_list": ["CrowdStrike Falcon"], "technique_codes": ["T1003"]},
    "risk_synthesize": {
        "findings": [
            {
                "source": "coverage_finding",
                "source_id": "T1003",
                "kind": "attack",
                "label": "ATT&CK T1003: gap",
            }
        ],
        "valid_techniques": ["T1003"],
        "valid_controls": ["GV.OC-01"],
    },
    "tech_debt_extract": {
        "rows": [{"name": "CrowdStrike Falcon", "annual_cost_usd": "$120,000"}],
        "context": {"source_filename": "inv.csv", "source_mime": "text/csv"},
    },
}


@pytest.fixture()
def db_session(tmp_path):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from app.models.user import User, UserRole
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    url = f"sqlite:///{tmp_path / 'smoke.db'}"
    os.environ["DATABASE_URL"] = url
    api_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    with Session(engine, future=True) as s:
        u = User(email="smoke@example.com", password_hash="x" * 64, role=UserRole.ADMIN)
        s.add(u)
        s.commit()
        s.refresh(u)
        yield s, u.id


@pytest.mark.parametrize("job_name", sorted(_JOB_INPUTS))
def test_live_call_parses_and_validates(db_session, job_name) -> None:
    db, admin_id = db_session
    settings = Settings(
        shield_llm_mode="live",
        shield_llm_provider="anthropic",
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
    )
    llm = LLMClient.from_settings(settings)
    result = run_job(
        db,
        llm,
        job_name,
        inputs=_JOB_INPUTS[job_name],
        requested_by=admin_id,
        client_id=uuid.uuid4(),
    )
    # The registry parser already ran; re-assert the raw text parses too.
    assert result.data is not None
    problems = validate_response(job_name, result.data)
    assert not problems, f"{job_name} live response failed validation: {problems}"
