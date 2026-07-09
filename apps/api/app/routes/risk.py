"""Risk Register routes (Work Order E).

A derived, admin-only, point-in-time deliverable. Admin-only and cross-tenant:
the client id is named in the path (like /admin/services/{id}); no X-Client-Id.

  GET  /risk/clients/{cid}/gate
  POST /risk/clients/{cid}/register/generate
  GET  /risk/clients/{cid}/register/latest
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.contracts import validate_response
from app.ai.engine import run_job
from app.ai.llm import LLMClient, has_preview_ack
from app.attack.catalog import all_codes as attack_all_codes
from app.audit import audit
from app.db.session import assessment_advisory_lock, get_db
from app.dependencies import require_role
from app.docx_export import DOCX_MIME
from app.middleware.ratelimit import rate_limit_user
from app.models._common import utcnow
from app.models.artifact import Artifact, ArtifactOrigin
from app.models.attack_assessment import (
    AttackAssessment,
    AttackAssessmentStatus,
    AttackCoverage,
)
from app.models.client import Client
from app.models.csf_assessment import CsfAnswer, CsfAssessment, CsfAssessmentStatus
from app.models.risk_register import RiskEntry, RiskRegister
from app.models.service import Service
from app.models.service_request import ServiceRequest
from app.models.user import User, UserRole
from app.models.zt_assessment import ZtAnswer, ZtAssessment, ZtAssessmentStatus
from app.risk import exporters as risk_exporters
from app.risk.engine import (
    Impact,
    Likelihood,
    RecommendedAction,
    RiskAxis,
    action_counts,
    axis_counts,
    tier_counts,
    tier_for,
)
from app.routes.artifacts import _storage_dep
from app.schemas.risk import (
    RiskEntryPatch,
    RiskEntryResponse,
    RiskGateSource,
    RiskGateStatus,
    RiskRegisterResponse,
)
from app.storage import StorageBackend
from app.tech_debt.filename import deliverable_filename

# F-3: labels for the source-assessment "Sources" line in exports.
_SOURCE_KIND_LABEL = {
    "attack": "MITRE ATT&CK",
    "csf": "NIST CSF",
    "zt": "Zero Trust",
}
# §15.5 deliverable service slug for the Risk Register.
_RISK_SERVICE_SLUG = "Risk_Register"

router = APIRouter(prefix="/risk", tags=["risk-register"])

_admin_required = Depends(require_role(UserRole.ADMIN))
# H-2: per-user token-bucket limiter on the AI generate endpoint.
_ai_rate_limited = Depends(rate_limit_user())


def _llm_dep() -> LLMClient:
    return LLMClient.from_settings()


def _latest(db: Session, model, client_id: uuid.UUID):
    return db.execute(
        select(model)
        .where(model.client_id == client_id)
        .order_by(model.version.desc(), model.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _status_str(a) -> str | None:
    if a is None or a.status is None:
        return None
    return a.status.value if hasattr(a.status, "value") else str(a.status)


def _gate(db: Session, client_id: uuid.UUID) -> RiskGateStatus:
    """F-3: unlocking requires APPROVED sources, not merely present ones.

    The register may only be generated once the ATT&CK coverage mapping is
    APPROVED and at least one CSF or Zero Trust assessment is APPROVED.
    """
    attack = _latest(db, AttackAssessment, client_id)
    csf = _latest(db, CsfAssessment, client_id)
    zt = _latest(db, ZtAssessment, client_id)

    # RELEASED is a post-approval state, so it satisfies the gate too; without
    # this, a client whose sources were already released could never generate
    # a register (approve endpoints 409 on released assessments).
    attack_ok = attack is not None and attack.status in (
        AttackAssessmentStatus.APPROVED,
        AttackAssessmentStatus.RELEASED,
    )
    csf_ok = csf is not None and csf.status in (
        CsfAssessmentStatus.APPROVED,
        CsfAssessmentStatus.RELEASED,
    )
    zt_ok = zt is not None and zt.status in (
        ZtAssessmentStatus.APPROVED,
        ZtAssessmentStatus.RELEASED,
    )
    unlocked = attack_ok and (csf_ok or zt_ok)

    missing: list[str] = []
    if not attack_ok:
        missing.append(
            "a MITRE ATT&CK coverage mapping"
            if attack is None
            else "an APPROVED MITRE ATT&CK coverage mapping"
        )
    if not (csf_ok or zt_ok):
        missing.append("an APPROVED CSF or Zero Trust assessment")

    sources: list[RiskGateSource] = []
    for kind, a, approved in (
        ("attack", attack, attack_ok),
        ("csf", csf, csf_ok),
        ("zt", zt, zt_ok),
    ):
        if a is not None:
            sources.append(RiskGateSource(kind=kind, status=_status_str(a), approved=approved))

    return RiskGateStatus(
        unlocked=unlocked,
        has_attack=attack_ok,
        has_csf=csf_ok,
        has_zt=zt_ok,
        missing=missing,
        sources=sources,
    )


def _csf_target_tier(db: Session, csf: CsfAssessment) -> int:
    """The client's CSF target maturity tier (via the source request), fallback 3.

    Mirrors the ZT target-stage pattern: a subcategory below the client's chosen
    target tier is a harvestable finding.
    """
    svc = db.get(Service, csf.service_id)
    if svc is None or svc.source_request_id is None:
        return 3
    sr = db.get(ServiceRequest, svc.source_request_id)
    if sr is None or sr.csf_target_tier is None:
        return 3
    return sr.csf_target_tier


def _source_labels(g: RiskGateStatus) -> list[str]:
    """Human labels for the export "Sources" line, e.g. "MITRE ATT&CK (approved)"."""
    return [f"{_SOURCE_KIND_LABEL.get(s.kind, s.kind)} ({s.status or 'none'})" for s in g.sources]


def _require_client(db: Session, cid: uuid.UUID) -> Client:
    client = db.get(Client, cid)
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found.")
    return client


@router.get(
    "/clients/{cid}/gate",
    response_model=RiskGateStatus,
    summary="Whether the Risk Register can be generated (admin)",
)
def gate(
    cid: uuid.UUID,
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> RiskGateStatus:
    _require_client(db, cid)
    return _gate(db, cid)


def _gather_findings(db: Session, client_id: uuid.UUID) -> tuple[list[dict], set[str], set[str]]:
    """Findings (one per gap) + the valid technique/control link universes.

    valid_techniques = every technique in the client's ATT&CK assessment.
    valid_controls   = CSF subcategory codes + ZT capability codes present.
    """
    findings: list[dict] = []
    valid_techniques: set[str] = set()
    valid_controls: set[str] = set()

    attack = _latest(db, AttackAssessment, client_id)
    if attack is not None:
        rows = (
            db.execute(select(AttackCoverage).where(AttackCoverage.assessment_id == attack.id))
            .scalars()
            .all()
        )
        valid_techniques = {r.technique_code for r in rows} or set(attack_all_codes())
        for r in rows:
            if r.status in ("gap", "partial"):
                findings.append(
                    {
                        "source": "coverage_finding",
                        "source_id": r.technique_code,
                        "kind": "attack",
                        "label": f"ATT&CK {r.technique_code}: {r.status}",
                    }
                )

    csf = _latest(db, CsfAssessment, client_id)
    if csf is not None:
        # F-3: harvest against the client's chosen target tier (fallback 3),
        # mirroring the ZT target-stage pattern below.
        csf_target = _csf_target_tier(db, csf)
        for r in (
            db.execute(select(CsfAnswer).where(CsfAnswer.assessment_id == csf.id)).scalars().all()
        ):
            valid_controls.add(r.subcategory_code)
            if r.maturity_tier is not None and r.maturity_tier < csf_target:
                findings.append(
                    {
                        "source": "questionnaire_response",
                        "source_id": r.subcategory_code,
                        "kind": "csf",
                        "label": f"CSF {r.subcategory_code}: tier {r.maturity_tier}",
                    }
                )

    zt = _latest(db, ZtAssessment, client_id)
    if zt is not None:
        for r in (
            db.execute(select(ZtAnswer).where(ZtAnswer.assessment_id == zt.id)).scalars().all()
        ):
            valid_controls.add(r.capability_code)
            tgt = r.target_stage if r.target_stage is not None else 3
            if r.maturity_stage is not None and r.maturity_stage < tgt:
                findings.append(
                    {
                        "source": "questionnaire_response",
                        "source_id": r.capability_code,
                        "kind": "zt",
                        "label": f"ZT {r.capability_code}: stage {r.maturity_stage}",
                    }
                )

    return findings, valid_techniques, valid_controls


def _enum_or_none(enum_cls, value):
    # Normalize a model-supplied token before coercion (Task S1-A A-4): the
    # enums are lowercase snake_case, so tolerate "Very Low" / " HIGH " and map
    # them to very_low / high before looking them up. Unknown tokens -> None.
    if isinstance(value, str):
        value = value.strip().lower().replace(" ", "_")
    try:
        return enum_cls(value)
    except (ValueError, KeyError):
        return None


@router.post(
    "/clients/{cid}/register/generate",
    response_model=RiskRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a new Risk Register version (admin)",
    dependencies=[_ai_rate_limited],
)
def generate(
    cid: uuid.UUID,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
    llm: Annotated[LLMClient, Depends(_llm_dep)],
    preview: Annotated[
        bool, Query(description="Dry-run: return the redacted payload only")
    ] = False,
) -> RiskRegisterResponse:
    client = _require_client(db, cid)
    g = _gate(db, cid)
    if not g.unlocked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Risk Register is locked. Missing: " + "; ".join(g.missing) + ".",
        )
    # H-6: a live (non-preview) run requires a recorded redaction-preview ack.
    if not preview and llm.mode == "live" and not has_preview_ack(db, cid):
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="Redaction preview acknowledgment required for this client before live AI runs",
        )
    # E-3: serialize concurrent Risk Register generations for this client (409
    # loser on Postgres). Keyed on the client id since the register is
    # client-scoped, not assessment-scoped.
    if not preview:
        assessment_advisory_lock(db, cid)

    findings, valid_techniques, valid_controls = _gather_findings(db, cid)
    client_org = None if client.legal_name == "(pending intake)" else client.legal_name
    inputs = {
        "findings": findings,
        "valid_techniques": sorted(valid_techniques),
        "valid_controls": sorted(valid_controls),
    }

    # H-6: preview short-circuits before any provider call or llm_calls row.
    if preview:
        prev = llm.preview(purpose="risk_synthesize", inputs=inputs, client_org_name=client_org)
        return JSONResponse({"preview": True, **prev})

    # E-1: findings are already plain data; release the request connection during
    # the provider call and re-query afterward (the apply phase below reconnects
    # lazily).
    db.close()
    result = run_job(
        db,
        llm,
        "risk_synthesize",
        inputs=inputs,
        requested_by=admin.id,
        client_id=cid,
        client_org_name=client_org,
    )
    # A-6: reject a wrong-shape response before any register row is written.
    problems = validate_response("risk_synthesize", result.data)
    if problems:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI response failed validation: " + "; ".join(problems),
        )
    data = result.data if isinstance(result.data, dict) else {}

    # New version; supersede the prior current one.
    prior = _latest(db, RiskRegister, cid)
    next_version = (prior.version + 1) if prior is not None else 1
    register = RiskRegister(client_id=cid, version=next_version, generated_by=admin.id)
    db.add(register)
    db.flush()
    if prior is not None:
        prior.superseded_by = register.id

    # F-3: carry locked (non-deleted) entries from the prior version verbatim.
    # Their source_ids suppress any duplicate the synthesis produces below, so a
    # locked analyst edit always wins over a fresh AI draft of the same finding.
    locked_source_ids: set[str] = set()
    if prior is not None:
        locked_prior = (
            db.execute(
                select(RiskEntry)
                .where(
                    RiskEntry.register_id == prior.id,
                    RiskEntry.locked.is_(True),
                    RiskEntry.deleted_at.is_(None),
                )
                .order_by(RiskEntry.created_at)
            )
            .scalars()
            .all()
        )
        for e in locked_prior:
            if e.source_id:
                locked_source_ids.add(e.source_id)
            db.add(
                RiskEntry(
                    register_id=register.id,
                    client_id=cid,
                    title=e.title,
                    description=e.description,
                    axis=e.axis,
                    source=e.source,
                    source_id=e.source_id,
                    linked_techniques=e.linked_techniques,
                    linked_controls=e.linked_controls,
                    likelihood=e.likelihood,
                    impact=e.impact,
                    tier=e.tier,
                    compensating_controls=e.compensating_controls,
                    residual_risk=e.residual_risk,
                    recommended_action=e.recommended_action,
                    rationale=e.rationale,
                    origin=e.origin,
                    trust=e.trust,
                    locked=True,
                )
            )

    unrecognized = 0
    for raw in data.get("entries", []):
        if not isinstance(raw, dict) or not raw.get("title"):
            continue
        # Skip a synthesis entry that duplicates a preserved locked finding.
        if raw.get("source_id") and raw.get("source_id") in locked_source_ids:
            continue
        lk = _enum_or_none(Likelihood, raw.get("likelihood"))
        im = _enum_or_none(Impact, raw.get("impact"))
        # Count entries where a supplied likelihood/impact failed to coerce so the
        # response can warn the analyst (Task S1-A A-4).
        if (raw.get("likelihood") and lk is None) or (raw.get("impact") and im is None):
            unrecognized += 1
        # Tier is ALWAYS code-derived, never AI-set.
        tier = tier_for(lk, im).value if (lk is not None and im is not None) else None
        techs = [t for t in (raw.get("linked_techniques") or []) if t in valid_techniques]
        controls = [c for c in (raw.get("linked_controls") or []) if c in valid_controls]
        axis = _enum_or_none(RiskAxis, raw.get("axis"))
        action = _enum_or_none(RecommendedAction, raw.get("recommended_action"))
        db.add(
            RiskEntry(
                register_id=register.id,
                client_id=cid,
                title=str(raw["title"])[:512],
                description=raw.get("description"),
                axis=axis.value if axis else None,
                source=raw.get("source"),
                source_id=raw.get("source_id"),
                linked_techniques=techs,
                linked_controls=controls,
                likelihood=lk.value if lk else None,
                impact=im.value if im else None,
                tier=tier,
                compensating_controls=raw.get("compensating_controls"),
                residual_risk=raw.get("residual_risk"),
                recommended_action=action.value if action else None,
                rationale=raw.get("rationale"),
                origin="ai_generated",
                trust="admin_assisted",
            )
        )

    audit(
        db,
        action="risk_register.generated",
        target_type="risk_register",
        target_id=register.id,
        actor_user_id=admin.id,
        details={"version": next_version, "findings": len(findings)},
    )
    db.commit()
    resp = _serialize(db, register)
    resp.mode = llm.mode
    if unrecognized:
        resp.warnings = [f"{unrecognized} entries had unrecognized likelihood/impact"]
    return resp


def _write_artifact(
    db: Session,
    *,
    storage: StorageBackend,
    user: User,
    client_id: uuid.UUID,
    filename: str,
    mime_type: str,
    data: bytes,
) -> Artifact:
    from hashlib import sha256

    key = f"risk_register/{user.id}/{uuid.uuid4()}/{filename}"
    storage.put(key, data, content_type=mime_type)
    art = Artifact(
        client_id=client_id,
        title=filename,
        file_storage_key=key,
        mime_type=mime_type,
        size_bytes=len(data),
        sha256=sha256(data).hexdigest(),
        origin=ArtifactOrigin.CONSULTANT_APPROVED,
        stage="risk_register.export",
        uploaded_by=user.id,
    )
    db.add(art)
    db.flush()
    return art


@router.post(
    "/clients/{cid}/register/export",
    response_model=RiskRegisterResponse,
    summary="Render + store the current Risk Register as XLSX/PDF/Word (admin)",
)
def export(
    cid: uuid.UUID,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[StorageBackend, Depends(_storage_dep)],
) -> RiskRegisterResponse:
    client = _require_client(db, cid)
    reg = _latest(db, RiskRegister, cid)
    if reg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generate a Risk Register before exporting.",
        )
    # F-3: only an APPROVED latest register may be exported.
    if reg.approved_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approve the latest Risk Register before exporting.",
        )
    entries = (
        db.execute(
            select(RiskEntry)
            .where(RiskEntry.register_id == reg.id, RiskEntry.deleted_at.is_(None))
            .order_by(RiskEntry.created_at)
        )
        .scalars()
        .all()
    )
    org = None if client.legal_name == "(pending intake)" else client.legal_name
    sources = _source_labels(_gate(db, cid))
    ctx = risk_exporters.build_context(
        client_legal_name=org, version=reg.version, entries=entries, sources=sources
    )
    # B-7: filenames route through the §15.5 deliverable convention. The register
    # version rides the version suffix (v2+ -> _v{n}).
    today = utcnow().date()

    def _name(ext: str) -> str:
        return deliverable_filename(
            company=org,
            service_slug=_RISK_SERVICE_SLUG,
            extension=ext,
            day=today,
            version=reg.version,
        )

    xlsx = _write_artifact(
        db,
        storage=storage,
        user=admin,
        client_id=cid,
        filename=_name("xlsx"),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        data=risk_exporters.render_xlsx(ctx),
    )
    pdf = _write_artifact(
        db,
        storage=storage,
        user=admin,
        client_id=cid,
        filename=_name("pdf"),
        mime_type="application/pdf",
        data=risk_exporters.render_pdf(ctx),
    )
    docx = _write_artifact(
        db,
        storage=storage,
        user=admin,
        client_id=cid,
        filename=_name("docx"),
        mime_type=DOCX_MIME,
        data=risk_exporters.render_docx(ctx),
    )
    reg.xlsx_artifact_id = xlsx.id
    reg.pdf_artifact_id = pdf.id
    reg.docx_artifact_id = docx.id
    reg.finalized_at = utcnow()
    audit(
        db,
        action="risk_register.exported",
        target_type="risk_register",
        target_id=reg.id,
        actor_user_id=admin.id,
        details={"version": reg.version},
    )
    db.commit()
    return _serialize(db, reg)


@router.get(
    "/clients/{cid}/register/latest",
    response_model=RiskRegisterResponse,
    summary="The current Risk Register version (admin)",
)
def latest(
    cid: uuid.UUID,
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> RiskRegisterResponse:
    _require_client(db, cid)
    reg = _latest(db, RiskRegister, cid)
    if reg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Risk Register generated yet.",
        )
    return _serialize(db, reg)


def _require_editable_entry(db: Session, entry_id: uuid.UUID) -> RiskEntry:
    """Fetch a live entry and reject edits when its register version is approved."""
    entry = db.get(RiskEntry, entry_id)
    if entry is None or entry.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Risk entry not found.")
    reg = db.get(RiskRegister, entry.register_id)
    if reg is not None and reg.approved_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This Risk Register version is approved and locked. "
                "Generate a new version to edit."
            ),
        )
    return entry


@router.patch(
    "/entries/{entry_id}",
    response_model=RiskEntryResponse,
    summary="Edit a Risk Register entry; tier is re-derived (admin)",
)
def patch_entry(
    entry_id: uuid.UUID,
    body: RiskEntryPatch,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> RiskEntryResponse:
    entry = _require_editable_entry(db, entry_id)
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field is required.",
        )

    if "title" in data and data["title"] is not None:
        entry.title = str(data["title"])[:512]
    if "description" in data:
        entry.description = data["description"]
    if "compensating_controls" in data:
        entry.compensating_controls = data["compensating_controls"]
    if "rationale" in data:
        entry.rationale = data["rationale"]
    if "recommended_action" in data:
        action = _enum_or_none(RecommendedAction, data["recommended_action"])
        entry.recommended_action = action.value if action else None
    if "likelihood" in data:
        lk = _enum_or_none(Likelihood, data["likelihood"])
        entry.likelihood = lk.value if lk else None
    if "impact" in data:
        im = _enum_or_none(Impact, data["impact"])
        entry.impact = im.value if im else None
    if "locked" in data and data["locked"] is not None:
        entry.locked = bool(data["locked"])

    # Tier is ALWAYS re-derived from the entry's current likelihood + impact,
    # never client-set.
    cur_lk = _enum_or_none(Likelihood, entry.likelihood)
    cur_im = _enum_or_none(Impact, entry.impact)
    entry.tier = tier_for(cur_lk, cur_im).value if (cur_lk and cur_im) else None
    entry.trust = "admin_edited"

    audit(
        db,
        action="risk_entry.updated",
        target_type="risk_entry",
        target_id=entry.id,
        actor_user_id=admin.id,
        details={"fields": sorted(data.keys())},
    )
    db.commit()
    db.refresh(entry)
    return RiskEntryResponse.model_validate(entry, from_attributes=True)


@router.delete(
    "/entries/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a Risk Register entry (admin)",
)
def delete_entry(
    entry_id: uuid.UUID,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    entry = _require_editable_entry(db, entry_id)
    entry.deleted_at = utcnow()
    audit(
        db,
        action="risk_entry.deleted",
        target_type="risk_entry",
        target_id=entry.id,
        actor_user_id=admin.id,
        details={},
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/clients/{cid}/register/approve",
    response_model=RiskRegisterResponse,
    summary="Approve the latest Risk Register version, freezing it (admin)",
)
def approve(
    cid: uuid.UUID,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> RiskRegisterResponse:
    _require_client(db, cid)
    reg = _latest(db, RiskRegister, cid)
    if reg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Generate a Risk Register before approving.",
        )
    if reg.approved_at is not None:
        return _serialize(db, reg)  # idempotent
    reg.approved_at = utcnow()
    reg.approved_by = admin.id
    audit(
        db,
        action="risk_register.approved",
        target_type="risk_register",
        target_id=reg.id,
        actor_user_id=admin.id,
        details={"version": reg.version},
    )
    db.commit()
    return _serialize(db, reg)


def _serialize(db: Session, register: RiskRegister) -> RiskRegisterResponse:
    entries = (
        db.execute(
            select(RiskEntry)
            .where(RiskEntry.register_id == register.id, RiskEntry.deleted_at.is_(None))
            .order_by(RiskEntry.created_at)
        )
        .scalars()
        .all()
    )
    from app.risk.engine import RiskTier

    tiers = [RiskTier(e.tier) for e in entries if e.tier]
    axes = [RiskAxis(e.axis) for e in entries if e.axis]
    actions = [RecommendedAction(e.recommended_action) for e in entries if e.recommended_action]

    def _fn(aid: uuid.UUID | None) -> str | None:
        if aid is None:
            return None
        art = db.get(Artifact, aid)
        return art.title if art else None

    return RiskRegisterResponse(
        id=register.id,
        client_id=register.client_id,
        version=register.version,
        generated_by=register.generated_by,
        finalized_at=register.finalized_at,
        approved_at=register.approved_at,
        approved_by=register.approved_by,
        created_at=register.created_at,
        xlsx_artifact_id=register.xlsx_artifact_id,
        pdf_artifact_id=register.pdf_artifact_id,
        docx_artifact_id=register.docx_artifact_id,
        xlsx_filename=_fn(register.xlsx_artifact_id),
        pdf_filename=_fn(register.pdf_artifact_id),
        docx_filename=_fn(register.docx_artifact_id),
        entries=[RiskEntryResponse.model_validate(e, from_attributes=True) for e in entries],
        tier_counts=tier_counts(tiers),
        axis_counts=axis_counts(axes),
        action_counts=action_counts(actions),
    )
