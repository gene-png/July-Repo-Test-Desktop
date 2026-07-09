"""Risk Register: gate, generate, tier-from-code, link validation (Work Order E)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app.ai.llm import FixtureProvider, LLMClient, LLMResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import register_admin_resp


@pytest.fixture()
def app_client(tmp_path) -> Iterator[tuple[TestClient, FixtureProvider]]:
    url = f"sqlite:///{tmp_path / 'shield-risk.db'}"
    os.environ["DATABASE_URL"] = url
    api_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from app.db.session import get_db
    from app.main import create_app
    from app.routes.artifacts import _storage_dep
    from app.routes.risk import _llm_dep
    from app.storage.local import LocalFilesystemStorage

    def override_get_db() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    provider = FixtureProvider()
    storage = LocalFilesystemStorage(tmp_path / "storage")
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[_llm_dep] = lambda: LLMClient(provider)
    app.dependency_overrides[_storage_dep] = lambda: storage
    with TestClient(app) as c:
        yield c, provider


def _admin(c: TestClient) -> tuple[str, str]:
    admin = register_admin_resp(c, "admin@kentro.example")
    bearer = admin.json()["tokens"]["access_token"]
    cid = c.post(
        "/admin/clients",
        headers={"Authorization": f"Bearer {bearer}"},
        json={"legal_name": "Acme"},
    ).json()["id"]
    return bearer, cid


def _seed_attack_and_zt(
    c: TestClient, bearer: str, cid: str, *, approve: bool = True
) -> tuple[str, str]:
    """Seed an ATT&CK gap + a ZT gap.

    F-3: the gate now requires APPROVED sources, so by default both assessments
    are approved. Returns (a gap technique_code, a ZT capability_code).
    """
    h = {"Authorization": f"Bearer {bearer}", "X-Client-Id": cid}
    asvc = c.post(
        "/attack/services", headers=h, json={"kind": "attack_coverage", "title": "ATT&CK"}
    )
    a = c.post(f"/attack/services/{asvc.json()['id']}/assessments", headers=h)
    attack_aid = a.json()["id"]
    cov = a.json()["coverage"][0]
    technique = cov["technique_code"]
    c.patch(f"/attack/coverage/{cov['id']}", headers=h, json={"status": "gap"})

    zsvc = c.post("/zt/services", headers=h, json={"kind": "zero_trust_cisa", "title": "ZT"})
    za = c.post(f"/zt/services/{zsvc.json()['id']}/assessments", headers=h)
    zt_aid = za.json()["id"]
    zans = za.json()["answers"][0]
    capability = zans["capability_code"]
    c.patch(f"/zt/answers/{zans['id']}", headers=h, json={"maturity_stage": 1})

    if approve:
        assert c.post(f"/attack/assessments/{attack_aid}/approve", headers=h).status_code == 200
        assert c.post(f"/zt/assessments/{zt_aid}/approve", headers=h).status_code == 200
    return technique, capability


@pytest.mark.unit
def test_gate_locked_without_attack(app_client) -> None:
    c, _ = app_client
    bearer, cid = _admin(c)
    bh = {"Authorization": f"Bearer {bearer}"}
    g = c.get(f"/risk/clients/{cid}/gate", headers=bh)
    assert g.status_code == 200
    assert g.json()["unlocked"] is False
    # Generate refuses while locked.
    r = c.post(f"/risk/clients/{cid}/register/generate", headers=bh)
    assert r.status_code == 409


@pytest.mark.unit
def test_generate_derives_tier_in_code_and_validates_links(app_client) -> None:
    c, provider = app_client
    bearer, cid = _admin(c)
    technique, capability = _seed_attack_and_zt(c, bearer, cid)
    bh = {"Authorization": f"Bearer {bearer}"}

    assert c.get(f"/risk/clients/{cid}/gate", headers=bh).json()["unlocked"] is True

    provider.register_static(
        "risk_synthesize",
        LLMResponse(
            '{"entries": [{"title": "Credential theft exposure",'
            ' "description": "EDR gap", "axis": "detection",'
            ' "source": "coverage_finding", "source_id": "' + technique + '",'
            ' "linked_techniques": ["' + technique + '", "T9999"],'
            ' "linked_controls": ["' + capability + '", "BOGUS.XX.01"],'
            ' "likelihood": "high", "impact": "catastrophic",'
            ' "recommended_action": "remediate", "rationale": "...",'
            ' "tier": "low"}]}'  # AI's "tier" must be ignored.
        ),
    )

    r = c.post(f"/risk/clients/{cid}/register/generate", headers=bh)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["version"] == 1
    assert len(body["entries"]) == 1
    e = body["entries"][0]
    # Tier is code-derived (High + Catastrophic -> Critical), NOT the AI's "low".
    assert e["tier"] == "critical"
    # Invented technique/control dropped; only the real ones remain.
    assert e["linked_techniques"] == [technique]
    assert e["linked_controls"] == [capability]
    assert e["origin"] == "ai_generated"
    assert body["tier_counts"]["critical"] == 1
    assert body["axis_counts"]["detection"] == 1


@pytest.mark.unit
def test_export_renders_and_stores_three_files(app_client) -> None:
    c, provider = app_client
    bearer, cid = _admin(c)
    technique, capability = _seed_attack_and_zt(c, bearer, cid)
    bh = {"Authorization": f"Bearer {bearer}"}
    provider.register_static(
        "risk_synthesize",
        LLMResponse(
            '{"entries": [{"title": "Risk one", "axis": "detection",'
            ' "likelihood": "high", "impact": "catastrophic",'
            ' "recommended_action": "remediate"}]}'
        ),
    )
    c.post(f"/risk/clients/{cid}/register/generate", headers=bh)
    # F-3: export requires an approved latest register.
    assert c.post(f"/risk/clients/{cid}/register/approve", headers=bh).status_code == 200
    r = c.post(f"/risk/clients/{cid}/register/export", headers=bh)
    assert r.status_code == 200, r.text
    body = r.json()
    # B-7: filenames follow the §15.5 deliverable convention (Company_Slug + date).
    assert body["xlsx_filename"].endswith(".xlsx")
    assert body["xlsx_filename"].startswith("Acme_Risk_Register")
    assert body["pdf_filename"].endswith(".pdf")
    assert body["docx_filename"].endswith(".docx")
    # Each downloads as a real file (artifact download is tenant-scoped, so the
    # admin names the active client via X-Client-Id).
    dh = {**bh, "X-Client-Id": cid}
    xlsx = c.get(f"/artifacts/{body['xlsx_artifact_id']}/download", headers=dh)
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    pdf = c.get(f"/artifacts/{body['pdf_artifact_id']}/download", headers=dh)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-")
    docx = c.get(f"/artifacts/{body['docx_artifact_id']}/download", headers=dh)
    assert docx.status_code == 200 and docx.content[:2] == b"PK"


@pytest.mark.unit
def test_enum_or_none_normalizes_casing_and_spaces() -> None:
    from app.risk.engine import Impact, Likelihood
    from app.routes.risk import _enum_or_none

    assert _enum_or_none(Likelihood, "Very Low") is Likelihood.VERY_LOW
    assert _enum_or_none(Likelihood, "  HIGH ") is Likelihood.HIGH
    assert _enum_or_none(Impact, "Catastrophic") is Impact.CATASTROPHIC
    assert _enum_or_none(Impact, "Very High") is None  # not an Impact token
    assert _enum_or_none(Likelihood, "banana") is None
    assert _enum_or_none(Likelihood, None) is None


@pytest.mark.unit
def test_generate_normalizes_enums_and_no_warning(app_client) -> None:
    c, provider = app_client
    bearer, cid = _admin(c)
    technique, capability = _seed_attack_and_zt(c, bearer, cid)
    bh = {"Authorization": f"Bearer {bearer}"}
    # Model returns display-cased tokens; normalization must still coerce them.
    provider.register_static(
        "risk_synthesize",
        LLMResponse(
            '{"entries": [{"title": "Cased tokens", "axis": "detection",'
            ' "likelihood": "Very High", "impact": "Major",'
            ' "recommended_action": "remediate"}]}'
        ),
    )
    r = c.post(f"/risk/clients/{cid}/register/generate", headers=bh)
    assert r.status_code == 201, r.text
    body = r.json()
    e = body["entries"][0]
    assert e["likelihood"] == "very_high" and e["impact"] == "major"
    # Very High + Major -> Critical (code-derived).
    assert e["tier"] == "critical"
    assert body["warnings"] == []


@pytest.mark.unit
def test_generate_warns_on_unrecognized_likelihood_impact(app_client) -> None:
    c, provider = app_client
    bearer, cid = _admin(c)
    _seed_attack_and_zt(c, bearer, cid)
    bh = {"Authorization": f"Bearer {bearer}"}
    provider.register_static(
        "risk_synthesize",
        LLMResponse(
            '{"entries": ['
            '{"title": "Bad likelihood", "axis": "detection",'
            ' "likelihood": "extremely_high", "impact": "major",'
            ' "recommended_action": "remediate"},'
            '{"title": "Bad impact", "axis": "detection",'
            ' "likelihood": "high", "impact": "apocalyptic",'
            ' "recommended_action": "remediate"}]}'
        ),
    )
    r = c.post(f"/risk/clients/{cid}/register/generate", headers=bh)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["warnings"] == ["2 entries had unrecognized likelihood/impact"]


@pytest.mark.unit
def test_each_generate_is_a_new_version(app_client) -> None:
    c, provider = app_client
    bearer, cid = _admin(c)
    _seed_attack_and_zt(c, bearer, cid)
    bh = {"Authorization": f"Bearer {bearer}"}
    provider.register_static("risk_synthesize", LLMResponse('{"entries": []}'))

    v1 = c.post(f"/risk/clients/{cid}/register/generate", headers=bh).json()
    v2 = c.post(f"/risk/clients/{cid}/register/generate", headers=bh).json()
    assert v1["version"] == 1
    assert v2["version"] == 2
    latest = c.get(f"/risk/clients/{cid}/register/latest", headers=bh)
    assert latest.json()["version"] == 2


# ---------------------------------------------------------------------------
# F-3 governance: gate approval, entry edit/lock/delete, approve, regenerate
# ---------------------------------------------------------------------------


def _static_one_entry(provider: FixtureProvider, technique: str) -> None:
    provider.register_static(
        "risk_synthesize",
        LLMResponse(
            '{"entries": [{"title": "Credential theft exposure",'
            ' "description": "EDR gap", "axis": "detection",'
            ' "source": "coverage_finding", "source_id": "' + technique + '",'
            ' "likelihood": "high", "impact": "catastrophic",'
            ' "recommended_action": "remediate", "rationale": "..."}]}'
        ),
    )


@pytest.mark.unit
def test_gate_requires_approved_sources(app_client) -> None:
    c, _ = app_client
    bearer, cid = _admin(c)
    bh = {"Authorization": f"Bearer {bearer}"}
    # Seed but do NOT approve: gate stays locked even though sources exist.
    _seed_attack_and_zt(c, bearer, cid, approve=False)
    g = c.get(f"/risk/clients/{cid}/gate", headers=bh).json()
    assert g["unlocked"] is False
    assert g["has_attack"] is False
    # sources are listed with their (unapproved) statuses.
    kinds = {s["kind"]: s for s in g["sources"]}
    assert kinds["attack"]["approved"] is False
    assert c.post(f"/risk/clients/{cid}/register/generate", headers=bh).status_code == 409


@pytest.mark.unit
def test_gate_unlocks_when_sources_approved(app_client) -> None:
    c, _ = app_client
    bearer, cid = _admin(c)
    bh = {"Authorization": f"Bearer {bearer}"}
    _seed_attack_and_zt(c, bearer, cid)  # approve=True
    g = c.get(f"/risk/clients/{cid}/gate", headers=bh).json()
    assert g["unlocked"] is True
    assert g["has_attack"] is True and g["has_zt"] is True
    kinds = {s["kind"]: s for s in g["sources"]}
    assert kinds["attack"]["approved"] is True and kinds["attack"]["status"] == "approved"


@pytest.mark.unit
def test_patch_entry_rederives_tier(app_client) -> None:
    c, provider = app_client
    bearer, cid = _admin(c)
    technique, _ = _seed_attack_and_zt(c, bearer, cid)
    bh = {"Authorization": f"Bearer {bearer}"}
    _static_one_entry(provider, technique)
    reg = c.post(f"/risk/clients/{cid}/register/generate", headers=bh).json()
    entry_id = reg["entries"][0]["id"]
    assert reg["entries"][0]["tier"] == "critical"  # high x catastrophic

    # Lower the likelihood/impact; tier must re-derive (very_low x negligible ->
    # negligible), never taking a client-supplied tier.
    r = c.patch(
        f"/risk/entries/{entry_id}",
        headers=bh,
        json={"likelihood": "Very Low", "impact": "negligible", "title": "Edited title"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["likelihood"] == "very_low" and body["impact"] == "negligible"
    assert body["tier"] == "negligible"
    assert body["title"] == "Edited title"


@pytest.mark.unit
def test_soft_delete_entry_excluded_from_serialization(app_client) -> None:
    c, provider = app_client
    bearer, cid = _admin(c)
    technique, _ = _seed_attack_and_zt(c, bearer, cid)
    bh = {"Authorization": f"Bearer {bearer}"}
    _static_one_entry(provider, technique)
    reg = c.post(f"/risk/clients/{cid}/register/generate", headers=bh).json()
    entry_id = reg["entries"][0]["id"]
    assert c.delete(f"/risk/entries/{entry_id}", headers=bh).status_code == 204
    latest = c.get(f"/risk/clients/{cid}/register/latest", headers=bh).json()
    assert latest["entries"] == []


@pytest.mark.unit
def test_regenerate_preserves_locked_entries_verbatim(app_client) -> None:
    c, provider = app_client
    bearer, cid = _admin(c)
    technique, _ = _seed_attack_and_zt(c, bearer, cid)
    bh = {"Authorization": f"Bearer {bearer}"}
    _static_one_entry(provider, technique)
    reg = c.post(f"/risk/clients/{cid}/register/generate", headers=bh).json()
    entry_id = reg["entries"][0]["id"]
    # Edit + lock the entry.
    c.patch(
        f"/risk/entries/{entry_id}",
        headers=bh,
        json={"title": "Analyst-owned finding", "locked": True},
    )
    # Regenerate: the synthesis produces the same source_id; the locked copy wins.
    reg2 = c.post(f"/risk/clients/{cid}/register/generate", headers=bh).json()
    assert reg2["version"] == 2
    titles = [e["title"] for e in reg2["entries"]]
    assert titles.count("Analyst-owned finding") == 1
    # The AI's fresh "Credential theft exposure" draft of the same finding is
    # suppressed in favor of the locked copy.
    assert "Credential theft exposure" not in titles
    locked = [e for e in reg2["entries"] if e["locked"]]
    assert len(locked) == 1 and locked[0]["title"] == "Analyst-owned finding"


@pytest.mark.unit
def test_approve_then_export_and_edits_rejected(app_client) -> None:
    c, provider = app_client
    bearer, cid = _admin(c)
    technique, _ = _seed_attack_and_zt(c, bearer, cid)
    bh = {"Authorization": f"Bearer {bearer}"}
    _static_one_entry(provider, technique)
    reg = c.post(f"/risk/clients/{cid}/register/generate", headers=bh).json()
    entry_id = reg["entries"][0]["id"]

    # Export before approve -> 409.
    assert c.post(f"/risk/clients/{cid}/register/export", headers=bh).status_code == 409

    ap = c.post(f"/risk/clients/{cid}/register/approve", headers=bh)
    assert ap.status_code == 200
    assert ap.json()["approved_at"] is not None

    # PATCH/DELETE after approve -> 409.
    assert c.patch(f"/risk/entries/{entry_id}", headers=bh, json={"title": "x"}).status_code == 409
    assert c.delete(f"/risk/entries/{entry_id}", headers=bh).status_code == 409

    # Export now succeeds.
    assert c.post(f"/risk/clients/{cid}/register/export", headers=bh).status_code == 200

    # A fresh generate creates the next (draft) version, preserving the chain.
    reg2 = c.post(f"/risk/clients/{cid}/register/generate", headers=bh).json()
    assert reg2["version"] == 2
    assert reg2["approved_at"] is None


def _migrated_session(tmp_path):
    """A migrated sqlite DB + session for function-level engine tests."""
    url = f"sqlite:///{tmp_path / 'shield-harvest.db'}"
    os.environ["DATABASE_URL"] = url
    api_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(api_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = create_engine(url, future=True)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


@pytest.mark.unit
def test_csf_harvest_uses_client_target_tier(tmp_path) -> None:
    """CSF findings harvest against the client's csf_target_tier (fallback 3)."""
    from app.csf.catalog import all_codes as csf_all_codes
    from app.models.client import Client
    from app.models.csf_assessment import CsfAnswer, CsfAssessment
    from app.models.service import Service, ServiceKind, ServiceStatus
    from app.models.service_request import ServiceRequest, ServiceType
    from app.models.user import User, UserRole
    from app.routes.risk import _gather_findings

    db = _migrated_session(tmp_path)
    codes = sorted(csf_all_codes())
    below_code, at_code = codes[0], codes[1]

    client = Client(legal_name="Acme")
    db.add(client)
    db.flush()
    user = User(email="a@k.example", password_hash="x", role=UserRole.ADMIN)
    db.add(user)
    db.flush()

    sr = ServiceRequest(
        client_id=client.id,
        service_type=ServiceType.NIST_CSF,
        requested_by=user.id,
        csf_target_tier=4,
    )
    db.add(sr)
    db.flush()
    svc = Service(
        client_id=client.id,
        kind=ServiceKind.NIST_CSF,
        title="CSF",
        status=ServiceStatus.IN_PROGRESS,
        source_request_id=sr.id,
        opened_by=user.id,
    )
    db.add(svc)
    db.flush()
    assessment = CsfAssessment(service_id=svc.id, client_id=client.id, version=1)
    db.add(assessment)
    db.flush()
    # Tier 3 is BELOW the client's target tier of 4 -> harvested (a hardcoded <3
    # rule would have missed it). Tier 4 meets target -> not harvested.
    db.add(
        CsfAnswer(
            assessment_id=assessment.id,
            client_id=client.id,
            subcategory_code=below_code,
            maturity_tier=3,
        )
    )
    db.add(
        CsfAnswer(
            assessment_id=assessment.id,
            client_id=client.id,
            subcategory_code=at_code,
            maturity_tier=4,
        )
    )
    db.commit()

    findings, _techs, controls = _gather_findings(db, client.id)
    harvested = {f["source_id"] for f in findings if f["kind"] == "csf"}
    assert below_code in harvested
    assert at_code not in harvested
    # both codes remain in the valid-controls universe regardless of harvest.
    assert {below_code, at_code} <= controls
    db.close()


