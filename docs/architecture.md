# Architecture

> Authoritative spec: [`reference-docs/SHIELDv2_Master_Spec.txt`](../reference-docs/SHIELDv2_Master_Spec.txt) §§ 4, 11, 16. This document is the narrative version and describes the system **as actually built** (see `DECISIONS.md` for where implementation diverged from the original spec).

## 10,000-foot view

SHIELD is a **multi-tenant** web platform: one deployment serves many client
organizations, isolated by a `client_id` column on every business row (see
_Multi-tenancy_ below; introduced in Alembic migration `0013`, `DECISIONS.md`
D-015). Each deployment exposes three experiences:

- A **public experience** for unauthenticated users (marketing, intake start).
- An **operational dashboard** for Admins (Kentro consultants) and Reviewers.
- An **executive experience** for Client leadership.

All three are delivered by one Next.js app talking to one FastAPI service over
shared infrastructure.

## Components

```
┌────────────────────────────────────────────────────────────┐
│                  Browser (3 experiences)                   │
└──────────────────┬─────────────────────────────────────────┘
                   │ TLS 1.2+
                   ▼
┌────────────────────────────────────────────────────────────┐
│  apps/web — Next.js 14 (App Router, TS strict)             │
│  • NextAuth CredentialsProvider → SHIELD-issued JWT (v1)   │
│  • Tailwind + shadcn (Round 6 design language)             │
│  • Server Components / Server Actions; forwards X-Client-Id │
└──────────────────┬─────────────────────────────────────────┘
                   │ HTTPS (server-side calls)
                   ▼
┌────────────────────────────────────────────────────────────┐
│  apps/api — FastAPI (Python 3.12)                          │
│  • Pydantic v2 schemas; SQLAlchemy 2 + Alembic             │
│  • current_client dependency → per-tenant data scoping     │
│  • Redis token-bucket rate limiter (auth + AI endpoints)   │
│  • Synchronous AI engine (app/ai/engine.py) — NO worker    │
│  • PII redactor as a SECURITY BOUNDARY on every LLM call   │
│  • Global exception handler → correlation-id-only 500      │
└──────┬───────────────┬───────────────┬───────────────┬─────┘
       │               │               │               │
       ▼               ▼               ▼               ▼
   Postgres 16     Redis 7         MinIO (S3)      Keycloak 25
   (all data,     (rate-limit     (artifacts,     (realm scaffold;
    audit trail)   buckets;        uploads;        NOT consumed by
                   cache)          SSE-KMS prod)   the API in v1)

   MailHog (dev SMTP sink)   — email delivery is flag-gated (off in v1)
```

There is **no Celery worker and no async queue.** `apps/worker/` contains only a
`.gitkeep`, and `docker-compose.yml` defines no worker service. AI runs execute
inline in the request that triggers them (see _AI subsystem_). Redis is used by
the rate limiter and as an optional cache; it has no queue consumer.

## Tech stack (as built)

| Layer           | Choice                                                                                     | Notes                                                                     |
| --------------- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------- |
| Frontend        | Next.js 14 App Router + React + TypeScript + Tailwind + shadcn/ui (self-hosted, copied in) | Matches Round 6 design language; SSR for executive PDFs                   |
| Backend         | FastAPI on Python 3.12                                                                     | Native async; OpenAPI for type generation                                 |
| Database        | PostgreSQL 16                                                                              | Alembic migrations; one shared DB, tenant column (not schema-per-tenant)  |
| Cache + limiter | Redis 7                                                                                    | Token-bucket rate limiter; optional cache. **Not** a Celery broker        |
| Object storage  | S3-compatible (MinIO in dev; AWS S3 + KMS or Azure Blob in prod)                           | Deliverables + uploads                                                    |
| IdP scaffold    | Keycloak 25 (OIDC)                                                                         | Realm exported under `infra/keycloak/`; **not** consumed by the API in v1 |
| AI              | Anthropic Claude (env-configurable provider/model; `fixture` mode offline)                 | Per-job model selection (Haiku/Sonnet); synchronous                       |
| Migrations      | Alembic                                                                                    | No manual schema edits                                                    |
| Tests           | pytest + Playwright                                                                        | Unit + integration + e2e in CI                                            |

## Multi-tenancy

- Every business table carries a `client_id`; every data route resolves the
  active tenant via the `current_client` FastAPI dependency and filters by it.
  Id-based fetches verify ownership through `app/tenant.py` helpers that return
  **404 on tenant mismatch** (no existence oracle).
- Client-role users are pinned to their `User.client_id`. Platform admins /
  reviewers have `client_id IS NULL` and select the active tenant via the
  `X-Client-Id` request header (surfaced as a top-nav client switcher; the web
  app forwards a `shield_active_client_id` cookie as that header).
- Isolation model is **shared-DB-with-tenant-column**, not schema- or
  DB-per-tenant. Rationale and the migration-0013 backfill are recorded in
  `DECISIONS.md` D-015.

## AI subsystem (synchronous)

AI runs are **synchronous**: the `run-ai` / generate / extract endpoints call
`app/ai/engine.py::run_job` inline and return when the model responds. There is
no background job, queue, or worker.

