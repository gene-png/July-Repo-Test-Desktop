# FABLE Remediation Plan - SHIELD

Repository: gene-png/July-Repo-Test-Desktop (SHIELD by Kentro v3.0)
Source document: SHIELD_Remediation_Plan_2.docx (Revision 3, July 9, 2026)
Baseline commit: 5161996
Plan authored: July 9, 2026, after a full verification pass of all 45 fixes against this exact checkout.

## How this plan differs from the source document

The remediation document was written against an older checkout ("SHIELD062626"). This repo has drifted in both directions since then. Some fixes already landed (the anthropic SDK is declared, the provider streams with max_tokens=128000, the fixture-fabrication module was deleted, Tech Debt exports already carry overlap and consolidation content). Meanwhile a chain of Dependabot merges broke the frontend build entirely: pnpm-lock.yaml has duplicated mapping keys and cannot be parsed, and the web manifests carry an incoherent mix (Next 16.2.10 with React 18.3.1 and @types/react 19.2.17). The e2e directory is empty; no Playwright suite exists anywhere in the repo despite the source document referencing s5/s7/s8 specs.

Every finding below was verified against this checkout with current file and line references. A Sprint 0 (baseline repair) was added; it is a precondition for everything else.

## Standing assumptions and decisions

1. Framework majors are reverted, not adopted. The source plan defers the Next 15/16, React 19, Tailwind 4 bundle and says never to do it during fix sprints. The Dependabot merges that half-applied it are rolled back to the coherent set from the initial commit (Next 14.2.15, React 18.3.1, Tailwind 3.4.13, eslint 8.57.1). The lockfile is restored from the same commit.
2. A-3 keeps streaming instead of chunking. The current provider already streams with max_tokens=128000, which removes the truncation failure the source plan's chunking addressed. Chunking (effort L) is not re-implemented. What is implemented from A-3: per-job `model` and `max_tokens` fields on the AIJob registry plus the decided Haiku/Sonnet split. This honors the Section 8 model decision at a fraction of the risk. Chunking remains a backlog option if live cost/latency demands it.
3. Model IDs verified against the Claude Platform docs on July 9, 2026: global default `claude-sonnet-5` (current Sonnet); `claude-haiku-4-5` for the two high-volume structured jobs (mitre_map, csf_score). Both overridable by env (global) and registry (per job).
4. Live smoke tests are env-gated (SHIELD_LIVE_SMOKE=1 plus a real key) and are NOT run in this engagement; no Anthropic API key is present in this environment. Contract tests with mocked providers are the verification vehicle.
5. Playwright runs against a locally started stack: Postgres, MinIO and Redis in Docker; the API via uvicorn from the venv; the web app via next start. The container has Playwright 1.56.0 with preinstalled Chromium.
6. No push to remote. One commit per completed sprint.
7. H-4's CHANGELOG claim was checked and is not present in this repo (no duplicate [3.0.0] headings exist); that sub-item is dropped. The duplicate D-015 in DECISIONS.md is real and is fixed.
8. E-3's instruction to copy the CSF open-draft guard could not be followed literally; the guard does not exist in this checkout's CSF create route either. The guard is written once and applied to all three services.

---

## F - Findings

Legend: BROKEN = defect verified present. PARTIAL = some of the fix landed, rest verified missing. FIXED = already resolved in this checkout, no action. NEW = not in the source document, found during baseline verification.

### Sprint 0 findings (NEW - baseline is broken)

| ID   | Sev      | Status | Finding                                                                                                                                                                                       | Evidence                                                   |
| ---- | -------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| S0-1 | CRITICAL | NEW    | pnpm-lock.yaml is unparseable (duplicated mapping key at line 839); `pnpm install --frozen-lockfile` fails, so the web app cannot install, lint, typecheck or build                           | ERR_PNPM_BROKEN_LOCKFILE                                   |
| S0-2 | CRITICAL | NEW    | Web manifests incoherent from Dependabot merges: next 16.2.10 + react 18.3.1 + @types/react 19.2.17 + tailwindcss 4.3.2 (no v4 config migration) + eslint 10.6.0 + eslint-config-next 14.2.15 | apps/web/package.json, packages/design-system/package.json |
| S0-3 | HIGH     | NEW    | No Playwright suite exists: e2e/ contains only .gitkeep, no playwright.config, no @playwright/test dependency                                                                                 | e2e/, package.json files                                   |
| S0-4 | MEDIUM   | NEW    | Leftover test-run artifacts committed to the repo show 2 failing unit tests in the last recorded run                                                                                          | apps/api/\_pytest_out.txt, apps/api/\_run2.txt             |
| S0-5 | LOW      | NEW    | API requires Python >=3.12 (pyproject); baseline verified with a 3.12 venv                                                                                                                    | apps/api/pyproject.toml                                    |

### Workstream A - Live AI

| ID  | Sev      | Status   | Finding                                                                                                                                                                                                                                            | Evidence                                                                         |
| --- | -------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| A-1 | CRITICAL | PARTIAL  | anthropic>=0.40 is declared (fixed), but no boot-time import check exists; a live container that cannot import the SDK still fails at first click                                                                                                  | pyproject.toml:25; app/ai/llm.py:190 (from_settings never touches SDK)           |
| A-2 | CRITICAL | BROKEN   | CSF prompt demands `{"subcategories":[{"code":...}]}` with no tier; route reads `data["scores"]` keyed `tier\|subcategory_code`. Compliant live response matches zero rows, silent no-op. Payload is ungrounded (tier/code lists only, no answers) | app/ai/jobs.py:50-53; routes/csf.py:1073-1086                                    |
| A-3 | CRITICAL | DIVERGED | max_tokens now 128000 with streaming (truncation mitigated); but no per-job model/max_tokens on AIJob, no Haiku/Sonnet split, every job runs the one global model                                                                                  | app/ai/llm.py:147-149; app/ai/engine.py:27-42                                    |
| A-4 | CRITICAL | BROKEN   | Risk prompt asks display labels ("Very Low".."Catastrophic"); enums are lowercase snake_case; `_enum_or_none` has no normalization and nulls on mismatch, silently blanking likelihood/impact/tier                                                 | app/ai/jobs.py:110-112; app/risk/engine.py:23-36; routes/risk.py:177-181,232-235 |
| A-5 | HIGH     | BROKEN   | Default model id `claude-opus-4-7` is not a valid Anthropic id; ai-status checks only mode and key presence; missing key raises bare RuntimeError, not typed error                                                                                 | app/config.py:53; routes/admin.py:643-682; app/ai/llm.py:107-111                 |
| A-6 | HIGH     | BROKEN   | Zero contract tests; no test imports AnthropicProvider; run-ai tests inject fixtures in the route's shape, so A-2/A-4 drift passes green                                                                                                           | apps/api/tests/unit (grep verified)                                              |

