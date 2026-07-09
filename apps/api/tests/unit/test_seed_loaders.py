"""E-6 seed-loader path resolution.

Asserts that `scripts._common` resolves the seed-data `packages/` directory from
`SHIELD_SEED_DATA_DIR` when set, and otherwise falls back to the repo-relative
`packages/`. Also checks that both loader modules import cleanly (the sys.path
bootstrap that makes the plain `python scripts/load_*.py` form work) and point
their sources under the resolved directory.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# apps/api on sys.path so `scripts.*` imports resolve (mirrors the module form).
API_ROOT = Path(__file__).resolve().parents[2]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

REPO_ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.unit
def test_resolve_packages_honors_seed_data_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SHIELD_SEED_DATA_DIR", str(tmp_path))
    common = importlib.reload(importlib.import_module("scripts._common"))
    assert common._resolve_packages() == tmp_path.resolve()


@pytest.mark.unit
def test_resolve_packages_falls_back_to_repo_packages(monkeypatch) -> None:
    monkeypatch.delenv("SHIELD_SEED_DATA_DIR", raising=False)
    common = importlib.reload(importlib.import_module("scripts._common"))
    assert common._resolve_packages() == REPO_ROOT / "packages"


@pytest.mark.unit
def test_loaders_import_and_reference_seed_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SHIELD_SEED_DATA_DIR", str(tmp_path))
    # Reload _common first so the loaders pick up the overridden PACKAGES.
    importlib.reload(importlib.import_module("scripts._common"))
    for mod_name in (
        "scripts.load_zt_questionnaires",
        "scripts.load_csf_tier_questionnaires",
    ):
        mod = importlib.reload(importlib.import_module(mod_name))
        assert mod.SOURCES, f"{mod_name} declares no sources"
        for source in mod.SOURCES:
            assert Path(source).resolve().is_relative_to(tmp_path.resolve())