```
route → engine.run_job(job_name, inputs, client_id, ...)
          → LLMClient.invoke:
              1. redact payload (app/ai/redact.py) — SECURITY BOUNDARY
              2. write llm_calls audit row (before send)
              3. call provider (or fixture)
          → job.parser(response) → validated result
```

- **Contract layer** (`app/ai/contracts.py`): each job declares an expected
  response shape; `validate_response(name, data)` checks the model's output
  against that contract so a malformed/hallucinated response is rejected rather
  than persisted. `describe_shape` renders the contract into the prompt.
- **Per-job models** (`app/ai/jobs.py`): jobs register with their own model and
  token budget — cheaper Haiku (`claude-haiku-4-5`) for extraction/scoring-style
  jobs, Sonnet for synthesis — instead of one global model. `model=None`
  inherits the deployment default (`SHIELD_LLM_MODEL`).
- **Provider** is selected by `SHIELD_LLM_PROVIDER`; no endpoint is hardcoded.
  `SHIELD_LLM_MODE=fixture` short-circuits to canned responses for offline tests.
- **Rate limiting**: AI run endpoints are per-user rate-limited via the Redis
  token bucket (`SHIELD_RATE_LIMIT_AI_PER_MIN`); auth endpoints are per-IP.
- **Advisory locks + preview/ack gate**: generate paths take a Postgres advisory
  lock to serialize concurrent runs, and the first live (non-fixture) run per
  client requires a one-time recorded acknowledgment after a redaction
  **preview** (`?preview=1` returns the redacted payload + a redaction summary
  without calling the provider).

## Redaction (one-way security boundary)

- `app/ai/redact.py` redacts PII/org identifiers out of every payload before it
  leaves the trust boundary for the LLM. It is a **security boundary, not a
  convenience**, and it is **one-way**: there is no `unredact` — redacted spans
  are replaced with placeholder tokens and never reconstructed. The model's
  response is used as returned; nothing is "de-redacted".
- The redaction preview (above) lets an operator inspect exactly what would be
  sent before the first live run, and the acknowledgment is audited.

## Audit trail

- Every state-changing route writes one row to the **`audit_entries`** table
  (model `AuditEntry`) via `app/audit`’s `audit()` helper. Rows record actor
  user id + role, action verb, target type + id, a JSON details/diff,
  correlation id, and timestamp. Rows are immutable by contract (append-only;
  no application path deletes them).
- Every LLM call additionally writes an **`llm_calls`** row (model `LLMCall`):
  purpose, prompt version, model, token counts, and (H-5) the `client_id` for
  per-tenant usage accounting surfaced at `GET /admin/ai-usage`.

## Deliverable pipeline

Each service (Tech Debt, Zero Trust, NIST CSF, ATT&CK, Risk Register) produces
downloadable deliverables:

1. **Analysis** runs in-engine (`app/<service>/…`) over the client's answers /
   capability list, optionally enriched by a reviewed AI narrative.
2. **Exporters** (`app/<service>/exporters.py`) render PDF (executive), XLSX
   (working detail), and DOCX where applicable, from the same analysis object so
   formats stay consistent.
3. **Filenames** route through a shared `deliverable_filename` helper for a
   uniform, slugified naming scheme.
4. Artifacts are stored in object storage (tenant-scoped) and surfaced through
   the admin/reviewer workspace and the client's executive view. Client release
   is deprecated for v1 (terminal statuses read "Complete: your consultant will
   deliver your report"; see `DECISIONS.md` D-006 / G-1 remediation).

## Security controls: present vs planned

**Present (enforced today):**

- Multi-tenant isolation with 404-on-mismatch (no existence oracle).
- Argon2id password hashing; short-lived HS256 JWT access tokens; account
  lockout after N failed attempts in a window.
- PII redaction on every LLM payload (one-way); redaction preview + one-time
  live-run acknowledgment (audited).
- Redis token-bucket rate limiting on auth (per-IP) and AI (per-user) endpoints.
- Append-only audit trail (`audit_entries`) + per-call LLM ledger (`llm_calls`).
- Global exception handler (correlation-id-only 500s; no stack traces to users);
  security headers (HSTS, X-Frame-Options, Referrer-Policy, Permissions-Policy).
- Backup/restore scripts + a CI-ready restore drill (`docs/runbooks/backup-restore.md`).

**Planned (NOT yet enforced):**

- **MFA + email verification** — deferred (Master Spec §2); flags exist but the
  enrollment/verification flows are not built.
- **Idle timeout, forced re-auth, refresh-token rotation/revocation** —
  `SHIELD_IDLE_TIMEOUT_SECONDS` / `SHIELD_FORCED_REAUTH_SECONDS` are loaded but
  read by no enforcement path; folded into the MFA work package with
  refresh-token rotation named first (`DECISIONS.md` D-017).
- **Keycloak/OIDC federation** — realm scaffolded but not consumed by the API in
  v1; the API issues and validates its own JWTs.
- **FedRAMP-authorized LLM connector** — the commercial provider may egress
  outside the boundary; redaction is the accepted compensating control.

## Failure model

- 4xx renders a plain-English page; never a raw JSON body in the user surface.
- 5xx returns a page with the correlation ID only — no stack trace, no internal
  error message.
- Because AI runs are synchronous, their failures surface directly as the
  triggering request's error (with correlation id), not as a deferred job status.