### Workstream B - Deliverable integrity

| ID  | Sev      | Status            | Finding                                                                                                                                                                  | Evidence                                                                                                       |
| --- | -------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- |
| B-1 | CRITICAL | BROKEN            | ZT finalize calls analyze_gaps with no target args; falls to default 3; export contradicts dashboard for any other target                                                | routes/zt.py:1073; zt/scoring.py:32,235                                                                        |
| B-2 | CRITICAL | BROKEN            | CSF finalize same defect; no target_tier passed                                                                                                                          | routes/csf.py:1374; csf/gap.py:40,117                                                                          |
| B-3 | CRITICAL | BROKEN            | Playbook export gates only on "rows exist"; seeding creates all-zero rows read as Level 1; export clears documents_stale unconditionally; no scored_at exists            | routes/csf.py:1153-1157,1262; models/csf_profile.py:49-53                                                      |
| B-4 | MEDIUM   | BROKEN            | Gap lists capped at DEFAULT_TOP_N=20 in exports with no "of N" marker; finalize never passes top_n=None                                                                  | zt/scoring.py:33,278; csf/gap.py:41,150; zt/exporters.py:170,466                                               |
| B-5 | MEDIUM   | PARTIAL           | build_roadmap never called by any exporter; no Roadmap sheet/section; DOCX lacks Answers section (XLSX and PDF already carry answers, narrower than source plan claimed) | zt/scoring.py:297; zt/exporters.py:91-154,422-488                                                              |
| B-6 | MEDIUM   | FIXED (minor gap) | Overlap Analysis and Consolidation Plan are in the XLSX/DOCX/HTML already; only per-item disposition rows missing from the consolidation sheet (summary-level today)     | tech_debt/exporters.py:98,233,263,773,793                                                                      |
| B-7 | MEDIUM   | BROKEN            | ATT&CK gap list alphabetical cut at 50; Risk DOCX missing 5x5 matrix; ATT&CK DOCX blank name fallback; Playbook and Risk filenames bypass deliverable_filename           | attack/exporters.py:250-252,352-354,261-263; risk/exporters.py:294-330; routes/csf.py:1172; routes/risk.py:332 |

### Workstream C - Extraction honesty

| ID  | Sev      | Status  | Finding                                                                                                                                                                                             | Evidence                                                                                                                                   |
| --- | -------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| C-1 | CRITICAL | PARTIAL | Fabrication itself is gone (fixtures.py deleted; demo data now seed-only). Missing: typed 422 on zero data rows before the LLM call; `__truncated__` sentinel still sent to the model as a data row | tech_debt/extract.py:171-195; parsers.py:65                                                                                                |
| C-2 | CRITICAL | BROKEN  | .xls accepted everywhere then crashes openpyxl with a raw 500; extract catches ValueError only, so corrupt .xlsx also 500s                                                                          | routes/artifacts.py:43; parsers.py:29; routes/tech_debt.py:187; Dropzone.tsx:23; TechDebtWorkspace.tsx:236; IntakeDocumentsPanel.tsx:20,26 |
| C-3 | HIGH     | BROKEN  | Only wb.active parsed; duplicate headers collapse; overflow cells dropped; no sheet/rows-parsed metadata returned or rendered                                                                       | parsers.py:82-99                                                                                                                           |
| C-4 | HIGH     | BROKEN  | Bare float()/int() coercion silently nulls "$120,000", "500 seats"; confidence not clamped anywhere (250 stores as 250)                                                                             | tech_debt/extract.py:128-144,154; schemas/tech_debt.py                                                                                     |
| C-5 | HIGH     | BROKEN  | Valid-JSON wrong-shape LLM response yields empty list and 201 "Draft vN, 0 items"; zero items from non-empty input not an error                                                                     | tech_debt/extract.py:116; routes/tech_debt.py:197-235                                                                                      |
| C-6 | MEDIUM   | BROKEN  | MIME check trusts client-asserted type; full body buffered before size check in API and again in the Next proxy                                                                                     | routes/artifacts.py:79-96; api/proxy/artifacts/route.ts:55                                                                                 |
| C-7 | MEDIUM   | BROKEN  | Extraction fetches bytes via private-attr sniff plus urllib with no timeout; S3 get() maps every error to FileNotFoundError (outage reads as 410 data loss)                                         | tech_debt/extract.py:88-101; storage/s3.py:43-47                                                                                           |
| C-8 | MEDIUM   | BROKEN  | evidence_artifact_id assigned with no existence/tenant check on all three services (helper exists, unused there); no sha256 upload dedup                                                            | routes/attack.py:361-362; routes/csf.py:471-472; routes/zt.py:612-613; routes/artifacts.py:71-133                                          |

### Workstream D - Navigation

| ID  | Sev    | Status | Finding                                                                                                                                                                                               | Evidence                                                                                                                                    |
| --- | ------ | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| D-1 | HIGH   | BROKEN | Submitted assessment cards lose their link; tech_debt/attack cards never link; /messages is a static card, not an inbox                                                                               | AssessmentsView.tsx:310-338; self-assessment page.tsx:16-32; messages/page.tsx                                                              |
| D-2 | HIGH   | BROKEN | ClientSwitcher only in PublicHeader; admin shell has none; Risk Register and Inbox empty states dead-end                                                                                              | AdminShell.tsx:98-115; RiskRegisterDashboard.tsx:216-231; InboxView.tsx:95-101                                                              |
| D-3 | MEDIUM | BROKEN | ProxyError renders "Intake proxy 422"; typed API detail discarded; no Finish-intake link                                                                                                              | lib/intake/client.ts:18-25; AssessmentsView.tsx:116-119                                                                                     |
| D-4 | MEDIUM | BROKEN | All six potholes present: no auth redirect on /assessments; registered=1 ignored; /intake only via hero; bad-type fallback references dead screen; dev preview unauthenticated; Active Work is a stub | assessments/page.tsx:12-23; SignInForm.tsx:9; Hero.tsx:26; self-assessment page.tsx:77-89; dev/questionnaire-preview; admin/active/page.tsx |

