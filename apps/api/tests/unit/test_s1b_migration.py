"""Task S1-B / B-3: migration 0029 adds nullable scored_at to dimension scores.

Uses batch_alter_table so it applies under SQLite (the test database). Verifies
the column arrives on upgrade and is removed on downgrade.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def _cfg(url: str) -> Config:
    api_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _cols(url: str) -> set[str]:
    insp = inspect(create_engine(url, future=True))
    return {c["name"] for c in insp.get_columns("csf_dimension_scores")}


@pytest.mark.unit
def test_scored_at_migration_up_and_down(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'shield-s1b-mig.db'}"
    os.environ["DATABASE_URL"] = url
    cfg = _cfg(url)

    command.upgrade(cfg, "head")
    assert "scored_at" in _cols(url)

    command.downgrade(cfg, "0028")
    assert "scored_at" not in _cols(url)

    command.upgrade(cfg, "0029")
    assert "scored_at" in _cols(url)
