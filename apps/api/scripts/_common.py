"""Shared helpers for seed loaders.

Loaders are idempotent — they upsert by primary key (or natural key) so
re-running them is safe.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Resolve the workspace root from this file:
# apps/api/scripts/_common.py -> <repo root>.
WORKSPACE = Path(__file__).resolve().parents[3]


def _resolve_packages() -> Path:
    """Locate the seed-data `packages/` directory.

    Priority:
      1. ``SHIELD_SEED_DATA_DIR`` env var (absolute path). Set this in the API
         container, where the repo-relative ``parents[3]`` walk climbs above the
         image's ``/app`` root. docker-compose mounts the host ``packages/`` and
         points this at it.
      2. The repo-relative ``<repo root>/packages`` fallback, which is correct
         for a checkout run from ``apps/api`` on a developer workstation.
    """
    override = os.environ.get("SHIELD_SEED_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return WORKSPACE / "packages"


PACKAGES = _resolve_packages()


def print_progress(loader: str, message: str) -> None:
    print(f"[seed:{loader}] {message}", flush=True)


def die(loader: str, message: str, *, exit_code: int = 1) -> None:
    print(f"[seed:{loader}] ERROR: {message}", file=sys.stderr, flush=True)
    raise SystemExit(exit_code)
