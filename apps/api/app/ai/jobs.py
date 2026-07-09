"""Built-in AI job definitions (Work Order C1).

Each job is a prompt + a parser. Registered on import. The score/map/synthesize
jobs return DRAFT SUGGESTIONS only; the deterministic math lives in the
per-domain pure functions and is never asked of the model.

The prompt bodies here are the engine-level skeletons. The service phases
(D2/D3/D4/E) refine the exact suggestion schema each job emits; the parser is
`parse_json` so any well-formed JSON suggestion object round-trips.
"""

from __future__ import annotations

from app.ai.contracts import describe_shape
from app.ai.engine import AIJob, parse_json, register_job

# --- Tech Debt extraction (moved behind the registry) ----------------------
# Keeps the historical "extract.capabilities" purpose so existing fixtures and
# llm_calls history stay stable.
from app.tech_debt.extract import (  # noqa: E402  (import after engine to avoid a cycle)
    PROMPT as _TECH_DEBT_PROMPT,
)
from app.tech_debt.extract import (
    PROMPT_VERSION as _TECH_DEBT_PROMPT_VERSION,
)
from app.tech_debt.extract import (
    _parse_response as _parse_tech_debt,
)

register_job(
    AIJob(
        name="tech_debt_extract",
        purpose="extract.capabilities",
        prompt=_TECH_DEBT_PROMPT,
        prompt_version=_TECH_DEBT_PROMPT_VERSION,
        parser=_parse_tech_debt,
    )
)


# --- CSF dimension-score suggestions ---------------------------------------
_CSF_SCORE_PROMPT = f"""You are assisting a Kentro analyst scoring a NIST CSF 2.0
assessment. From the supplied seeded tier list, per-subcategory questionnaire
answers (maturity tier + notes), and evidence flags, SUGGEST a draft only.

The payload's `subcategories` array lists every in-scope (tier, subcategory_code)
row you must score. For EACH one return the five dimension scores — Governance,
Policy and Process, Implementation, Monitoring and Measurement, Continuous
Improvement — each an integer 0, 1, or 2, plus a short "what we found" narrative.
Echo back the SAME `tier` and `subcategory_code` you were given so the score
lands on the right row (the same subcategory can appear under more than one tier).

Do NOT compute totals, maturity levels, roll-ups, gaps, or priorities — those are
calculated by code. Return strictly JSON of this shape:
{describe_shape("csf_score")}
"""

# csf_score can span the full Playbook (hundreds of tier x subcategory rows), so
# it runs on the fast/cheap model at that model's output ceiling (Task S1-A A-3).
# claude-haiku-4-5 caps output at 64000 tokens; requesting more is a live 400
# (caught by the FS-D live smoke run). ~318 scored rows fit comfortably.
register_job(
    AIJob(
        name="csf_score",
        prompt=_CSF_SCORE_PROMPT,
        parser=parse_json,
        model="claude-haiku-4-5",
        max_tokens=64000,
    )
)


# --- Zero Trust current/target suggestions ---------------------------------
_ZT_SCORE_PROMPT = f"""You are assisting a Kentro analyst scoring a Zero Trust
assessment for the stated framework (CISA ZTMM 2.0 or DoD ZTRA). From the
questionnaire answers and evidence, SUGGEST a draft only.

For each capability return a suggested current maturity level and a suggested
target level, on the framework's own scale (CISA 1-4, DoD 1-3), plus a per-pillar
"what we found" narrative. Do NOT compute pillar roll-ups, overall posture, gaps,
or the roadmap — code does that. Return strictly JSON of this shape:
{describe_shape("zt_score")}
"""

register_job(AIJob(name="zt_score", prompt=_ZT_SCORE_PROMPT, parser=parse_json))


# --- MITRE ATT&CK coverage suggestions -------------------------------------
_MITRE_MAP_PROMPT = f"""You are assisting a Kentro analyst mapping a security tool
inventory to the MITRE ATT&CK Enterprise matrix. From the capability list and any
context, SUGGEST a draft only.

For every technique, suggest a coverage status: covered, partial, gap, or
not_applicable. You may ONLY name tools that appear in the supplied capability
list. Do NOT compute coverage percentages — code does that.

Keep the output COMPACT so all techniques fit in a single response:
- For "covered" or "partial": include the relevant detection_tools /
  prevention_tools / response_tools and a ONE-SENTENCE rationale.
- For "gap" or "not_applicable": return ONLY technique_code and status — omit the
  tool arrays and the rationale entirely (there is nothing to cite).

Return strictly JSON of this shape:
{describe_shape("mitre_map")}
"""

# mitre_map runs per-tactic batches (routes/attack.py), so each call emits a
# slice of the 600+ technique matrix, far under claude-haiku-4-5's 64000-token
# output ceiling (requesting more is a live 400; caught by the FS-D smoke run).
register_job(
    AIJob(
        name="mitre_map",
        prompt=_MITRE_MAP_PROMPT,
        parser=parse_json,
        model="claude-haiku-4-5",
        max_tokens=64000,
    )
)


# --- Risk Register synthesis -----------------------------------------------
_RISK_SYNTHESIZE_PROMPT = f"""You are assisting a Kentro analyst drafting a Risk
Register by synthesizing gaps and findings from a client's completed assessments
(ATT&CK coverage gaps plus CSF and/or Zero Trust gaps). SUGGEST a draft only.

For each finding draft one candidate entry: weakness title + description; SHIELD
axis (detection, prevention, or response); the linked ATT&CK techniques and
control references (you may ONLY cite techniques/controls that appear in the
supplied assessments); a likelihood and an impact; compensating controls;
residual risk; and a recommended action with rationale.

Use EXACTLY these lowercase snake_case tokens (no other spelling, casing, or
spacing):
- likelihood: very_low, low, medium, high, very_high
- impact: negligible, minor, moderate, major, catastrophic
- axis: detection, prevention, response
- recommended_action: remediate, mitigate, accept, transfer, avoid

Do NOT set the risk tier — code derives it from likelihood and impact. Return
strictly JSON of this shape:
{describe_shape("risk_synthesize")}
"""

register_job(AIJob(name="risk_synthesize", prompt=_RISK_SYNTHESIZE_PROMPT, parser=parse_json))
