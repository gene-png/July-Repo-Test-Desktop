"""Documented AI response shapes (Sprint 1 Task S1-A).

One JSON-schema-ish constant per registered AI job describes the *exact* JSON the
model must return. The deterministic apply loops in the routes consume exactly
these shapes; the prompts in ``app/ai/jobs.py`` embed ``describe_shape(name)`` so
the prompt text and the code that parses the response can never silently drift
apart (that drift was the csf_score bug this task fixes).

These are lightweight, dependency-free descriptors (nested dict / list literals),
not a validation library. Each leaf string documents the allowed type or the
closed token set (e.g. ``"int 0-2"`` or ``"detection|prevention|response"``). A
list value carries a single element that documents the shape of every item.
``describe_shape`` renders one shape to a compact, prompt-friendly JSON block.
"""

from __future__ import annotations

import json
from typing import Any

# --- csf_score: consumed by routes/csf.py run_ai apply loop -----------------
# Keyed in the route by f"{tier}|{subcategory_code}"; each dimension is clamped
# to 0..2; what_we_found is a free-text narrative.
CSF_SCORE: dict[str, Any] = {
    "scores": [
        {
            "tier": "high|moderate|low",
            "subcategory_code": "str, e.g. GV.OC-01",
            "governance": "int 0-2",
            "policy": "int 0-2",
            "implementation": "int 0-2",
            "monitoring": "int 0-2",
            "improvement": "int 0-2",
            "what_we_found": "str, one short narrative sentence",
        }
    ],
    "executive_summary": "str (optional)",
}

# --- mitre_map: consumed by routes/attack.py run-ai apply loop ---------------
MITRE_MAP: dict[str, Any] = {
    "techniques": [
        {
            "technique_code": "str, e.g. T1003",
            "status": "covered|partial|gap|not_applicable",
            "detection_tools": ["str (covered/partial only; omit otherwise)"],
            "prevention_tools": ["str (covered/partial only; omit otherwise)"],
            "response_tools": ["str (covered/partial only; omit otherwise)"],
            "rationale": "str, one sentence (covered/partial only; omit otherwise)",
        }
    ],
    "executive_summary": "str (optional)",
    "top_blind_spots": ["str (optional)"],
}

# --- zt_score: consumed by routes/zt.py run-ai apply loop --------------------
ZT_SCORE: dict[str, Any] = {
    "capabilities": [
        {
            "code": "str",
            "current": "int (CISA 1-4 / DoD 1-3)",
            "target": "int (CISA 1-4 / DoD 1-3)",
        }
    ],
    "pillar_narratives": {"<pillar_code>": "str"},
    "executive_summary": "str (optional)",
    "roadmap_summary": "str (optional)",
}

# --- risk_synthesize: consumed by routes/risk.py generate apply loop ---------
# likelihood/impact/axis/recommended_action are the lowercase snake_case tokens
# of the enums in app/risk/engine.py; the tier is derived in code, never set here.
RISK_SYNTHESIZE: dict[str, Any] = {
    "entries": [
        {
            "title": "str",
            "description": "str",
            "axis": "detection|prevention|response",
            "linked_techniques": ["str (must appear in the supplied assessments)"],
            "linked_controls": ["str (must appear in the supplied assessments)"],
            "likelihood": "very_low|low|medium|high|very_high",
            "impact": "negligible|minor|moderate|major|catastrophic",
            "compensating_controls": "str",
            "residual_risk": "str",
            "recommended_action": "remediate|mitigate|accept|transfer|avoid",
            "rationale": "str",
            "source": "coverage_finding|questionnaire_response",
            "source_id": "str",
        }
    ],
}

# --- tech_debt_extract: consumed by app/tech_debt/extract.py _parse_response -
TECH_DEBT_EXTRACT: dict[str, Any] = {
    "items": [
        {
            "name": "str, short capability name",
            "vendor": "str or null",
            "category": "str or null (e.g. CNAPP, EDR, SIEM, IAM, GRC)",
            "function": "str or null, one-line function the capability serves",
            "annual_cost_usd": "number or null",
            "license_count": "integer or null",
            "notes": "str or null",
            "confidence_pct": "integer 0-100",
            "source_row_index": "integer index into the supplied rows[]",
        }
    ],
}