### Workstream E - Operational hardening

| ID  | Sev    | Status  | Finding                                                                                                                                                                                                         | Evidence                                                                                          |
| --- | ------ | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| E-1 | HIGH   | PARTIAL | Provider timeout=120/max_retries=2 exists but is a streaming per-read gap, no typed 504; DB session held across the LLM call (pool exhaustion at ~15 concurrent); no AbortSignal or cancel client-side          | app/ai/llm.py:125-129,234-264; db/session.py:19-29; lib/api.ts                                    |
| E-2 | HIGH   | BROKEN  | llm_calls rows flush-only, never independently committed; failed calls leave no audit row; unit test reads in-session and self-masks                                                                            | app/ai/llm.py:234-264; db/session.py:32-38; tests/unit/test_llm_client.py:113-115                 |
| E-3 | MEDIUM | BROKEN  | No advisory locks anywhere; no unique constraint on risk_registers (client_id, version); no open-draft guard on ATT&CK, ZT, or CSF create (the CSF guard the source plan referenced does not exist here either) | routes/attack.py:245-260; routes/zt.py:472-497; routes/csf.py:350-365; models/risk_register.py:31 |
| E-4 | MEDIUM | BROKEN  | ZT narratives and ATT&CK ai_summaries returned once, never persisted; no columns exist                                                                                                                          | models/zt_assessment.py; models/attack_assessment.py                                              |
| E-5 | MEDIUM | BROKEN  | Banner claims fixture mode means AI "disabled"/"won't produce results"; no simulated badge; run-ai responses do not carry the mode                                                                              | routes/admin.py:665-668; AiStatusBanner.tsx:44-46                                                 |
| E-6 | LOW    | BROKEN  | Seed loaders resolve repo root as parents[3], breaks in container; no SHIELD_SEED_DATA_DIR; no make seed                                                                                                        | scripts/\_common.py:14-15                                                                         |

### Workstream F - Process simplification

| ID  | Sev    | Status | Finding                                                                                                                                                                                              | Evidence                                            |
| --- | ------ | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| F-1 | MEDIUM | BROKEN | run-ai 409s when unseeded; seeding is a separate manual endpoint                                                                                                                                     | routes/csf.py:1057-1061,808-855                     |
| F-2 | LOW    | BROKEN | run-ai 404s before Start on ATT&CK and ZT                                                                                                                                                            | routes/attack.py:456-459; routes/zt.py:357-360      |
| F-3 | HIGH   | BROKEN | Risk routes are gate/generate/export/latest only; no entry PATCH/lock/delete, no Approve; gate checks existence only; CSF harvest hardcodes < 3 (ZT harvest is already target-aware, pattern exists) | routes/risk.py:72-88,98,147,163-164,184,305-322,376 |

### Workstream G - Product decisions

| ID  | Sev    | Status | Finding                                                                                                                   | Evidence                                                                              |
| --- | ------ | ------ | ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| G-1 | MEDIUM | BROKEN | "Report released" label and released-gated client views fully live                                                        | AssessmentsView.tsx:41,56-58; routes/csf.py:410-413; attack.py:304-307; zt.py:541-544 |
| G-2 | MEDIUM | BROKEN | \_client_tool_names joins every capability list version, any status; no APPROVED/latest filter; no empty-universe warning | routes/attack.py:405-424,465                                                          |
| G-3 | MEDIUM | BROKEN | assert_safe_for_runtime has no prod+fixture guard; no shield_demo setting exists                                          | app/config.py:54,101-109                                                              |

### Workstream H - Security governance and documentation truth

| ID  | Sev    | Status            | Finding                                                                                                                                                                                                                                                | Evidence                                                                             |
| --- | ------ | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------ |
| H-1 | HIGH   | BROKEN            | Compensating-control claims still in README/BUILD_REPORT; flags now loaded in config (drift) but enforced nowhere; refresh re-issues pairs with no rotation                                                                                            | README.md:101; BUILD_REPORT.md:60; config.py:85-86; routes/auth.py:322-340           |
| H-2 | HIGH   | BROKEN            | No rate limiting anywhere; Redis declared with zero consumers                                                                                                                                                                                          | grep verified; config.py:34-35                                                       |
| H-3 | HIGH   | BROKEN            | No backup/restore scripts, runbook, or CI drill; runbooks and terraform dirs are .gitkeep only                                                                                                                                                         | docs/runbooks/; infra/terraform/                                                     |
| H-4 | MEDIUM | BROKEN (adjusted) | architecture.md claims single-tenant, Celery worker, audit_events, unredact; README repeats tenancy/worker claims; DECISIONS.md has duplicate D-015. The claimed duplicate [3.0.0] CHANGELOG headings do NOT exist in this checkout (sub-item dropped) | docs/architecture.md:7,13,46,59,62,68,72,77; README.md:3,22,94; DECISIONS.md:112,134 |
| H-5 | MEDIUM | BROKEN            | llm_calls has no client_id; no /admin/ai-usage report                                                                                                                                                                                                  | models/llm_call.py:42-83; routes/admin.py                                            |
| H-6 | MEDIUM | BROKEN            | No preview/dry-run mode on run-ai; redaction inspectable nowhere                                                                                                                                                                                       | grep verified                                                                        |
| H-7 | LOW    | BROKEN            | No /admin/audit viewer over the existing AuditEntry data                                                                                                                                                                                               | routes/admin.py                                                                      |
| H-8 | MEDIUM | BROKEN            | No CsfActionItem model, no owner/due-date fields, no Action Plan sheet                                                                                                                                                                                 | grep verified; no migration past 0028                                                |

---

## A - Actions

Concrete changes per fix, adjusted to this checkout. File paths relative to apps/api/app or apps/web/src unless noted.

### Sprint 0 actions

