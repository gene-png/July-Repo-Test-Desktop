"""NIST CSF 2.0 service routes (Phase 4 stage 2).

Endpoint surface:
  POST   /csf/services
         Open a CSF assessment service. Admin-only.
  GET    /csf/catalog
         Static reference data. Any signed-in role.
  POST   /csf/services/{service_id}/assessments
         Create a draft assessment for the service. Admin-only.
  GET    /csf/services/{service_id}/assessments/latest
         Most recent assessment (admin sees draft; client sees released).
  PATCH  /csf/answers/{answer_id}
         Inline update of one subcategory answer. Admin-only.
  POST   /csf/assessments/{assessment_id}/approve
         Flip status -> approved. Admin-only.
  GET    /csf/services/{service_id}/score
         Roll-up score for the latest assessment. Admin-only.
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
from app.csf import playbook_export as csf_playbook_export
from app.csf.catalog import (
    CATEGORIES,
    FUNCTIONS,
    SUBCATEGORIES,
    all_codes,
    min_profile_for_category,
    subcategory_by_code,
)
from app.csf.exporters import build_context as build_csf_context
from app.csf.exporters import render_docx as render_csf_docx
from app.csf.exporters import render_pdf as render_csf_pdf
from app.csf.exporters import render_xlsx as render_csf_xlsx
from app.csf.gap import DEFAULT_TARGET_TIER
from app.csf.gap import analyze as analyze_gaps
from app.csf.maturity import TIER_DEFINITIONS
from app.csf.playbook import (
    DimensionScores,
    Tier,
    gap_priority,
    is_gap,
    score_tier,
    weighted_floor_rollup,
)
from app.csf.scoring import compute as compute_score
from app.db.session import assessment_advisory_lock, get_db
from app.dependencies import current_client, current_user, require_role
from app.middleware.ratelimit import rate_limit_user
from app.models._common import utcnow
from app.models.artifact import Artifact, ArtifactOrigin
from app.models.client import Client
from app.models.csf_action_item import CsfActionItem, CsfActionStatus
from app.models.csf_assessment import (
    CsfAnswer,
    CsfAssessment,
    CsfAssessmentStatus,
)
from app.models.csf_profile import CsfDimensionScore
from app.models.deliverable import Deliverable
from app.models.questionnaire import Question
from app.models.service import Service, ServiceKind, ServiceStatus
from app.models.service_request import ServiceRequest
from app.models.user import User, UserRole
from app.routes.artifacts import _storage_dep
from app.schemas.csf import (
    CatalogCategory,
    CatalogFunction,
    CatalogResponse,
    CatalogSubcategory,
    CatalogTier,
    CsfActionItemCreate,
    CsfActionItemPatch,
    CsfActionItemResponse,
    CsfAnswerPatch,
    CsfAnswerResponse,
    CsfAssessmentResponse,
    CsfDimensionChange,
    CsfDimensionScorePatch,
    CsfDimensionScoreResponse,
    CsfPlaybookExportResponse,
    CsfProfileResponse,
    CsfQuestionnaireResponse,
    CsfRunAiResponse,
    CsfScoreSummary,
    CsfSelfAssessmentSubmit,
    CsfServiceCreateRequest,
    CsfServiceResponse,
    EnterpriseProfileResponse,
    EnterpriseSubcategory,
    ExportedArtifact,
    FunctionScore,
    GapAnalysisResponse,
    GapItem,
    InterviewQuestion,
    ProfileSeedRequest,
)
from app.schemas.tech_debt import DeliverableResponse
from app.storage import StorageBackend
from app.tech_debt.filename import (
    SERVICE_SLUG_CSF_PLAYBOOK,
    SERVICE_SLUG_NIST_CSF,
    deliverable_filename,
)
from app.tenant import (
    require_artifact_in_tenant,
    require_csf_assessment_in_tenant,
    require_service_in_tenant,
)

router = APIRouter(prefix="/csf", tags=["csf"])

_admin_required = Depends(require_role(UserRole.ADMIN))
# H-2: per-user token-bucket limiter on the AI run endpoint.
_ai_rate_limited = Depends(rate_limit_user())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize_answers(rows: Iterable[CsfAnswer]) -> list[CsfAnswerResponse]:
    # Stable ordering: by NIST code so the workspace tab renders predictably.
    ordered = sorted(rows, key=lambda r: r.subcategory_code)
    return [CsfAnswerResponse.model_validate(r, from_attributes=True) for r in ordered]


def _client_target_tier(db: Session, service_id: uuid.UUID) -> int | None:
    """The CSF target tier the client chose at intake, via the source request.

    Lets the admin workspace default its gap target to the client's goal
    instead of a hardcoded tier.
    """
    svc = db.get(Service, service_id)
    if svc is None or svc.source_request_id is None:
        return None
    sr = db.get(ServiceRequest, svc.source_request_id)
    return sr.csf_target_tier if sr is not None else None


def _client_profile(db: Session, service_id: uuid.UUID) -> str | None:
    """The CSF impact profile the client chose at intake (LOW/MOD/HIGH)."""
    svc = db.get(Service, service_id)
    if svc is None or svc.source_request_id is None:
        return None
    sr = db.get(ServiceRequest, svc.source_request_id)
    return sr.csf_profile if sr is not None else None


def _serialize_assessment(db: Session, a: CsfAssessment) -> CsfAssessmentResponse:
    rows = db.execute(select(CsfAnswer).where(CsfAnswer.assessment_id == a.id)).scalars().all()
    return CsfAssessmentResponse(
        id=a.id,
        service_id=a.service_id,
        version=a.version,
        status=a.status,
        approved_at=a.approved_at,
        approved_by=a.approved_by,
        documents_stale=a.documents_stale,
        answers=_serialize_answers(rows),
        client_target_tier=_client_target_tier(db, a.service_id),
        client_profile=_client_profile(db, a.service_id),
    )


def _latest_assessment(db: Session, service_id: uuid.UUID) -> CsfAssessment | None:
    return db.execute(
        select(CsfAssessment)
        .where(CsfAssessment.service_id == service_id)
        .order_by(CsfAssessment.version.desc())
        .limit(1)
    ).scalar_one_or_none()


# Impact profile (set at intake) -> the interview-questionnaire framework_key
# loaded into the `questions` table. HIGH is the most complete questionnaire,
# so it's the fallback when no profile has been chosen yet.
_PROFILE_TO_TIER_KEY = {
    "LOW": "csf-tier-low",
    "MOD": "csf-tier-moderate",
    "HIGH": "csf-tier-high",
}
_DEFAULT_TIER_KEY = "csf-tier-high"


@router.get(
    "/services/{service_id}/questionnaire",
    response_model=CsfQuestionnaireResponse,
    summary="Interview prompts for the service's impact tier",
)
def get_interview_questionnaire(
    service_id: uuid.UUID,
    _user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfQuestionnaireResponse:
    """Tier-resolved interview prompts (read-only).

    Each prompt carries the CSF subcategories it informs so the workspace can
    surface it inline on those subcategory cards. Any signed-in role scoped to
    the tenant may read it.
    """
    require_service_in_tenant(db, service_id, client.id)
    profile = _client_profile(db, service_id)
    framework_key = _PROFILE_TO_TIER_KEY.get((profile or "").upper(), _DEFAULT_TIER_KEY)
    rows = (
        db.execute(
            select(Question)
            .where(Question.framework_key == framework_key)
            .order_by(Question.order_index)
        )
        .scalars()
        .all()
    )
    return CsfQuestionnaireResponse(
        framework_key=framework_key,
        profile=profile,
        questions=[
            InterviewQuestion(
                external_id=q.external_id,
                section_name=q.pillar,
                order_index=q.order_index,
                stem=q.stem,
                cues=list(q.cues or []),
                csf_subcategories=list(q.framework_activities or []),
            )
            for q in rows
        ],
    )


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


@router.post(
    "/services",
    response_model=CsfServiceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a CSF assessment service (admin)",
)
def create_csf_service(
    body: CsfServiceCreateRequest,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfServiceResponse:
    if body.kind != ServiceKind.NIST_CSF:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Service kind must be nist_csf for this endpoint.",
        )
    svc = Service(
        kind=ServiceKind.NIST_CSF,
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
        action="csf.service.opened",
        target_type="service",
        target_id=svc.id,
        actor_user_id=user.id,
        details={"title": svc.title},
    )
    db.commit()
    db.refresh(svc)
    return CsfServiceResponse.model_validate(svc, from_attributes=True)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@router.get(
    "/catalog",
    response_model=CatalogResponse,
    summary="NIST CSF 2.0 reference catalog",
)
def get_catalog(
    _user: Annotated[User, Depends(current_user)],
) -> CatalogResponse:
    functions: list[CatalogFunction] = []
    for fn in FUNCTIONS:
        categories: list[CatalogCategory] = []
        for cat in CATEGORIES:
            if cat.function != fn.code:
                continue
            subs = [
                CatalogSubcategory(
                    code=s.code,
                    function=s.function.value,
                    category=s.category,
                    name=s.name,
                    outcome=s.outcome,
                    min_profile=min_profile_for_category(s.category),
                )
                for s in SUBCATEGORIES
                if s.category == cat.code
            ]
            categories.append(
                CatalogCategory(
                    code=cat.code,
                    function=cat.function.value,
                    name=cat.name,
                    purpose=cat.purpose,
                    subcategories=subs,
                )
            )
        functions.append(
            CatalogFunction(
                code=fn.code.value,
                name=fn.name,
                purpose=fn.purpose,
                categories=categories,
            )
        )
    tiers = [
        CatalogTier(tier=int(t.tier), short_label=t.short_label, description=t.description)
        for t in TIER_DEFINITIONS
    ]
    return CatalogResponse(
        functions=functions,
        tiers=tiers,
        total_subcategories=len(SUBCATEGORIES),
    )


# ---------------------------------------------------------------------------
# Assessments
# ---------------------------------------------------------------------------


@router.post(
    "/services/{service_id}/assessments",
    response_model=CsfAssessmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new draft assessment for the service (admin)",
)
def create_assessment(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
    response: Response,
) -> CsfAssessmentResponse:
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    prior = _latest_assessment(db, svc.id)
    # E-3 open-draft guard: an assessment still in a pre-approval working status
    # (DRAFT or SUBMITTED) is returned as-is (200) rather than minting a new
    # version, so a double-create can't orphan in-progress work.
    if prior is not None and prior.status in (
        CsfAssessmentStatus.DRAFT,
        CsfAssessmentStatus.SUBMITTED,
    ):
        response.status_code = status.HTTP_200_OK
        return _serialize_assessment(db, prior)
    version = (prior.version + 1) if prior else 1
    assessment = CsfAssessment(
        service_id=svc.id,
        client_id=client.id,
        version=version,
        status=CsfAssessmentStatus.DRAFT,
    )
    db.add(assessment)
    db.flush()
    # Pre-create empty answer rows so the workspace UI gets a deterministic
    # answer grid back from the very first GET. Cheap (~106 rows).
    for sc in SUBCATEGORIES:
        db.add(
            CsfAnswer(
                assessment_id=assessment.id,
                client_id=client.id,
                subcategory_code=sc.code,
            )
        )
    # F-1: seed the Working Profile for the client's intake tier so the workspace
    # (and run-ai) has scoreable rows from the start; re-sync via the seed endpoint.
    _seed_dimension_rows(db, assessment.id, client.id, _default_seed_tiers(db, svc.id))
    audit(
        db,
        action="csf.assessment.created",
        target_type="csf_assessment",
        target_id=assessment.id,
        actor_user_id=user.id,
        details={"service_id": str(svc.id), "version": version},
    )
    db.commit()
    db.refresh(assessment)
    return _serialize_assessment(db, assessment)


@router.get(
    "/services/{service_id}/assessments/latest",
    response_model=CsfAssessmentResponse,
    summary="Most recent assessment for the service",
)
def latest_assessment(
    service_id: uuid.UUID,
    user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfAssessmentResponse:
    svc = require_service_in_tenant(db, service_id, client.id)
    assessment = _latest_assessment(db, svc.id)
    if assessment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    # Phase 4 keeps assessment scoreboards admin-only until the
    # deliverable is released to the client (mirrors Phase 3 stage 9).
    # RELEASED is deprecated for v1 (no in-app release; G-1)
    if user.role != UserRole.ADMIN and assessment.status != CsfAssessmentStatus.RELEASED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSF assessments are admin-only until released.",
        )
    return _serialize_assessment(db, assessment)


# ---------------------------------------------------------------------------
# Answer editing
# ---------------------------------------------------------------------------


@router.patch(
    "/answers/{answer_id}",
    response_model=CsfAnswerResponse,
    summary="Inline-update a single subcategory answer (admin)",
)
def patch_answer(
    answer_id: uuid.UUID,
    body: CsfAnswerPatch,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfAnswerResponse:
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field is required.",
        )
    row = db.get(CsfAnswer, answer_id)
    if row is None or row.client_id != client.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Answer not found.",
        )
    # Refuse edits to approved or released assessments.
    a = db.get(CsfAssessment, row.assessment_id)
    if a is None or a.status in (
        CsfAssessmentStatus.APPROVED,
        CsfAssessmentStatus.RELEASED,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This assessment is locked.",
        )
    # Validation: subcategory code already pinned at create-time, so we
    # only validate the tier values that arrive here.
    if "maturity_tier" in data and data["maturity_tier"] is not None:
        t = int(data["maturity_tier"])
        if not 1 <= t <= 4:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="maturity_tier must be 1-4.",
            )
        row.maturity_tier = t
    elif "maturity_tier" in data:
        row.maturity_tier = None
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
        action="csf.answer.updated",
        target_type="csf_answer",
        target_id=row.id,
        actor_user_id=user.id,
        details={
            "subcategory_code": row.subcategory_code,
            "fields": sorted(data.keys()),
        },
    )
    db.commit()
    db.refresh(row)
    return CsfAnswerResponse.model_validate(row, from_attributes=True)


# ---------------------------------------------------------------------------
# Client self-assessment (client fills their own draft, then submits for review)
# ---------------------------------------------------------------------------


@router.get(
    "/services/{service_id}/self-assessment",
    response_model=CsfAssessmentResponse,
    summary="The client's own assessment for this service (any status)",
)
def get_self_assessment(
    service_id: uuid.UUID,
    _user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfAssessmentResponse:
    """Read the client's own assessment so they can fill the questionnaire.

    Tenant-scoped (current_client), so a client only ever reaches their own.
    Unlike the admin `assessments/latest`, this is not gated on RELEASED - the
    client owns these answers. The score/gap/deliverable stay admin-only until
    the report is released.
    """
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    assessment = _latest_assessment(db, svc.id)
    if assessment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    return _serialize_assessment(db, assessment)


@router.patch(
    "/self-assessment/answers/{answer_id}",
    response_model=CsfAnswerResponse,
    summary="Client updates one answer on their own draft self-assessment",
)
def patch_self_assessment_answer(
    answer_id: uuid.UUID,
    body: CsfAnswerPatch,
    user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfAnswerResponse:
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field is required.",
        )
    row = db.get(CsfAnswer, answer_id)
    if row is None or row.client_id != client.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Answer not found.",
        )
    a = db.get(CsfAssessment, row.assessment_id)
    if a is None or a.status != CsfAssessmentStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your self-assessment is no longer editable.",
        )
    if "maturity_tier" in data and data["maturity_tier"] is not None:
        t = int(data["maturity_tier"])
        if not 1 <= t <= 4:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="maturity_tier must be 1-4.",
            )
        row.maturity_tier = t
    elif "maturity_tier" in data:
        row.maturity_tier = None
    if "notes" in data:
        row.notes = data["notes"]
    row.answered_by = user.id
    row.answered_at = utcnow()
    db.commit()
    db.refresh(row)
    return CsfAnswerResponse.model_validate(row, from_attributes=True)


@router.post(
    "/services/{service_id}/self-assessment/submit",
    response_model=CsfAssessmentResponse,
    summary="Client submits their self-assessment for admin review",
)
def submit_self_assessment(
    service_id: uuid.UUID,
    body: CsfSelfAssessmentSubmit,
    user: Annotated[User, Depends(current_user)],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfAssessmentResponse:
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    if a.status != CsfAssessmentStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This self-assessment has already been submitted.",
        )
    # Persist the (possibly adjusted) maturity target so the gap engine measures
    # against the client's goal.
    if body.target_tier is not None and svc.source_request_id is not None:
        sr = db.get(ServiceRequest, svc.source_request_id)
        if sr is not None:
            sr.csf_target_tier = body.target_tier
    a.status = CsfAssessmentStatus.SUBMITTED
    audit(
        db,
        action="csf.self_assessment.submitted",
        target_type="csf_assessment",
        target_id=a.id,
        actor_user_id=user.id,
        details={"service_id": str(svc.id), "version": a.version},
    )
    db.commit()
    db.refresh(a)
    return _serialize_assessment(db, a)


@router.post(
    "/assessments/{assessment_id}/approve",
    response_model=CsfAssessmentResponse,
    summary="Approve the assessment (admin)",
)
def approve_assessment(
    assessment_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfAssessmentResponse:
    a = require_csf_assessment_in_tenant(db, assessment_id, client.id)
    if a.status == CsfAssessmentStatus.APPROVED:
        return _serialize_assessment(db, a)
    if a.status == CsfAssessmentStatus.RELEASED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assessment already released.",
        )
    a.status = CsfAssessmentStatus.APPROVED
    a.approved_at = utcnow()
    a.approved_by = user.id
    audit(
        db,
        action="csf.assessment.approved",
        target_type="csf_assessment",
        target_id=a.id,
        actor_user_id=user.id,
        details={"version": a.version},
    )
    db.commit()
    db.refresh(a)
    return _serialize_assessment(db, a)


# ---------------------------------------------------------------------------
# Action plan (H-8) — admin-managed remediation tasks, client-invisible
# ---------------------------------------------------------------------------


@router.post(
    "/assessments/{assessment_id}/action-items",
    response_model=CsfActionItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a remediation action item for an assessment (admin)",
)
def create_action_item(
    assessment_id: uuid.UUID,
    body: CsfActionItemCreate,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfActionItemResponse:
    a = require_csf_assessment_in_tenant(db, assessment_id, client.id)
    if body.subcategory_code not in all_codes():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unknown subcategory code.",
        )
    item = CsfActionItem(
        assessment_id=a.id,
        client_id=client.id,
        subcategory_code=body.subcategory_code,
        owner=body.owner,
        due_date=body.due_date,
        milestone=body.milestone,
        status=body.status,
        created_by=user.id,
    )
    db.add(item)
    db.flush()
    audit(
        db,
        action="csf.action_item.created",
        target_type="csf_action_item",
        target_id=item.id,
        actor_user_id=user.id,
        details={
            "assessment_id": str(a.id),
            "subcategory_code": item.subcategory_code,
        },
    )
    db.commit()
    db.refresh(item)
    return CsfActionItemResponse.model_validate(item, from_attributes=True)


@router.get(
    "/assessments/{assessment_id}/action-items",
    response_model=list[CsfActionItemResponse],
    summary="List remediation action items for an assessment (admin)",
)
def list_action_items(
    assessment_id: uuid.UUID,
    _user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> list[CsfActionItemResponse]:
    a = require_csf_assessment_in_tenant(db, assessment_id, client.id)
    rows = (
        db.execute(
            select(CsfActionItem)
            .where(CsfActionItem.assessment_id == a.id)
            .order_by(CsfActionItem.created_at)
        )
        .scalars()
        .all()
    )
    return [CsfActionItemResponse.model_validate(r, from_attributes=True) for r in rows]


@router.patch(
    "/action-items/{item_id}",
    response_model=CsfActionItemResponse,
    summary="Update a remediation action item (admin)",
)
def patch_action_item(
    item_id: uuid.UUID,
    body: CsfActionItemPatch,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfActionItemResponse:
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field is required.",
        )
    item = db.get(CsfActionItem, item_id)
    if item is None or item.client_id != client.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Action item not found.",
        )
    if "owner" in data:
        item.owner = data["owner"]
    if "due_date" in data:
        item.due_date = data["due_date"]
    if "milestone" in data:
        item.milestone = data["milestone"]
    if "status" in data and data["status"] is not None:
        item.status = CsfActionStatus(data["status"])
    audit(
        db,
        action="csf.action_item.updated",
        target_type="csf_action_item",
        target_id=item.id,
        actor_user_id=user.id,
        details={"fields": sorted(data.keys())},
    )
    db.commit()
    db.refresh(item)
    return CsfActionItemResponse.model_validate(item, from_attributes=True)


@router.delete(
    "/action-items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a remediation action item (admin)",
)
def delete_action_item(
    item_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    item = db.get(CsfActionItem, item_id)
    if item is None or item.client_id != client.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Action item not found.",
        )
    audit(
        db,
        action="csf.action_item.deleted",
        target_type="csf_action_item",
        target_id=item.id,
        actor_user_id=user.id,
        details={"assessment_id": str(item.assessment_id)},
    )
    db.delete(item)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@router.get(
    "/services/{service_id}/score",
    response_model=CsfScoreSummary,
    summary="Roll-up score for the latest assessment (admin)",
)
def score_latest(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfScoreSummary:
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    rows = db.execute(select(CsfAnswer).where(CsfAnswer.assessment_id == a.id)).scalars().all()
    answers: dict[str, int | None] = {r.subcategory_code: r.maturity_tier for r in rows}
    # Defensive: ignore unknown codes.
    valid = all_codes()
    answers = {k: v for k, v in answers.items() if k in valid}
    score = compute_score(answers)
    return CsfScoreSummary(
        assessment_id=a.id,
        version=a.version,
        total_subcategories=score.total_subcategories,
        answered_subcategories=score.answered_subcategories,
        coverage_pct=score.coverage_pct,
        average_tier=score.average_tier,
        overall_maturity_label=score.overall_maturity_label,
        by_function=[
            FunctionScore(
                function=fs.function.value,
                function_name=fs.function_name,
                subcategory_count=fs.subcategory_count,
                answered_count=fs.answered_count,
                average_tier=fs.average_tier,
                coverage_pct=fs.coverage_pct,
                weakest_subcategory_codes=list(fs.weakest_subcategory_codes),
            )
            for fs in score.by_function
        ],
    )


# ---------------------------------------------------------------------------
# Gap analysis
# ---------------------------------------------------------------------------


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
    target_tier: int = 3,
    top_n: int = 20,
) -> GapAnalysisResponse:
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    rows = db.execute(select(CsfAnswer).where(CsfAnswer.assessment_id == a.id)).scalars().all()
    valid = all_codes()
    answers: dict[str, int | None] = {
        r.subcategory_code: r.maturity_tier for r in rows if r.subcategory_code in valid
    }
    notes: dict[str, str | None] = {
        r.subcategory_code: r.notes for r in rows if r.subcategory_code in valid
    }
    analysis = analyze_gaps(answers, notes=notes, target_tier=target_tier, top_n=top_n)
    return GapAnalysisResponse(
        assessment_id=a.id,
        version=a.version,
        target_tier=analysis.target_tier,
        target_label=analysis.target_label,
        total_gap_count=analysis.total_gap_count,
        unscored_count=len(analysis.unscored_codes),
        gap_count_by_function=analysis.gap_count_by_function,
        gaps=[
            GapItem(
                code=g.code,
                function=g.function.value,
                function_name=g.function_name,
                category=g.category,
                name=g.name,
                outcome=g.outcome,
                current_tier=g.current_tier,
                target_tier=g.target_tier,
                gap_size=g.gap_size,
                priority_score=g.priority_score,
                notes=g.notes,
            )
            for g in analysis.gaps
        ],
    )


# ---------------------------------------------------------------------------
# Full-Playbook tiered Working Profile (Work Order D4)
# ---------------------------------------------------------------------------

_VALID_TIERS = {t.value for t in Tier}

# F-1: the client's intake impact profile maps to the Working-Profile tier that
# run-ai / create-assessment auto-seed when no rows exist. HIGH is the fallback
# (the most complete profile) when no intake profile has been chosen.
_PROFILE_TO_SEED_TIER = {"LOW": "low", "MOD": "moderate", "HIGH": "high"}


def _seed_dimension_rows(
    db: Session, assessment_id: uuid.UUID, client_id: uuid.UUID, tiers: Iterable[str]
) -> int:
    """Idempotently seed CsfDimensionScore rows for the given tiers (F-1).

    Shared by the seed endpoint, create-assessment, and run-ai auto-seed. Adds
    rows to the session (caller flushes/commits); returns the count created.
    """
    existing = {
        (r.tier, r.subcategory_code)
        for r in db.execute(
            select(CsfDimensionScore.tier, CsfDimensionScore.subcategory_code).where(
                CsfDimensionScore.assessment_id == assessment_id
            )
        ).all()
    }
    created = 0
    for tier in tiers:
        for sc in SUBCATEGORIES:
            if (tier, sc.code) in existing:
                continue
            db.add(
                CsfDimensionScore(
                    assessment_id=assessment_id,
                    client_id=client_id,
                    tier=tier,
                    subcategory_code=sc.code,
                )
            )
            created += 1
    return created


def _default_seed_tiers(db: Session, service_id: uuid.UUID) -> list[str]:
    """The tier(s) auto-seeded for a service, from the client's intake profile."""
    profile = (_client_profile(db, service_id) or "").upper()
    return [_PROFILE_TO_SEED_TIER.get(profile, "high")]


