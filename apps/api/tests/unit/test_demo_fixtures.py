"""Runtime fixtures for fixture mode (QA finding, Sprint 3).

A fixture-mode stack must simulate every AI job from the caller's own
payload - never canned rows (C-1) and never a KeyError 500 (E-5).
"""

from __future__ import annotations

import json

import pytest
from app.ai.demo_fixtures import register_demo_fixtures
from app.ai.llm import FixtureProvider, LLMClient
from app.config import Settings


@pytest.mark.unit
def test_from_settings_fixture_mode_registers_all_job_purposes() -> None:
    client = LLMClient.from_settings(Settings(shield_llm_mode="fixture"))
    assert isinstance(client.provider, FixtureProvider)
    for purpose in (
        "csf_score",
        "mitre_map",
        "zt_score",
        "risk_synthesize",
        "extract.capabilities",
    ):
        response = client.provider.complete("", {"__purpose__": purpose})
        assert json.loads(response.content) is not None


@pytest.mark.unit
def test_fixtures_are_input_grounded_and_deterministic() -> None:
    provider = FixtureProvider()
    register_demo_fixtures(provider)

    # Empty inputs yield empty outputs: nothing is fabricated (C-1).
    empty = json.loads(
        provider.complete("", {"__purpose__": "extract.capabilities", "rows": []}).content
    )
    assert empty["items"] == []
    no_findings = json.loads(
        provider.complete("", {"__purpose__": "risk_synthesize", "findings": []}).content
    )
    assert no_findings["entries"] == []

    # Grounded: every response element echoes a caller-supplied code.
    payload = {
        "__purpose__": "csf_score",
        "tiers": ["high"],
        "subcategories": [{"tier": "high", "subcategory_code": "GV.OC-01"}],
    }
    first = json.loads(provider.complete("", payload).content)
    second = json.loads(provider.complete("", payload).content)
    assert first == second  # deterministic
    assert first["scores"][0]["subcategory_code"] == "GV.OC-01"
    assert first["scores"][0]["tier"] == "high"
    assert all(0 <= first["scores"][0][d] <= 2 for d in ("governance", "policy", "implementation"))


@pytest.mark.unit
def test_mitre_fixture_all_gaps_without_tools_and_extract_maps_rows() -> None:
    provider = FixtureProvider()
    register_demo_fixtures(provider)

    no_tools = json.loads(
        provider.complete(
            "",
            {
                "__purpose__": "mitre_map",
                "capability_list": [],
                "technique_codes": ["T1003", "T1005"],
            },
        ).content
    )
    assert {t["status"] for t in no_tools["techniques"]} == {"gap"}
    assert {t["technique_code"] for t in no_tools["techniques"]} == {"T1003", "T1005"}

    extract = json.loads(
        provider.complete(
            "",
            {
                "__purpose__": "extract.capabilities",
                "rows": [
                    {"Tool Name": "Splunk", "Vendor": "Splunk Inc", "Annual Cost": "$100,000"}
                ],
            },
        ).content
    )
    assert len(extract["items"]) == 1
    assert extract["items"][0]["name"] == "Splunk"
    assert extract["items"][0]["vendor"] == "Splunk Inc"
