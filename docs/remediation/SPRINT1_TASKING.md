# Sprint 1 subagent tasking - Trustworthy core

Lead: FABLE session. Subagents work sequentially on this 2-CPU box. Every task:
work ONLY in the listed files plus new test files; no drive-by refactors; run
`.venv/bin/ruff check <touched>` and `.venv/bin/black <touched>` before finishing;
run the listed pytest subsets and report results verbatim. Additive migrations only.
Typed errors use the existing {reason, message} pattern in app/exceptions.py.

## Task S1-A: AI contract layer (A-1, A-2, A-3 adjusted, A-4, A-5)

Files: apps/api/app/ai/contracts.py (new), ai/jobs.py, ai/engine.py, ai/llm.py,
config.py, routes/csf.py (run-ai payload+apply only), routes/risk.py
(\_enum_or_none and generate warnings only), routes/admin.py (ai-status),
.env.example + docker-compose.yml (model id refs), tests/unit (new + updated).

1. ai/contracts.py: one documented response-shape constant per AI job
   (csf_score, mitre_map, zt_score, risk_synthesize, tech_debt_extract):
   JSON-schema-ish dicts plus a `describe_shape(name)` helper the prompts embed.
2. A-2: rewrite the CSF score prompt to demand {"scores":[{"tier","subcategory_code",
   "governance","policy","implementation","monitoring","improvement","what_we_found"}]}
   (the shape routes/csf.py:1083-1086 consumes). Payload grounding: include the
   seeded tier list plus per-subcategory questionnaire answers (tier + notes) and
   evidence flags, through the existing redaction path (see csf.py:1073-1076 for
   where the payload is built today).
3. A-3: AIJob gains `model: str | None = None`, `max_tokens: int | None = None`.
   Thread through engine.run_job -> LLMClient.invoke -> provider.complete.
   mitre_map and csf_score: model claude-haiku-4-5, max_tokens 128000.
   Others: None (inherit global), max_tokens 16000 default constant.
4. A-4: risk prompt states exact snake_case enum tokens for likelihood/impact;
   \_enum_or_none normalizes (strip, lower, spaces->underscores) before coercion;
   generate response gains warnings: ["N entries had unrecognized likelihood/impact"]
   when coercions fail (count them).
5. A-5: config default shield_llm_model becomes claude-sonnet-5; fix
   .env.example and docker-compose.yml references to claude-opus-4-7.
   ai-status returns SDK importable (bool), key present, mode, global model,
   per-job overrides; missing key/SDK in live mode raises the typed pattern
   mapped to 503 instead of bare RuntimeError.
6. A-1: LLMClient.from_settings (or \_build_provider) eagerly imports anthropic
   when mode==live and raises the same typed configuration error at boot.
7. Tests: contract test per job (render prompt, build a conformant response,
   run through the route's apply loop with a stubbed provider, assert rows change);
   enum normalization tests ("Very Low" -> very_low etc); ai-status three states;
   per-job model/max_tokens threading test; boot import check test (monkeypatch
   import failure). Update any test broken by the new prompt text.

Acceptance: pytest tests/unit -k "contract or llm or ai_status or risk" green;
full unit suite green; a schema-conformant CSF live response applied through the
route changes at least one row (proven by test); no other routes touched.

## Task S1-B: Deliverable targets and export gate (B-1, B-2, B-3)

Files: routes/zt.py (finalize), routes/csf.py (finalize + playbook export),
models/csf_profile.py, new alembic migration (additive), csf exporters (Unscored
fallback), zt exporters (summary line), tests/unit.

1. B-1: finalize resolves per-capability targets from answers plus
   ServiceRequest.zt_target_stage fallback (3 only when neither exists) and calls
   analyze_gaps(cat_fw, stage_map, notes=notes_map, target_stage=..., targets=...)
   exactly as the dashboard endpoint (zt.py:897-907) does. Deliverable summary
   line prints the resolved target.
2. B-2: CSF finalize resolves ServiceRequest.csf_target_tier (fallback 3) and
   passes target_tier into analyze_gaps; summary line prints it.
3. B-3: additive migration adds nullable scored_at (DateTime tz) to
   csf_dimension_scores. Set scored_at=now on every human PATCH and every AI
   apply that touches a row; seeding leaves it null. Playbook export: typed 409
   unless every in-scope row has scored_at AND assessment status is approved
   (message: "N of M rows unscored" or "assessment not approved"). Exporters
   render "Unscored" for null-scored rows if any slip through.
   documents_stale cleared only when the gate passes.
4. Tests: the B-1 regression (target 4, all stages 3, finalize, parse XLSX,
   Gap Plan row count == dashboard total_gap_count); B-2 mirror with tiers;
   B-3 seed->export 409, score-all+approve->200 with no "Unscored" cells;
   migration up/down test if the suite has a pattern for it.

Acceptance: those tests green; full unit suite green; no changes to run-ai code.

## Task S1-C: Extraction honesty + approved tool universe (C-1 rest, C-2, G-2)

Files: routes/tech_debt.py, tech_debt/parsers.py, tech_debt/extract.py,
routes/artifacts.py (allowlist), routes/attack.py (\_client_tool_names + run-ai
warning), apps/web/src/components (TechDebtWorkspace.tsx, intake/Dropzone.tsx,
admin/IntakeDocumentsPanel.tsx accept lists), tests/unit.

1. C-1: extract route returns typed 422 ("No data rows found in this file;
   check that the inventory is on the first sheet with a header row") when
   parse_inventory yields zero data rows, before run_job. Strip the
   {"**truncated**": true} sentinel from rows sent to the model; carry
   truncated=true in the extract response instead.
2. C-2: remove application/vnd.ms-excel from artifacts allowlist and
   parsers SUPPORTED_MIME; uploads of .xls get typed 415 ("Legacy .xls is not
   supported; re-save the file as .xlsx and upload again"); wrap workbook open
   so BadZipFile/InvalidFileException -> same typed 422 as C-1 (not 500);
   remove .xls/vnd.ms-excel from the three frontend accept lists.
3. G-2: \_client_tool_names filters to the latest APPROVED CapabilityList per
   tech-debt service, unioned across the client's services. When empty, the
   run-ai response gains warning: "no approved capability list; mapping will
   cite no tools" (rendered later; API field now).
4. Tests: header-only CSV -> 422; empty sheet -> 422; 501-row file -> 500 items,
   truncated flag, no phantom; .xls upload -> 415; corrupt .xlsx -> 422;
   draft-only lists -> empty universe + warning; approved v2 excludes v1 items.

Acceptance: those tests green; full unit suite green; frontend typecheck+lint
green; no exporter or finalize changes.
