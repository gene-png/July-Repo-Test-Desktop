#!/usr/bin/env bash
# Start the local e2e stack: assumes Postgres/Redis/MinIO already run on
# localhost (docker), the venv exists at .venv, and the web app is built.
# Usage: scripts/e2e-stack.sh [start|stop]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

start() {
  set -a; source .env.e2e; set +a
  (cd apps/api && ../../.venv/bin/alembic upgrade head)
  (cd apps/api && nohup ../../.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 \
      > /tmp/shield-api.log 2>&1 & echo $! > /tmp/shield-api.pid)
  (cd apps/web && API_BASE_URL=http://localhost:8000 NEXTAUTH_URL=http://localhost:3000 \
      NEXTAUTH_SECRET=e2e-local-nextauth-secret nohup node_modules/.bin/next start -p 3000 \
      > /tmp/shield-web.log 2>&1 & echo $! > /tmp/shield-web.pid)
  for i in $(seq 1 60); do
    curl -sf http://localhost:8000/health >/dev/null 2>&1 && api=1 || api=0
    curl -sf http://localhost:3000 >/dev/null 2>&1 && web=1 || web=0
    [ "$api" = 1 ] && [ "$web" = 1 ] && { echo "stack up"; exit 0; }
    sleep 2
  done
  echo "stack failed to come up" >&2
  tail -20 /tmp/shield-api.log /tmp/shield-web.log >&2
  exit 1
}

stop() {
  for f in /tmp/shield-api.pid /tmp/shield-web.pid; do
    [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null || true
    rm -f "$f"
  done
  echo "stack stopped"
}

"${1:-start}"