def _dims(row: CsfDimensionScore) -> DimensionScores:
    return DimensionScores(
        governance=row.governance,
        policy=row.policy,
        implementation=row.implementation,
        monitoring=row.monitoring,
        improvement=row.improvement,
    )


def _score_response(row: CsfDimensionScore) -> CsfDimensionScoreResponse:
    result = score_tier(_dims(row), has_evidence=row.has_evidence)
    return CsfDimensionScoreResponse(
        id=row.id,
        tier=row.tier,
        subcategory_code=row.subcategory_code,
        governance=row.governance,
        policy=row.policy,
        implementation=row.implementation,
        monitoring=row.monitoring,
        improvement=row.improvement,
        in_scope=row.in_scope,
        rationale=row.rationale,
        what_we_found=row.what_we_found,
        has_evidence=row.has_evidence,
        target_level=row.target_level,
        locked=row.locked,
        total=result.total,
        level=result.level,
        evidence_capped=result.evidence_capped,
    )


@router.post(
    "/services/{service_id}/profiles/seed",
    response_model=list[str],
    summary="Seed the tiered Working Profile rows for the requested tiers (admin)",
)
def seed_profiles(
    service_id: uuid.UUID,
    body: ProfileSeedRequest,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> list[str]:
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Create an assessment first."
        )
    tiers = [t for t in body.tiers if t in _VALID_TIERS]
    if not tiers:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No valid tiers (high/moderate/low).",
        )
    created = _seed_dimension_rows(db, a.id, client.id, tiers)
    audit(
        db,
        action="csf.profiles_seeded",
        target_type="csf_assessment",
        target_id=a.id,
        actor_user_id=user.id,
        details={"tiers": tiers, "created": created},
    )
    db.commit()
    return tiers


