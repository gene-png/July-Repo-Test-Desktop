"""H-1 / H-4 / D-017 documentation-truth greps.

Originally these asserted the retraction ("planned, not yet enforced"). The
D-017 auth package then landed (refresh rotation, revocation, idle timeout,
forced re-auth), so the docs must now claim the controls as ENFORCED and the
retraction phrasing must be gone. Anchor assertion: "enforced" appears in the
session-control claims and "not yet enforced" appears nowhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
README = REPO_ROOT / "README.md"
BUILD_REPORT = REPO_ROOT / "BUILD_REPORT.md"


@pytest.mark.unit
def test_readme_and_build_report_state_controls_enforced() -> None:
    readme = README.read_text(encoding="utf-8")
    build_report = BUILD_REPORT.read_text(encoding="utf-8")

    # The retraction phrasing is gone from both docs.
    assert "not yet enforced" not in readme.lower()
    assert "not yet enforced" not in build_report.lower()

    # Both claim rotation and the session limits as enforced, tied to D-017.
    assert "rotation" in readme.lower()
    assert "enforced" in readme.lower()
    assert "d-017" in readme.lower()
    assert "rotation" in build_report.lower()
    assert "enforced" in build_report.lower()


@pytest.mark.unit
def test_session_flags_documented_as_active_controls() -> None:
    readme = README.read_text(encoding="utf-8")
    for flag in ("SHIELD_IDLE_TIMEOUT_SECONDS", "SHIELD_FORCED_REAUTH_SECONDS"):
        assert flag in readme  # documented at all
    # No surface still marks them reserved/unimplemented.
    for path in (README, REPO_ROOT / ".env.example", REPO_ROOT / "docker-compose.yml"):
        text = path.read_text(encoding="utf-8").lower()
        assert "reserved / unimplemented" not in text, path
        assert "unimplemented" not in text, path


@pytest.mark.unit
def test_decision_log_records_the_landing() -> None:
    decisions = (REPO_ROOT / "DECISIONS.md").read_text(encoding="utf-8")
    assert "D-017" in decisions
    # The landing note exists and names the remaining scope.
    assert "0037_refresh_tokens" in decisions
    assert "MFA (TOTP)" in decisions


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
