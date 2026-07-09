# Runbook: Backup & Restore (H-3)

Covers the SHIELD database and object-store backup pipeline, the restore
procedure, and the automated restore drill that proves backups are recoverable.

Scope: the two stateful stores that hold customer data:

- **PostgreSQL** — all relational data (clients, services, assessments, audit
  trail, `llm_calls`).
- **Object store** — deliverable artifacts and uploads (MinIO in dev; S3 + KMS
  in prod), addressed by `S3_*` env vars.

Redis is a cache/placeholder with no durable state (see `docs/architecture.md`)
and is **not** backed up. Keycloak realm config is version-controlled under
`infra/keycloak/` and re-imported on start.

## Tooling

| Script                     | Purpose                                                             |
| -------------------------- | ------------------------------------------------------------------- |
| `scripts/backup.sh`        | `pg_dump -Fc` + object-store mirror into `backups/<timestamp>/`.    |
| `scripts/restore.sh`       | `pg_restore` into a target DB + push objects back into the bucket.  |
| `scripts/object_sync.py`   | boto3 bucket ↔ local-dir mirror (no `mc`/`aws` CLI needed).         |
| `scripts/restore_drill.py` | End-to-end recoverability proof (seed → backup → restore → assert). |
| `make backup`              | Wrapper for `scripts/backup.sh`.                                    |
| `make restore-drill`       | Wrapper for `scripts/restore_drill.py`.                             |

All scripts read `DATABASE_URL` and `S3_*` from the environment. A SQLAlchemy
`+driver` suffix (`postgresql+psycopg://…`) is stripped automatically for the
libpq tools.

## Schedule & retention

Recommended policy (implement via cron / the platform scheduler in prod):

| Tier            | Cadence          | Retention         |
| --------------- | ---------------- | ----------------- |
| Nightly full    | daily 02:00 UTC  | 14 daily copies   |
| Weekly full     | Sunday 02:00 UTC | 8 weekly copies   |
| Monthly archive | 1st of month     | 12 monthly copies |

- Store copies **off the primary host/region** (a second bucket or an offline
  vault). `backups/<timestamp>/` is the staging directory; ship it onward.
- **RPO** target: ≤ 24 h (nightly). **RTO** target: ≤ 1 h (single-DB restore).
- Prune expired copies from the archive location; `backups/` is gitignored and
  local-only.

## Taking a backup

```bash
set -a && . ./.env.e2e && set +a      # or your prod env
make backup                            # -> backups/<UTC-timestamp>/
```

Each backup directory contains:

```
backups/20260709T140958Z/
  db.dump          # pg_dump custom format (-Fc)
  objects/         # mirror of the artifacts bucket
  MANIFEST.txt     # timestamp, redacted DB URL, object count
```

### Optional: encrypt at rest (GPG hook)

Encryption is **documented but not implemented** in `backup.sh` (no key
material is created or assumed). To enable it, add after the `pg_dump` step:

```bash
# requires a trusted public key imported for $BACKUP_GPG_RECIPIENT
gpg --encrypt --recipient "$BACKUP_GPG_RECIPIENT" \
    --output "${OUT_DIR}/db.dump.gpg" "${OUT_DIR}/db.dump"
rm -f "${OUT_DIR}/db.dump"
```

Restore then decrypts with `gpg --decrypt db.dump.gpg > db.dump` before
`pg_restore`. `backup.sh` warns if `BACKUP_GPG_RECIPIENT` is set so the gap is
visible; wire the two lines above (and the decrypt in `restore.sh`) before
relying on it in production.

## Restoring

The target database must already exist and be reachable via `DATABASE_URL`.
`restore.sh` runs `pg_restore --clean --if-exists`, so a populated target is
reset to the dump's contents; prefer a fresh/empty DB for disaster recovery.

```bash
# 1. point DATABASE_URL at the RECOVERY database
export DATABASE_URL=postgresql+psycopg://shield:shield@localhost:5432/shield_recovery

# 2. (create it if needed)
psql "postgresql://shield:shield@localhost:5432/postgres" -c 'CREATE DATABASE shield_recovery;'

# 3. restore DB + objects
bash scripts/restore.sh backups/20260709T140958Z

# 4. bring the app up against the recovered DB and smoke-test
```

Step-by-step notes:

1. Freeze writes to the environment being recovered (stop the API), so the
   restore is consistent.
2. Restore into a **new** database first, validate, then cut over — never
   restore straight over a live production DB.
3. `pg_restore` emits benign "already exists" / drop notices under
   `--clean --if-exists`; a non-zero exit is a real failure.
4. Objects are pushed back into whatever bucket `S3_BUCKET` names; ensure it
   points at the recovery bucket, not production, during a test restore.

## Restore drill (recoverability proof)

`scripts/restore_drill.py` is the automated proof that a backup is actually
recoverable, suitable for CI later. It:

1. seeds a uniquely-named marker `Client` row into the source DB,
2. runs `scripts/backup.sh`,
3. drops + recreates a scratch database `<dbname>_drill` (autocommit, since
   `CREATE`/`DROP DATABASE` cannot run in a transaction),
4. runs `scripts/restore.sh` into the scratch DB,
5. asserts the marker row is present in the restored scratch DB,
6. prints `RESTORE DRILL: PASS`/`FAIL` and exits non-zero on failure,
7. cleans up (drops the scratch DB, removes the marker).

Run it against the local docker Postgres:

```bash
set -a && . ./.env.e2e && set +a
make restore-drill          # or: .venv/bin/python scripts/restore_drill.py
```

Expected tail:

```
drill: (e) marker row present in restored scratch DB
RESTORE DRILL: PASS
```

Run the drill on every schema change and at least weekly against a real backup
copy. A backup you have never restored is not a backup.

## Cloud migration note

In managed cloud (AWS GovCloud / Azure Government), prefer the platform's native
mechanisms as the primary control and keep these scripts as a portable,
audit-visible secondary:

- **Database:** enable automated snapshots + point-in-time recovery (RDS/Aurora
  PITR or Azure Database for PostgreSQL). `pg_dump`/`pg_restore` remain the
  cross-provider portable path and the drill's engine.
- **Objects:** enable bucket **versioning** + cross-region replication and
  lifecycle rules instead of the boto3 mirror; `object_sync.py` still works
  against any S3-compatible endpoint for portability.
- **Encryption:** rely on SSE-KMS (`S3_KMS_KEY_ID`) at rest and TLS in transit;
  the GPG hook is for environments without managed KMS.
- **Secrets:** source `DATABASE_URL` / `S3_*` from the platform secret manager,
  never from a committed file.
- Keep running `make restore-drill` in CI against a scratch database regardless
  of provider — managed snapshots still need a rehearsed restore path.