_SHAPES: dict[str, dict[str, Any]] = {
    "csf_score": CSF_SCORE,
    "mitre_map": MITRE_MAP,
    "zt_score": ZT_SCORE,
    "risk_synthesize": RISK_SYNTHESIZE,
    "tech_debt_extract": TECH_DEBT_EXTRACT,
}


def get_shape(name: str) -> dict[str, Any]:
    """Return the documented response shape for a registered job name."""
    try:
        return _SHAPES[name]
    except KeyError as exc:
        raise KeyError(
            f"No response shape documented for {name!r}. Known: {sorted(_SHAPES)}"
        ) from exc


def describe_shape(name: str) -> str:
    """Render a job's response shape as a compact JSON block for a prompt."""
    return json.dumps(get_shape(name), indent=2)


# --- A-6: lightweight structural validation --------------------------------
# Closed token sets mirrored from the shape descriptors above. Kept here (not
# imported from the domain enums) so contracts.py stays dependency-free.
_CSF_TIERS = {"high", "moderate", "low"}
_MITRE_STATUS = {"covered", "partial", "gap", "not_applicable"}


def validate_response(name: str, data: Any) -> list[str]:
    """Structurally validate a parsed AI response against its documented shape.

    Returns a list of human-readable problems; an empty list means the response
    is structurally sound. This is a lightweight, dependency-free check (no
    jsonschema): it verifies the top-level container keys exist and are lists,
    and that closed-token fields carry allowed values. It deliberately does NOT
    police fields the apply loops already coerce/clamp (dimension ints, tool
    lists, per-entry likelihood/impact which the risk route tolerantly maps and
    counts as warnings — see Task S1-A A-4). The goal is to reject a wholesale
    wrong-shape response (a list where an object was required, a missing
    container, a garbage status token) before the deterministic apply loop runs.
    """
    if not isinstance(data, dict):
        return [f"{name} response must be a JSON object, got {type(data).__name__}"]

    problems: list[str] = []
    if name == "csf_score":
        scores = data.get("scores")
        if not isinstance(scores, list):
            problems.append("'scores' must be a list")
        else:
            for i, s in enumerate(scores):
                if not isinstance(s, dict):
                    problems.append(f"scores[{i}] must be an object")
                    continue
                if not s.get("subcategory_code"):
                    problems.append(f"scores[{i}] is missing subcategory_code")
                tier = s.get("tier")
                if tier is not None and tier not in _CSF_TIERS:
                    problems.append(f"scores[{i}].tier {tier!r} not in {sorted(_CSF_TIERS)}")
    elif name == "mitre_map":
        techniques = data.get("techniques")
        if not isinstance(techniques, list):
            problems.append("'techniques' must be a list")
        else:
            for i, t in enumerate(techniques):
                if not isinstance(t, dict):
                    problems.append(f"techniques[{i}] must be an object")
                    continue
                if not t.get("technique_code"):
                    problems.append(f"techniques[{i}] is missing technique_code")
                st = t.get("status")
                if st is not None and st not in _MITRE_STATUS:
                    problems.append(f"techniques[{i}].status {st!r} not in {sorted(_MITRE_STATUS)}")
    elif name == "zt_score":
        caps = data.get("capabilities")
        if not isinstance(caps, list):
            problems.append("'capabilities' must be a list")
        else:
            for i, c in enumerate(caps):
                if not isinstance(c, dict):
                    problems.append(f"capabilities[{i}] must be an object")
                    continue
                if not c.get("code"):
                    problems.append(f"capabilities[{i}] is missing code")
        pn = data.get("pillar_narratives")
        if pn is not None and not isinstance(pn, dict):
            problems.append("'pillar_narratives' must be an object")
    elif name == "risk_synthesize":
        entries = data.get("entries")
        if not isinstance(entries, list):
            problems.append("'entries' must be a list")
        else:
            for i, e in enumerate(entries):
                if not isinstance(e, dict):
                    problems.append(f"entries[{i}] must be an object")
    elif name == "tech_debt_extract":
        items = data.get("items")
        if not isinstance(items, list):
            problems.append("'items' must be a list")
    else:
        problems.append(f"no validator registered for job {name!r}")
    return problems