- S0-1/S0-2: restore apps/web/package.json, packages/design-system/package.json, root package.json and pnpm-lock.yaml from commit c3f458b (the last coherent set: Next 14.2.15, React 18.3.1, Tailwind 3.4.13, eslint 8.57.1, next-auth 4.24.10, lucide-react 0.453.0, tailwind-merge 2.5.4, @types/\* matching). Verify `pnpm install --frozen-lockfile`, lint, typecheck, build all pass. No feature commit ever touched these manifests, so nothing is lost.
- S0-3: add @playwright/test 1.56.0 (matches preinstalled browsers), playwright.config.ts at repo root (testDir e2e, baseURL http://localhost:3000, chromium project, trace on-first-retry), an e2e/README note, and a smoke spec (home page renders, sign-in page renders). Wire `pnpm test:e2e` script.
- S0-4: run the unit suite, fix the 2 failing tests, delete apps/api/\_pytest_out.txt and \_run2.txt, add both patterns to .gitignore.
- S0-5: document the 3.12 venv in the plan evidence (no code change).

### Workstream A actions

- A-1: in LLMClient.from_settings (ai/llm.py), when mode is live, eagerly `import anthropic` and raise a clear ConfigurationError naming the missing SDK at boot. Unit test with import patched out.
- A-2: rewrite \_CSF_SCORE_PROMPT (ai/jobs.py) to demand `{"scores":[{"tier","subcategory_code","governance","policy","implementation","monitoring","improvement","what_we_found"}]}` matching routes/csf.py:1083-1086. Define CSF_SCORE_RESPONSE_SHAPE once (new ai/contracts.py) referenced by prompt text, route parser and tests. Ground the payload: include seeded tier list plus per-subcategory questionnaire answers (tier, notes) and evidence flags, redacted as usual. Keep the locked-row guard.
- A-3 (adjusted): add `model: str | None = None` and `max_tokens: int | None = None` to AIJob (ai/engine.py); thread through engine -> LLMClient -> provider.complete; set mitre_map and csf_score to claude-haiku-4-5, leave others on the global default. Global default becomes claude-sonnet-5 (A-5). Keep 128000 streaming for the two big jobs; default 16000 for the rest. No chunking.
- A-4: fix both sides. Prompt states exact snake_case token lists; `_enum_or_none` (routes/risk.py) normalizes (strip, lower, spaces to underscores) before coercion; count failed coercions and include a `warnings` field in the generate response.
- A-5: config.py default model to claude-sonnet-5; extend GET /admin/ai-status to report SDK importability, key presence, global and per-job models; convert missing-key/bad-model to the typed {reason, message} error pattern (app/exceptions.py) mapped to 503; surface via the existing banner.
- A-6: ai/contracts.py holds one response schema per job (five jobs); prompts reference it; routes validate against it; contract tests per job generate a conformant response, run it through the route's application loop, and assert changes land. Env-gated live smoke test skipped unless SHIELD_LIVE_SMOKE=1 and key present. Wire into CI (pytest already runs in CI).

### Workstream B actions

- B-1: in ZT finalize (routes/zt.py:1073), resolve per-capability targets from answers plus ServiceRequest.zt_target_stage fallback (default 3 only when neither exists), pass target_stage/targets exactly as the dashboard endpoint does (zt.py:901); print the resolved target in the deliverable summary line.
- B-2: mirror in CSF finalize (routes/csf.py:1374): resolve ServiceRequest.csf_target_tier fallback 3, pass target_tier; print in summary.
- B-3: additive migration adding nullable scored_at to csf_dimension_scores; set on every human PATCH and AI apply; seeding leaves null. Export 409s (typed) unless all in-scope rows scored AND assessment approved, message counts unscored rows. Exporters render "Unscored" for null-scored rows as belt and suspenders. documents_stale cleared only when the gate passed.
- B-4: finalize paths pass top_n=None so XLSX Gap Plan sheets carry the full list; PDF/DOCX keep top 20 but title "Top 20 of N remediation gaps" from total_gap_count plus a pointer line to the XLSX.
- B-5: finalize calls build_roadmap on the target-correct analysis; add Roadmap sheet to ZT XLSX (month, capability, pillar, from-stage, to-stage), roadmap section to PDF/DOCX after the gap table, and an Answers section to the DOCX mirroring the XLSX Answers sheet.
- B-6 (reduced): upgrade the XLSX Consolidation Plan sheet from summary tallies to per-item rows (disposition, target, rationale, per-item savings, totals with savings-cost-known flag). Everything else verified present.
- B-7: sort ATT&CK gap lists by tactic coverage ascending then status (gap before partial) then code, title the section with the rule; port the PDF 5x5 matrix to the Risk DOCX; DOCX unknown-technique fallback renders the code; route Playbook and Risk export filenames through deliverable_filename.

### Workstream C actions

- C-1 (remaining): in extract route/extract_capabilities, return typed 422 ("No data rows found in this file...") when parse_inventory yields zero data rows, before any LLM call; strip the `__truncated__` sentinel from rows sent to the model and surface truncation in the response instead.
- C-2: remove application/vnd.ms-excel from routes/artifacts.py allowlist and parsers.py SUPPORTED_MIME; return typed 415 for .xls ("re-save as .xlsx"); remove .xls from all three frontend accept lists; wrap workbook open so BadZipFile/InvalidFileException return the same typed 422 as C-1.
- C-3: parse all sheets, pick best candidate (most data rows under a plausible header); uniquify duplicate headers (name, name_2); keep overflow cells under generated headers; include sheet_used, rows_parsed, rows_skipped, truncated in the extract response and render that line in TechDebtWorkspace above the results.
- C-4: tolerant numeric parser (currency symbols, thousands separators, trailing words; leading-number parse) for annual_cost_usd and license_count; unparseable raw strings appended to notes; clamp confidence_pct 0-100 at extraction and PATCH validation.
- C-5: \_parse_response raises ValueError (mapped to typed 502) when decoded JSON is not the documented shape; zero items from non-empty input also 502; validate against the A-6 shared schema.
- C-6: sniff magic bytes server-side (PK zip family, %PDF, OLE2, UTF-8 text heuristic) and 415 on mismatch with the claimed type; check Content-Length before reading with an incremental stream cap; same Content-Length pre-check in the Next proxy route.
- C-7: replace the private-attr sniff and urllib fallback with the storage backend's own get() plus boto connect/read timeouts and bounded retries; S3Storage catches only missing-key codes as FileNotFoundError and raises StorageUnavailable (typed 503) for credential/connection errors; extract maps missing bytes to the download route's 410.
- C-8: route all three evidence PATCHes through require_artifact_in_tenant (404 on missing/cross-tenant); on upload, return the existing artifact (200 plus already_uploaded flag) when sha256+client matches; workspace asks before auto-re-extracting a file that already produced a version.

### Workstream D actions

- D-1: every assessment card links at every status; self-assessment page renders read-only post-submit (inputs disabled, MessageThread active); minimal client detail page for tech_debt and attack_coverage (service name, status timeline, MessageThread); /messages becomes a thread inbox with unread counts linking into those pages.
- D-2: mount ClientSwitcher in AdminShell header; Risk Register and Inbox no-client empty states render the switcher inline; map the messages 400 to that empty state.
- D-3: ProxyError prefers the typed detail message from the payload, friendly generic per status otherwise; AssessmentsView special-cases incomplete-intake with a Finish intake link to /intake; sweep err.message render sites in AssessmentsView and InboxView.
- D-4: one batch: auth redirect with callbackUrl on /assessments; "Account created, sign in" note when registered=1; Intake link in client header while intake incomplete; bad-type fallback links to /assessments; dev preview gated behind admin auth; build the Active Work list (cross-client in-progress services with workspace links from existing queue data).

### Workstream E actions

- E-1: whole-call timeout mapped to typed 504 ("the AI call timed out; nothing was changed"); gather inputs, detach/close the DB session during provider.complete, reopen to apply; AbortSignal plus client-side timeout and a visible cancel on Running state.
- E-2: llm_calls rows written in their own short-lived session: commit RUNNING before the provider call, commit COMPLETED/FAILED after, independent of the request transaction; fix test_llm_client to read from a fresh session.
- E-3: per-assessment advisory lock (pg_advisory_xact_lock; SQLite-safe no-op) on every run-ai/generate route returning typed 409 to the loser; unique constraint on risk_registers (client_id, version) via additive migration; open-draft guard (return the open draft instead of minting) on CSF, ZT and ATT&CK create routes.
- E-4: additive migration: zt_assessments.narratives and attack_assessments.ai_summaries (nullable JSON); persist on run-ai; render as editable draft text in workspaces; feed reviewed text into B-5/B-7 executive sections.
- E-5: rewrite banner copy both sides ("AI suggestions are simulated (deterministic fixtures)... set SHIELD_LLM_MODE=live for real analysis"); run-ai responses carry mode; "simulated" badge next to AI suggestions in fixture mode.
- E-6: scripts/\_common.py resolves data dir from SHIELD_SEED_DATA_DIR with repo-relative fallback; compose mounts packages/ read-only for loaders; make seed target runs demo seed plus both loaders.

### Workstream F actions

- F-1: seed working profiles lazily inside run-ai (and at create); keep endpoint as re-sync; remove the 409.
- F-2: with E-3's guard in place, run-ai creates the draft when none exists on ATT&CK and ZT; Start button stays but stops being mandatory.
- F-3: add PATCH /risk/entries/{id} (title, description, likelihood, impact, compensating controls, action, rationale; tier re-derived), lock flag, soft DELETE; regenerate preserves locked entries; gate requires ATT&CK approved plus at least one of CSF/ZT approved; CSF harvest threshold uses client target tier (fallback 3, ZT pattern at risk.py:163 already does this); formal Approve step locks the version and unlocks Export; dashboard and export labeled with source assessments and statuses.

### Workstream G actions

- G-1: remove the client-facing released label; terminal client statuses read "Complete: your consultant will deliver your report"; comment-gate dead released transitions and client result-view checks; keep enum members (deprecated comments).
- G-2: \_client_tool_names filters to latest APPROVED CapabilityList per tech-debt service, unioned across services; empty result adds a warning field to the run-ai response rendered in the workspace.
- G-3: extend assert_safe_for_runtime: ENVIRONMENT=production plus SHIELD_LLM_MODE=fixture refuses boot unless SHIELD_DEMO=1; add shield_demo to Settings; document in .env.example.

### Workstream H actions

- H-1: correct README and BUILD_REPORT A07 to say the controls are planned, not present; mark the two flags RESERVED/UNIMPLEMENTED in .env.example and compose; decision-log entry deferring enforcement to the MFA package with rotation first. No behavior change.
- H-2: Redis-backed token-bucket limiter (slowapi or minimal middleware): per-IP 10/min on /auth/login, /auth/register, /auth/refresh with typed 429 plus Retry-After; per-user 6/min on run-ai/generate/extract; structured log on every 429; limits in config. In-memory fallback for tests.
- H-3: scripts/backup.sh (pg_dump plus object-storage sync, timestamped) and scripts/restore.sh; docs/runbooks/backup-restore.md; CI job (or make target) performing a restore drill against the compose stack asserting a seeded record survives.
- H-4: rewrite docs/architecture.md to the actual system (multi-tenant, no worker, synchronous AI, audit_entry table, no unredact); fix README tenancy/worker/queue claims; renumber the second D-015 with cross-reference; add D-006 supersession note; correct the two OpenAPI "(admin/reviewer)" summaries; check the v2 Work Order into reference-docs (if provided); amend D-009 to rescind i18n for v1; fold in H-1. Add prettier format check to CI if absent (it is present already, verify only).
- H-5: additive migration: nullable indexed client_id on llm_calls; populate in LLMClient.invoke from tenant context; GET /admin/ai-usage (calls, tokens, estimated cost per client per month, price table in config) with CSV download and a simple admin table.
- H-6: run-ai?preview=1 builds the payload, runs the redactor, returns redacted payload plus redaction summary without calling the provider; workspace "Preview what will be sent" action; first live run per client requires a one-time recorded acknowledgment (audit row).
- H-7: read-only /admin/audit page over AuditEntry: filter by client, action type, actor, date range; paginated; CSV export; admin-gated.
- H-8: CsfActionItem model plus additive migration (subcategory code, assessment link, owner free text, due date, milestone note, status); one-click create from a gap row in the workspace; Action Plan sheet in CSF XLSX and section in DOCX/PDF.

---

## B - Build Plan

Four sprints. One at a time, lead review of every diff, validation gates between sprints, commit per sprint.

### Sprint 0 - Baseline repair (the build must work before anything else)

- Goal: green install, lint, typecheck, build, unit tests; Playwright harness exists and runs a smoke spec.
- Issues: S0-1 through S0-5.
- Likely files: pnpm-lock.yaml, apps/web/package.json, packages/design-system/package.json, package.json, playwright.config.ts, e2e/\*, .gitignore, the 2 failing test files.
- Subagent: build/validation subagent (manifest restore is lead-driven since it is git surgery; Playwright harness by QA subagent).
- Risks: lockfile restore could miss a manifest the feature commits touched (verified none did); Playwright version must match preinstalled browsers (pinned 1.56.0).
- Acceptance: pnpm install --frozen-lockfile, pnpm -F web lint/typecheck/build all exit 0; pytest unit suite 100% pass; npx playwright test e2e/smoke passes against the running stack; ruff/black/bandit clean.

### Sprint 1 - Trustworthy core

- Goal: live AI physically correct end to end, no fabricated data, no deliverable/dashboard contradictions.
- Issues: A-1 (boot check), A-2, A-3 (adjusted), A-4, A-5 (model id, folded in here since A-3 needs it), B-1, B-2, B-3, C-1 (remaining), C-2, G-2.
- Likely files: ai/llm.py, ai/engine.py, ai/jobs.py, ai/contracts.py (new), config.py, routes/csf.py, routes/zt.py, routes/risk.py, routes/attack.py, routes/artifacts.py, routes/tech_debt.py, tech_debt/parsers.py, tech_debt/extract.py, models/csf_profile.py, new alembic migration, exporters touched by B-3 unscored rendering, three frontend accept lists, .env.example, docker-compose.yml model refs.
- Subagents: backend remediation (AI contracts: A-1/A-2/A-3/A-4/A-5), backend remediation (deliverable targets and gates: B-1/B-2/B-3), backend remediation (extraction honesty: C-1/C-2 plus frontend accept lists), backend (G-2). Playwright QA subagent extends smoke into the affected flows after implementation.
- Risks: B-3's migration must stay additive; A-2 payload grounding touches redaction (keep redactor in path); G-2 must not break clients with zero tech-debt services (warning, not error).
- Acceptance: contract tests prove each prompt shape round-trips through its route; B-1/B-2 regression tests (target 4, stages/tiers 3) assert export equals dashboard; seed-then-export returns 409; header-only CSV returns 422; .xls upload returns 415 with clear message; ATT&CK tool universe excludes draft lists and warns when empty; full unit suite, lint, build green.

### Sprint 2 - Solid operations

- Goal: bounded, audited, guarded runtime; extraction survives real files; users never stranded.
- Issues: A-6, C-3 through C-8, D-1, D-2, D-3, E-1 through E-5, F-1, F-2, G-3, H-2, H-5, H-6.
- Likely files: ai/contracts.py, ai/llm.py, ai/engine.py, db/session.py, tech_debt/parsers.py, tech_debt/extract.py, routes (artifacts, tech_debt, csf, zt, attack, risk, admin, auth), storage/s3.py, app/tenant.py wiring, models (zt_assessment, attack_assessment, llm_call, risk_register) plus migrations, middleware (rate limit), web/src (AssessmentsView, AdminShell, InboxView, RiskRegisterDashboard, intake/client.ts, api.ts, messages pages, TechDebtWorkspace, AiStatusBanner), e2e specs.
- Subagents: backend remediation (extraction C-3..C-8), backend remediation (runtime E-1/E-2/E-3/G-3/H-2), backend remediation (contracts and persistence A-6/E-4/H-5/H-6/F-1/F-2), frontend remediation (D-1/D-2/D-3/E-5 badge), Playwright QA (click-path specs for D-1/D-2), validation subagent per merge.
- Risks: E-1 session detach interacts with E-2's independent audit session (E-2 lands first inside the same subagent task); advisory locks need the SQLite no-op for tests; rate limiter needs in-memory fallback where Redis is absent; D-1 read-only rendering must not regress draft editing.
- Acceptance: contract tests in CI; concurrency tests (two threads, one 409) pass; timeout test maps to 504 with no state change; failed-call audit row visible from a fresh session; multi-sheet and $-formatted xlsx extracts correctly with a parse-report line; storage outage reads as 503 storage-unavailable; client click-path e2e (submit, admin reply, client reads reply) passes; admin can reach Risk Register via UI switcher from fresh session; prod+fixture boot refused without SHIELD_DEMO=1; 429s past thresholds with Retry-After; preview returns redacted payload with no provider call; usage report attributes calls per client.

### Sprint 3 - Complete deliverables and truth

- Goal: exports contain everything the dashboard and spec promise; risk register governed; docs and ops honest.
- Issues: B-4, B-5, B-6 (reduced), B-7, D-4, E-6, F-3, G-1, H-1, H-3, H-4, H-7, H-8.
- Likely files: zt/exporters.py, csf exporters, attack/exporters.py, risk/exporters.py, tech_debt/exporters.py, routes/risk.py plus models/migration for lock/approve, routes/zt.py, routes/csf.py finalize, scripts/\_common.py, scripts/backup.sh, scripts/restore.sh (new), docs/architecture.md, README.md, BUILD_REPORT.md, DECISIONS.md, docs/runbooks/backup-restore.md (new), web/src (nav potholes, active work page, released label, risk register editor UI), models/csf_action_item.py (new) plus migration, admin audit viewer route and page, e2e additions.
- Subagents: backend remediation (exports B-4..B-7, H-8), backend remediation (risk governance F-3), backend remediation (ops E-6/H-3), frontend remediation (D-4, G-1, risk editor, audit viewer page), security remediation (H-1, H-4 docs truth), Playwright QA (edit-then-export flow, terminal-status copy), validation subagent.
- Risks: F-3 is the largest single fix (L) and changes the risk data model; export content tests must compare against engine outputs, not fixtures; H-3 restore drill kept as make target here (CI has no compose stack in this environment).
- Acceptance: every service's XLSX/DOCX/PDF verified against its dashboard by automated content tests including non-default targets; risk entries editable, lockable, approve-gated before export, locked entries survive regenerate; no client-facing released label; CSF export carries Action Plan sheet; backup/restore round-trip drill green locally; architecture.md matches reality; full unit and e2e suites green.

---

## L - Live Validation

Per-sprint gates, all run locally in this environment:

1. Unit tests: `.venv/bin/python -m pytest apps/api/tests -q` (Python 3.12 venv). Must be 100% pass after every sprint; new fixes ship with their named regression tests.
2. Lint and static: `ruff check .`, `black --check .`, `bandit -q -c pyproject.toml -r apps/api/app` for the API; `pnpm format:check`, `pnpm -F web lint`, `pnpm -F web typecheck` for the web app.
3. Build: `pnpm -F web build` (Next production build) after every sprint that touches the frontend.
4. Playwright: stack started locally (Postgres 16, MinIO, Redis via docker; alembic upgrade head; seed scripts; API via uvicorn; web via next start). Specs live in e2e/. Each sprint adds specs for the flows it changed: Sprint 0 smoke; Sprint 1 extraction error pills and export gate; Sprint 2 client re-entry click path, admin switcher, simulated badge; Sprint 3 risk edit-then-export, terminal-status copy. Traces retained on failure and referenced in Evidence.
5. Live AI smoke: env-gated only (SHIELD_LIVE_SMOKE=1 plus ANTHROPIC_API_KEY); not executable here (no key in this environment); documented as a deferred verification for the owner.
6. Manual review: lead agent reviews every subagent diff before merge; documentation fixes (H-1, H-4) get a checklist review against the specific false claims.

## E - Evidence

Filled in as sprints complete. Format per fix: what changed, subagent, files, tests run, Playwright validation, result, remaining issues.

### Sprint 0 evidence

- S0-1/S0-2 (build repair): restored package.json, apps/web/package.json, packages/design-system/package.json and pnpm-lock.yaml from commit c3f458b (verified no feature commit ever touched them). Handled by: lead (git surgery, no subagent). Result: `pnpm install --frozen-lockfile` OK; `pnpm -F web lint` 0 warnings; `pnpm -F web typecheck` clean; `pnpm -F web build` production build succeeds; `pnpm format:check` clean.
- S0-3 (Playwright harness): added @playwright/test 1.56.0 (matches preinstalled browsers), playwright.config.ts, e2e/smoke.spec.ts, scripts/e2e-stack.sh, .env.e2e (dev-only values, no secrets). Playwright validation: 2/2 smoke tests pass in Chromium against the locally running stack (Postgres 16 + Redis 7 + MinIO in Docker, alembic upgrade head, demo seed, uvicorn API, next start web).
- S0-4 (test artifacts): deleted apps/api/\_pytest_out.txt and \_run2.txt, added ignore patterns. The 2 failures they recorded were already fixed upstream (commit a6310ba); full unit suite now exits 0 (414 tests, warnings only, StarletteDeprecationWarning noise noted for later).
- S0-5: Python 3.12 venv at .venv; `pip install -e "apps/api[dev]"` clean.
- Also verified during seeding: E-6 reproduced exactly as described (loaders fail with ModuleNotFoundError when run as documented; `python -m scripts.load_*` from apps/api works and was used for the e2e stack).
- Lint/static baseline: ruff clean, black clean, bandit clean.
- Pass/fail: PASS. Remaining issues: none for Sprint 0 scope.

### Sprint 1 evidence

All three implementation tasks were done by Opus subagents; the lead reviewed every diff before the gate.

- A-1 (subagent S1-A): eager SDK import + key check in \_build_provider for live mode, raising typed LLMConfigurationError(reason, message) at boot instead of first click. Files: ai/llm.py. Tests: boot import-check tests in test_ai_contracts.py.
- A-2 (S1-A): CSF prompt rewritten to the route's shape (scores keyed tier|subcategory_code) via the new shared ai/contracts.py descriptors; payload now grounded with per-row questionnaire answers (tier, notes), evidence flags, in_scope and rationale through the redaction path. Files: ai/contracts.py (new), ai/jobs.py, routes/csf.py. Tests: contract round-trip test proves a schema-conformant response changes rows.
- A-3 adjusted (S1-A): AIJob gains model/max_tokens; threaded engine -> client -> provider; csf_score and mitre_map run claude-haiku-4-5 at 128000 tokens; others inherit the global model with DEFAULT_MAX_TOKENS=16000. llm_calls rows record the effective model. Files: ai/engine.py, ai/llm.py, ai/jobs.py.
- A-4 (S1-A): risk prompt states exact snake_case tokens; \_enum_or_none normalizes before coercion; generate response carries warnings with the unrecognized-count. Files: ai/jobs.py, routes/risk.py, schemas/risk.py. Tests: normalization plus warnings, 3 cases.
- A-5 (S1-A): default model claude-sonnet-5 (verified current against platform docs) in config, .env.example, docker-compose; ai-status reports sdk_importable, key_present, per-job overrides; live misconfiguration returns typed 503. Files: config.py, routes/admin.py, schemas/admin.py.
- B-1 (S1-B): ZT finalize passes per-capability targets plus the engagement target from the originating ServiceRequest into analyze_gaps, mirroring the dashboard; summary line prints the resolved target. Regression test: engagement target 4, capabilities below target, XLSX Gap Plan row count equals dashboard total_gap_count.
- B-2 (S1-B): CSF finalize resolves csf_target_tier (fallback 3) and passes target_tier; mirror regression test with tiers.
- B-3 (S1-B): scored_at column (migration 0029, batch-safe, up/down tested); stamped on human PATCH and AI apply; seeding leaves null; export 409s with unscored counts or missing approval; exporter renders "Unscored" as belt and suspenders; documents_stale cleared only past the gate. Tests: seed->409 ("318 of 318 in-scope rows are unscored"), scored-unapproved->409, scored+approved->200 with no Unscored cells.
- C-1 remaining (S1-C): typed 422 on zero data rows before any LLM call; **truncated** sentinel stripped from the model payload and surfaced as truncated in the response. Tests assert no llm_calls row is written for empty input and exactly 500 rows reach the model for a 501-row file.
- C-2 (S1-C): vnd.ms-excel removed from API allowlist and parser map; .xls upload returns 415 with re-save instruction; corrupt .xlsx returns 422 (BadZipFile/InvalidFileException caught); .xls removed from all three frontend accept lists.
- G-2 (S1-C): tool universe now the latest APPROVED capability list per tech-debt service, unioned across services; empty universe adds the warning to the run-ai response. Tests: draft-only lists yield empty universe plus warning; approved v2 excludes v1 ghosts.

Gate results: full unit suite 510 tests, 0 failures, 0 errors (junit-verified; +96 tests over baseline). ruff, black, bandit clean; prettier, eslint, tsc clean; Next production build green. Playwright: 5/5 passing twice consecutively (smoke x2 plus C-1 empty-CSV error pill through the real UI, C-2 accept-list plus proxy 415, B-3 export gate 409 end to end through the live API). No product bugs found by QA. Ops finding fixed by lead: scripts/e2e-stack.sh stop now reaps orphaned servers so stale code can never silently serve during validation.

Remaining issues carried forward: none within Sprint 1 scope. Live-mode smoke against the real Anthropic API remains deferred (no key in this environment), covered by the env-gated smoke test added in Sprint 2 (A-6).

### Sprint 2 evidence

Four Opus implementation subagents (S2-A runtime, S2-B extraction, S2-C contracts/persistence, S2-D frontend) plus a Playwright QA subagent; every diff lead-reviewed. The QA subagent was interrupted by an account spend limit mid-run; the lead completed its diagnosis, fixed the two real bugs it surfaced, repaired its spec locators, and reran the suite to green.

- E-1 (S2-A): whole-call LLM deadline (300s default, config) via a worker thread; timeout maps to a typed 504 "the AI call timed out; nothing was changed" with the llm_calls row marked FAILED; run-ai routes release the request DB connection during the provider call (db.close() then re-fetch by id), proven by a single-connection-pool concurrency test. Known limitation recorded: with the connection released, the Postgres advisory lock gates entry rather than spanning the whole run.
- E-2 (S2-A): llm_calls rows written on an independent session, RUNNING committed before the call, COMPLETED/FAILED after; the self-masking unit test now reads from a fresh session.
- E-3 (S2-A): advisory-lock helper (pg_try_advisory_xact_lock on Postgres, no-op on SQLite) on every run-ai and risk generate returning 409 to the loser; unique constraint on risk_registers (client_id, version) (migration 0030); open-draft guard on all three create-assessment routes. Honest coverage note: real two-transaction contention is not exercisable on the SQLite suite; helper SQL and 409 mapping are unit-tested.
- E-4 (S2-C): zt_assessments.narratives and attack_assessments.ai_summaries (migrations 0031, 0032) persisted on run-ai and returned by GET.
- E-5 (S2-C backend, S2-D frontend): truthful banner copy both sides; run-ai responses carry mode; "simulated" pill rendered in the four workspaces. The pill's e2e spec is a documented fixme (QA agent interrupted); banner copy and mode field are e2e-verified, the pill itself is unit-visible only until Sprint 3 QA.
- G-3 (S2-A): assert_safe_for_runtime refuses production+fixture unless SHIELD_DEMO=1; guard matrix unit-tested.
- H-2 (S2-A): token-bucket rate limiting, per-IP 10/min on the three auth endpoints and per-user 6/min on run-ai/generate/extract; Redis-backed with in-memory fallback; 429 plus Retry-After; limits in config, disabled in the test suite except dedicated tests.
- C-3 (S2-B): all worksheets parsed with best-candidate selection; duplicate headers uniquified; overflow cells kept; parse_report (sheet_used, rows_parsed, rows_skipped, truncated) returned by extraction.
- C-4 (S2-B): conservative money/count parser ($ and thousands separators stripped, trailing words tolerated, multipliers refused to notes); confidence clamped 0-100 at extraction and PATCH.
- C-5 (S2-B): wrong-shape and empty-for-nonempty LLM responses raise to typed 502 with no CapabilityList minted.
- C-6 (S2-B): magic-byte sniffing (zip family, PDF, OLE2 rejected, PNG/JPEG, UTF-8 text heuristic); Content-Length pre-check plus streamed reads with an incremental cap in the API and the Next proxy.
- C-7 (S2-B): extraction fetches bytes through storage.get() with boto connect/read timeouts and bounded retries; StorageUnavailableError maps to 503, missing key to 410.
- C-8 (S2-B): evidence PATCHes on all three services go through require_artifact_in_tenant; sha256 upload dedup returns the existing artifact (200, already_uploaded). Lead fix after QA: the dedup lookup uses first() on newest rather than scalar_one_or_none, which 500ed for tenants holding pre-dedup duplicate rows (found live by QA).
- A-6 (S2-C): validate_response structural checks wired into every run-ai/generate route (502 with joined problems); env-gated live smoke test module (5 tests, skipped without SHIELD_LIVE_SMOKE=1 plus a key).
- F-1/F-2 (S2-C): CSF auto-seeds at create and run-ai (409 removed); ZT/ATT&CK run-ai auto-create a seeded draft (using the open-draft guard).
- H-5 (S2-C): llm_calls.client_id (migration 0033) populated everywhere; GET /admin/ai-usage per-client per-month with cost estimates and CSV.
- H-6 (S2-C): run-ai?preview=1 returns the redacted payload plus redaction summary with no provider call and no llm_calls row; live runs require a recorded per-client preview acknowledgment (428 otherwise); fixture mode exempt.
- D-1 (S2-D): every assessment card links at every status; self-assessment renders read-only post-submit; new client detail page /client-services/[serviceId] for tech_debt and attack; /messages is a real inbox with unread counts.
- D-2 (S2-D plus lead fix): ClientSwitcher mounted in AdminShell; inline switcher in the Risk Register and Inbox empty states. QA found the inline picker did not unstick the page (client components read the tenant only on mount; router.refresh() does not rerun them); lead added an onChanged callback to ClientSwitcher and reload keys to both views. e2e-verified.
- D-3 (S2-D plus lead fix): ProxyError surfaces the API's typed message; QA found the Finish-intake link never rendered because isIncompleteIntakeError read payload.detail while the API envelope is error.message; lead fixed the helper to accept both. e2e-verified including the link.

Gate results: 592 unit tests, 0 failures, 5 gated live-smoke skips (junit-verified); ruff, black, bandit, prettier, eslint, tsc clean; Next production build green. Playwright 11 passed, 0 failed, 1 documented fixme, across smoke, Sprint 1 and Sprint 2 specs against the freshly migrated (0029-0033) local stack. Migrations up/down verified where tested.

Session note: partway through Sprint 2 QA the account hit its monthly API spend limit (the QA agent died mid-diagnosis) and the sandbox container restarted (stack and Docker state rebuilt by the lead). All remaining Sprint 2 work above was completed by the lead in the main session.

### Sprint 3 evidence

(pending)
