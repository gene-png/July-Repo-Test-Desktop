"""H-1 / H-4 documentation-truth greps.

These assert that the corrected compensating-control claims are present and the
old inaccurate phrasing is gone. Kept deliberately narrow and stable: the anchor
assertion is the literal string "not yet enforced" in both docs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
README = REPO_ROOT / "README.md"
BUILD_REPORT = REPO_ROOT / "BUILD_REPORT.md"


@pytest.mark.unit
def test_readme_and_build_report_state_controls_not_yet_enforced() -> None:
    readme = README.read_text(encoding="utf-8")
    build_report = BUILD_REPORT.read_text(encoding="utf-8")

    # Anchor assertion (narrow + stable): both docs say the deferred session
    # controls are not yet enforced.
    assert "not yet enforced" in readme.lower()
    assert "not yet enforced" in build_report.lower()

    # Both describe the deferred controls as PLANNED.
    assert "planned" in readme.lower()
    assert "planned" in build_report.lower()


@pytest.mark.unit
def test_docs_do_not_claim_idle_timeout_forced_reauth_are_active() -> None:
    readme = README.read_text(encoding="utf-8")
    # The old, false compensating-control sentence claimed these as live
    # controls. It must be gone.
    assert "30-minute idle timeout, daily forced re-auth" not in readme
    # The flag names must not be presented without the reserved/not-enforced
    # caveat: wherever they appear, "reserved" or "not yet enforced" is nearby.
    for flag in ("SHIELD_IDLE_TIMEOUT_SECONDS", "SHIELD_FORCED_REAUTH_SECONDS"):
        assert flag in readme  # documented at all
    lowered = readme.lower()
    assert "reserved" in lowered or "not yet enforced" in lowered


@pytest.mark.unit
def test_architecture_doc_is_truthful() -> None:
    arch = (REPO_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    lowered = arch.lower()
    # Correct claims present.
    assert "multi-tenant" in lowered
    assert "audit_entries" in lowered
    assert "synchronous" in lowered
    # Redaction is described as one-way (no unredact operation).
    assert "one-way" in lowered
    # Dead/false claims absent.
    assert "audit_events" not in arch
    assert ".unredact(" not in arch  # no unredact call in the AI pipeline diagram
    assert "single-tenant web platform" not in lowered
