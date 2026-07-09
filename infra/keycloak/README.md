# Keycloak realm

`shield-realm.json` is imported automatically when the `keycloak` service starts (`docker-compose.yml` mounts this directory at `/opt/keycloak/data/import` and the service runs `start-dev --import-realm`).

## What the realm provides

- **Realm:** `shield`. SSO session idle = 30 min, max = 24 h (mirrors the intended values of `SHIELD_IDLE_TIMEOUT_SECONDS` and `SHIELD_FORCED_REAUTH_SECONDS`). NOTE: for v1 the API issues its own JWTs and does **not** consume Keycloak tokens (see "v1 vs v1.x federation" below), so this realm's idle/max-session enforcement is **not active** on the live login path; the two SHIELD env vars are enforced by the API itself on `/auth/refresh` (D-017 auth package: rotation, revocation, idle timeout, forced re-auth); when v1.x federates through Keycloak, this realm's settings take over that role.
- **Realm roles:** `admin` (Kentro consultant), `reviewer` (read-only auditor), `client` (default).
- **Clients:**
  - `shield-web` — public OIDC client with PKCE (S256). Maps realm roles into the access token as `roles` and includes `shield-api` in the `aud` claim so the API can validate without an extra lookup.
  - `shield-api` — bearer-only client. The API uses this client ID to validate inbound tokens once OIDC federation switches on in v1.x.
- **Bootstrap user:** `dev-admin@shield.local` / `DevAdminPass2026!` (temporary password — must change on first login). For local development only.
- **Brute-force protection:** 10 failed attempts → lockout (matches Master Spec §4.5; both SHIELD's own login path and Keycloak's enforce the same counter).
- **Password policy:** 12+ chars, not equal to username or email (matches `apps/api/app/security/password.py`).

## v1 vs v1.x federation

For v1, the FastAPI API issues its own JWTs (see `apps/api/app/security/jwt.py`); Keycloak is deployed but the API does not consume Keycloak tokens yet. Flipping to Keycloak federation in v1.x requires no schema migration:

- The web app already uses NextAuth, which can switch its provider from Credentials to Keycloak by changing one config object.
- The API's audience (`KEYCLOAK_AUDIENCE=shield-api`) and issuer claims are stable across the switch — the same JWTs validate.

## Regenerating

If you change the realm in the Keycloak admin console, export it back with:

```bash
docker compose exec keycloak /opt/keycloak/bin/kc.sh export \
  --file /opt/keycloak/data/import/shield-realm.json \
  --realm shield
```

Then commit the diff.
