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
