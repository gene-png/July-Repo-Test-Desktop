"""AI contract layer: response shapes, prompt embedding, per-job provider
overrides, and the boot-time configuration check (Sprint 1 Task S1-A)."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from app.ai import contracts, jobs  # noqa: F401  (jobs import registers the jobs)
from app.ai.contracts import describe_shape, get_shape
from app.ai.engine import get_job, registered_jobs, run_job
from app.ai.llm import (
    DEFAULT_MAX_TOKENS,
    LLMClient,
    LLMConfigurationError,
    LLMResponse,
)
from app.config import Settings
from app.models.llm_call import LLMCall
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

_JOB_NAMES = ("csf_score", "mitre_map", "zt_score", "risk_synthesize", "tech_debt_extract")


@pytest.fixture()
def db_session(tmp_path) -> Iterator[Session]:
    url = f"sqlite:///{tmp_path / 'shield-contracts.db'}"
    os.environ["DATABASE_URL"] = url
    api_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = TestSession()
    try:
        yield db
    finally:
        db.close()


class _RecordingProvider:
    """Captures the model/max_tokens the client threaded into each call."""

    name = "recording"
    model = "global-model-1"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        prompt: str,
        payload: dict[str, Any],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        self.calls.append(
            {"purpose": payload.get("__purpose__"), "model": model, "max_tokens": max_tokens}
        )
        return LLMResponse("{}")


# --- Response shapes / prompt embedding -------------------------------------


@pytest.mark.unit
def test_every_job_has_a_documented_shape() -> None:
    for name in _JOB_NAMES:
        shape = get_shape(name)
        assert isinstance(shape, dict) and shape
        # describe_shape renders valid JSON.
        json.loads(describe_shape(name))


@pytest.mark.unit
def test_prompts_embed_their_documented_shape() -> None:
    # Each prompt (except tech_debt, whose prompt is owned elsewhere) embeds the
    # rendered contract, so prompt text can't drift from the parser's shape.
    for name in ("csf_score", "mitre_map", "zt_score", "risk_synthesize"):
        prompt = get_job(name).prompt
        assert describe_shape(name) in prompt, name


@pytest.mark.unit
def test_csf_shape_matches_route_apply_keys() -> None:
    item = get_shape("csf_score")["scores"][0]
    # These are exactly the keys routes/csf.py run_ai reads.
    assert {
        "tier",
        "subcategory_code",
        "governance",
        "policy",
        "implementation",
        "monitoring",
        "improvement",
        "what_we_found",
    } <= set(item)


@pytest.mark.unit
def test_risk_shape_uses_snake_case_enum_tokens() -> None:
    item = get_shape("risk_synthesize")["entries"][0]
    assert item["likelihood"] == "very_low|low|medium|high|very_high"
    assert item["impact"] == "negligible|minor|moderate|major|catastrophic"


# --- Per-job model / max_tokens threading (A-3) -----------------------------


@pytest.mark.unit
def test_per_job_overrides_thread_to_provider(db_session) -> None:
    provider = _RecordingProvider()
    client = LLMClient(provider)

    run_job(
        db_session,
        client,
        "csf_score",
        inputs={"tiers": ["high"], "subcategories": []},
        requested_by=uuid.uuid4(),
    )
    run_job(
        db_session,
        client,
        "risk_synthesize",
        inputs={"findings": []},
        requested_by=uuid.uuid4(),
    )

    by_purpose = {c["purpose"]: c for c in provider.calls}
    # csf_score overrides both.
    assert by_purpose["csf_score"]["model"] == "claude-haiku-4-5"
    assert by_purpose["csf_score"]["max_tokens"] == 128000
    # risk_synthesize inherits (None threaded through).
    assert by_purpose["risk_synthesize"]["model"] is None
    assert by_purpose["risk_synthesize"]["max_tokens"] is None

    # The llm_calls row records the EFFECTIVE model for the overridden job.
    rows = {r.purpose: r for r in db_session.execute(select(LLMCall)).scalars().all()}
    assert rows["csf_score"].model == "claude-haiku-4-5"
    assert rows["risk_synthesize"].model == "global-model-1"


@pytest.mark.unit
def test_overrides_declared_on_the_two_big_jobs_only() -> None:
    overridden = {
        n for n in registered_jobs() if get_job(n).model or get_job(n).max_tokens is not None
    }
    assert overridden == {"csf_score", "mitre_map"}
    for n in ("csf_score", "mitre_map"):
        assert get_job(n).model == "claude-haiku-4-5"
        assert get_job(n).max_tokens == 128000
    # Sanity: the shared default the others fall back to.
    assert DEFAULT_MAX_TOKENS == 16000


# --- Boot-time configuration check (A-1) ------------------------------------


def _live_settings(*, key: str) -> Settings:
    return Settings(
        shield_llm_mode="live",
        shield_llm_provider="anthropic",
        shield_llm_model="claude-sonnet-5",
        anthropic_api_key=key,
    )


@pytest.mark.unit
def test_from_settings_live_missing_key_raises_typed() -> None:
    with pytest.raises(LLMConfigurationError) as ei:
        LLMClient.from_settings(_live_settings(key=""))
    assert ei.value.reason == "missing_api_key"
    assert "ANTHROPIC_API_KEY" in ei.value.message


@pytest.mark.unit
def test_from_settings_live_missing_sdk_raises_typed(monkeypatch) -> None:
    from app.ai import llm

    monkeypatch.setattr(llm, "anthropic_sdk_available", lambda: False)
    with pytest.raises(LLMConfigurationError) as ei:
        LLMClient.from_settings(_live_settings(key="sk-present"))
    assert ei.value.reason == "sdk_unavailable"


@pytest.mark.unit
def test_from_settings_fixture_builds_without_key() -> None:
    client = LLMClient.from_settings(Settings(shield_llm_mode="fixture"))
    assert client.provider.name == "fixture"
