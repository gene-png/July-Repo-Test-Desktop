"""Capability extraction - call the LLM with redacted inventory rows.

Master Spec §15 Phase 3 + §12. The flow:

  1. Load the source artifact bytes via the storage backend.
  2. Parse into row-dicts (tech_debt.parsers).
  3. Build a structured prompt + a payload of {"rows": [...], "context": {...}}.
  4. Call LLMClient.invoke(purpose="extract.capabilities"). The client
     redacts the payload before send and writes an llm_calls audit row.
  5. Parse the LLM's JSON response into ExtractedCapability rows. The
     route layer turns those into CapabilityItem ORM rows.

The prompt is versioned (`PROMPT_VERSION` constant) so a future change
to the prompt shape doesn't silently regress past extractions; the
llm_calls row records the version that ran.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.ai.llm import LLMClient
from app.models.artifact import Artifact
from app.models.client import Client
from app.models.llm_call import LLMCall
from app.models.user import User
from app.storage import StorageBackend
from app.tech_debt.parsers import EmptyInventoryError, ParseReport, parse_inventory

PROMPT_VERSION = "v1"

PROMPT = """You extract a structured capability list from a raw security \
tool inventory.

For each row in the JSON `rows` array, decide if it represents a security \
capability the organization is paying for (tool, platform, service, \
subscription). Skip rows that are notes, blank, or duplicates.

Return ONLY a JSON object of the form:

  {
    "items": [
      {
        "name": "<short name>",
        "vendor": "<vendor or null>",
        "category": "<category like CNAPP, EDR, SIEM, IAM, GRC, or null>",
        "function": "<one-line function the capability serves, or null>",
        "annual_cost_usd": <number or null>,
        "license_count": <integer or null>,
        "notes": "<short note, or null>",
        "confidence_pct": <integer 0-100>,
        "source_row_index": <integer index into rows[]>
      },
      ...
    ]
  }

Do not include any text outside the JSON object. Set confidence_pct \
honestly - 100 for unambiguous rows, lower when the row needs human \
review."""


@dataclass(frozen=True)
class ExtractedCapability:
    name: str
    vendor: str | None
    category: str | None
    function: str | None
    annual_cost_usd: float | None
    license_count: int | None
    notes: str | None
    confidence_pct: int | None
    source_row_index: int | None


@dataclass
class ExtractionResult:
    items: list[ExtractedCapability]
    llm_call: LLMCall
    # True when the inventory was longer than parsers.MAX_ROWS and the tail
    # was dropped before the model saw it. Surfaced in the extract response
    # so the admin knows the list may be incomplete.
    truncated: bool = False
    # Structured parse metadata (C-3): sheet used, rows parsed/skipped,
    # truncation. None only on the legacy direct-construction path.
    parse_report: ParseReport | None = None


def _load_artifact_bytes(storage: StorageBackend, artifact: Artifact) -> bytes:
    """Read the raw artifact bytes through the storage backend (C-7).

    Every backend implements ``get(key)`` (local reads the file, S3 does a
    ``get_object``). Missing objects raise FileNotFoundError and a storage
    outage raises StorageUnavailableError; the route maps those to 410 / 503.
    """
    return storage.get(artifact.file_storage_key)


# Leading currency symbol / whitespace, then a plain number (optional thousands
# commas + optional decimal). Trailing text after the number is captured so we
# can distinguish a trailing word (allowed: "500 seats") from an attached
# multiplier/unit (rejected: "1.2M", "50%").
_LEADING_NUMBER_RE = re.compile(r"\$?\s*(\d[\d,]*(?:\.\d+)?)(.*)$", re.DOTALL)


def _leading_number(raw: Any) -> float | None:
    """Conservatively parse a leading plain number from ``raw``.

    Returns None when nothing plausible parses. We deliberately do NOT guess
    multipliers ("120k", "1.2M") or currencies ("EUR 120") - those come back
    None so the raw string can be preserved in notes for human review.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return None
    m = _LEADING_NUMBER_RE.match(s)
    if m is None:
        return None
    num_str, rest = m.group(1), m.group(2)
    # A trailing word (space-separated) is fine; an attached unit/multiplier is
    # ambiguous, so bail rather than guess.
    if rest and not rest[0].isspace():
        return None
    try:
        return float(num_str.replace(",", ""))
    except ValueError:
        return None


