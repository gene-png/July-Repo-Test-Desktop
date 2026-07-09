# Decision Log

Append-only record of every choice made during the SHIELD v2.0 autonomous build. Per AI Prompt §7 / §4.9, every time a non-obvious option is picked over an alternative, it must land here.

Each entry: `D-NNN` · date (UTC) · category · subject · decision · rationale · spec/AI-Prompt reference.

---

## D-001 — Tech stack confirmation

**2026-05-19 · architecture**
Confirm locked stack from Master Spec §2: Next.js 14 (App Router) + TypeScript + Tailwind + shadcn/ui (frontend), FastAPI on Python 3.12 (backend), PostgreSQL 16, Redis, S3-compatible object storage (MinIO in dev, S3 + KMS in prod), Keycloak/OIDC, Celery workers, Alembic migrations, Playwright E2E.
**Rationale:** Locked by Eugene in spec §2. No deviation.
**Ref:** Master Spec §2, AI Prompt §2, §8.2 (D-001).

## D-002 — AI provider for v1

**2026-05-19 · ai**
Default LLM provider is **Anthropic Claude** via `ANTHROPIC_API_KEY`, configured by `SHIELD_LLM_PROVIDER` and `SHIELD_LLM_MODEL`. Default model `claude-opus-4-7`. Env-configurable; never hardcoded.
**Rationale:** Eugene answered spec §17 Q6 with "developer's choice"; Anthropic Claude is the recommended default in spec §2 and `.env.example`. Best output quality for analytic prompts, cleanest API for redacted-payload pattern. Risk of non-FedRAMP egress accepted by Eugene; PII redaction (§12) is the primary compensating control.
**Ref:** Master Spec §2, §4.4, §17 Q6, AI Prompt §8.2 (D-002).

## D-003 — Marketing landing page (spec §17 Q1)

**2026-05-19 · ux**
Implement a polished one-page marketing landing at `/` (hero, mission, service cards, resource center, contact, footer). NOT a redirect to `/sign-in`.
**Rationale:** Eugene confirmed recommended option. Aligns with Round 6 design contract's PUBLIC / EXTERNAL EXPERIENCE tier (USWDS + Microsoft public portal styling).
**Ref:** Master Spec §17 Q1, Round 6 Design Contract (public-experience tier).

## D-004 — Self-registration allowed (spec §17 Q2)

**2026-05-19 · auth**
Allow self-registration. The first registrant on a fresh deployment becomes that deployment's Primary POC. A Kentro consultant verifies and attaches them post-registration.
**Rationale:** Eugene confirmed recommended option. Preserves the v1 onboarding process Eugene wants to keep. Compensating controls for the open-door surface: account lockout, short JWT TTLs, idle timeout, forced re-auth (Master Spec §4.5).
**Ref:** Master Spec §17 Q2, §4.5.

## D-005 — Reviewer assignment is deployment-wide (spec §17 Q3)

**2026-05-19 · auth**
Any admin in a deployment may attach a reviewer. A reviewer's scope is the entire deployment — they see all services in this single-tenant deployment, not service-by-service.
**Rationale:** Eugene confirmed recommended option. Single-tenant means one deployment = one client engagement; per-service slicing is over-engineering for v1.
**Ref:** Master Spec §17 Q3, §2 (single-tenant).

## D-006 — Deliverable approval flow (spec §17 Q4)

**2026-05-19 · workflow**
Approval flow: **admin marks deliverable "final"** → **reviewer (if any) approves** → **admin releases to client**. Reviewer step is skipped when no reviewer is attached to the engagement.
**Rationale:** Eugene confirmed recommended option. Matches Phase 5 reviewer audit-walk surface (Master Spec §15 Phase 5). The "if any" guard handles engagements without a reviewer without needing a second release path.
**Superseded in part:** the approval/release flow now runs **within a single client tenant** rather than deployment-wide (per the multi-tenant D-015 — reviewer scope is the active tenant), and the client-facing "release to client" step is **deprecated for v1** (clients never see a release action; terminal statuses read "Complete: your consultant will deliver your report" per the G-1 remediation, see `CHANGELOG.md` [Unreleased]).
**Ref:** Master Spec §17 Q4, §15 Phase 5; superseded in part by D-015.

