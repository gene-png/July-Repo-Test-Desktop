#!/usr/bin/env bash
#
# SHIELD restore (H-3): restore a backup directory produced by scripts/backup.sh
# into a target database and push objects back into the artifacts bucket.
#
# Usage:
#   scripts/restore.sh BACKUP_DIR
#
# Environment:
#   DATABASE_URL   target DB URL (required). The target database must already
#                  exist; restore does NOT create it (see restore_drill.py for
#                  the create-scratch-DB path). A "+driver" suffix is stripped.
#   S3_*           object-store creds. Object push is skipped if S3_BUCKET unset.
#
# pg_restore runs with --clean --if-exists so an already-populated target is
# reset to the dump's contents. Use a fresh/empty database in production DR.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BACKUP_DIR="${1:-}"
if [[ -z "${BACKUP_DIR}" ]]; then
  echo "restore: usage: scripts/restore.sh BACKUP_DIR" >&2
  exit 2
fi
if [[ ! -f "${BACKUP_DIR}/db.dump" ]]; then
  echo "restore: ${BACKUP_DIR}/db.dump not found" >&2
  exit 2
fi
if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "restore: DATABASE_URL is required" >&2
  exit 2
fi

PG_URL="${DATABASE_URL/postgresql+psycopg:/postgresql:}"
PG_URL="${PG_URL/postgresql+psycopg2:/postgresql:}"

echo "restore: pg_restore into target ..."
# --clean --if-exists so re-restoring over existing objects is idempotent.
pg_restore --clean --if-exists --no-owner --no-privileges \
  --dbname="${PG_URL}" "${BACKUP_DIR}/db.dump"

PYTHON_BIN="${PYTHON_BIN:-${REPO_ROOT}/.venv/bin/python}"
[[ -x "${PYTHON_BIN}" ]] || PYTHON_BIN="python3"

if [[ -n "${S3_BUCKET:-}" && -d "${BACKUP_DIR}/objects" ]]; then
  echo "restore: pushing objects back into the bucket ..."
  "${PYTHON_BIN}" "${SCRIPT_DIR}/object_sync.py" --reverse --dir "${BACKUP_DIR}/objects"
else
  echo "restore: S3_BUCKET unset or no objects/ dir — skipping object push" >&2
fi

echo "restore: complete"