def _money_field(raw: Any) -> tuple[float | None, str | None]:
    """Parse an annual-cost value; return (value, note-or-None).

    Unparseable non-empty raw strings return None plus a "cost: '<raw>'" note
    so nothing is silently dropped.
    """
    val = _leading_number(raw)
    if val is not None:
        return val, None
    if raw is None:
        return None, None
    s = str(raw).strip()
    if not s:
        return None, None
    return None, f"cost: '{s}'"


def _count_field(raw: Any) -> tuple[int | None, str | None]:
    """Parse a license-count value; return (value, note-or-None)."""
    val = _leading_number(raw)
    if val is not None:
        return int(val), None
    if raw is None:
        return None, None
    s = str(raw).strip()
    if not s:
        return None, None
    return None, f"licenses: '{s}'"


def _parse_response(
    content: str, *, input_row_count: int | None = None
) -> list[ExtractedCapability]:
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError as exc:
        # Some providers wrap the JSON in prose despite the instruction.
        # Strip everything outside the outermost {...} and retry once.
        first = content.find("{")
        last = content.rfind("}")
        if first == -1 or last == -1 or last <= first:
            raise ValueError(f"LLM response was not parseable JSON: {exc}") from exc
        decoded = json.loads(content[first : last + 1])

    # C-5: the response must be the documented {"items": [...]} object. A
    # list-shaped or wrong-key response is an upstream contract violation, not
    # an empty result - raise so the route can surface a 502.
    if not isinstance(decoded, dict) or "items" not in decoded:
        got = sorted(decoded) if isinstance(decoded, dict) else type(decoded).__name__
        raise ValueError(
            f"AI response was not the expected {{'items': [...]}} object (got keys: {got})"
        )
    raw_items = decoded["items"]
    if not isinstance(raw_items, list):
        raise ValueError("AI response 'items' was not a list.")

    items = [_coerce_item(item) for item in raw_items if isinstance(item, dict)]
    if input_row_count and not items:
        raise ValueError(
            "AI returned no capabilities for a non-empty inventory "
            f"({input_row_count} rows submitted)."
        )
    return items


def _coerce_item(item: dict[str, Any]) -> ExtractedCapability:
    def _opt_str(key: str) -> str | None:
        v = item.get(key)
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    def _opt_int(key: str) -> int | None:
        v = item.get(key)
        if v is None or v == "":
            return None
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    # Tolerant numeric parsing (C-4): unparseable cost/license values are
    # preserved in notes rather than dropped.
    note_parts: list[str] = []
    base_note = _opt_str("notes")
    if base_note:
        note_parts.append(base_note)
    cost, cost_note = _money_field(item.get("annual_cost_usd"))
    if cost_note:
        note_parts.append(cost_note)
    licenses, license_note = _count_field(item.get("license_count"))
    if license_note:
        note_parts.append(license_note)
    notes = "; ".join(note_parts) or None

    # Clamp confidence to the documented 0-100 range at extraction (C-4).
    confidence = _opt_int("confidence_pct")
    if confidence is not None:
        confidence = max(0, min(100, confidence))

    return ExtractedCapability(
        name=(_opt_str("name") or "Unknown capability"),
        vendor=_opt_str("vendor"),
        category=_opt_str("category"),
        function=_opt_str("function"),
        annual_cost_usd=cost,
        license_count=licenses,
        notes=notes,
        confidence_pct=confidence,
        source_row_index=_opt_int("source_row_index"),
    )