## D-007 — ATT&CK technique scope (spec §17 Q5) **[FLIPPED FROM RECOMMENDATION]**

**2026-05-19 · service**
**Use the full MITRE ATT&CK Enterprise matrix (~600 techniques)** for every engagement. NOT the recommended curated 33–40 most-relevant subset.
**Rationale:** Eugene explicitly flipped this answer ("we should build it to use all of the 600+ items").
**Implications and requirements:**

1. `packages/attack-data/` vendors the full MITRE ATT&CK Enterprise JSON (STIX 2.1 bundle) and is load-bearing.
2. The ATT&CK questionnaire UI MUST be designed for ~600 items from day one: tactic-grouped sections (14 tactics), pagination or virtualization, search by technique ID / name / data source / platform, filter by tactic / platform / data-source-availability, bulk-mark workflows, progress persistence, auto-save on every cell.
3. Master Spec §6.10 already forbids "single massive scroll" questionnaires; this decision reinforces it.
4. Coverage scoring math is unchanged per technique; only rendering scales.
5. Coverage Report deliverable (Phase 5) must paginate by tactic to remain readable as PDF/XLSX.
   **Ref:** Master Spec §17 Q5, §15 Phase 5, §6.10.

## D-008 — AI provider for v1 (spec §17 Q6)

**2026-05-19 · ai**
See D-002. Anthropic Claude API as the v1 default, env-swappable.
**Ref:** Master Spec §17 Q6.

## D-009 — Languages and locale (spec §17 Q7)

**2026-05-19 · i18n**
English only at v1.0. Build i18n-aware (no hardcoded strings; locale-keyed message files via `next-intl` for web and `babel`/`gettext`-style catalogs for API responses). Additional locales added in v1.x as content-only PRs.
**Rationale:** Eugene confirmed recommended option. Avoids translation cost in v1 while preserving zero-rewrite extensibility.
**Rescinded for v1 (2026-07-09 remediation):** the i18n-plumbing requirement is **withdrawn for v1**. The shipped product is English-only with plain hardcoded copy; `next-intl` / gettext catalogs were not built and are **not** a v1 deliverable. The extensibility goal is deferred to whenever a second locale is actually funded; treat any claim that v1 is "i18n-aware / locale-keyed" as inaccurate. English-only at v1.0 still holds.
**Ref:** Master Spec §17 Q7.

## D-010 — Repo layout: monorepo with pnpm workspaces + Python workspace

**2026-05-19 · architecture**
Single repository, pnpm workspaces for `apps/web`, `apps/api` consumers (shared TS types), and `packages/*`. Python apps (`apps/api`, `apps/worker`) managed via Poetry with a shared root `pyproject.toml` for tooling config. CI runs all checks from the repo root.
**Rationale:** Spec §16 prescribes the directory shape. Monorepo simplifies sharing of `packages/shared-types`, `packages/csf-data`, `packages/attack-data`, `packages/zt-data` across web and API without publishing.
**Ref:** Master Spec §16, AI Prompt §8.2 (repo layout).

## D-011 — Working directory deviation

**2026-05-19 · environment**
Spec §3.2 mandates working directory `/workspaces/SHIELD062626`. Actual working directory is `/workspaces/repos/SHIELD062626` because the persistent dev-container mount in this environment is `/workspaces/repos`. All in-container paths in scripts and docs use relative paths from the repo root to remain portable across both mount points.
**Rationale:** `/workspaces/SHIELD062626` is on the overlay FS in this environment (ephemeral on container rebuild). The mounted path persists.
**Ref:** AI Prompt §3.2.

## D-012 — Dev container runs as `appuser` with passwordless sudo

**2026-05-19 · environment**
`.devcontainer/Dockerfile` creates non-root `appuser` (uid 1000) with passwordless sudo for development convenience. Production runtime images (separate Dockerfiles under `infra/docker/`) use a least-privilege non-shell user with no sudo.
**Rationale:** Required by AI Prompt §3.10 / §3.11 to prevent the autonomous agent from stalling on sudo prompts. Production posture is unchanged.
**Ref:** AI Prompt §3.10, §3.11.

