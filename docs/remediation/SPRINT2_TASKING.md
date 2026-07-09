# Sprint 2 subagent tasking - Solid operations

Same standing rules as Sprint 1: work only in listed files plus tests, no drive-by
refactors, ruff+black on touched files, targeted pytest subsets only (lead runs the
full suite), plain-string HTTPException detail unless a structured detail is
specified, SQLite-batch-safe migrations (op.batch_alter_table), do not commit.

Sequencing: S2-A and S2-B first (parallel, disjoint), then S2-C, then S2-D
(frontend) with S2-E (QA) last.

## Task S2-A: Runtime hardening (E-1, E-2, E-3, G-3, H-2)

Files: apps/api/app/ai/llm.py, db/session.py, config.py (limits, shield_demo),
main.py or middleware/ (rate limiter wiring), models/risk_register.py, new
alembic migrations (risk_registers unique constraint), routes/csf.py + zt.py +
attack.py (create-assessment open-draft guards), routes/risk.py (generate lock),
run-ai routes (advisory lock helper), routes/auth.py (limiter decorators only),
tests/unit.

1. E-2 first: llm_calls audit rows written in an independent short-lived session
   (same engine, new session): commit RUNNING before the provider call, commit
   COMPLETED/FAILED after, regardless of the request transaction. Fix
   tests/unit/test_llm_client.py to read the row from a fresh session.
2. E-1: whole-call timeout for AI runs: wrap provider.complete with a bounded
   deadline (thread + join(timeout) or SDK-level whole-request budget; choose the
   simplest reliable approach) defaulting 300s, config-overridable; on timeout
   raise a typed error mapped to 504 "the AI call timed out; nothing was changed".
   Release the request DB session during the provider call: gather inputs, then
   db.close() (SQLAlchemy returns the connection to the pool; the Session object
   can be reused after close when it is re-bound on next use) or expunge+detach
   pattern; re-apply results afterwards. Prove with a test that the pool is not
   held: a provider stub that blocks can run while another request uses the DB.
3. E-3: helper `assessment_advisory_lock(db, assessment_id)` using
   pg_advisory_xact_lock on Postgres and a no-op on SQLite; call it at the top of
   every run-ai route (csf, zt, attack) and risk generate; concurrent loser path
   returns 409 "a run is already in progress" via pg_try_advisory_xact_lock.
   Unique constraint on risk_registers (client_id, version) via migration.
   Open-draft guard on CSF, ZT, ATT&CK create-assessment routes: if an
   assessment in a draft/in-progress status exists, return it (200) instead of
   minting a new version.
4. G-3: assert_safe_for_runtime refuses ENVIRONMENT=production +
   SHIELD_LLM_MODE=fixture unless shield_demo=="1" (new Settings field
   SHIELD_DEMO, documented in .env.example). Unit tests for the guard matrix.
5. H-2: rate limiting middleware: token bucket keyed per-IP for /auth/login,
   /auth/register, /auth/refresh (default 10/min) and per-user for run-ai,
   generate, extract endpoints (default 6/min). Redis-backed when redis_url is
   reachable, in-memory fallback otherwise (tests use in-memory). 429 with
   Retry-After header and a structured log line. Limits in config.

Tests: two-thread concurrency test (one 409); timeout test (blocking stub -> 504,
no state change); fresh-session FAILED audit row test; guard matrix; 429 tests
with Retry-After; open-draft guard tests for all three services.

## Task S2-B: Extraction robustness (C-3 through C-8)

Files: apps/api/app/tech_debt/parsers.py, tech_debt/extract.py,
routes/tech_debt.py, routes/artifacts.py, storage/s3.py, storage/base or
factory as needed, routes/attack.py + csf.py + zt.py (evidence PATCH tenant
check only), schemas/tech_debt.py (parse report fields), apps/web proxy
api/proxy/artifacts/route.ts (Content-Length pre-check), tests/unit.

