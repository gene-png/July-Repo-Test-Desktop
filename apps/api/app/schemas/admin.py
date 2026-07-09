"""Admin schemas (Phase 2 stage 7: intake queue)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.service import ServiceKind, ServiceStatus
from app.models.service_request import ServiceType
from app.models.user import UserRole
from app.schemas.intake import ClientProfileResponse


class AdminServiceDetail(BaseModel):
    """Minimal service lookup so a workspace can resolve its owning tenant."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: ServiceKind
    status: ServiceStatus
    title: str
    client_id: uuid.UUID


class AiJobOverride(BaseModel):
    """Per-job provider override reported by ai-status (Task S1-A A-3/A-5)."""

    model: str | None = None
    max_tokens: int | None = None


class AdminAiStatus(BaseModel):
    """AI pipeline readiness. Never includes the API key itself."""

    mode: str
    provider: str
    model: str
    ready: bool
    detail: str
    # Boot-readiness diagnostics (Task S1-A A-5).
    sdk_importable: bool
    key_present: bool
    # name -> {model, max_tokens} for every job that overrides the global model.
    per_job_overrides: dict[str, AiJobOverride] = {}


class AiUsageRow(BaseModel):
    """One (client, month) usage bucket for GET /admin/ai-usage (H-5)."""

    client_id: uuid.UUID | None
    month: str  # "YYYY-MM"
    calls: int
    input_tokens: int
    output_tokens: int
    # Null when any row in the bucket used a model absent from the price table.
    estimated_cost_usd: float | None


class AiUsageResponse(BaseModel):
    rows: list[AiUsageRow]


class AiPreviewAckRequest(BaseModel):
    """Body of POST /admin/ai-preview-ack: acknowledge the redaction preview for
    a client so live AI runs are permitted (H-6)."""

    client_id: uuid.UUID


class AiPreviewAckResponse(BaseModel):
    client_id: uuid.UUID
    acknowledged: bool


class AdminUserSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    display_name: str | None
    title: str | None
    role: UserRole
    last_login_at: datetime | None
    created_at: datetime


class AdminUserDetail(BaseModel):
    """One row in the platform-wide user list (admin view).

    `email` is a plain str (not EmailStr): this is a read-only view and must not
    500 on a stored value that doesn't pass strict validation - e.g. a bootstrap
    address on a reserved TLD, or a retention tombstone. Emails are validated on
    the way in (register / AdminUserCreateRequest), not on the way out.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str | None
    title: str | None
    role: UserRole
    client_id: uuid.UUID | None
    is_active: bool
    last_login_at: datetime | None
    deactivated_at: datetime | None
    purged_at: datetime | None
    created_at: datetime


class AdminUserListResponse(BaseModel):
    users: list[AdminUserDetail]


class AdminUserCreateRequest(BaseModel):
    """Admin-initiated account creation. Role is explicit (admin or client).

    `client_id` is required when role=client (the tenant the user belongs to)
    and must be omitted/null when role=admin (admins are cross-tenant).
    """

    email: EmailStr
    password: str
    display_name: str = Field(min_length=1, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    role: UserRole
    client_id: uuid.UUID | None = None


class AdminServiceRow(BaseModel):
    """One row in the platform-wide service/engagement list (admin view)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: ServiceKind
    status: ServiceStatus
    title: str
    client_id: uuid.UUID
    created_at: datetime


class AdminServiceListResponse(BaseModel):
    services: list[AdminServiceRow]


class AdminServiceRequestRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    service_type: ServiceType
    requested_at: datetime
    requested_by: AdminUserSummary
    notes: str | None
    deadline: datetime | None
    csf_target_tier: int | None
    csf_profile: str | None
    zt_target_stage: int | None
    fulfilled_service_id: uuid.UUID | None
    declined_at: datetime | None
    declined_reason: str | None


class AdminArtifactRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    mime_type: str
    size_bytes: int
    uploaded_by: uuid.UUID
    uploaded_at: datetime


class AdminIntakeQueueResponse(BaseModel):
    client: ClientProfileResponse | None
    intake_completed_at: datetime | None
    service_requests: list[AdminServiceRequestRow]
    artifacts: list[AdminArtifactRow]
    total_users: int


class FulfillServiceRequestResponse(BaseModel):
    """Result of publishing a service request: the live engagement workspace."""

    service_id: uuid.UUID
    service_type: ServiceType
    title: str
    already_fulfilled: bool


class AdminClientSummary(BaseModel):
    """One row in the platform-wide client list (admin/reviewer view)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    legal_name: str
    dba_name: str | None
    industry: str | None
    size_band: str | None
    intake_completed_at: datetime | None
    created_at: datetime


class AdminClientListResponse(BaseModel):
    clients: list[AdminClientSummary]


class AdminClientCreateRequest(BaseModel):
    """Minimum payload to create a new tenant. Intake fills in the rest."""

    legal_name: str
    dba_name: str | None = None
    industry: str | None = None
    size_band: str | None = None


class AdminDomainRow(BaseModel):
    """One approved email domain for a client (Work Order B2)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    client_id: uuid.UUID
    domain: str
    created_at: datetime


class AdminDomainListResponse(BaseModel):
    domains: list[AdminDomainRow]


class AdminDomainCreateRequest(BaseModel):
    domain: str


class AdminAuditRow(BaseModel):
    """One append-only audit entry (H-7 viewer).

    `client_id` is surfaced from the entry's `details` payload when the acting
    route recorded it there; it is not a column on the audit table.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    at: datetime
    action: str
    target_type: str
    target_id: uuid.UUID | None = None
    actor_user_id: uuid.UUID | None = None
    client_id: uuid.UUID | None = None
    details: dict | None = None
    correlation_id: str | None = None


class AdminAuditListResponse(BaseModel):
    """A page of audit entries plus the total matching the active filters."""

    rows: list[AdminAuditRow]
    total: int
    limit: int
    offset: int