@router.get(
    "/services/{service_id}/profile/{tier}",
    response_model=CsfProfileResponse,
    summary="The tiered Working Profile for one tier, with computed totals/levels (admin)",
)
def get_profile(
    service_id: uuid.UUID,
    tier: str,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfProfileResponse:
    if tier not in _VALID_TIERS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown tier.")
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No assessment yet.")
    rows = (
        db.execute(
            select(CsfDimensionScore)
            .where(
                CsfDimensionScore.assessment_id == a.id,
                CsfDimensionScore.tier == tier,
            )
            .order_by(CsfDimensionScore.subcategory_code)
        )
        .scalars()
        .all()
    )
    return CsfProfileResponse(tier=tier, rows=[_score_response(r) for r in rows])


@router.patch(
    "/dimension-scores/{score_id}",
    response_model=CsfDimensionScoreResponse,
    summary="Set dimension scores / scope / target / lock on one row (admin)",
)
def patch_dimension_score(
    score_id: uuid.UUID,
    body: CsfDimensionScorePatch,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> CsfDimensionScoreResponse:
    row = db.get(CsfDimensionScore, score_id)
    if row is None or row.client_id != client.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Score row not found.")
    data = body.model_dump(exclude_unset=True)
    for f in (
        "governance",
        "policy",
        "implementation",
        "monitoring",
        "improvement",
        "in_scope",
        "rationale",
        "what_we_found",
        "has_evidence",
        "target_level",
        "locked",
    ):
        if f in data and data[f] is not None:
            setattr(row, f, data[f])
        elif f in data and f in ("rationale", "what_we_found", "target_level"):
            setattr(row, f, None)  # explicit clear allowed for nullable text/target
    # A human PATCH counts as scoring the row (B-3): stamp scored_at so the
    # playbook export gate treats this row as scored.
    row.scored_at = utcnow()
    db.commit()
    return _score_response(row)


@router.get(
    "/services/{service_id}/enterprise-profile",
    response_model=EnterpriseProfileResponse,
    summary="Roll the tiered profiles up to one Enterprise level per subcategory (admin)",
)
def enterprise_profile(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> EnterpriseProfileResponse:
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No assessment yet.")
    out, tiers_in_use = _enterprise_subcategories(db, a)
    return EnterpriseProfileResponse(tiers_in_use=sorted(tiers_in_use), subcategories=out)


def _enterprise_subcategories(
    db: Session, a: CsfAssessment
) -> tuple[list[EnterpriseSubcategory], set[str]]:
    """The weighted-floor Enterprise roll-up per in-scope subcategory."""
    rows = (
        db.execute(select(CsfDimensionScore).where(CsfDimensionScore.assessment_id == a.id))
        .scalars()
        .all()
    )
    by_subcat: dict[str, dict[str, CsfDimensionScore]] = {}
    tiers_in_use: set[str] = set()
    for r in rows:
        if not r.in_scope:
            continue
        by_subcat.setdefault(r.subcategory_code, {})[r.tier] = r
        tiers_in_use.add(r.tier)

    out: list[EnterpriseSubcategory] = []
    for code in sorted(by_subcat):
        tier_rows = by_subcat[code]
        tier_levels = {
            tier: score_tier(_dims(row), has_evidence=row.has_evidence).level
            for tier, row in tier_rows.items()
        }
        rollup = weighted_floor_rollup(
            {Tier(t): lvl for t, lvl in tier_levels.items()},
            # IG core/supporting metadata isn't in the catalog yet; defaults keep
            # the roll-up on rules 1/3/4/6 until the IG import lands.
            is_core_primary=False,
            is_supporting_or_supplemental=False,
        )
        targets = [row.target_level for row in tier_rows.values() if row.target_level]
        target = max(targets) if targets else None
        gap = is_gap(rollup.score, target) if target is not None else False
        priority = (
            gap_priority(
                is_core=False,
                high_tier=Tier.HIGH.value in tier_rows,
                multi_system=len(tier_rows) > 1,
            )
            if gap
            else None
        )
        sc = subcategory_by_code(code)
        out.append(
            EnterpriseSubcategory(
                subcategory_code=code,
                name=getattr(sc, "name", code),
                function=str(getattr(sc, "function", "")),
                tier_levels=tier_levels,
                enterprise_level=rollup.score,
                rollup_rule=rollup.rule,
                target_level=target,
                gap=gap,
                priority=priority,
            )
        )
    return out, tiers_in_use


def _llm_dep() -> LLMClient:
    return LLMClient.from_settings()


_DIM_FIELDS = ("governance", "policy", "implementation", "monitoring", "improvement")
_RUN_FIELDS = (*_DIM_FIELDS, "what_we_found")


@router.post(
    "/services/{service_id}/run-ai",
    response_model=CsfRunAiResponse,
    summary="Run the csf_score AI job: suggest dimension scores + narrative (admin)",
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
) -> CsfRunAiResponse:
    """The CSF full-Playbook 'Run AI'. Suggests the five dimension scores (0-2)
    + a 'what we found' narrative per (tier, subcategory). AI suggests; locked
    rows are untouched; code does the total/level/cap + Enterprise roll-up.
    Returns a 'what changed' list.
    """
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Create an assessment first."
        )
    if a.status in (CsfAssessmentStatus.APPROVED, CsfAssessmentStatus.RELEASED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This assessment is locked."
        )
    # H-6: a live (non-preview) run requires a recorded redaction-preview ack for
    # this client. Fixture mode and preview runs are exempt.
    if not preview and llm.mode == "live" and not has_preview_ack(db, client.id):
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="Redaction preview acknowledgment required for this client before live AI runs",
        )
    # E-3: serialize concurrent runs for this assessment (409 loser on Postgres).
    if not preview:
        assessment_advisory_lock(db, a.id)
    aid = a.id
    svc_id = svc.id
    client_id = client.id
    # Capture the actor id as a plain value: an auto-seed commit below expires
    # the dependency-loaded `user`, and the E-1 db.close() detaches it.
    user_id = user.id
    rows = {
        f"{r.tier}|{r.subcategory_code}": r
        for r in db.execute(
            select(CsfDimensionScore).where(CsfDimensionScore.assessment_id == a.id)
        )
        .scalars()
        .all()
    }
    if not rows:
        # F-1: auto-seed the Working Profile for the client's intake tier rather
        # than 409-ing. The seed endpoint remains for re-sync. Commit before the
        # E-1 db.close() below (which discards uncommitted work); a preview run
        # never commits, so its transient seed is discarded on close.
        _seed_dimension_rows(db, a.id, client.id, _default_seed_tiers(db, svc.id))
        db.flush()
        if not preview:
            db.commit()
        rows = {
            f"{r.tier}|{r.subcategory_code}": r
            for r in db.execute(
                select(CsfDimensionScore).where(CsfDimensionScore.assessment_id == a.id)
            )
            .scalars()
            .all()
        }
    locked_keys = frozenset(k for k, r in rows.items() if r.locked)

    # E-1: snapshot to plain data BEFORE the call so we can diff after the request
    # session is released and the ORM rows are re-loaded.
    before = {k: {f: getattr(r, f) for f in _RUN_FIELDS} for k, r in rows.items()}
    client_org = None if client.legal_name == "(pending intake)" else client.legal_name
    # Ground the suggestion in the seeded tier list plus, per (tier, subcategory)
    # row, the client's questionnaire answer (maturity tier + notes) and evidence
    # flag. The redaction path (run_job -> LLMClient.invoke) scrubs the notes.
    answers = {
        ans.subcategory_code: ans
        for ans in db.execute(select(CsfAnswer).where(CsfAnswer.assessment_id == a.id))
        .scalars()
        .all()
    }
    subcategory_payload = []
    for r in sorted(rows.values(), key=lambda r: (r.tier, r.subcategory_code)):
        ans = answers.get(r.subcategory_code)
        subcategory_payload.append(
            {
                "tier": r.tier,
                "subcategory_code": r.subcategory_code,
                "in_scope": r.in_scope,
                "has_evidence": r.has_evidence,
                "rationale": r.rationale,
                "questionnaire_tier": ans.maturity_tier if ans is not None else None,
                "questionnaire_notes": ans.notes if ans is not None else None,
            }
        )
    inputs = {
        "tiers": sorted({r.tier for r in rows.values()}),
        "subcategories": subcategory_payload,
    }

    # H-6: preview short-circuits before any provider call or llm_calls row.
    if preview:
        prev = llm.preview(purpose="csf_score", inputs=inputs, client_org_name=client_org)
        return JSONResponse({"preview": True, **prev})

    # E-1: release the request DB connection to the pool for the duration of the
    # (potentially long) provider call. The llm_calls audit row is written on an
    # independent session inside LLMClient.invoke, so nothing here needs the
    # connection while the model runs. close() expunges the ORM objects above, so
    # we re-load the rows afterward by id.
    db.close()
    result = run_job(
        db,
        llm,
        "csf_score",
        inputs=inputs,
        requested_by=user_id,
        service_id=svc_id,
        client_id=client_id,
        client_org_name=client_org,
    )
    # A-6: reject a wrong-shape response before the apply loop (nothing written).
    problems = validate_response("csf_score", result.data)
    if problems:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI response failed validation: " + "; ".join(problems),
        )
    data = result.data if isinstance(result.data, dict) else {}

    a = db.get(CsfAssessment, aid)
    rows = {
        f"{r.tier}|{r.subcategory_code}": r
        for r in db.execute(select(CsfDimensionScore).where(CsfDimensionScore.assessment_id == aid))
        .scalars()
        .all()
    }

    for sugg in data.get("scores", []):
        if not isinstance(sugg, dict):
            continue
        row = rows.get(f"{sugg.get('tier')}|{sugg.get('subcategory_code')}")
        if row is None or row.locked:
            continue
        for dim in _DIM_FIELDS:
            if dim in sugg:
                try:
                    v = int(sugg[dim])
                except (TypeError, ValueError):
                    continue
                if 0 <= v <= 2:
                    setattr(row, dim, v)
        if isinstance(sugg.get("what_we_found"), str):
            row.what_we_found = sugg["what_we_found"]
        row.scored_at = utcnow()  # B-3: AI apply counts as scoring this row

    db.flush()
    after = {k: {f: getattr(r, f) for f in _RUN_FIELDS} for k, r in rows.items()}
    diffs = diff_keyed_rows(before, after, list(_RUN_FIELDS), locked_keys=locked_keys)
    changes: list[CsfDimensionChange] = []
    for d in diffs:
        tier, _, code = d.key.partition("|")
        for ch in d.changes:
            changes.append(
                CsfDimensionChange(
                    tier=tier, subcategory_code=code, field=ch.field, old=ch.old, new=ch.new
                )
            )

    a.documents_stale = True  # Work Order C3
    audit(
        db,
        action="csf.run_ai",
        target_type="csf_assessment",
        target_id=a.id,
        actor_user_id=user_id,
        details={"changed_rows": len(diffs)},
    )
    db.commit()
    out_rows = [
        _score_response(r)
        for r in sorted(rows.values(), key=lambda r: (r.tier, r.subcategory_code))
    ]
    return CsfRunAiResponse(changed=changes, rows=out_rows, mode=llm.mode)


