"""Deterministic runtime fixtures for SHIELD_LLM_MODE=fixture.

The old static fixture module was deleted when C-1 removed its
empty-input fabrication path, but nothing replaced RUNTIME registration:
`LLMClient.from_settings()` built a bare FixtureProvider whose registry
was only ever filled inside pytest, so in a running fixture-mode stack
every Run AI endpoint 500ed with "No fixture registered". That directly
contradicted the E-5 banner ("AI suggestions are simulated ...").

These fixtures are INPUT-GROUNDED: every response is derived from the
redacted payload the route actually sent, never from canned demo rows.
An empty input produces an empty (or minimal) response, so the C-1
guarantee - the system cannot invent client data - holds. Values are
seeded from a stable hash of each item's code/name, so runs are
deterministic for demos and e2e tests.

Registered by `LLMClient.from_settings` / `_llm_dep` construction when
the mode is fixture (see llm.py). Tests that register their own
per-purpose fixtures override these (dict assignment replaces them).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.ai.llm import FixtureProvider, LLMResponse

_LIKELIHOODS = ("very_low", "low", "medium", "high", "very_high")
_IMPACTS = ("negligible", "minor", "moderate", "major", "catastrophic")
_AXES = {"attack": "detection", "csf": "prevention", "zt": "response"}


def _stable(code: str, modulus: int) -> int:
    """Deterministic small int derived from a string (stable across runs)."""
    digest = hashlib.sha256(code.encode("utf-8")).digest()
    return digest[0] % modulus


def _resp(data: dict[str, Any]) -> LLMResponse:
    text = json.dumps(data)
    # Rough token accounting so the ai-usage report shows non-zero numbers.
    return LLMResponse(text, input_tokens=len(text) // 8, output_tokens=len(text) // 4)


def _csf_score(payload: dict[str, Any]) -> LLMResponse:
    scores = []
    for row in payload.get("subcategories", []):
        if not isinstance(row, dict):
            continue
        code = str(row.get("subcategory_code") or "")
        tier = row.get("tier")
        if not code or not tier:
            continue
        base = _stable(f"{tier}|{code}", 3)
        scores.append(
            {
                "tier": tier,
                "subcategory_code": code,
                "governance": base,
                "policy": _stable(f"p|{code}", 3),
                "implementation": _stable(f"i|{code}", 3),
                "monitoring": _stable(f"m|{code}", 3),
                "improvement": _stable(f"c|{code}", 3),
                "what_we_found": f"Simulated assessment narrative for {code} (fixture mode).",
            }
        )
    return _resp(
        {"scores": scores, "executive_summary": "Simulated executive summary (fixture mode)."}
    )


def _mitre_map(payload: dict[str, Any]) -> LLMResponse:
    tools = [t for t in payload.get("capability_list", []) if isinstance(t, str)]
    techniques: list[dict[str, Any]] = []
    for code in payload.get("technique_codes", []):
        if not isinstance(code, str):
            continue
        if not tools:
            techniques.append({"technique_code": code, "status": "gap"})
            continue
        bucket = _stable(code, 4)
        if bucket == 0:
            tool = tools[_stable(f"t|{code}", len(tools))]
            techniques.append(
                {
                    "technique_code": code,
                    "status": "covered",
                    "detection_tools": [tool],
                    "prevention_tools": [],
                    "response_tools": [],
                    "rationale": f"Simulated: {tool} maps to {code} (fixture mode).",
                }
            )
        elif bucket == 1:
            tool = tools[_stable(f"u|{code}", len(tools))]
            techniques.append(
                {
                    "technique_code": code,
                    "status": "partial",
                    "detection_tools": [tool],
                    "prevention_tools": [],
                    "response_tools": [],
                    "rationale": f"Simulated partial coverage of {code} (fixture mode).",
                }
            )
        else:
            techniques.append({"technique_code": code, "status": "gap"})
    return _resp(
        {
            "techniques": techniques,
            "executive_summary": "Simulated ATT&CK coverage summary (fixture mode).",
            "top_blind_spots": [t["technique_code"] for t in techniques[:3]],
        }
    )


def _zt_score(payload: dict[str, Any]) -> LLMResponse:
    framework = str(payload.get("framework") or "")
    max_stage = 3 if "dod" in framework.lower() else 4
    answers = payload.get("answers") if isinstance(payload.get("answers"), dict) else {}
    capabilities = []
    for code in payload.get("capabilities", []):
        if not isinstance(code, str):
            continue
        prior = answers.get(code, {}) if isinstance(answers, dict) else {}
        current = prior.get("current") if isinstance(prior, dict) else None
        if not isinstance(current, int) or not 1 <= current <= max_stage:
            current = 1 + _stable(code, max_stage)
        capabilities.append(
            {"code": code, "current": current, "target": min(current + 1, max_stage)}
        )
    return _resp(
        {
            "capabilities": capabilities,
            "pillar_narratives": {},
            "executive_summary": "Simulated Zero Trust summary (fixture mode).",
            "roadmap_summary": "Simulated roadmap summary (fixture mode).",
        }
    )


def _risk_synthesize(payload: dict[str, Any]) -> LLMResponse:
    valid_techniques = set(payload.get("valid_techniques", []))
    entries = []
    for finding in payload.get("findings", [])[:25]:
        if not isinstance(finding, dict):
            continue
        source_id = str(finding.get("source_id") or "")
        kind = str(finding.get("kind") or "attack")
        label = str(finding.get("label") or source_id)
        if not source_id:
            continue
        entries.append(
            {
                "title": f"Simulated risk: {label}",
                "description": f"Fixture-mode draft synthesized from finding {source_id}.",
                "axis": _AXES.get(kind, "detection"),
                "linked_techniques": [source_id] if source_id in valid_techniques else [],
                "linked_controls": [] if source_id in valid_techniques else [source_id],
                "likelihood": _LIKELIHOODS[_stable(f"l|{source_id}", len(_LIKELIHOODS))],
                "impact": _IMPACTS[_stable(f"i|{source_id}", len(_IMPACTS))],
                "compensating_controls": "None recorded (simulated).",
                "residual_risk": "Simulated residual risk statement.",
                "recommended_action": "remediate",
                "rationale": "Simulated rationale (fixture mode).",
                "source": str(finding.get("source") or "coverage_finding"),
                "source_id": source_id,
            }
        )
    return _resp({"entries": entries})


def _extract_capabilities(payload: dict[str, Any]) -> LLMResponse:
    """Input-grounded extraction: one item per parsed row, values passed
    through untouched. Empty input yields zero items (the route 422s before
    the call anyway), so nothing is ever fabricated (C-1)."""
    name_keys = ("name", "tool", "product", "capability", "application", "software")
    items = []
    for idx, row in enumerate(payload.get("rows", [])):
        if not isinstance(row, dict):
            continue
        name = None
        for key in name_keys:
            for k, v in row.items():
                if key in str(k).lower() and isinstance(v, str) and v.strip():
                    name = v.strip()
                    break
            if name:
                break
        if name is None:
            # Fall back to the first non-empty string cell.
            for v in row.values():
                if isinstance(v, str) and v.strip():
                    name = v.strip()
                    break
        if name is None:
            continue

        def _field(*keys: str, row: dict[str, Any] = row) -> Any:
            for key in keys:
                for k, v in row.items():
                    if key in str(k).lower() and v not in (None, ""):
                        return v
            return None

        items.append(
            {
                "name": name,
                "vendor": _field("vendor", "publisher", "manufacturer"),
                "category": _field("category", "type"),
                "function": _field("function", "purpose", "use"),
                "annual_cost_usd": _field("cost", "spend", "price"),
                "license_count": _field("license", "seat", "count", "qty"),
                "notes": "Simulated extraction (fixture mode).",
                "confidence_pct": 80,
                "source_row_index": idx,
            }
        )
    return _resp({"items": items})


def register_demo_fixtures(provider: FixtureProvider) -> None:
    """Register input-grounded fixtures for every AI job purpose."""
    provider.register("csf_score", _csf_score)
    provider.register("mitre_map", _mitre_map)
    provider.register("zt_score", _zt_score)
    provider.register("risk_synthesize", _risk_synthesize)
    provider.register("extract.capabilities", _extract_capabilities)