@pytest.mark.unit
def test_docx_matrix_present_and_edit_lands_in_xlsx(app_client) -> None:
    from io import BytesIO

    from docx import Document
    from openpyxl import load_workbook

    c, provider = app_client
    bearer, cid = _admin(c)
    technique, _ = _seed_attack_and_zt(c, bearer, cid)
    bh = {"Authorization": f"Bearer {bearer}"}
    _static_one_entry(provider, technique)
    reg = c.post(f"/risk/clients/{cid}/register/generate", headers=bh).json()
    entry_id = reg["entries"][0]["id"]
    c.patch(f"/risk/entries/{entry_id}", headers=bh, json={"title": "Edited weakness ABC"})
    c.post(f"/risk/clients/{cid}/register/approve", headers=bh)
    body = c.post(f"/risk/clients/{cid}/register/export", headers=bh).json()

    dh = {**bh, "X-Client-Id": cid}
    docx = c.get(f"/artifacts/{body['docx_artifact_id']}/download", headers=dh)
    doc = Document(BytesIO(docx.content))
    headings = [p.text for p in doc.paragraphs]
    assert "Likelihood x Impact matrix" in headings

    xlsx = c.get(f"/artifacts/{body['xlsx_artifact_id']}/download", headers=dh)
    wb = load_workbook(BytesIO(xlsx.content))
    ws = wb["Risk Register"]
    cells = [str(cell.value) for row in ws.iter_rows() for cell in row]
    assert any(v == "Edited weakness ABC" for v in cells)
