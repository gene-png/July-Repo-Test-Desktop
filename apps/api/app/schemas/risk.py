"""Risk Register schemas (Work Order E)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RiskGateSource(BaseModel):
    """A source assessment feeding the Risk Register, with its approval status."""

    kind: str  # attack | csf | zt
    status: str | None  # the assessment's status token, or None when absent
    approved: bool


class RiskGateStatus(BaseModel):
    """Whether the Risk Register can be generated for a client.

    Threshold (F-3): an APPROVED MITRE ATT&CK coverage mapping AND at least one
    APPROVED CSF or Zero Trust assessment.
    """

    unlocked: bool
    has_attack: bool
    has_csf: bool
    has_zt: bool
    missing: list[str]
    sources: list[RiskGateSource] = []


class RiskEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    axis: str | None
    source: str | None
    source_id: str | None
    linked_techniques: list[str] | None
    linked_controls: list[str] | None
    likelihood: str | None
    impact: str | None
    tier: str | None
    compensating_controls: str | None
    residual_risk: str | None
    recommended_action: str | None
    rationale: str | None
    origin: str
    trust: str | None
    locked: bool = False


class RiskEntryPatch(BaseModel):
    """Admin edit of a single Risk Register entry (F-3).

    Any subset may be supplied. Likelihood/impact tokens are normalized and the
    tier is always re-derived in code (never client-set). `locked` toggles the
    regenerate-preservation flag.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    description: str | None = None
    likelihood: str | None = None
    impact: str | None = None
    compensating_controls: str | None = None
    recommended_action: str | None = None
    rationale: str | None = None
    locked: bool | None = None


class RiskRegisterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    client_id: uuid.UUID
    version: int
    generated_by: uuid.UUID | None
    finalized_at: datetime | None
    approved_at: datetime | None = None
    approved_by: uuid.UUID | None = None
    created_at: datetime
    xlsx_artifact_id: uuid.UUID | None = None
    pdf_artifact_id: uuid.UUID | None = None
    docx_artifact_id: uuid.UUID | None = None
    xlsx_filename: str | None = None
    pdf_filename: str | None = None
    docx_filename: str | None = None
    entries: list[RiskEntryResponse]
    # Dashboard rollups (code-computed).
    tier_counts: dict[str, int] = {}
    axis_counts: dict[str, int] = {}
    action_counts: dict[str, int] = {}
    # Non-fatal advisories from the last generate (Task S1-A A-4), e.g. a count
    # of entries whose likelihood/impact the model returned in an unknown form.
    warnings: list[str] = []
    # E-5: "fixture" (simulated) or "live" for the last generate; None on a
    # plain latest/export read that didn't run the model.
    mode: str | None = None
