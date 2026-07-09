#!/usr/bin/env bash
#
# SHIELD backup (H-3): dump Postgres + mirror the artifacts object store into a
# single timestamped directory.
#
# Usage:
#   scripts/backup.sh [OUTPUT_ROOT]
#
# Environment:
#   DATABASE_URL   SQLAlchemy or libpq URL for the source DB (required).
#                  A "+driver" suffix (postgresql+psycopg://...) is stripped for
#                  pg_dump.
#   S3_*           object-store creds (see object_sync.py). Object mirroring is
#                  skipped with a warning if S3_BUCKET is unset.
#   BACKUP_ROOT    default output root (default: ./backups). Overridden by the
#                  first positional argument.
#   BACKUP_GPG_RECIPIENT
#                  OPTIONAL. If set, the runbook's GPG hook (see
#                  docs/runbooks/backup-restore.md) would encrypt db.dump to
#                  db.dump.gpg. NOT enabled by default — encryption is documented
#                  but intentionally not implemented here.
#
# Output layout:
#   <root>/<UTC-timestamp>/
#       db.dump          pg_dump custom format (-Fc), restore with pg_restore
#       objects/         mirror of the artifacts bucket
#       MANIFEST.txt     provenance (timestamp, db host/name, object count)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "backup: DATABASE_URL is required" >&2
  exit 2
fi

# pg_dump does not understand SQLAlchemy's "+driver" URL suffix.
PG_URL="${DATABASE_URL/postgresql+psycopg:/postgresql:}"
PG_URL="${PG_URL/postgresql+psycopg2:/postgresql:}"

BACKUP_ROOT="${1:-${BACKUP_ROOT:-${REPO_ROOT}/backups}}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${BACKUP_ROOT}/${STAMP}"
mkdir -p "${OUT_DIR}/objects"

echo "backup: writing to ${OUT_DIR}"

# --- Database ---------------------------------------------------------------
echo "backup: pg_dump (custom format) ..."
pg_dump --format=custom --no-owner --no-privileges --file="${OUT_DIR}/db.dump" "${PG_URL}"
echo "backup: db.dump $(du -h "${OUT_DIR}/db.dump" | cut -f1)"

# Optional GPG encryption hook (documented, not implemented). See runbook.
if [[ -n "${BACKUP_GPG_RECIPIENT:-}" ]]; then
  echo "backup: BACKUP_GPG_RECIPIENT is set but encryption is a documented-only" \
       "hook; see docs/runbooks/backup-restore.md to enable it." >&2
fi

# --- Objects ----------------------------------------------------------------
PYTHON_BIN="${PYTHON_BIN:-${REPO_ROOT}/.venv/bin/python}"
[[ -x "${PYTHON_BIN}" ]] || PYTHON_BIN="python3"

if [[ -n "${S3_BUCKET:-}" ]]; then
  echo "backup: mirroring object store ..."
  "${PYTHON_BIN}" "${SCRIPT_DIR}/object_sync.py" --dir "${OUT_DIR}/objects"
else
  echo "backup: S3_BUCKET unset — skipping object mirror" >&2
fi

OBJ_COUNT="$(find "${OUT_DIR}/objects" -type f | wc -l | tr -d ' ')"
{
  echo "created_utc=${STAMP}"
  echo "database_url=$(echo "${PG_URL}" | sed 's#://[^@]*@#://***@#')"
  echo "object_count=${OBJ_COUNT}"
} >"${OUT_DIR}/MANIFEST.txt"

echo "backup: complete -> ${OUT_DIR}"
echo "${OUT_DIR}"