@router.post(
    "/services/{service_id}/playbook/export",
    response_model=CsfPlaybookExportResponse,
    summary="Render + store the CSF full-Playbook workbook (XLSX) (admin)",
)
def export_playbook(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[StorageBackend, Depends(_storage_dep)],
) -> CsfPlaybookExportResponse:
    """An Enterprise Profile sheet (weighted-floor roll-up) + one sheet per tier
    with the five dimension scores and computed total/level/cap."""
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    a = _latest_assessment(db, svc.id)
    if a is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No assessment yet.")
    all_rows = (
        db.execute(select(CsfDimensionScore).where(CsfDimensionScore.assessment_id == a.id))
        .scalars()
        .all()
    )
    if not all_rows:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Seed the Working Profile before exporting.",
        )
    # B-3 export gate: every in-scope row must be scored (scored_at set) and the
    # assessment must be approved before we render the playbook. Unscored rows
    # export as "Unscored" placeholders, so exporting them silently would ship a
    # misleading maturity picture.
    in_scope_rows = [r for r in all_rows if r.in_scope]
    unscored = [r for r in in_scope_rows if r.scored_at is None]
    if unscored:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{len(unscored)} of {len(in_scope_rows)} in-scope rows are unscored; "
                "score every in-scope row before exporting the playbook."
            ),
        )
    if a.status not in (CsfAssessmentStatus.APPROVED, CsfAssessmentStatus.RELEASED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assessment is not approved; approve it before exporting the playbook.",
        )
    enterprise_rows, _ = _enterprise_subcategories(db, a)
    tier_profiles: dict[str, list] = {}
    for tier in ("high", "moderate", "low"):
        trows = sorted(
            (r for r in all_rows if r.tier == tier),
            key=lambda r: r.subcategory_code,
        )
        if trows:
            tier_profiles[tier] = [_score_response(r) for r in trows]

    from app.docx_export import DOCX_MIME

    org = None if client.legal_name == "(pending intake)" else client.legal_name
    name = org or "Client"
    on = utcnow().strftime("%Y-%m-%d")
    today = utcnow().date()

    # B-7: playbook filenames route through the §15.5 deliverable_filename helper
    # (company + service slug + date) instead of a bare "CSF_Playbook_v{n}".
    def _pb_name(qualifier: str, ext: str) -> str:
        fname = deliverable_filename(
            company=org,
            service_slug=SERVICE_SLUG_CSF_PLAYBOOK,
            extension=ext,
            day=today,
            version=a.version,
        )
        if qualifier:
            stem, _, e = fname.rpartition(".")
            return f"{stem}_{qualifier}.{e}"
        return fname

    xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    pdf_mime = "application/pdf"

    # H-8: action items for this assessment feed the "Action Plan" sheet.
    action_items = (
        db.execute(
            select(CsfActionItem)
            .where(CsfActionItem.assessment_id == a.id)
            .order_by(CsfActionItem.created_at)
        )
        .scalars()
        .all()
    )

    specs = [
        (
            "xlsx",
            "Data workbook (XLSX)",
            _pb_name("", "xlsx"),
            xlsx_mime,
            csf_playbook_export.render_xlsx(
                client_name=name,
                version=a.version,
                enterprise_rows=enterprise_rows,
                tier_profiles=tier_profiles,
                unscored_keys=frozenset(
                    (r.tier, r.subcategory_code) for r in all_rows if r.scored_at is None
                ),
                action_items=action_items,
            ),
        ),
        (
            "exec_pdf",
            "Executive briefing (PDF)",
            _pb_name("Executive", "pdf"),
            pdf_mime,
            csf_playbook_export.render_exec_pdf(
                client_name=name,
                version=a.version,
                enterprise_rows=enterprise_rows,
                generated_on=on,
            ),
        ),
        (
            "exec_docx",
            "Executive briefing (Word)",
            _pb_name("Executive", "docx"),
            DOCX_MIME,
            csf_playbook_export.render_exec_docx(
                client_name=name,
                version=a.version,
                enterprise_rows=enterprise_rows,
                generated_on=on,
            ),
        ),
        (
            "full_pdf",
            "Full playbook (PDF)",
            _pb_name("Full", "pdf"),
            pdf_mime,
            csf_playbook_export.render_full_pdf(
                client_name=name,
                version=a.version,
                enterprise_rows=enterprise_rows,
                generated_on=on,
            ),
        ),
        (
            "full_docx",
            "Full playbook (Word)",
            _pb_name("Full", "docx"),
            DOCX_MIME,
            csf_playbook_export.render_full_docx(
                client_name=name,
                version=a.version,
                enterprise_rows=enterprise_rows,
                generated_on=on,
            ),
        ),
    ]
    artifacts: list[ExportedArtifact] = []
    for kind, label, filename, mime, data in specs:
        art = _write_artifact(
            db,
            storage=storage,
            user=user,
            client_id=client.id,
            filename=filename,
            mime_type=mime,
            data=data,
        )
        artifacts.append(
            ExportedArtifact(kind=kind, label=label, artifact_id=art.id, filename=art.title)
        )

    audit(
        db,
        action="csf.playbook_exported",
        target_type="csf_assessment",
        target_id=a.id,
        actor_user_id=user.id,
        details={"version": a.version, "artifacts": len(artifacts)},
    )
    a.documents_stale = False  # Work Order C3: exporting refreshes the documents
    db.commit()
    return CsfPlaybookExportResponse(artifacts=artifacts)


