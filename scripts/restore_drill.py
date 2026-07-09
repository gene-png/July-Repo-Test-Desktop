#!/usr/bin/env python3
"""Backup/restore drill (H-3).

Proves the backup pipeline actually round-trips by:

  (a) seeding a uniquely-named marker ``Client`` row via SQLAlchemy into the
      source database (``DATABASE_URL``),
  (b) running ``scripts/backup.sh`` to produce a timestamped dump,
  (c) dropping + recreating a scratch database (``<dbname>_drill``) — CREATE /
      DROP DATABASE require an autocommit connection to a maintenance DB,
  (d) running ``scripts/restore.sh`` into the scratch database,
  (e) asserting the marker row is present in the restored scratch database,
  (f) printing PASS / FAIL and exiting non-zero on failure.

Intended to run against the local docker Postgres (see ``.env.e2e``) and to be
reusable in CI later. Object-store mirroring is skipped (S3_BUCKET is cleared
for the child scripts) so the drill isolates the database round-trip.

Usage:
  DATABASE_URL=postgresql+psycopg://shield:shield@localhost:5432/shield \\
    .venv/bin/python scripts/restore_drill.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

# Bootstrap: apps/api on sys.path so `app.*` imports resolve.
REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

SCRIPTS = REPO_ROOT / "scripts"


def _run(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    print(f"drill: $ {' '.join(cmd)}", flush=True)
    # noqa: S603 - fixed argv (["bash", <repo script>]); no shell, no user input.
    proc = subprocess.run(cmd, env=env, text=True, capture_output=True)  # noqa: S603
    if proc.stdout:
        print(proc.stdout, end="", flush=True)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr, flush=True)
        raise SystemExit(f"drill: command failed ({proc.returncode}): {' '.join(cmd)}")
    return proc


def main() -> int:
    source_url_str = os.environ.get("DATABASE_URL")
    if not source_url_str:
        print("drill: DATABASE_URL is required", file=sys.stderr)
        return 2

    source_url = make_url(source_url_str)
    base_db = source_url.database or "shield"
    scratch_db = f"{base_db}_drill"
    # render_as_string(hide_password=False): SQLAlchemy's str(url) masks the
    # password as "***", which breaks real connections / subprocess env.
    maintenance_url = source_url.set(database="postgres").render_as_string(hide_password=False)
    scratch_url = source_url.set(database=scratch_db).render_as_string(hide_password=False)

    marker = f"restore-drill-marker-{uuid.uuid4()}"
    print(f"drill: marker legal_name = {marker}")

    # (a) Seed the marker row into the source DB via SQLAlchemy.
    from app.models.client import Client  # noqa: E402  (needs sys.path bootstrap)

    src_engine = create_engine(source_url_str)
    try:
        with src_engine.begin() as conn:
            conn.execute(Client.__table__.insert().values(id=uuid.uuid4(), legal_name=marker))
    finally:
        src_engine.dispose()
    print("drill: (a) seeded marker row in source DB")

    # Child scripts inherit env but with the object mirror disabled.
    child_env = dict(os.environ)
    child_env.pop("S3_BUCKET", None)

    # (b) Backup. backup.sh prints the output dir as its last stdout line.
    backup_proc = _run(["bash", str(SCRIPTS / "backup.sh")], child_env)
    out_dir = backup_proc.stdout.strip().splitlines()[-1]
    print(f"drill: (b) backup dir = {out_dir}")

    # (c) Drop + recreate the scratch database (autocommit for CREATE/DROP).
    admin_engine = create_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch_db}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{scratch_db}"'))
    finally:
        admin_engine.dispose()
    print(f"drill: (c) recreated scratch database {scratch_db}")

    # (d) Restore into the scratch DB.
    restore_env = dict(child_env)
    restore_env["DATABASE_URL"] = scratch_url
    _run(["bash", str(SCRIPTS / "restore.sh"), out_dir], restore_env)
    print("drill: (d) restored into scratch DB")

    # (e) Assert the marker survived in the scratch DB.
    scratch_engine = create_engine(scratch_url)
    try:
        with scratch_engine.connect() as conn:
            found = conn.execute(
                text("SELECT count(*) FROM client WHERE legal_name = :n"),
                {"n": marker},
            ).scalar_one()
    finally:
        scratch_engine.dispose()

    # Cleanup: drop the scratch DB and remove the marker from the source DB.
    admin_engine = create_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch_db}" WITH (FORCE)'))
    finally:
        admin_engine.dispose()
    src_engine = create_engine(source_url_str)
    try:
        with src_engine.begin() as conn:
            conn.execute(Client.__table__.delete().where(Client.__table__.c.legal_name == marker))
    finally:
        src_engine.dispose()

    if found == 1:
        print("drill: (e) marker row present in restored scratch DB")
        print("RESTORE DRILL: PASS")
        return 0
    print(f"drill: (e) marker row NOT found (count={found})", file=sys.stderr)
    print("RESTORE DRILL: FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
