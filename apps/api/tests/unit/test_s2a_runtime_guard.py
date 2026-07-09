"""G-3: assert_safe_for_runtime production + fixture-LLM guard matrix (Task S2-A)."""

from __future__ import annotations

import pytest
from app.config import Settings

_SAFE = {"jwt_signing_secret": "x" * 64, "shield_redaction_mode": "strict"}


@pytest.mark.unit
def test_production_fixture_without_demo_is_rejected() -> None:
    s = Settings(environment="production", shield_llm_mode="fixture", shield_demo="", **_SAFE)
    with pytest.raises(RuntimeError, match="SHIELD_LLM_MODE=fixture is forbidden"):
        s.assert_safe_for_runtime()


@pytest.mark.unit
def test_production_fixture_with_demo_flag_is_allowed() -> None:
    s = Settings(environment="production", shield_llm_mode="fixture", shield_demo="1", **_SAFE)
    s.assert_safe_for_runtime()  # explicit demo opt-in: no raise


@pytest.mark.unit
def test_production_fixture_with_wrong_demo_value_is_rejected() -> None:
    # Only the exact string "1" arms the exemption; anything else keeps the guard.
    s = Settings(environment="production", shield_llm_mode="fixture", shield_demo="true", **_SAFE)
    with pytest.raises(RuntimeError, match="SHIELD_LLM_MODE=fixture is forbidden"):
        s.assert_safe_for_runtime()


@pytest.mark.unit
def test_production_live_mode_is_allowed() -> None:
    s = Settings(environment="production", shield_llm_mode="live", **_SAFE)
    s.assert_safe_for_runtime()  # live in prod is the expected configuration


@pytest.mark.unit
def test_development_fixture_is_allowed() -> None:
    s = Settings(environment="development", shield_llm_mode="fixture", shield_demo="")
    s.assert_safe_for_runtime()  # the guard only bites in production