# ---------------------------------------------------------------------------
# Deliverables
# ---------------------------------------------------------------------------


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
    return DeliverableResponse(
        id=deliv.id,
        service_id=deliv.service_id,
        title=deliv.title,
        summary=deliv.summary,
        version=deliv.version,
        pdf_artifact_id=deliv.pdf_artifact_id,
        xlsx_artifact_id=deliv.xlsx_artifact_id,
        docx_artifact_id=deliv.docx_artifact_id,
        pdf_filename=pdf_title,
        xlsx_filename=xlsx_title,
        docx_filename=docx_title,
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
        stage="csf.deliverable",
        uploaded_by=user.id,
    )
    db.add(art)
    db.flush()
    return art


@router.post(
    "/services/{service_id}/deliverables/finalize",
    response_model=DeliverableResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Render PDF + XLSX deliverable from the latest approved CSF assessment (admin)",
)
def finalize_csf_deliverable(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
    storage: Annotated[StorageBackend, Depends(_storage_dep)],
) -> DeliverableResponse:
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
    assessment = _latest_assessment(db, svc.id)
    if assessment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No assessment yet.",
        )
    if assessment.status not in (
        CsfAssessmentStatus.APPROVED,
        CsfAssessmentStatus.RELEASED,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assessment must be approved before finalizing the deliverable.",
        )
    answers = (
        db.execute(select(CsfAnswer).where(CsfAnswer.assessment_id == assessment.id))
        .scalars()
        .all()
    )
    valid = all_codes()
    tier_map: dict[str, int | None] = {
        r.subcategory_code: r.maturity_tier for r in answers if r.subcategory_code in valid
    }
    notes_map: dict[str, str | None] = {
        r.subcategory_code: r.notes for r in answers if r.subcategory_code in valid
    }
    score = compute_score(tier_map)
    # Engagement-level target: the client's intake goal via the source request,
    # falling back to the engine default (T3) only when the intake goal is
    # absent (B-2). The summary line and exporters print the resolved tier.
    target_tier = _client_target_tier(db, svc.id) or DEFAULT_TARGET_TIER
    # B-4: full gap list in the XLSX Gap Plan; the PDF/DOCX narrative caps at 20.
    gap = analyze_gaps(tier_map, notes=notes_map, target_tier=target_tier, top_n=None)

    client_name = client.legal_name
    if client_name == "(pending intake)":
        client_name = None

    # Filename version: same-day re-finalize -> v2, v3, ...
    today = utcnow().date()
    existing_count = db.execute(select(Deliverable).where(Deliverable.service_id == svc.id)).all()
    next_version = len(existing_count) + 1

    pdf_name = deliverable_filename(
        company=client_name,
        service_slug=SERVICE_SLUG_NIST_CSF,
        extension="pdf",
        day=today,
        version=next_version,
    )
    xlsx_name = deliverable_filename(
        company=client_name,
        service_slug=SERVICE_SLUG_NIST_CSF,
        extension="xlsx",
        day=today,
        version=next_version,
    )
    docx_name = deliverable_filename(
        company=client_name,
        service_slug=SERVICE_SLUG_NIST_CSF,
        extension="docx",
        day=today,
        version=next_version,
    )

    ctx = build_csf_context(
        client_legal_name=client_name,
        service_title=svc.title,
        assessment=assessment,
        answers=answers,
        score=score,
        gap=gap,
    )
    pdf_bytes = render_csf_pdf(ctx)
    xlsx_bytes = render_csf_xlsx(ctx)
    docx_bytes = render_csf_docx(ctx)

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

    summary_line = (
        f"Overall maturity: {score.overall_maturity_label}. "
        f"{score.answered_subcategories}/{score.total_subcategories} subcategories scored; "
        f"{gap.total_gap_count} gap(s) at target T{gap.target_tier}."
    )

    deliv = Deliverable(
        service_id=svc.id,
        title=f"{svc.title} v{next_version}",
        summary=summary_line,
        version=next_version,
        pdf_artifact_id=pdf_artifact.id,
        xlsx_artifact_id=xlsx_artifact.id,
        docx_artifact_id=docx_artifact.id,
        finalized_at=utcnow(),
        finalized_by=user.id,
    )
    db.add(deliv)
    db.flush()

    audit(
        db,
        action="csf.deliverable.finalized",
        target_type="deliverable",
        target_id=deliv.id,
        actor_user_id=user.id,
        details={
            "service_id": str(svc.id),
            "assessment_id": str(assessment.id),
            "assessment_version": assessment.version,
            "version": next_version,
            "overall_maturity_label": score.overall_maturity_label,
            "average_tier": score.average_tier,
            "coverage_pct": score.coverage_pct,
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
    summary="Most recent CSF deliverable for a service (admin)",
)
def latest_csf_deliverable(
    service_id: uuid.UUID,
    user: Annotated[User, _admin_required],
    client: Annotated[Client, Depends(current_client)],
    db: Annotated[Session, Depends(get_db)],
) -> DeliverableResponse:
    # Deliverables are admin-only (Work Order A1): clients never see or
    # download them in-app.
    svc = require_service_in_tenant(db, service_id, client.id, kind=ServiceKind.NIST_CSF)
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
