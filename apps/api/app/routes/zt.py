"""Zero Trust assessment routes (Phase 5 stage 2).

Mirrors the CSF route layout but parameterized by framework. The
framework is locked at service-create time via Service.kind:

  kind=zero_trust_cisa -> ZtFramework.CISA_ZTMM_2_0
  kind=zero_trust_dod  -> ZtFramework.DOD_ZTRA

Endpoint surface:
  POST   /zt/services
  GET    /zt/catalog?framework=cisa_ztmm_2_0|dod_ztra
  POST   /zt/services/{service_id}/assessments
  GET    /zt/services/{service_id}/assessments/latest
  PATCH  /zt/answers/{answer_id}
  POST   /zt/assessments/{assessment_id}/approve
  GET    /zt/services/{service_id}/score
  GET    /zt/services/{service_id}/gap-analysis
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.contracts import validate_response
from app.ai.diff import diff_keyed_rows
from app.ai.engine import run_job
from app.ai.llm import LLMClient, has_preview_ack
from app.audit import audit
from app.db.session import assessment_advisory_lock, get_db
from app.dependencies import current_client, current_user, require_role
from app.middleware.ratelimit import rate_limit_user
from app.models._common import utcnow
from app.models.artifact import Artifact, ArtifactOrigin
from app.models.client import Client
from app.models.deliverable import Deliverable
from app.models.questionnaire import Question
from app.models.service import Service, ServiceKind, ServiceStatus
from app.models.service_request import ServiceRequest
from app.models.user import User, UserRole
from app.models.zt_assessment import (
    ZtAnswer,
    ZtAssessment,
    ZtAssessmentStatus,
    ZtFramework,
)
from app.routes.artifacts import _storage_dep
from app.schemas.tech_debt import DeliverableResponse
from app.schemas.zt import (
    CatalogCapability,
    CatalogPillar,
    CatalogResponse,
    CatalogStage,
    GapAnalysisResponse,
    GapItem,
    PillarScore,
    RoadmapEntry,
    ZtAnswerPatch,
    ZtAnswerResponse,
    ZtAssessmentResponse,
    ZtCapabilityChange,
    ZtInterviewQuestion,
    ZtQuestionnaireResponse,
    ZtRunAiResponse,
    ZtScoreSummary,
    ZtSelfAssessmentSubmit,
    ZtServiceCreateRequest,
    ZtServiceResponse,
)
from app.storage import StorageBackend
from app.tech_debt.filename import (
    SERVICE_SLUG_ZT_CISA,
    SERVICE_SLUG_ZT_DOD,
    deliverable_filename,
)
from app.tenant import (
    require_artifact_in_tenant,
    require_service_in_tenant,
    require_zt_assessment_in_tenant,
)
from app.zt.catalog import (
    all_codes,
    capabilities,
    pillars,
)
from app.zt.exporters import build_context as build_zt_context
from app.zt.exporters import render_docx as render_zt_docx
from app.zt.exporters import render_html_dashboard as render_zt_html
from app.zt.exporters import render_pdf as render_zt_pdf
from app.zt.exporters import render_xlsx as render_zt_xlsx
from app.zt.maturity import ZtFrameworkCode, level_count, stage_definitions
from app.zt.scoring import analyze_gaps, build_roadmap
from app.zt.scoring import compute as compute_score

router = APIRouter(prefix="/zt", tags=["zt"])

_admin_required = Depends(require_role(UserRole.ADMIN))
# H-2: per-user token-bucket limiter on the AI run endpoint.
_ai_rate_limited = Depends(rate_limit_user())


# ---------------------------------------------------------------------------
# Framework <-> ServiceKind mapping
# ---------------------------------------------------------------------------

_SERVICE_KIND_TO_FRAMEWORK: dict[ServiceKind, ZtFramework] = {
    ServiceKind.ZERO_TRUST_CISA: ZtFramework.CISA_ZTMM_2_0,
    ServiceKind.ZERO_TRUST_DOD: ZtFramework.DOD_ZTRA,
}


def _framework_for_kind(kind: ServiceKind) -> ZtFramework:
    try:
        return _SERVICE_KIND_TO_FRAMEWORK[kind]
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Service kind must be zero_trust_cisa or zero_trust_dod.",
        ) from exc


def _to_catalog_framework(fw: ZtFramework) -> ZtFrameworkCode:
    return (
        ZtFrameworkCode.CISA_ZTMM_2_0
        if fw == ZtFramework.CISA_ZTMM_2_0
        else ZtFrameworkCode.DOD_ZTRA
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_answers(rows: Iterable[ZtAnswer]) -> list[ZtAnswerResponse]:
    ordered = sorted(rows, key=lambda r: r.capability_code)
    return [ZtAnswerResponse.model_validate(r, from_attributes=True) for r in ordered]


def _client_target_stage(db: Session, service_id: uuid.UUID) -> int | None:
    """The ZT target stage the client chose at intake, via the source request.

    Lets the admin workspace default its gap target to the client's goal
    instead of a hardcoded stage.
    """
    svc = db.get(Service, service_id)
    if svc is None or svc.source_request_id is None:
        return None
    sr = db.get(ServiceRequest, svc.source_request_id)
    return sr.zt_target_stage if sr is not None else None


def _serialize_assessment(db: Session, a: ZtAssessment) -> ZtAssessmentResponse:
    rows = db.execute(select(ZtAnswer).where(ZtAnswer.assessment_id == a.id)).scalars().all()
    return ZtAssessmentResponse(
        id=a.id,
        service_id=a.service_id,
        framework=a.framework,
        version=a.version,
        status=a.status,
        approved_at=a.approved_at,
        approved_by=a.approved_by,
        documents_stale=a.documents_stale,
        answers=_serialize_answers(rows),
        client_target_stage=_client_target_stage(db, a.service_id),
        narratives=a.narratives,
    )


def _latest_assessment(db: Session, service_id: uuid.UUID) -> ZtAssessment | None:
    return db.execute(
        select(ZtAssessment)
        .where(ZtAssessment.service_id == service_id)
        .order_by(ZtAssessment.version.desc())
        .limit(1)
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


@router.post(
    "/services",
    response_model=ZtServiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a Zero Trust assessment service (admin)",
)
def create_zt_service(
    body: ZtServiceCreateRequest,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ZtServiceResponse:
    framework = _framework_for_kind(body.kind)
    svc = Service(
        kind=body.kind,
        status=ServiceStatus.IN_PROGRESS,
        title=body.title,
        client_id=client.id,
        source_request_id=body.source_request_id,
        opened_by=user.id,
    )
    db.add(svc)
    db.flush()
    audit(
        db,
        action="zt.service.opened",
        target_type="service",
        target_id=svc.id,
        actor_user_id=user.id,
        details={"title": svc.title, "framework": framework.value},
    )
    db.commit()
    db.refresh(svc)
    return ZtServiceResponse.model_validate(svc, from_attributes=True)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@router.get(
    "/catalog",
    response_model=CatalogResponse,
    summary="Zero Trust reference catalog",
)
def get_catalog(
    _user: Annotated[User, Depends(current_user)],
    framework: Annotated[
        ZtFramework,
        Query(description="cisa_ztmm_2_0 or dod_ztra"),
    ] = ZtFramework.CISA_ZTMM_2_0,
) -> CatalogResponse:
    cat_fw = _to_catalog_framework(framework)
    pillar_rows: list[CatalogPillar] = []
    for p in pillars(cat_fw):
        caps = [
            CatalogCapability(
                code=c.code,
                pillar_code=c.pillar_code,
                name=c.name,
                outcome=c.outcome,
            )
            for c in capabilities(cat_fw)
            if c.pillar_code == p.code
        ]
        pillar_rows.append(
            CatalogPillar(
                code=p.code,
                name=p.name,
                purpose=p.purpose,
                capabilities=caps,
            )
        )
    stages = [
        CatalogStage(
            stage=int(d.stage),
            label=d.label,
            description=d.description,
        )
        for d in stage_definitions(cat_fw)
    ]
    total = sum(len(p.capabilities) for p in pillar_rows)
    return CatalogResponse(
        framework=framework,
        pillars=pillar_rows,
        stages=stages,
        total_capabilities=total,
    )


_FRAMEWORK_TO_QUESTION_KEY: dict[ZtFramework, str] = {
    ZtFramework.CISA_ZTMM_2_0: "zt-cisa",
    ZtFramework.DOD_ZTRA: "zt-dod",
}


@router.get(
    "/services/{service_id}/questionnaire",
    response_model=ZtQuestionnaireResponse,
    summary="Verbatim ZT interview prompts for the service's framework",
)
def get_interview_questionnaire(
    service_id: uuid.UUID,
    _user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ZtQuestionnaireResponse:
    """Framework-resolved verbatim interview prompts (read-only, Work Order C8).

    Each prompt carries the ZT capability hints it informs so the workspace can
    surface it alongside the per-capability scoring grid.
    """
    svc = require_service_in_tenant(db, service_id, client.id)
    if svc.kind not in _SERVICE_KIND_TO_FRAMEWORK:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zero Trust service not found.",
        )
    framework = _framework_for_kind(svc.kind)
    framework_key = _FRAMEWORK_TO_QUESTION_KEY[framework]
    rows = (
        db.execute(
            select(Question)
            .where(Question.framework_key == framework_key)
            .order_by(Question.order_index)
        )
        .scalars()
        .all()
    )
    return ZtQuestionnaireResponse(
        framework_key=framework_key,
        framework=framework,
        questions=[
            ZtInterviewQuestion(
                external_id=q.external_id,
                section_name=q.pillar,
                order_index=q.order_index,
                stem=q.stem,
                cues=list(q.cues or []),
                capabilities=list(q.framework_activities or []),
            )
            for q in rows
        ],
    )


def _llm_dep() -> LLMClient:
    return LLMClient.from_settings()


def _new_assessment(db: Session, svc: Service, client: Client, user: User) -> ZtAssessment:
    """Mint a fresh draft ZT assessment + seeded capability answer rows (F-2).

    Adds to the session and flushes (caller commits); shared by create-assessment
    and the run-ai auto-create path. Does not apply the open-draft guard — callers
    that must respect it check _latest_assessment first.
    """
    framework = _framework_for_kind(svc.kind)
    cat_fw = _to_catalog_framework(framework)
    prior = _latest_assessment(db, svc.id)
    version = (prior.version + 1) if prior else 1
    assessment = ZtAssessment(
        service_id=svc.id,
        client_id=client.id,
        framework=framework,
        version=version,
        status=ZtAssessmentStatus.DRAFT,
    )
    db.add(assessment)
    db.flush()
    for cap in capabilities(cat_fw):
        db.add(
            ZtAnswer(
                assessment_id=assessment.id,
                client_id=client.id,
                capability_code=cap.code,
            )
        )
    audit(
        db,
        action="zt.assessment.created",
        target_type="zt_assessment",
        target_id=assessment.id,
        actor_user_id=user.id,
        details={
            "service_id": str(svc.id),
            "version": version,
            "framework": framework.value,
        },
    )
    db.flush()
    return assessment


@router.post(
    "/services/{service_id}/run-ai",
    response_model=ZtRunAiResponse,
    summary="Run the zt_score AI job: suggest current + target per capability (admin)",
    dependencies=[_ai_rate_limited],
)
def run_ai(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
    llm: Annotated[LLMClient, Depends(_llm_dep)],
    preview: Annotated[
        bool, Query(description="Dry-run: return the redacted payload only")
    ] = False,
) -> ZtRunAiResponse:
    """The ZT 'Run AI'. Suggests a current and target maturity level per
    capability (on the framework's own scale) plus per-pillar narratives. AI
    suggests; locked rows are untouched; code does the pillar roll-up + roadmap.
    Returns a 'what changed' list.
    """
    svc = require_service_in_tenant(db, service_id, client.id)
    if svc.kind not in _SERVICE_KIND_TO_FRAMEWORK:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Zero Trust service not found."
        )
    a = _latest_assessment(db, svc.id)
    auto_created = False
    if a is None:
        # F-2: auto-create a seeded draft assessment (mirrors create-assessment)
        # rather than 404-ing; the open-draft guard means a later create returns
        # this same draft.
        a = _new_assessment(db, svc, client, user)
        auto_created = True
    if a.status in (ZtAssessmentStatus.APPROVED, ZtAssessmentStatus.RELEASED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This assessment is locked."
        )
    # H-6: a live (non-preview) run requires a recorded redaction-preview ack.
    if not preview and llm.mode == "live" and not has_preview_ack(db, client.id):
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="Redaction preview acknowledgment required for this client before live AI runs",
        )
    # F-2/E-1: persist an auto-created assessment before the E-1 db.close()
    # below (which discards uncommitted work). A preview never commits it.
    if auto_created and not preview:
        db.commit()
    # E-3: serialize concurrent runs for this assessment (409 loser on Postgres).
    if not preview:
        assessment_advisory_lock(db, a.id)
    aid = a.id
    svc_id = svc.id
    client_id = client.id
    # Capture the actor id as a plain value: an auto-create commit above expires
    # the dependency-loaded `user`, and the E-1 db.close() below detaches it.
    user_id = user.id
    framework_value = a.framework.value

    cat_fw = _to_catalog_framework(a.framework)
    max_stage = level_count(cat_fw)
    valid = all_codes(cat_fw)
    rows = {
        r.capability_code: r
        for r in db.execute(select(ZtAnswer).where(ZtAnswer.assessment_id == a.id)).scalars().all()
        if r.capability_code in valid
    }
    locked_keys = frozenset(code for code, r in rows.items() if r.locked)

    # E-1: snapshot to plain data before releasing the session.
    before = {
        code: {"maturity_stage": r.maturity_stage, "target_stage": r.target_stage}
        for code, r in rows.items()
    }

    def _coerce(v: object) -> int | None:
        try:
            iv = int(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return iv if 1 <= iv <= max_stage else None

    client_org = None if client.legal_name == "(pending intake)" else client.legal_name
    inputs = {
        "framework": framework_value,
        "capabilities": sorted(rows),
        "answers": {
            code: {"notes": r.notes, "current": r.maturity_stage} for code, r in rows.items()
        },
    }

    # H-6: preview short-circuits before any provider call or llm_calls row.
    if preview:
        prev = llm.preview(purpose="zt_score", inputs=inputs, client_org_name=client_org)
        return JSONResponse({"preview": True, **prev})

    # E-1: release the request connection during the provider call; re-load rows
    # by id afterward (close() expunges the ORM objects above).
    db.close()
    result = run_job(
        db,
        llm,
        "zt_score",
        inputs=inputs,
        requested_by=user_id,
        service_id=svc_id,
        client_id=client_id,
        client_org_name=client_org,
    )
    # A-6: reject a wrong-shape response before the apply loop (nothing written).
    problems = validate_response("zt_score", result.data)
    if problems:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI response failed validation: " + "; ".join(problems),
        )
    data = result.data if isinstance(result.data, dict) else {}

    a = db.get(ZtAssessment, aid)
    rows = {
        r.capability_code: r
        for r in db.execute(select(ZtAnswer).where(ZtAnswer.assessment_id == aid)).scalars().all()
        if r.capability_code in valid
    }

    for sugg in data.get("capabilities", []):
        if not isinstance(sugg, dict):
            continue
        row = rows.get(sugg.get("code"))
        if row is None or row.locked:
            continue
        cur = _coerce(sugg.get("current"))
        if cur is not None:
            row.maturity_stage = cur
        tgt = _coerce(sugg.get("target"))
        if tgt is not None:
            row.target_stage = tgt
        row.answered_by = user_id
        row.answered_at = utcnow()

    db.flush()
    after = {
        code: {"maturity_stage": r.maturity_stage, "target_stage": r.target_stage}
        for code, r in rows.items()
    }
    diffs = diff_keyed_rows(
        before, after, ["maturity_stage", "target_stage"], locked_keys=locked_keys
    )
    changes = [
        ZtCapabilityChange(capability_code=d.key, field=ch.field, old=ch.old, new=ch.new)
        for d in diffs
        for ch in d.changes
    ]
    narratives = data.get("pillar_narratives")
    narratives = narratives if isinstance(narratives, dict) else {}
    pillar_narratives = {str(k): str(v) for k, v in narratives.items()}
    executive_summary = data.get("executive_summary") or None
    roadmap_summary = data.get("roadmap_summary") or None

    # E-4: persist the AI narrative output on the assessment so the GET echoes it.
    a.narratives = {
        "pillar_narratives": pillar_narratives,
        "executive_summary": executive_summary,
        "roadmap_summary": roadmap_summary,
    }
    a.documents_stale = True  # Work Order C3
    audit(
        db,
        action="zt.run_ai",
        target_type="zt_assessment",
        target_id=a.id,
        actor_user_id=user_id,
        details={"changed_rows": len(diffs)},
    )
    db.commit()

    answers_out = [
        ZtAnswerResponse.model_validate(r, from_attributes=True)
        for r in sorted(rows.values(), key=lambda r: r.capability_code)
    ]
    return ZtRunAiResponse(
        changed=changes,
        answers=answers_out,
        pillar_narratives=pillar_narratives,
        executive_summary=executive_summary,
        roadmap_summary=roadmap_summary,
        mode=llm.mode,
    )


# ---------------------------------------------------------------------------
# Assessments
# ---------------------------------------------------------------------------


@router.post(
    "/services/{service_id}/assessments",
    response_model=ZtAssessmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new draft Zero Trust assessment (admin)",
)
def create_assessment(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
    response: Response,
) -> ZtAssessmentResponse:
    svc = require_service_in_tenant(db, service_id, client.id)
    if svc.kind not in _SERVICE_KIND_TO_FRAMEWORK:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zero Trust service not found.",
        )

    prior = _latest_assessment(db, svc.id)
    # E-3 open-draft guard: return an in-progress (DRAFT/SUBMITTED) assessment
    # as-is (200) rather than minting a new version.
    if prior is not None and prior.status in (
        ZtAssessmentStatus.DRAFT,
        ZtAssessmentStatus.SUBMITTED,
    ):
        response.status_code = status.HTTP_200_OK
        return _serialize_assessment(db, prior)
    assessment = _new_assessment(db, svc, client, user)
    db.commit()
    db.refresh(assessment)
    return _serialize_assessment(db, assessment)


@router.get(
    "/services/{service_id}/assessments/latest",
    response_model=ZtAssessmentResponse,
    summary="Most recent Zero Trust assessment",
)
def latest_assessment(
    service_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ZtAssessmentResponse:
    svc = require_service_in_tenant(db, service_id, client.id)
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    # RELEASED is deprecated for v1 (no in-app release; G-1)
    if user.role != UserRole.ADMIN and a.status != ZtAssessmentStatus.RELEASED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ZT assessments are admin-only until released.",
        )
    return _serialize_assessment(db, a)


# ---------------------------------------------------------------------------
# Answer editing
# ---------------------------------------------------------------------------


@router.patch(
    "/answers/{answer_id}",
    response_model=ZtAnswerResponse,
    summary="Inline-update a single capability answer (admin)",
)
def patch_answer(
    answer_id: uuid.UUID,
    body: ZtAnswerPatch,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ZtAnswerResponse:
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field is required.",
        )
    row = db.get(ZtAnswer, answer_id)
    if row is None or row.client_id != client.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Answer not found.",
        )
    a = db.get(ZtAssessment, row.assessment_id)
    if a is None or a.status in (
        ZtAssessmentStatus.APPROVED,
        ZtAssessmentStatus.RELEASED,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This assessment is locked.",
        )
    if "maturity_stage" in data and data["maturity_stage"] is not None:
        s = int(data["maturity_stage"])
        # Stages are 1..level_count for the framework (CISA 1-4, DoD 1-3).
        max_stage = level_count(_to_catalog_framework(a.framework))
        if not 1 <= s <= max_stage:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"maturity_stage must be 1-{max_stage}.",
            )
        row.maturity_stage = s
    elif "maturity_stage" in data:
        row.maturity_stage = None
    if "target_stage" in data and data["target_stage"] is not None:
        ts = int(data["target_stage"])
        max_stage = level_count(_to_catalog_framework(a.framework))
        if not 1 <= ts <= max_stage:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"target_stage must be 1-{max_stage}.",
            )
        row.target_stage = ts
    elif "target_stage" in data:
        row.target_stage = None
    if "notes" in data:
        row.notes = data["notes"]
    if "evidence_artifact_id" in data:
        ev = data["evidence_artifact_id"]
        if ev is not None:
            require_artifact_in_tenant(db, ev, client.id)
        row.evidence_artifact_id = ev
    if data.get("locked") is not None:
        row.locked = bool(data["locked"])
    row.answered_by = user.id
    row.answered_at = utcnow()
    audit(
        db,
        action="zt.answer.updated",
        target_type="zt_answer",
        target_id=row.id,
        actor_user_id=user.id,
        details={
            "capability_code": row.capability_code,
            "fields": sorted(data.keys()),
        },
    )
    db.commit()
    db.refresh(row)
    return ZtAnswerResponse.model_validate(row, from_attributes=True)


# ---------------------------------------------------------------------------
# Client self-assessment (client fills their own draft, then submits for review)
# ---------------------------------------------------------------------------


@router.get(
    "/services/{service_id}/self-assessment",
    response_model=ZtAssessmentResponse,
    summary="The client's own assessment for this service (any status)",
)
def get_self_assessment(
    service_id: uuid.UUID,
    _user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ZtAssessmentResponse:
    """Read the client's own assessment so they can fill the questionnaire.

    Tenant-scoped, so a client only ever reaches their own. The score/gap/
    deliverable stay admin-only until the report is released.
    """
    svc = require_service_in_tenant(db, service_id, client.id)
    if svc.kind not in _SERVICE_KIND_TO_FRAMEWORK:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zero Trust service not found.",
        )
    assessment = _latest_assessment(db, svc.id)
    if assessment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    return _serialize_assessment(db, assessment)


@router.patch(
    "/self-assessment/answers/{answer_id}",
    response_model=ZtAnswerResponse,
    summary="Client updates one answer on their own draft self-assessment",
)
def patch_self_assessment_answer(
    answer_id: uuid.UUID,
    body: ZtAnswerPatch,
    user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ZtAnswerResponse:
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field is required.",
        )
    row = db.get(ZtAnswer, answer_id)
    if row is None or row.client_id != client.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Answer not found.",
        )
    a = db.get(ZtAssessment, row.assessment_id)
    if a is None or a.status != ZtAssessmentStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your self-assessment is no longer editable.",
        )
    if "maturity_stage" in data and data["maturity_stage"] is not None:
        s = int(data["maturity_stage"])
        # Stages are 1..level_count for the framework (CISA 1-4, DoD 1-3).
        max_stage = level_count(_to_catalog_framework(a.framework))
        if not 1 <= s <= max_stage:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"maturity_stage must be 1-{max_stage}.",
            )
        row.maturity_stage = s
    elif "maturity_stage" in data:
        row.maturity_stage = None
    if "notes" in data:
        row.notes = data["notes"]
    row.answered_by = user.id
    row.answered_at = utcnow()
    db.commit()
    db.refresh(row)
    return ZtAnswerResponse.model_validate(row, from_attributes=True)


@router.post(
    "/services/{service_id}/self-assessment/submit",
    response_model=ZtAssessmentResponse,
    summary="Client submits their self-assessment for admin review",
)
def submit_self_assessment(
    service_id: uuid.UUID,
    body: ZtSelfAssessmentSubmit,
    user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ZtAssessmentResponse:
    svc = require_service_in_tenant(db, service_id, client.id)
    if svc.kind not in _SERVICE_KIND_TO_FRAMEWORK:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zero Trust service not found.",
        )
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    if a.status != ZtAssessmentStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This self-assessment has already been submitted.",
        )
    if body.target_stage is not None and svc.source_request_id is not None:
        sr = db.get(ServiceRequest, svc.source_request_id)
        if sr is not None:
            sr.zt_target_stage = body.target_stage
    a.status = ZtAssessmentStatus.SUBMITTED
    audit(
        db,
        action="zt.self_assessment.submitted",
        target_type="zt_assessment",
        target_id=a.id,
        actor_user_id=user.id,
        details={"service_id": str(svc.id), "version": a.version},
    )
    db.commit()
    db.refresh(a)
    return _serialize_assessment(db, a)


@router.post(
    "/assessments/{assessment_id}/approve",
    response_model=ZtAssessmentResponse,
    summary="Approve the Zero Trust assessment (admin)",
)
def approve_assessment(
    assessment_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ZtAssessmentResponse:
    a = require_zt_assessment_in_tenant(db, assessment_id, client.id)
    if a.status == ZtAssessmentStatus.APPROVED:
        return _serialize_assessment(db, a)
    if a.status == ZtAssessmentStatus.RELEASED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assessment already released.",
        )
    a.status = ZtAssessmentStatus.APPROVED
    a.approved_at = utcnow()
    a.approved_by = user.id
    audit(
        db,
        action="zt.assessment.approved",
        target_type="zt_assessment",
        target_id=a.id,
        actor_user_id=user.id,
        details={"version": a.version, "framework": a.framework.value},
    )
    db.commit()
    db.refresh(a)
    return _serialize_assessment(db, a)


# ---------------------------------------------------------------------------
# Scoring + gap
# ---------------------------------------------------------------------------


@router.get(
    "/services/{service_id}/score",
    response_model=ZtScoreSummary,
    summary="Roll-up score for the latest Zero Trust assessment (admin)",
)
def score_latest(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> ZtScoreSummary:
    svc = require_service_in_tenant(db, service_id, client.id)
    if svc.kind not in _SERVICE_KIND_TO_FRAMEWORK:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zero Trust service not found.",
        )
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    cat_fw = _to_catalog_framework(a.framework)
    valid = all_codes(cat_fw)
    rows = db.execute(select(ZtAnswer).where(ZtAnswer.assessment_id == a.id)).scalars().all()
    answers: dict[str, int | None] = {
        r.capability_code: r.maturity_stage for r in rows if r.capability_code in valid
    }
    score = compute_score(cat_fw, answers)
    return ZtScoreSummary(
        assessment_id=a.id,
        version=a.version,
        framework=a.framework,
        total_capabilities=score.total_capabilities,
        answered_capabilities=score.answered_capabilities,
        coverage_pct=score.coverage_pct,
        average_stage=score.average_stage,
        maturity_pct=score.maturity_pct,
        overall_stage_label=score.overall_stage_label,
        by_pillar=[
            PillarScore(
                pillar_code=ps.pillar_code,
                pillar_name=ps.pillar_name,
                capability_count=ps.capability_count,
                answered_count=ps.answered_count,
                average_stage=ps.average_stage,
                maturity_pct=ps.maturity_pct,
                coverage_pct=ps.coverage_pct,
                weakest_capability_codes=list(ps.weakest_capability_codes),
            )
            for ps in score.by_pillar
        ],
    )


@router.get(
    "/services/{service_id}/gap-analysis",
    response_model=GapAnalysisResponse,
    summary="Prioritized remediation gaps for the latest assessment (admin)",
)
def gap_analysis(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
    target_stage: int = 3,
    top_n: int = 20,
) -> GapAnalysisResponse:
    svc = require_service_in_tenant(db, service_id, client.id)
    if svc.kind not in _SERVICE_KIND_TO_FRAMEWORK:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zero Trust service not found.",
        )
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    cat_fw = _to_catalog_framework(a.framework)
    valid = all_codes(cat_fw)
    rows = db.execute(select(ZtAnswer).where(ZtAnswer.assessment_id == a.id)).scalars().all()
    answers: dict[str, int | None] = {
        r.capability_code: r.maturity_stage for r in rows if r.capability_code in valid
    }
    notes: dict[str, str | None] = {
        r.capability_code: r.notes for r in rows if r.capability_code in valid
    }
    targets: dict[str, int | None] = {
        r.capability_code: r.target_stage for r in rows if r.capability_code in valid
    }
    analysis = analyze_gaps(
        cat_fw,
        answers,
        notes=notes,
        target_stage=target_stage,
        targets=targets,
        top_n=top_n,
    )
    roadmap = build_roadmap(analysis.gaps)
    return GapAnalysisResponse(
        assessment_id=a.id,
        version=a.version,
        framework=a.framework,
        target_stage=analysis.target_stage,
        target_label=analysis.target_label,
        total_gap_count=analysis.total_gap_count,
        unscored_count=len(analysis.unscored_codes),
        gap_count_by_pillar=analysis.gap_count_by_pillar,
        gaps=[
            GapItem(
                code=g.code,
                pillar_code=g.pillar_code,
                pillar_name=g.pillar_name,
                name=g.name,
                outcome=g.outcome,
                current_stage=g.current_stage,
                target_stage=g.target_stage,
                gap_size=g.gap_size,
                priority_score=g.priority_score,
                notes=g.notes,
            )
            for g in analysis.gaps
        ],
        roadmap=[
            RoadmapEntry(
                month=it.month,
                code=it.code,
                pillar_code=it.pillar_code,
                pillar_name=it.pillar_name,
                name=it.name,
                current_stage=it.current_stage,
                target_stage=it.target_stage,
                priority_score=it.priority_score,
            )
            for it in roadmap
        ],
    )


# ---------------------------------------------------------------------------
# Deliverables
# ---------------------------------------------------------------------------


_SERVICE_SLUG_BY_FRAMEWORK: dict[ZtFramework, str] = {
    ZtFramework.CISA_ZTMM_2_0: SERVICE_SLUG_ZT_CISA,
    ZtFramework.DOD_ZTRA: SERVICE_SLUG_ZT_DOD,
}


def _serialize_deliverable(db: Session, deliv: Deliverable) -> DeliverableResponse:
    pdf_title = None
    xlsx_title = None
    docx_title = None
    if deliv.pdf_artifact_id:
        a = db.get(Artifact, deliv.pdf_artifact_id)
        pdf_title = a.title if a else None
    if deliv.xlsx_artifact_id:
        a = db.get(Artifact, deliv.xlsx_artifact_id)
        xlsx_title = a.title if a else None
    if deliv.docx_artifact_id:
        a = db.get(Artifact, deliv.docx_artifact_id)
        docx_title = a.title if a else None
    html_title = None
    if deliv.html_artifact_id:
        a = db.get(Artifact, deliv.html_artifact_id)
        html_title = a.title if a else None
    return DeliverableResponse(
        id=deliv.id,
        service_id=deliv.service_id,
        title=deliv.title,
        summary=deliv.summary,
        version=deliv.version,
        pdf_artifact_id=deliv.pdf_artifact_id,
        xlsx_artifact_id=deliv.xlsx_artifact_id,
        docx_artifact_id=deliv.docx_artifact_id,
        html_artifact_id=deliv.html_artifact_id,
        pdf_filename=pdf_title,
        xlsx_filename=xlsx_title,
        docx_filename=docx_title,
        html_filename=html_title,
        finalized_at=deliv.finalized_at,
        finalized_by=deliv.finalized_by,
        superseded_by=deliv.superseded_by,
    )


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

    key = f"deliverable/{user.id}/{uuid.uuid4()}/{filename}"
    storage.put(key, data, content_type=mime_type)
    art = Artifact(
        client_id=client_id,
        title=filename,
        file_storage_key=key,
        mime_type=mime_type,
        size_bytes=len(data),
        sha256=sha256(data).hexdigest(),
        origin=ArtifactOrigin.CONSULTANT_APPROVED,
        stage="zt.deliverable",
        uploaded_by=user.id,
    )
    db.add(art)
    db.flush()
    return art


@router.post(
    "/services/{service_id}/deliverables/finalize",
    response_model=DeliverableResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Render PDF + XLSX deliverable from the latest approved ZT assessment (admin)",
)
def finalize_zt_deliverable(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[StorageBackend, Depends(_storage_dep)],
) -> DeliverableResponse:
    svc = require_service_in_tenant(db, service_id, client.id)
    if svc.kind not in _SERVICE_KIND_TO_FRAMEWORK:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zero Trust service not found.",
        )
    assessment = _latest_assessment(db, svc.id)
    if assessment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    if assessment.status not in (
        ZtAssessmentStatus.APPROVED,
        ZtAssessmentStatus.RELEASED,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assessment must be approved before finalizing the deliverable.",
        )
    cat_fw = _to_catalog_framework(assessment.framework)
    valid = all_codes(cat_fw)
    answers = (
        db.execute(select(ZtAnswer).where(ZtAnswer.assessment_id == assessment.id)).scalars().all()
    )
    stage_map: dict[str, int | None] = {
        r.capability_code: r.maturity_stage for r in answers if r.capability_code in valid
    }
    notes_map: dict[str, str | None] = {
        r.capability_code: r.notes for r in answers if r.capability_code in valid
    }
    targets_map: dict[str, int | None] = {
        r.capability_code: r.target_stage for r in answers if r.capability_code in valid
    }
    # Engagement-level target: the client's intake goal via the source request,
    # falling back to the engine default only when neither per-capability
    # targets nor an intake goal exists (B-1). Mirrors the dashboard endpoint.
    engagement_target = _client_target_stage(db, svc.id)
    score = compute_score(cat_fw, stage_map)
    if engagement_target is not None:
        gap = analyze_gaps(
            cat_fw,
            stage_map,
            notes=notes_map,
            target_stage=engagement_target,
            targets=targets_map,
            top_n=None,  # B-4: XLSX Gap Plan carries every gap; PDF/DOCX cap at 20.
        )
    else:
        gap = analyze_gaps(
            cat_fw,
            stage_map,
            notes=notes_map,
            targets=targets_map,
            top_n=None,  # B-4: XLSX Gap Plan carries every gap; PDF/DOCX cap at 20.
        )

    client_name = client.legal_name
    if client_name == "(pending intake)":
        client_name = None

    today = utcnow().date()
    existing = db.execute(select(Deliverable).where(Deliverable.service_id == svc.id)).all()
    next_version = len(existing) + 1

    service_slug = _SERVICE_SLUG_BY_FRAMEWORK[assessment.framework]
    pdf_name = deliverable_filename(
        company=client_name,
        service_slug=service_slug,
        extension="pdf",
        day=today,
        version=next_version,
    )
    xlsx_name = deliverable_filename(
        company=client_name,
        service_slug=service_slug,
        extension="xlsx",
        day=today,
        version=next_version,
    )
    docx_name = deliverable_filename(
        company=client_name,
        service_slug=service_slug,
        extension="docx",
        day=today,
        version=next_version,
    )
    html_name = deliverable_filename(
        company=client_name,
        service_slug=service_slug,
        extension="html",
        day=today,
        version=next_version,
    )

    ctx = build_zt_context(
        client_legal_name=client_name,
        service_title=svc.title,
        framework=cat_fw,
        assessment=assessment,
        answers=answers,
        score=score,
        gap=gap,
    )
    pdf_bytes = render_zt_pdf(ctx)
    xlsx_bytes = render_zt_xlsx(ctx)
    docx_bytes = render_zt_docx(ctx)
    html_bytes = render_zt_html(ctx)

    pdf_artifact = _write_artifact(
        db,
        storage=storage,
        user=user,
        client_id=client.id,
        filename=pdf_name,
        mime_type="application/pdf",
        data=pdf_bytes,
    )
    xlsx_artifact = _write_artifact(
        db,
        storage=storage,
        user=user,
        client_id=client.id,
        filename=xlsx_name,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        data=xlsx_bytes,
    )
    from app.docx_export import DOCX_MIME

    docx_artifact = _write_artifact(
        db,
        storage=storage,
        user=user,
        client_id=client.id,
        filename=docx_name,
        mime_type=DOCX_MIME,
        data=docx_bytes,
    )
    html_artifact = _write_artifact(
        db,
        storage=storage,
        user=user,
        client_id=client.id,
        filename=html_name,
        mime_type="text/html",
        data=html_bytes,
    )

    summary_line = (
        f"Overall stage: {score.overall_stage_label}. "
        f"{score.answered_capabilities}/{score.total_capabilities} capabilities scored; "
        f"{gap.total_gap_count} gap(s) at target S{gap.target_stage}."
    )

    deliv = Deliverable(
        service_id=svc.id,
        title=f"{svc.title} v{next_version}",
        summary=summary_line,
        version=next_version,
        pdf_artifact_id=pdf_artifact.id,
        xlsx_artifact_id=xlsx_artifact.id,
        docx_artifact_id=docx_artifact.id,
        html_artifact_id=html_artifact.id,
        finalized_at=utcnow(),
        finalized_by=user.id,
    )
    db.add(deliv)
    db.flush()

    audit(
        db,
        action="zt.deliverable.finalized",
        target_type="deliverable",
        target_id=deliv.id,
        actor_user_id=user.id,
        details={
            "service_id": str(svc.id),
            "assessment_id": str(assessment.id),
            "framework": assessment.framework.value,
            "assessment_version": assessment.version,
            "version": next_version,
            "overall_stage_label": score.overall_stage_label,
            "average_stage": score.average_stage,
            "gap_count": gap.total_gap_count,
        },
    )
    assessment.documents_stale = False  # Work Order C3
    db.commit()
    db.refresh(deliv)
    return _serialize_deliverable(db, deliv)


@router.get(
    "/services/{service_id}/deliverables/latest",
    response_model=DeliverableResponse,
    summary="Most recent ZT deliverable for a service (admin)",
)
def latest_zt_deliverable(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> DeliverableResponse:
    # Deliverables are admin-only (Work Order A1): clients never see or
    # download them in-app.
    svc = require_service_in_tenant(db, service_id, client.id)
    if svc.kind not in _SERVICE_KIND_TO_FRAMEWORK:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Zero Trust service not found.",
        )
    deliv = db.execute(
        select(Deliverable)
        .where(Deliverable.service_id == svc.id)
        .order_by(Deliverable.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    if deliv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No deliverable yet. Finalize one first.",
        )
    return _serialize_deliverable(db, deliv)