## D-013 — Reference docs renamed and relocated

**2026-05-19 · housekeeping**
Reference docs in the original GitHub repo root were renamed (whitespace → underscores, parenthetical suffixes removed) and moved to `reference-docs/`. Examples:

- `AI Prompt` → `reference-docs/AI_Prompt`
- `Shield UX fix round 6 full design update for 2.0.txt` → `reference-docs/Shield_UX_Round6_Design_Contract.txt`
- `Ongoing CSF2 Artifact Tracker (1).xlsx` → `reference-docs/CSF2_Artifact_Tracker.xlsx`
- All `Step N.M ... .docx`/`.xlsx` → `reference-docs/Step_N_M_...` (underscores, no spaces, no parentheticals).
  Moves use `git mv` so history is preserved. No file deletions.
  **Rationale:** Whitespace and parentheses in filenames are hostile to scripts, CI, and Windows paths. `reference-docs/` keeps the spec library separate from build artifacts.
  **Ref:** Master Spec §15.5 (slugifier conventions apply to deliverables; we apply the same hygiene to reference filenames).

## D-015 — Multi-tenant: shared DB with `client_id` on every row

**2026-05-21 · architecture**
Platform now supports many `client` rows per deployment instead of exactly one. Tenant isolation is enforced at the data-access layer (every business table carries `client_id`; every data route filters by it) rather than via per-tenant schemas or databases. Platform-level admin/reviewer users (`User.client_id IS NULL`) pick the active tenant via an `X-Client-Id` request header surfaced as a top-nav client switcher in the frontend; client-role users are pinned to their `User.client_id` and cannot escape it. New client tenants are created by either an admin via `POST /admin/clients` or implicitly when a non-admin self-registers (a fresh `Client(legal_name="(pending intake)")` row is created and bound to the new user, which the intake wizard then fills in).
**Rationale:** Eugene requested multi-client support. The schema already denormalized `client_id` on assessment tables (Master Spec §11.1 future-proofing); this migration (0013) adds it to the remaining business tables (`services`, `service_requests`, `artifacts`) and makes every business `client_id` `NOT NULL`. Shared-DB-with-tenant-column was chosen over schema-per-tenant and DB-per-tenant because: (1) the existing data model is one column short of being ready, (2) cross-tenant admin/reporting features remain cheap, (3) operational burden (one DB to back up, migrate, monitor) does not scale with tenant count.
**Implications and requirements:**

1. Every data route (`csf`, `zt`, `attack`, `tech_debt`, `artifacts`, `deliverables`) takes a `current_client` FastAPI dependency that resolves the active tenant; reads filter by `client_id`; writes set `client_id` at row creation; id-based fetches (`db.get(Service, id)` etc.) verify ownership via `app/tenant.py` helpers that return 404 on tenant mismatch (no existence oracle).
2. `User.client_id` stays nullable for platform admins/reviewers; everyone else's is set on registration.
3. The frontend forwards the cookie-driven `shield_active_client_id` as `X-Client-Id` through `lib/api.ts`; admin-only cross-tenant routes (e.g. `GET /admin/clients`, `POST /admin/clients`) pass `clientId: ""` to suppress that header.
4. Backwards compatibility: migration `0013` backfills all existing rows to the deployment's existing singleton `client` row (or creates a `"(legacy backfill)"` placeholder if business data exists but no `client` row does).
5. D-005 ("reviewer attachment is deployment-wide") still holds _within a tenant_; a reviewer can see every service for the active client they're scoped to.

**Ref:** Master Spec §11.1 (denormalized client_id), §2 (single-tenant — now superseded for this platform), §4.5 (auth), DECISIONS D-004 (self-registration extends to per-tenant client creation).

## D-014 — Opening commit on `main`, push deferred

**2026-05-19 · git**
Opening commit lands directly on `main`. Push is deferred until the dev container has credentials configured per AI Prompt §3.3 (no agent-introduced credentials).
**Rationale:** AI Prompt §3.9 prescribes "push frequently" but §3.3 forbids the agent from introducing its own credentials. Eugene will push when he attaches a PAT or SSH key to the container.
**Ref:** AI Prompt §3.3, §3.9.

