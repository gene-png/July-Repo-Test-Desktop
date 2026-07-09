# Sprint 3 subagent tasking - Complete deliverables and truth

Standing rules unchanged: listed files plus tests only, no drive-by refactors,
ruff+black on touched files, targeted pytest subsets (lead runs the full suite),
plain-string HTTPException detail, batch-safe migrations (next number after 0033),
do not commit. Re-read shared files before each edit.

Sequencing: S3-A (exports) and S3-B (risk governance) in parallel (S3-A stays out
of routes/risk.py; the risk exporter file is S3-B's), then S3-C (ops/docs) and
S3-D (frontend) in parallel, S3-E (QA) last.

## Task S3-A: Export completeness (B-4, B-5, B-6 reduced, B-7 minus risk docx, H-8)

Files: apps/api/app/zt/scoring.py + zt/exporters.py, csf/gap.py + csf playbook
and finalize exporters, attack/exporters.py, tech_debt/exporters.py,
routes/zt.py + csf.py (finalize wiring, filename helpers), routes/csf.py +
models + migration for CsfActionItem (H-8), schemas/csf.py (action items),
tests/unit.

1. B-4: finalize paths pass top_n=None so the XLSX Gap Plan sheets carry every
   gap (analyze_gaps already supports top_n; verify and thread). PDF/DOCX keep
   top 20 but title "Top 20 of N remediation gaps" using total_gap_count, plus
   one line pointing to the XLSX for the full list. Update the S1-B tests if
   their bounded-12 assumption interacts (they asserted sheet rows == total; with
   top_n=None that still holds).
2. B-5: finalize calls build_roadmap (zt/scoring.py:297) on the target-correct
   analysis; XLSX gains a "Roadmap" sheet (month, capability, pillar, from-stage,
   to-stage); PDF/DOCX gain a roadmap section after the gap table; DOCX gains an
   "Answers" section mirroring the XLSX Answers sheet. If zt run-ai narratives
   exist (E-4), render the reviewed executive summary into the DOCX/PDF
   executive section.
3. B-6 reduced: the XLSX "Consolidation Plan" sheet becomes per-item rows
   (item name, disposition, consolidation target, rationale, per-item annual
   savings when cost known) with a totals row and the savings-cost-known flag;
   keep the summary block above or on the summary sheet.
4. B-7 (ATT&CK parts + filenames): sort ATT&CK gap lists by priority: tactic
   coverage percent ascending, then status (gap before partial), then technique
   code; title the section with the rule ("ordered by weakest tactic"). DOCX
   unknown-technique fallback renders the code instead of blank. Playbook export
   (routes/csf.py:~1172 base name) and Risk export filename (S3-B owns risk.py;
   you do the CSF one only) route through deliverable_filename. ATT&CK ai
   summaries (E-4) render into the exec section when present.
5. H-8: CsfActionItem model + migration (subcategory_code, assessment FK, owner
   text, due_date date, milestone note text, status enum open/in_progress/done,
   timestamps). Routes: POST /csf/assessments/{id}/action-items (create from a
   gap row), PATCH /csf/action-items/{id}, GET list on the assessment,
   DELETE (hard delete acceptable, no export history). CSF XLSX gains an
   "Action Plan" sheet; DOCX/PDF a section. Keep client-invisible (admin-gated,
   consistent with G-1).

Tests: export-content tests opening the XLSX/DOCX and comparing to engine
outputs (full gap list length; roadmap rows == build_roadmap output; "Top 20 of
N" heading; consolidation per-item rows == analyze_overlap dispositions; ATT&CK
sort order property: coverage ascending; action plan sheet mirrors created
items).

## Task S3-B: Risk register governance (F-3) plus risk export fixes (B-7 risk parts)

Files: apps/api/app/routes/risk.py, models/risk_register.py (+ entry model file
if separate - check models/), new migration (lock/approve columns), risk/engine.py
(only if tier derivation needs a helper export), risk/exporters.py (5x5 matrix in
DOCX, filename), schemas/risk.py, tests/unit.

1. Entry editing: PATCH /risk/entries/{id} (title, description, likelihood,
   impact, compensating_controls, recommended_action, rationale; tier re-derived
   from likelihood+impact via tier_for; reject edits when the register version is
   approved). Lock flag on entries (locked bool, migration): PATCH toggles;
   locked entries are preserved verbatim by regenerate (only unlocked entries are
   redrafted; keep locked rows attached to the new version or carry them over -
   design: regenerate creates a new version, copy locked entries into it, then
   fill the rest from synthesis). Soft DELETE (deleted_at nullable timestamp;
   excluded from serialization and exports).
2. Approve step: POST /risk/registers/{id}/approve sets approved_at/approved_by
   and locks the version (no further entry PATCH/DELETE/generate supersession
   until a new generate creates a draft version). Export (POST /risk/export)
   returns 409 unless the latest register is approved.
3. Gate strengthening: \_gate requires the ATT&CK assessment APPROVED and at
   least one of CSF/ZT APPROVED (not just existing). CSF harvest threshold uses
   the client's csf_target_tier (fallback 3), mirroring the ZT pattern at
   risk.py:163. Gate response lists source assessments and their statuses;
   exports label the same.
4. B-7 risk parts: DOCX gains the 5x5 matrix table (port the PDF's
   matrix_counts rendering at risk/exporters.py:242-256); export filename routes
   through deliverable_filename (import it; pattern in routes/csf.py finalize).
5. Preserve E-3 (advisory lock), H-2 (rate limit), H-6 (preview/ack) behavior on
   generate; preserve A-4 warnings.

Tests: entry PATCH/lock/delete; tier re-derivation on edit; regenerate preserves
locked entries verbatim and redrafts the rest; approve then export 200, export
before approve 409, PATCH after approve 409; gate rejects unapproved sources and
uses the client target tier for CSF harvest; DOCX matrix present; filename
pattern. Extend an export-content test asserting an edited entry lands in the
XLSX.

## Task S3-C: Ops and docs truth (E-6, H-1, H-3, H-4)

Files: apps/api/scripts/\_common.py, docker-compose.yml (packages mount if
needed), Makefile (new, or scripts/seed-all.sh), scripts/backup.sh +
scripts/restore.sh (new), docs/runbooks/backup-restore.md (new),
docs/architecture.md, README.md, BUILD_REPORT.md, DECISIONS.md, .env.example,
infra/keycloak/README.md, the two OpenAPI summaries in routes/admin.py
(lines ~323, ~376), reference-docs/ (work order copy note), tests/unit (grep
tests where specified).

1. E-6: \_common.py resolves the data dir from SHIELD_SEED_DATA_DIR env var with
   the repo-relative parents[3] fallback; ensure both module form
   (`python -m scripts.load_csf_tier_questionnaires`) and script form
   (`python scripts/load_csf_tier_questionnaires.py`) work inside the API
   container layout (fix sys.path handling); compose mounts packages/
   read-only for the api service if not already; add a `make seed` (or
   scripts/seed-all.sh invoked by a Makefile target) running demo seed plus
   both loaders.
2. H-1: README.md:~101 and BUILD_REPORT.md:~60 (A07 row) corrected to state the
   idle-timeout/forced-reauth/session controls are PLANNED, not present; mark
   SHIELD_IDLE_TIMEOUT_SECONDS/SHIELD_FORCED_REAUTH_SECONDS as
   RESERVED/UNIMPLEMENTED in .env.example and docker-compose.yml; keep the two
   config fields but comment them as loaded-but-unenforced; append a DECISIONS.md
   entry (next free D-number) deferring enforcement into the MFA package with
   refresh-token rotation and revocation named first. Add a unit test grepping
   that the flag names never appear undocumented as active controls (simple
   assertion reading the two docs for the corrected phrasing).
3. H-3: scripts/backup.sh (pg_dump custom format + `mc mirror`/aws-cli-free
   object sync via a small python boto script if mc is unavailable; timestamped
   directory; optional GPG encryption hook documented), scripts/restore.sh
   (restores the dump into a target database and syncs objects back);
   docs/runbooks/backup-restore.md documents schedule, retention, restore steps,
   and the drill; Makefile target `make restore-drill` that backs up, drops and
   restores a scratch database, and asserts a seeded record survives (implemented
   as a python script scripts/restore_drill.py usable in CI later; it must work
   against the local docker Postgres using DATABASE_URL).
4. H-4: rewrite docs/architecture.md truthfully (multi-tenant client_id scoping
   since migration 0013, no worker/Celery (apps/worker is empty; compose has no
   worker), synchronous AI runs through app/ai/engine.py, audit_entry table (not
   audit_events), redaction is one-way (no unredact), current service list,
   rate limiting + advisory locks + preview gate from this remediation).
   README.md: fix tenancy claim (line ~3), worker references (~22, ~94).
   DECISIONS.md: renumber the SECOND duplicate D-015 (line ~134) to the next
   free number with a cross-reference note; add the D-006 supersession note;
   amend D-009 to rescind i18n for v1. routes/admin.py: fix the two
   "(admin/reviewer)" summaries to "(admin)". reference-docs: add a README line
   noting the v2 Work Order is external (do not fabricate the document).
   CHANGELOG: add an Unreleased entry for this remediation work summarizing the
   three sprints briefly.

Tests: doc greps (corrected claims present; dead phrasing absent); seed loader
test invoking the module entrypoint with SHIELD_SEED_DATA_DIR set; restore
drill exercised manually by the lead (document how).

## Task S3-D: Frontend completeness (D-4, G-1, risk editor UI, H-7 viewer)

Files: apps/web/src (assessments page auth redirect, SignInForm registered
note, PublicHeader intake link, self-assessment bad-type fallback, dev
questionnaire-preview gate, admin/active real page, released-label removal,
risk register entry editing/lock/approve UI, admin audit viewer page), plus
apps/api/app/routes/admin.py + schemas + tests for the H-7 audit endpoint
(backend part of H-7 lives here to keep the pairing tight).

1. D-4 batch: /assessments does a server-side session check redirecting to
   /sign-in?callbackUrl=/assessments; SignInForm renders "Account created,
   sign in" when registered=1; PublicHeader shows an Intake link for client
   users while intake is incomplete (reuse the intake state the client pages
   already fetch; acceptable: always show Intake for client role); the bad-type
   self-assessment fallback links to /assessments; /dev/questionnaire-preview
   gated: server component checks session role==admin, else notFound();
   admin/active becomes a real cross-client list of in-progress services with
   links into the right workspaces (the admin queue endpoint already returns
   the data - inspect routes/admin.py queue and reuse; add fields only if
   already present in the API).
2. G-1: remove the client-facing "Report released" label; terminal statuses
   render "Complete: your consultant will deliver your report."; comment-gate
   the released client-visibility checks in the API? NO - API side: the three
   released-gated client views in routes/csf.py:410, attack.py:304, zt.py:541
   stay functional but get a comment marking RELEASED as deprecated-for-v1
   (client never sees a release action); enum members stay. Frontend: statusTone
   and label maps updated; no dead "released" copy anywhere client-facing.
3. Risk governance UI (pairs with S3-B): entry rows in RiskRegisterDashboard
   become editable (inline edit or small dialog: title, likelihood, impact,
   compensating controls, action, rationale), lock toggle per row, soft-delete
   button, Approve button (register-level) that then enables Export; surface
   the gate's source-assessment statuses and the generate response warnings.
   Follow existing admin workspace patterns.
4. H-7: backend GET /admin/audit (filters: client_id, action, actor_user_id,
   date from/to; paginated limit/offset; admin-gated; CSV via ?format=csv)
   over AuditEntry, plus schemas + unit tests (route filters, role gate).
   Frontend /admin/audit page: filter bar, paginated table, CSV download link;
   add to AdminShell nav.

Validation: pnpm lint/typecheck/build green; API tests for the audit endpoint
and any route you touched.

## Task S3-E: Playwright QA for Sprint 3

Files: e2e/sprint3.spec.ts. Flows: risk register full governance loop through
the UI or API (generate from approved sources, edit an entry, lock it,
regenerate preserves it, approve, export contains the edit; export before
approve 409); unauthenticated /assessments redirects to sign-in; client
terminal status shows the new "Complete" copy and no "Report released"
anywhere; /admin/audit renders entries for a known action; admin/active lists
an in-progress service linking to its workspace; dev preview 404s for
non-admin. Restart stack with new migrations first; keep all prior specs green.