def _build_extract_payload(
    storage: StorageBackend, artifact: Artifact
) -> tuple[dict[str, Any], list, Any]:
    """Load + parse the inventory into the AI job payload (shared by the live
    extract and the H-6 redaction preview). Raises EmptyInventoryError for a
    header-only/empty file."""
    raw = _load_artifact_bytes(storage, artifact)
    rows, report = parse_inventory(raw, artifact.mime_type)
    if not rows:
        raise EmptyInventoryError(
            "No data rows found in this file; check that the inventory has a "
            "header row above the data"
        )
    payload: dict[str, Any] = {
        "rows": rows,
        "context": {
            "source_filename": artifact.title,
            "source_mime": artifact.mime_type,
        },
    }
    return payload, rows, report


def preview_extraction(
    *,
    storage: StorageBackend,
    artifact: Artifact,
    client_org_name: str | None,
    name_hints: Iterable[str] = (),
    llm: LLMClient,
) -> dict[str, Any]:
    """H-6: build the extract payload and return the redaction preview WITHOUT
    calling the provider or writing an llm_calls row."""
    payload, _rows, _report = _build_extract_payload(storage, artifact)
    return llm.preview(
        purpose="extract.capabilities",
        inputs=payload,
        client_org_name=client_org_name,
        name_hints=tuple(name_hints),
    )


def extract_capabilities(
    *,
    db: Session,
    storage: StorageBackend,
    artifact: Artifact,
    requested_by: User,
    service_id: uuid.UUID,
    client_id: uuid.UUID | None = None,
    client_org_name: str | None,
    name_hints: Iterable[str] = (),
    llm: LLMClient,
) -> ExtractionResult:
    """Top-level entry point used by the ingest route."""
    payload, rows, report = _build_extract_payload(storage, artifact)

    # Runs through the AI job registry (Work Order C1); the "tech_debt_extract"
    # job keeps the historical "extract.capabilities" llm purpose.
    from app.ai.engine import run_job

    result = run_job(
        db,
        llm,
        "tech_debt_extract",
        inputs=payload,
        requested_by=requested_by.id,
        service_id=service_id,
        client_id=client_id,
        client_org_name=client_org_name,
        name_hints=tuple(name_hints),
    )
    # C-5: the registry parser validated the response shape; here we know the
    # input row count, so we can reject an empty result for a non-empty input
    # (a ValueError the route maps to 502, before any CapabilityList is minted).
    if not result.data:
        raise ValueError(
            "AI returned no capabilities for a non-empty inventory "
            f"({len(rows)} rows submitted)."
        )
    return ExtractionResult(
        items=result.data,
        llm_call=result.llm_call,
        truncated=report.truncated,
        parse_report=report,
    )


def name_hints_for_tenant(db: Session, client_id) -> list[str]:
    """Pull display_name + email-local-parts off every user in this tenant.

    The redactor uses these as a name dictionary so the inventory's
    "owner" / "POC" columns don't leak into the LLM payload. Multi-tenant:
    only the tenant's own user names are leaked into the dictionary so
    one client's names don't end up in another's redaction pass.
    """
    from sqlalchemy import select

    rows = db.execute(
        select(User.display_name, User.email).where(User.client_id == client_id)
    ).all()
    hints: list[str] = []
    for name, email in rows:
        if name:
            hints.append(name)
        if email and "@" in email:
            hints.append(email.split("@", 1)[0])
    return [h for h in hints if h and len(h) >= 2]


def client_org_name_for_tenant(db: Session, client_id) -> str | None:
    """Pull the named tenant's legal name (or None for placeholders)."""
    row = db.get(Client, client_id)
    if row is None:
        return None
    name = row.legal_name
    if not name or name == "(pending intake)":
        return None
    return name


# Back-compat shims so callers updated incrementally still resolve.
def name_hints_for_deployment(db: Session) -> list[str]:  # pragma: no cover
    raise RuntimeError(
        "name_hints_for_deployment is removed (multi-tenant). "
        "Use name_hints_for_tenant(db, client_id) instead."
    )


def client_org_name_for_deployment(db: Session) -> str | None:  # pragma: no cover
    raise RuntimeError(
        "client_org_name_for_deployment is removed (multi-tenant). "
        "Use client_org_name_for_tenant(db, client_id) instead."
    )
