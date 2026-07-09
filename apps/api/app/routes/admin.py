"""Admin routes.

Master Spec §15 Phase 2 acceptance:
  - "Submitting intake reflects correctly in the admin queue with the
    new-lead timestamp."
  - "All intake data round-trips correctly: client enters X, admin reads X."

Phase 2 ships the read-only queue view. Phase 3+ adds the workflow surfaces
(attach reviewer, mark final, release deliverable) on top of this.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.engine import get_job, registered_jobs
from app.ai.llm import (
    AI_PREVIEW_ACK_ACTION,
    LLMClient,
    LLMConfigurationError,
    anthropic_sdk_available,
)
from app.audit import audit
from app.config import get_settings
from app.db.session import get_db
from app.dependencies import require_role
from app.models._common import utcnow
from app.models.artifact import Artifact
from app.models.audit_entry import AuditEntry
from app.models.client import Client
from app.models.client_domain import ClientDomain
from app.models.llm_call import LLMCall
from app.models.service import Service, ServiceKind, ServiceStatus
from app.models.service_request import ServiceRequest, ServiceType
from app.models.user import User, UserRole
from app.schemas.admin import (
    AdminAiStatus,
    AdminArtifactRow,
    AdminAuditListResponse,
    AdminAuditRow,
    AdminClientCreateRequest,
    AdminClientListResponse,
    AdminClientSummary,
    AdminDomainCreateRequest,
    AdminDomainListResponse,
    AdminDomainRow,
    AdminIntakeQueueResponse,
    AdminServiceDetail,
    AdminServiceListResponse,
    AdminServiceRequestRow,
    AdminServiceRow,
    AdminUserCreateRequest,
    AdminUserDetail,
    AdminUserListResponse,
    AdminUserSummary,
    AiJobOverride,
    AiPreviewAckRequest,
    AiPreviewAckResponse,
    AiUsageResponse,
    AiUsageRow,
    FulfillServiceRequestResponse,
)
from app.schemas.intake import ClientProfileResponse
from app.security.email_domains import domain_of, is_generic_provider
from app.security.password import PasswordPolicyError, hash_password

router = APIRouter(prefix="/admin", tags=["admin"])

_admin_required = Depends(require_role(UserRole.ADMIN))

# Human-readable service titles used when a request graduates to a workspace.
_SERVICE_TITLES: dict[ServiceType, str] = {
    ServiceType.TECH_DEBT: "Technical Debt Review",
    ServiceType.ZERO_TRUST_CISA: "Zero Trust (CISA ZTMM 2.0)",
    ServiceType.ZERO_TRUST_DOD: "Zero Trust (DoD ZTRA)",
    ServiceType.NIST_CSF: "NIST CSF 2.0 Assessment",
    ServiceType.ATTACK_COVERAGE: "MITRE ATT&CK Coverage",
}


@router.get(
    "/intake-queue",
    response_model=AdminIntakeQueueResponse,
    summary="Intake queue (admin)",
)
def intake_queue(
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
    client_id: uuid.UUID | None = None,
) -> AdminIntakeQueueResponse:
    """Cross-tenant intake queue.

    Without `client_id` filter: shows requests/artifacts from all clients
    (consultant overview). The `client` field in the response is then the
    most-recently-created tenant for display continuity; treat it as advisory.
    With `client_id`: scopes to that tenant.
    """
    if client_id is not None:
        client = db.get(Client, client_id)
        if client is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Client not found.",
            )
    else:
        client = db.execute(
            select(Client).order_by(Client.created_at.desc()).limit(1)
        ).scalar_one_or_none()

    sr_stmt = select(ServiceRequest, User).join(User, ServiceRequest.requested_by == User.id)
    if client_id is not None:
        sr_stmt = sr_stmt.where(ServiceRequest.client_id == client_id)
    sr_stmt = sr_stmt.order_by(ServiceRequest.requested_at.desc())
    rows = db.execute(sr_stmt).all()
    service_requests: list[AdminServiceRequestRow] = []
    for sr, requester in rows:
        service_requests.append(
            AdminServiceRequestRow(
                id=sr.id,
                service_type=sr.service_type,
                requested_at=sr.requested_at,
                requested_by=AdminUserSummary.model_validate(requester, from_attributes=True),
                notes=sr.notes,
                deadline=sr.deadline,
                csf_target_tier=sr.csf_target_tier,
                csf_profile=sr.csf_profile,
                zt_target_stage=sr.zt_target_stage,
                fulfilled_service_id=sr.fulfilled_service_id,
                declined_at=sr.declined_at,
                declined_reason=sr.declined_reason,
            )
        )

    art_stmt = select(Artifact)
    if client_id is not None:
        art_stmt = art_stmt.where(Artifact.client_id == client_id)
    art_stmt = art_stmt.order_by(Artifact.uploaded_at.desc())
    artifact_rows = db.execute(art_stmt).scalars().all()
    artifacts = [AdminArtifactRow.model_validate(a, from_attributes=True) for a in artifact_rows]

    user_stmt = select(func.count()).select_from(User)
    if client_id is not None:
        user_stmt = user_stmt.where(User.client_id == client_id)
    total_users = db.execute(user_stmt).scalar_one()

    return AdminIntakeQueueResponse(
        client=(
            ClientProfileResponse.model_validate(client, from_attributes=True)
            if client is not None
            else None
        ),
        intake_completed_at=client.intake_completed_at if client else None,
        service_requests=service_requests,
        artifacts=artifacts,
        total_users=total_users,
    )


# -----------------------------------------------------------------------------
# Users (admin-only account management)
# -----------------------------------------------------------------------------


def _active_admin_count(db: Session) -> int:
    return db.execute(
        select(func.count())
        .select_from(User)
        .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
    ).scalar_one()


@router.get(
    "/users",
    response_model=AdminUserListResponse,
    summary="List all user accounts (admin)",
)
def list_users(
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserListResponse:
    """Cross-tenant list of every account, newest first."""
    rows = db.execute(select(User).order_by(User.created_at.desc())).scalars().all()
    return AdminUserListResponse(
        users=[AdminUserDetail.model_validate(r, from_attributes=True) for r in rows]
    )


@router.post(
    "/users",
    response_model=AdminUserDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user account (admin)",
)
def create_user(
    body: AdminUserCreateRequest,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserDetail:
    """Create an admin or client account directly (the only way to mint admins).

    Admins are cross-tenant (no client_id); client users must name an existing
    client tenant.
    """
    email = body.email.strip().lower()
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account already exists for that email.",
        )

    if body.role == UserRole.CLIENT:
        if body.client_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="client_id is required when creating a client user.",
            )
        if db.get(Client, body.client_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No client with that id.",
            )
        client_id = body.client_id
    else:  # admin: cross-tenant, never pinned to a client
        if body.client_id is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Admin users are cross-tenant and must not have a client_id.",
            )
        client_id = None

    try:
        password_hash = hash_password(body.password)
    except PasswordPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    user = User(
        email=email,
        password_hash=password_hash,
        role=body.role,
        display_name=body.display_name,
        title=body.title,
        client_id=client_id,
    )
    db.add(user)
    db.flush()
    audit(
        db,
        action="user.created",
        target_type="user",
        target_id=user.id,
        actor_user_id=admin.id,
        details={"role": body.role.value, "source": "admin", "client_id": str(client_id)},
    )
    db.commit()
    db.refresh(user)
    return AdminUserDetail.model_validate(user, from_attributes=True)


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate a user account (admin)",
)
def deactivate_user(
    user_id: uuid.UUID,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Deactivate (not hard-delete) an account: blocks login and starts the
    retention clock. Guards against locking the platform out of admin access."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if target.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You can't deactivate your own account.",
        )
    if target.role == UserRole.ADMIN and target.is_active and _active_admin_count(db) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Can't deactivate the last active admin.",
        )

    if target.is_active:
        target.is_active = False
        target.deactivated_at = utcnow()
        audit(
            db,
            action="user.deactivated",
            target_type="user",
            target_id=target.id,
            actor_user_id=admin.id,
            details={"role": target.role.value},
        )
        db.commit()


@router.post(
    "/users/{user_id}/reactivate",
    response_model=AdminUserDetail,
    summary="Reactivate a deactivated user account (admin)",
)
def reactivate_user(
    user_id: uuid.UUID,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserDetail:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    if not target.is_active:
        target.is_active = True
        target.deactivated_at = None
        audit(
            db,
            action="user.reactivated",
            target_type="user",
            target_id=target.id,
            actor_user_id=admin.id,
            details={"role": target.role.value},
        )
        db.commit()
        db.refresh(target)
    return AdminUserDetail.model_validate(target, from_attributes=True)


@router.get(
    "/clients",
    response_model=AdminClientListResponse,
    summary="List all clients (admin)",
)
def list_clients(
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AdminClientListResponse:
    rows = db.execute(select(Client).order_by(Client.created_at.desc())).scalars().all()
    return AdminClientListResponse(
        clients=[AdminClientSummary.model_validate(r, from_attributes=True) for r in rows]
    )


@router.post(
    "/clients",
    response_model=AdminClientSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new client tenant (admin)",
)
def create_client(
    body: AdminClientCreateRequest,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AdminClientSummary:
    legal_name = body.legal_name.strip()
    if not legal_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="legal_name is required.",
        )
    client = Client(
        legal_name=legal_name,
        dba_name=body.dba_name,
        industry=body.industry,
        size_band=body.size_band,
    )
    db.add(client)
    db.flush()
    audit(
        db,
        action="client.created",
        target_type="client",
        target_id=client.id,
        actor_user_id=admin.id,
        details={"legal_name": legal_name, "source": "admin"},
    )
    db.commit()
    db.refresh(client)
    return AdminClientSummary.model_validate(client, from_attributes=True)


@router.get(
    "/clients/{cid}",
    response_model=AdminClientSummary,
    summary="Client detail (admin)",
)
def get_client(
    cid: uuid.UUID,
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AdminClientSummary:
    client = db.get(Client, cid)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client not found.",
        )
    return AdminClientSummary.model_validate(client, from_attributes=True)


@router.get(
    "/clients/{cid}/domains",
    response_model=AdminDomainListResponse,
    summary="List a client's approved email domains (admin)",
)
def list_client_domains(
    cid: uuid.UUID,
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AdminDomainListResponse:
    if db.get(Client, cid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found.")
    rows = (
        db.execute(
            select(ClientDomain).where(ClientDomain.client_id == cid).order_by(ClientDomain.domain)
        )
        .scalars()
        .all()
    )
    return AdminDomainListResponse(
        domains=[AdminDomainRow.model_validate(r, from_attributes=True) for r in rows]
    )


@router.post(
    "/clients/{cid}/domains",
    response_model=AdminDomainRow,
    status_code=status.HTTP_201_CREATED,
    summary="Approve an email domain for a client (admin)",
)
def add_client_domain(
    cid: uuid.UUID,
    body: AdminDomainCreateRequest,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AdminDomainRow:
    if db.get(Client, cid) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found.")
    # Accept either a bare domain or a full email; normalize to the domain.
    raw = body.domain.strip().lower()
    domain = domain_of(raw) if "@" in raw else raw
    if not domain or "." not in domain:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Enter a valid domain, e.g. company.com.",
        )
    if is_generic_provider(domain):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Generic email providers can't be approved as a client domain.",
        )
    existing = db.execute(
        select(ClientDomain).where(ClientDomain.domain == domain)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That domain is already registered to a client.",
        )
    row = ClientDomain(client_id=cid, domain=domain, created_by=admin.id)
    db.add(row)
    db.flush()
    audit(
        db,
        action="client.domain.added",
        target_type="client",
        target_id=cid,
        actor_user_id=admin.id,
        details={"domain": domain},
    )
    db.commit()
    db.refresh(row)
    return AdminDomainRow.model_validate(row, from_attributes=True)


@router.delete(
    "/clients/{cid}/domains/{domain_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an approved email domain (admin)",
)
def remove_client_domain(
    cid: uuid.UUID,
    domain_id: uuid.UUID,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    row = db.get(ClientDomain, domain_id)
    if row is None or row.client_id != cid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found.")
    db.delete(row)
    audit(
        db,
        action="client.domain.removed",
        target_type="client",
        target_id=cid,
        actor_user_id=admin.id,
        details={"domain": row.domain},
    )
    db.commit()


@router.get(
    "/services",
    response_model=AdminServiceListResponse,
    summary="List all services / engagements (admin)",
)
def list_services(
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
    client_id: uuid.UUID | None = None,
    include_archived: bool = False,
) -> AdminServiceListResponse:
    """Cross-tenant list of services (engagements), newest first.

    Archived services are hidden by default; pass include_archived=true to see
    them (e.g. for an "Archived" view).
    """
    stmt = select(Service)
    if client_id is not None:
        stmt = stmt.where(Service.client_id == client_id)
    if not include_archived:
        stmt = stmt.where(Service.status != ServiceStatus.ARCHIVED)
    stmt = stmt.order_by(Service.created_at.desc())
    rows = db.execute(stmt).scalars().all()
    return AdminServiceListResponse(
        services=[AdminServiceRow.model_validate(r, from_attributes=True) for r in rows]
    )


@router.delete(
    "/services/{service_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive (remove) a service (admin)",
)
def archive_service(
    service_id: uuid.UUID,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """Soft-remove a service by archiving it. Data is retained per policy and
    the workspace drops out of active lists."""
    svc = db.get(Service, service_id)
    if svc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found.")
    svc.status = ServiceStatus.ARCHIVED
    audit(
        db,
        action="service.archived",
        target_type="service",
        target_id=svc.id,
        actor_user_id=admin.id,
        details={"client_id": str(svc.client_id), "kind": svc.kind.value},
    )
    db.commit()


@router.post(
    "/service-requests/{request_id}/fulfill",
    response_model=FulfillServiceRequestResponse,
    summary="Publish a service request for processing (admin)",
)
def fulfill_service_request(
    request_id: uuid.UUID,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> FulfillServiceRequestResponse:
    """Graduate a service request into a live engagement workspace.

    The admin reviews the client's inputs + uploads in the queue, then
    publishes: this opens the Service (status in_progress) so the consultant
    can run the assessment and the AI pipeline against vetted intake data.
    Idempotent — re-publishing returns the existing workspace.
    """
    sr = db.get(ServiceRequest, request_id)
    if sr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service request not found.",
        )
    if sr.service_type == ServiceType.CONSULTATION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Consultation requests are handled directly, not published as a service.",
        )
    if sr.fulfilled_service_id is not None:
        existing = db.get(Service, sr.fulfilled_service_id)
        if existing is not None:
            return FulfillServiceRequestResponse(
                service_id=existing.id,
                service_type=sr.service_type,
                title=existing.title,
                already_fulfilled=True,
            )

    client = db.get(Client, sr.client_id)
    org = client.legal_name if client is not None else "Client"
    svc = Service(
        kind=ServiceKind(sr.service_type.value),
        status=ServiceStatus.IN_PROGRESS,
        title=f"{org} — {_SERVICE_TITLES[sr.service_type]}",
        client_id=sr.client_id,
        source_request_id=sr.id,
        opened_by=admin.id,
    )
    db.add(svc)
    db.flush()
    sr.fulfilled_service_id = svc.id
    audit(
        db,
        action="service_request.fulfilled",
        target_type="service",
        target_id=svc.id,
        actor_user_id=admin.id,
        details={"service_type": sr.service_type.value, "request_id": str(sr.id)},
    )
    db.commit()
    db.refresh(svc)
    return FulfillServiceRequestResponse(
        service_id=svc.id,
        service_type=sr.service_type,
        title=svc.title,
        already_fulfilled=False,
    )


@router.get(
    "/services/{service_id}",
    response_model=AdminServiceDetail,
    summary="Service detail (admin) - resolves a workspace's owning tenant",
)
def get_service(
    service_id: uuid.UUID,
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AdminServiceDetail:
    """Look up a service by id, including its client_id.

    Cross-tenant on purpose (admin-only, no X-Client-Id): the workspace UI
    calls this to discover which client a service belongs to, then sets that
    as the active tenant before its tenant-scoped data calls.
    """
    svc = db.get(Service, service_id)
    if svc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Service not found.",
        )
    return AdminServiceDetail.model_validate(svc, from_attributes=True)


@router.get(
    "/ai-status",
    response_model=AdminAiStatus,
    summary="AI pipeline readiness (admin)",
)
def ai_status(_admin: Annotated[User, _admin_required]) -> AdminAiStatus:
    """Report whether AI features will actually run a live call.

    `ready` is true only when a real provider call will be made. Fixture mode
    reports ready=false. In LIVE mode a missing SDK or API key is a hard
    misconfiguration and raises the typed {reason, message} error mapped to 503
    (Task S1-A A-5) instead of silently reporting not-ready. The API key itself
    is never returned; only whether one is present.
    """
    s = get_settings()
    mode = s.shield_llm_mode
    provider = s.shield_llm_provider
    model = s.shield_llm_model
    sdk_importable = anthropic_sdk_available()
    key_present = bool(s.anthropic_api_key)

    overrides: dict[str, AiJobOverride] = {}
    for name in registered_jobs():
        job = get_job(name)
        if job.model is not None or job.max_tokens is not None:
            overrides[name] = AiJobOverride(model=job.model, max_tokens=job.max_tokens)

    if mode != "live":
        return AdminAiStatus(
            mode=mode,
            provider=provider,
            model=model,
            ready=False,
            detail=(
                "AI suggestions are simulated (deterministic fixtures) for demo "
                "and testing; set SHIELD_LLM_MODE=live for real analysis."
            ),
            sdk_importable=sdk_importable,
            key_present=key_present,
            per_job_overrides=overrides,
        )

    # Live mode: build the provider to reuse the eager SDK + key checks (A-1),
    # surfacing any configuration failure as a typed 503.
    try:
        LLMClient.from_settings(s)
    except LLMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"reason": exc.reason, "message": exc.message},
        ) from exc

    return AdminAiStatus(
        mode=mode,
        provider=provider,
        model=model,
        ready=True,
        detail=f"Live AI configured ({provider}/{model}).",
        sdk_importable=sdk_importable,
        key_present=key_present,
        per_job_overrides=overrides,
    )


def _estimated_cost(
    price_table: dict[str, dict[str, float]],
    per_model: dict[str, tuple[int, int]],
) -> float | None:
    """Sum estimated USD cost across a bucket's models. Returns None when any
    model in the bucket is absent from the price table (can't estimate)."""
    total = 0.0
    for model_name, (in_tok, out_tok) in per_model.items():
        price = price_table.get(model_name)
        if price is None:
            return None
        total += (in_tok / 1_000_000) * price.get("in", 0.0)
        total += (out_tok / 1_000_000) * price.get("out", 0.0)
    return round(total, 6)


@router.get(
    "/ai-usage",
    response_model=AiUsageResponse,
    summary="Per-client per-month AI usage + estimated cost (admin)",
)
def ai_usage(
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
    format: Annotated[str | None, Query(description="'csv' for a text/csv download")] = None,
) -> AiUsageResponse | Response:
    """Aggregate llm_calls into (client, month) buckets: call count, summed
    input/output tokens, and an estimated USD cost from the configured price
    table (H-5). ?format=csv returns the same rows as a CSV download."""
    settings = get_settings()
    price_table = settings.shield_llm_price_table

    rows = db.execute(
        select(
            LLMCall.client_id,
            LLMCall.requested_at,
            LLMCall.model,
            LLMCall.input_tokens,
            LLMCall.output_tokens,
        )
    ).all()

    # bucket key -> aggregate; track per-model token totals for costing.
    buckets: dict[tuple[str | None, str], dict] = {}
    for client_id, requested_at, model_name, in_tok, out_tok in rows:
        month = requested_at.strftime("%Y-%m") if requested_at is not None else "unknown"
        cid = str(client_id) if client_id is not None else None
        key = (cid, month)
        b = buckets.setdefault(key, {"calls": 0, "input": 0, "output": 0, "per_model": {}})
        b["calls"] += 1
        i = int(in_tok or 0)
        o = int(out_tok or 0)
        b["input"] += i
        b["output"] += o
        pm = b["per_model"].setdefault(model_name, [0, 0])
        pm[0] += i
        pm[1] += o

    out_rows: list[AiUsageRow] = []
    for (cid, month), b in sorted(buckets.items(), key=lambda kv: (kv[0][1], kv[0][0] or "")):
        per_model = {m: (v[0], v[1]) for m, v in b["per_model"].items()}
        out_rows.append(
            AiUsageRow(
                client_id=uuid.UUID(cid) if cid else None,
                month=month,
                calls=b["calls"],
                input_tokens=b["input"],
                output_tokens=b["output"],
                estimated_cost_usd=_estimated_cost(price_table, per_model),
            )
        )

    if (format or "").lower() == "csv":
        import csv
        import io

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            ["client_id", "month", "calls", "input_tokens", "output_tokens", "estimated_cost_usd"]
        )
        for r in out_rows:
            writer.writerow(
                [
                    str(r.client_id) if r.client_id else "",
                    r.month,
                    r.calls,
                    r.input_tokens,
                    r.output_tokens,
                    "" if r.estimated_cost_usd is None else r.estimated_cost_usd,
                ]
            )
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=ai-usage.csv"},
        )

    return AiUsageResponse(rows=out_rows)