1. C-3: parse all sheets; pick the sheet with the most plausible data rows
   (heuristic: most non-empty rows under a non-empty header); uniquify duplicate
   headers (name, name_2); keep overflow cells under generated headers (col_N);
   return parse metadata (sheet_used, rows_parsed, rows_skipped, truncated) in
   the extract response (extend the truncated field added in Sprint 1 into a
   parse_report object; keep truncated for compatibility).
2. C-4: tolerant numeric parser for annual_cost_usd and license_count: strip
   currency symbols/commas/trailing words, parse leading number; unparseable ->
   None plus append the raw string to notes ("cost: '120k EUR'"). Clamp
   confidence_pct 0-100 at extraction AND add validation on the PATCH schema.
3. C-5: \_parse_response raises ValueError when decoded JSON is not
   {"items": [...]} (list-shaped or wrong key), and when items is empty for
   non-empty input; route maps to 502 with a clear message; no CapabilityList
   row is created.
4. C-6: magic-byte sniffing in upload_artifact: PK\x03\x04 for zip-family
   (xlsx/docx/zip), %PDF, OLE2 D0CF11E0 (reject: legacy office), PNG/JPEG
   signatures, UTF-8/ASCII text heuristic for csv/txt. Mismatch with claimed
   MIME -> 415. Check Content-Length before reading (413 when over limit)
   and stream-read with an incremental cap. Same Content-Length pre-check in
   the Next proxy route (return 413 early).
5. C-7: \_load_artifact_bytes uses storage.get() for all backends with boto
   Config(connect_timeout=5, read_timeout=30, retries={'max_attempts': 2}) on
   the S3 client; S3Storage.get catches NoSuchKey/404 as FileNotFoundError and
   raises StorageUnavailableError (new, in storage module) for
   credential/connection errors; routes map FileNotFoundError -> 410 (existing
   text) and StorageUnavailableError -> 503 "document storage is temporarily
   unreachable".
6. C-8: the three evidence PATCH sites call require_artifact_in_tenant
   (404 on missing or cross-tenant). Upload dedup: when an artifact with the
   same sha256 for the same client exists, return it with 200 and
   already_uploaded=true instead of storing a copy (add the response field).

Tests: multi-sheet workbook (data on sheet 2), duplicate headers, ragged rows,
BOM CSV, money matrix ("$1,200,000", "1,200,000", "500 seats", "EUR 120k" ->
notes), confidence clamp, wrong-shape 502 (list, wrong key, empty items),
spoofed MIME 415, oversized Content-Length 413 without body read, storage
outage 503 vs missing key 410 (stubbed boto), cross-tenant evidence 404,
double-upload returns same artifact id.

## Task S2-C: Contracts, persistence, config truth (A-6, E-4, E-5, F-1, F-2, H-5, H-6)

Files: apps/api/app/ai/contracts.py (validation helpers), ai/llm.py (mode on
responses; preview), ai/engine.py (preview path), routes/csf.py (auto-seed,
run-ai), routes/zt.py + attack.py (auto-create on run-ai; narratives persist),
routes/admin.py (banner copy, ai-usage), routes/risk.py (uses contracts
validation), models/zt_assessment.py + attack_assessment.py + llm_call.py +
new migrations (narratives JSON, ai_summaries JSON, llm_calls.client_id),
schemas (run-ai responses carry mode + simulated flag; ai-usage), tests/unit.

1. A-6: add validate_response(name, data) in contracts.py (structural check:
   required keys, list types, enum membership; lightweight, no jsonschema dep);
   every run-ai/generate/extract route validates the parsed data before its
   apply loop and returns 502 on mismatch. Gated live smoke test: one test
   module skipped unless SHIELD_LIVE_SMOKE=1 and ANTHROPIC_API_KEY set, making
   the smallest real call per job and asserting parseability.
2. E-4: additive migrations + columns zt_assessments.narratives (JSON nullable)
   and attack_assessments.ai_summaries (JSON nullable); run-ai persists them;
   GET endpoints return them.