## D-016 — Part F: harden and ship decisions

**2026-06-26 · F (harden)** _(renumbered from duplicate D-015; see the multi-tenant D-015 above)_

- **Worker / async:** AI runs are **synchronous** — the `run-ai` endpoints invoke
  the LLM inline via `app.ai.engine.run_job`. There is no Celery worker; the
  orphaned `worker` service (which referenced a non-existent `app.worker`) was
  removed from `docker-compose.yml`. `redis` remains as a config placeholder for
  future rate-limiting/async but has no consumer today.
- **Auth seam:** NextAuth stays pluggable. The active login is `CredentialsProvider`
  (against the API); a Keycloak realm is scaffolded under `infra/keycloak/` so a
  SAML/OIDC provider can be added without touching call sites. MFA stays deferred.
- **Dependency audits:** `pip-audit` (API) and `pnpm audit --audit-level high`
  (web) run in CI (non-blocking; surface advisories), and `.github/dependabot.yml`
  opens the fix PRs (pip / npm / github-actions, weekly). pip-audit is clean today.
- **Accessibility:** static `jsx-a11y` rules are enforced in CI via
  `next/core-web-vitals` (the eslint step); skip-to-content links + a
  `#main-content` landmark are present in every shell (admin + client pages).
  Runtime axe/Pa11y in CI is the remaining a11y item (needs a dev-dep + a built
  app harness in CI — pnpm-lockfile change to be made in a pnpm environment).
- **IaC:** `apps/api/Dockerfile` exists; a production `apps/web/Dockerfile`
  (Next standalone) was added. `infra/terraform` for AWS GovCloud / Azure
  Government remains a skeleton — it needs concrete account/region/network
  decisions and is intentionally left as the next infra task.
- **Isolation:** `test_new_surface_authz.py` covers cross-tenant isolation for the
  new tables (messages, client_domain, risk register, CSF tier profiles); these
  run under `pytest -m unit` in CI.

**Ref:** Work Order Part F.

## D-017 — Session-control enforcement deferred into the MFA work package

**2026-07-09 · auth (remediation H-1)**
The idle-timeout and forced-re-auth session controls are **deferred**, not
shipped. `SHIELD_IDLE_TIMEOUT_SECONDS` and `SHIELD_FORCED_REAUTH_SECONDS` are
loaded by `config.py` but enforced by **no** code path today; earlier docs
(README compensating-controls, BUILD_REPORT OWASP A07, this log) presented them
as active controls, which was inaccurate. They are now annotated
RESERVED/UNIMPLEMENTED at every surface (`.env.example`, `docker-compose.yml`,
`config.py`, `infra/keycloak/README.md`).

**Decision:** enforcement of idle timeout, forced re-auth, and refresh-token
lifecycle is folded into the **MFA work package**, whose ordered scope is:

1. **Refresh-token rotation + server-side revocation** (first item) — rotate the
   refresh token on every use, persist a revocation/allow-list so a stolen or
   idled token can be invalidated server-side. This is the prerequisite that
   makes real idle-timeout and forced-re-auth enforceable.
2. Idle-timeout enforcement (reject access when `now - last_seen >
SHIELD_IDLE_TIMEOUT_SECONDS`).
3. Forced re-auth enforcement (reject when session age >
   `SHIELD_FORCED_REAUTH_SECONDS`).
4. MFA enrollment + verification (TOTP/WebAuthn) and email verification, gated by
   `SHIELD_AUTH_REQUIRE_MFA` / `SHIELD_AUTH_REQUIRE_EMAIL_VERIFY`.

Until that package lands, the **enforced** compensating controls are the short
JWT access-token TTL and account lockout only.

**Rationale:** honesty over aspiration — a control that no code reads is not a
control. Refresh-token rotation is named first because idle/forced-reauth
enforcement is not meaningful without server-side session invalidation.
**Ref:** Master Spec §2 (MFA deferred), §4.5 (session security); remediation H-1.