@router.post(
    "/ai-preview-ack",
    response_model=AiPreviewAckResponse,
    summary="Acknowledge the redaction preview for a client (admin)",
)
def ai_preview_ack(
    body: AiPreviewAckRequest,
    admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
) -> AiPreviewAckResponse:
    """Record that an admin has reviewed the redaction preview for a client
    (H-6). This audit row unlocks live (non-preview) AI runs for the client;
    fixture mode never requires it."""
    if db.get(Client, body.client_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found.")
    audit(
        db,
        action=AI_PREVIEW_ACK_ACTION,
        target_type="client",
        target_id=body.client_id,
        actor_user_id=admin.id,
        details={"acknowledged_by": str(admin.id)},
    )
    db.commit()
    return AiPreviewAckResponse(client_id=body.client_id, acknowledged=True)


_AUDIT_MAX_LIMIT = 200
_AUDIT_DEFAULT_LIMIT = 50


def _audit_client_filter(db: Session, client_id: uuid.UUID):
    """Dialect-portable predicate matching rows whose details.client_id == cid.

    client_id lives inside the JSONB `details` payload rather than a column, so
    filtering is best-effort against the rows that recorded it there.
    """
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        return AuditEntry.details["client_id"].astext == str(client_id)
    return func.json_extract(AuditEntry.details, "$.client_id") == str(client_id)


def _audit_row(entry: AuditEntry) -> AdminAuditRow:
    details = entry.details if isinstance(entry.details, dict) else None
    client_id: uuid.UUID | None = None
    if details:
        raw = details.get("client_id")
        if raw:
            try:
                client_id = uuid.UUID(str(raw))
            except (ValueError, AttributeError, TypeError):
                client_id = None
    return AdminAuditRow(
        id=entry.id,
        at=entry.at,
        action=entry.action,
        target_type=entry.target_type,
        target_id=entry.target_id,
        actor_user_id=entry.actor_user_id,
        client_id=client_id,
        details=details,
        correlation_id=entry.correlation_id,
    )


@router.get(
    "/audit",
    response_model=AdminAuditListResponse,
    summary="Append-only audit log with filters + pagination (admin)",
)
def audit_log(
    _admin: Annotated[User, _admin_required],
    db: Annotated[Session, Depends(get_db)],
    client_id: uuid.UUID | None = None,
    action: Annotated[str | None, Query(description="Exact action verb, e.g. user.created")] = None,
    actor_user_id: uuid.UUID | None = None,
    date_from: Annotated[
        datetime | None, Query(description="Include entries at/after this time")
    ] = None,
    date_to: Annotated[
        datetime | None, Query(description="Include entries at/before this time")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=_AUDIT_MAX_LIMIT)] = _AUDIT_DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
    format: Annotated[str | None, Query(description="'csv' for a text/csv download")] = None,
) -> AdminAuditListResponse | Response:
    """Query the append-only audit trail newest-first (H-7).

    Filters (client_id, action, actor_user_id, date_from/date_to) compose with
    AND. ?format=csv streams the current filter's matches as a CSV download
    (bounded by the same limit/offset as the JSON page)."""
    filters = []
    if client_id is not None:
        filters.append(_audit_client_filter(db, client_id))
    if action is not None:
        filters.append(AuditEntry.action == action)
    if actor_user_id is not None:
        filters.append(AuditEntry.actor_user_id == actor_user_id)
    if date_from is not None:
        filters.append(AuditEntry.at >= date_from)
    if date_to is not None:
        filters.append(AuditEntry.at <= date_to)

    count_stmt = select(func.count()).select_from(AuditEntry)
    for f in filters:
        count_stmt = count_stmt.where(f)
    total = db.execute(count_stmt).scalar_one()

    stmt = select(AuditEntry)
    for f in filters:
        stmt = stmt.where(f)
    stmt = stmt.order_by(AuditEntry.at.desc(), AuditEntry.id.desc()).limit(limit).offset(offset)
    rows = [_audit_row(e) for e in db.execute(stmt).scalars().all()]

    if (format or "").lower() == "csv":
        import csv
        import io

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "at",
                "action",
                "target_type",
                "target_id",
                "actor_user_id",
                "client_id",
                "correlation_id",
            ]
        )
        for r in rows:
            writer.writerow(
                [
                    r.at.isoformat(),
                    r.action,
                    r.target_type,
                    str(r.target_id) if r.target_id else "",
                    str(r.actor_user_id) if r.actor_user_id else "",
                    str(r.client_id) if r.client_id else "",
                    r.correlation_id or "",
                ]
            )
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=audit-log.csv"},
        )

    return AdminAuditListResponse(rows=rows, total=total, limit=limit, offset=offset)
