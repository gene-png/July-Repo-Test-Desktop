# SHIELD Remediation Handoff

Date: July 9, 2026
Branch: remediation/fable (not pushed, per instructions)
Base: 5161996 (main). Four commits: a932911 (Sprint 0), 2b7bc51 (Sprint 1), 243f812 (Sprint 2), 6c859dc (Sprint 3), plus this handoff commit.
Full detail per fix, with evidence, lives in FABLE_REMEDIATION_PLAN.md (Findings, Actions, Build Plan, Live Validation, Evidence).

## Summary of fixes completed

All 45 fixes from SHIELD_Remediation_Plan_2.docx were verified against this checkout first; several had partially landed and a few premises had drifted. Everything actionable was implemented:

- Workstream A (live AI): SDK boot check, CSF prompt/parser reconciliation with grounded payloads, per-job model/max_tokens with the Haiku/Sonnet split (streaming retained instead of chunking), risk enum normalization with warnings, valid default model (claude-sonnet-5, verified current), deep ai-status, contract layer (ai/contracts.py) with per-route response validation and env-gated live smoke tests.
- Workstream B (deliverable integrity): ZT and CSF finalize honor engagement targets; playbook export hard-blocked until scored and approved (scored_at migration); full gap lists in XLSX with honest "Top 20 of N" narratives; ZT roadmap and answers in exports; per-item consolidation plan; prioritized ATT&CK gap ordering; Risk DOCX 5x5 matrix; one filename convention.
- Workstream C (extraction honesty): typed 422 on empty input before any LLM call; .xls rejected everywhere; multi-sheet parsing with a parse report; tolerant money/count parsing; wrong-shape responses 502 with no empty versions; content sniffing and size caps; storage timeouts with 503-vs-410 distinction; tenant-checked evidence links and sha256 upload dedup.
- Workstream D (navigation): cards always link; read-only post-submit views with live message threads; client detail pages; real inbox; admin client switcher (with working inline pickers); typed error messages with a Finish-intake link; all six potholes; dev preview gated; real Active Work page.
- Workstream E (hardening): whole-call AI timeouts mapped to 504; DB connections released during AI calls; durable llm_calls audit rows; advisory locks and open-draft guards; persisted ZT/ATT&CK narratives; truthful simulation banner and badge; container-safe seed loaders.
- Workstream F: CSF auto-seed, ZT/ATT&CK auto-create, and full risk register governance (edit, lock, soft delete, approve, regenerate-preserves-locked, source-approval gate, target-tier harvest).
- Workstream G: no release fiction, approved-lists-only ATT&CK tool universe, production refuses fixture mode without SHIELD_DEMO=1.
- Workstream H: retracted auth-control claims (D-017 defers to the MFA package), rate limiting, tested backup/restore (drill PASS), documentation truth pass, per-tenant AI usage and cost report, redaction preview with per-client acknowledgment before live egress, admin audit viewer, CSF action plan (POA&M).
- Sprint 0 (not in the source plan): the frontend was unbuildable on arrival. Dependabot merges had corrupted pnpm-lock.yaml and mixed incompatible majors (Next 16 with React 18). Restored the coherent set (Next 14.2.15 line) per the plan's defer-majors decision, added the Playwright harness from scratch (none existed), and removed committed test artifacts.
- Two bugs found by e2e QA and fixed: fixture mode had no runtime fixtures (every demo Run AI 500ed; now input-grounded deterministic fixtures in app/ai/demo_fixtures.py) and the risk gate rejected RELEASED sources (seeded clients could never generate a register).

## Sprints completed

Sprint 0 (baseline repair), Sprint 1 (trustworthy core), Sprint 2 (solid operations), Sprint 3 (complete deliverables and truth). One commit per sprint, each gated on the full validation suite.

## Subagents used

Ten Opus implementation/QA subagents under FABLE lead orchestration: S1-A (AI contracts), S1-B (deliverable gates), S1-C (extraction honesty), S1-QA; S2-A (runtime hardening), S2-B (extraction robustness), S2-C (contracts/persistence), S2-D (frontend), S2-QA (interrupted by the account spend limit; completed by the lead); S3-A (exports), S3-B (risk governance), S3-C (ops/docs), S3-D (frontend/audit viewer), S3-QA. Five read-only audit agents verified the 45 findings against the checkout before planning. The lead reviewed every diff, ran every gate, and made the final QA-driven fixes directly.