3. E-5: banner truth: routes/admin.py fixture-mode copy becomes "AI suggestions
   are simulated (deterministic fixtures) for demo and testing; set
   SHIELD_LLM_MODE=live for real analysis." Run-ai responses gain
   mode: "fixture"|"live" (schemas), so the UI can badge simulated output
   (frontend badge is Task S2-D).
4. F-1: csf run-ai (and create-assessment) auto-seed working profiles when rows
   are absent (call the existing seed logic); the seed endpoint stays for
   re-sync; the 409 goes away.
5. F-2: zt and attack run-ai auto-create a draft assessment (with seeding)
   when none exists, using the open-draft guard from S2-A (depends on S2-A
   landing first; it will be in the tree).
6. H-5: additive migration llm_calls.client_id (UUID nullable, indexed);
   LLMClient.invoke accepts client_id and every run-ai/generate/extract call
   site passes the tenant id; GET /admin/ai-usage returns per-client per-month
   calls, input/output tokens, estimated cost (price table constant in config:
   per-model USD per Mtok in/out), plus CSV via ?format=csv.
7. H-6: POST .../run-ai?preview=1 on all five job routes: build inputs, run
   redaction, return {redacted_payload, redaction_summary} WITHOUT calling the
   provider and WITHOUT an llm_calls row (or one with mode=preview; choose and
   document). First non-preview live run per client requires a recorded
   acknowledgment: POST /admin/ai-preview-ack {client_id} writes an audit
   entry; live run-ai returns 428 "preview acknowledgment required" when no
   ack exists for the client (fixture mode unaffected). Keep it lightweight.

Tests: response validation per job (bad shapes 502); narrative persistence;
banner copy; auto-seed (no 409 on fresh); auto-create draft on run-ai;
client_id lands on llm_calls rows for every job type; ai-usage math against
seeded rows; preview returns redacted payload with zero provider calls; live
run without ack 428, with ack proceeds (stub provider).

## Task S2-D: Frontend navigation and truth (D-1, D-2, D-3, E-5 badge)

Files: apps/web/src (AssessmentsView.tsx, AdminShell.tsx, InboxView.tsx,
RiskRegisterDashboard.tsx, lib/intake/client.ts, lib/messages/client.ts,
messages page, self-assessment page + new client detail page for
tech_debt/attack, AiStatusBanner.tsx, workspace components showing AI results
for the simulated badge, api proxy routes as needed), e2e not included (QA task).

1. D-1: every assessment card links at every status (CSF/ZT -> self-assessment
   page which renders read-only post-submit with MessageThread; tech_debt and
   attack_coverage -> new minimal client detail page: service name, status
   timeline, MessageThread). /messages becomes a real inbox: list the client's
   threads with unread counts, each linking to the right page.
2. D-2: mount the existing ClientSwitcher in AdminShell header; Risk Register
   and Inbox no-client empty states render the switcher inline; messages 400
   maps to that empty state.
3. D-3: ProxyError prefers the typed detail message from the payload
   (error.message or detail string) with a friendly generic fallback per
   status; AssessmentsView renders a "Finish intake" link to /intake for the
   incomplete-intake case; sweep err.message render sites in AssessmentsView
   and InboxView.
4. E-5 badge: when a run-ai response carries mode:"fixture", show a small
   "simulated" badge next to AI-applied results in the CSF/ZT/ATT&CK/Tech Debt
   workspaces and render the corrected AiStatusBanner copy.

Validation: pnpm -F web lint, typecheck, build all green. Unit-test-free zone
(no web test runner configured); correctness is covered by the QA task's specs.

## Task S2-E: Playwright QA for Sprint 2

Files: e2e/sprint2.spec.ts (+helpers). Flows: client submits a self-assessment,
admin replies, client clicks from My Assessments into the thread and reads the
reply (the D-1 click path); fresh admin session picks a client via the switcher
in the admin shell and reaches the Risk Register (D-2); fixture-mode simulated
badge visible after a run-ai (E-5); intake-incomplete error shows friendly copy
with a Finish intake link (D-3). Restart stack with migrations first.