## Files changed

169 files changed since main (see `git diff --stat 5161996..HEAD`): apps/api (routes, ai, tech_debt, storage, exporters, models, 8 new migrations 0029-0036, 30+ new/updated test files), apps/web (navigation, workspaces, risk UI, audit viewer, proxies), e2e (4 spec files, helpers), scripts (backup/restore/drill, e2e stack), docs (architecture, runbooks, remediation taskings), Makefile, README, DECISIONS, CHANGELOG, BUILD_REPORT.

## Tests run

- API unit suite: 628 tests, 0 failures, 0 errors, 5 skips (the env-gated live smoke tests, which require SHIELD_LIVE_SMOKE=1 plus a real ANTHROPIC_API_KEY). Baseline at arrival was 414 tests; 214 added.
- Static: ruff, black, bandit, prettier, eslint, tsc all clean; Next production build green.
- Restore drill: PASS against the live local Postgres (backup, drop, restore, marker survives).

## Playwright validation performed

19 e2e tests, all passing against the real local stack (Postgres/Redis/MinIO in Docker, migrated to head, seeded demo data, uvicorn API, next start web): smoke (2), Sprint 1 (extraction error pill through the UI, .xls rejection, export gate 409), Sprint 2 (client reply click-path, admin switcher recovery, banner copy, simulated pill after a real UI Run AI, preview-no-audit-row, friendly intake errors with link), Sprint 3 (risk governance gate plus the full generate-edit-lock-regenerate-approve-export loop with artifact download, auth redirect, dev-preview gating, Active Work links, Complete copy with no release fiction, audit viewer with CSV). Traces retained on failure during development; final suite green twice consecutively.

## Issues still open

- Live-mode smoke against the real Anthropic API was not run (no API key in this environment). The env-gated test module is ready: SHIELD_LIVE_SMOKE=1 ANTHROPIC_API_KEY=... pytest apps/api/tests/unit/test_live_smoke.py.
- The Postgres advisory lock gates entry to concurrent AI runs but does not span the whole run, because E-1 releases the DB connection during the provider call. Near-simultaneous duplicate starts are blocked; a second run started mid-call proceeds. A session-scoped lock or a DB status flag would close this; noted as a deliberate trade-off.
- Real two-transaction advisory-lock contention is untested (the unit suite runs SQLite); verify once an integration environment with Postgres-backed tests exists.
- CI (.github/workflows/ci.yml) still runs unit tests only; the Playwright suite and the restore drill run locally via documented commands but are not yet CI jobs.
- The rate limiter uses in-memory buckets when Redis is unreachable, which is per-process; fine for the single-process deployment, revisit for replicas.

## Risks and assumptions

- Framework majors were rolled back, not fixed forward; Dependabot will likely re-open those PRs. The plan's guidance stands: do the Next/React/Tailwind majors as one dedicated pass with the e2e suite as the net, never during fix sprints. Consider disabling auto-merge for majors; the broken-lockfile incident came from merging Dependabot branches into each other.
- Model IDs (claude-sonnet-5 global, claude-haiku-4-5 for mitre_map/csf_score) were verified current against the platform docs on July 9, 2026 and are env/registry overridable.
- Demo fixtures are input-grounded by design; if a richer canned demo is ever wanted, key it to an explicit flag, never to empty input (C-1).
- .env.e2e contains only local dev values (no secrets) and drives the local validation stack via scripts/e2e-stack.sh.

## Recommended next sprint

1. Turn the local gates into CI: a compose-based job running alembic upgrade, seed, the Playwright suite, and make restore-drill.
2. Live-mode verification: run the gated smoke tests with a real key, then a paid-engagement dry run of all five jobs on a demo tenant, reviewing llm_calls costs in /admin/ai-usage.
3. The deferred auth package (D-017): refresh-token rotation and revocation first, then idle timeout and forced re-auth, then MFA; the doc claims are already retracted so the work is cleanly scoped.
4. The framework-majors bundle as its own e2e-netted pass.
