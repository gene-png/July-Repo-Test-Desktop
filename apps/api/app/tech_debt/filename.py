"""§15.5 filename convention.

`{Company_Name}_{Service_Name}{MMDDYY}.{extension}` — NO underscore between
the service name and the date; the date glues directly. Re-releases on the
same day append `_v2` / `_v3` AFTER the date but before the extension.

Slugifier (identical for company + service):
  1. trim whitespace
  2. whitespace runs -> single underscore
  3. strip anything outside [A-Z][a-z][0-9]_
  4. collapse runs of underscores
  5. trim leading/trailing underscores
  6. preserve case
  7. empty result -> "Unknown"
"""

from __future__ import annotations

import re
from datetime import date as _date


def slugify(value: str | None) -> str:
    """§15.5 slugifier."""
    if not value:
        return "Unknown"
    out = value.strip()
    out = re.sub(r"\s+", "_", out)
    out = re.sub(r"[^A-Za-z0-9_]", "", out)
    out = re.sub(r"_+", "_", out).strip("_")
    return out or "Unknown"


def mmddyy(day: _date) -> str:
    return f"{day.month:02d}{day.day:02d}{day.year % 100:02d}"


def deliverable_filename(
    *,
    company: str | None,
    service_slug: str,
    extension: str,
    day: _date,
    version: int = 1,
    working: bool = False,
) -> str:
    """Build the deliverable filename per §15.5.

    `version` > 1 appends `_v{n}` after the date.
    `working=True` prefixes WORKING_ for admin-only intermediates.
    """
    company_slug = slugify(company)
    svc = slugify(service_slug)  # in case the caller passed un-slugified input
    ext = extension.lstrip(".").lower()
    head = f"{company_slug}_{svc}{mmddyy(day)}"
    if version > 1:
        head = f"{head}_v{version}"
    name = f"{head}.{ext}"
    if working:
        name = f"WORKING_{name}"
    return name


# Canonical service slugs (Master Spec §15.5).
SERVICE_SLUG_TECH_DEBT = "Tech_Debt_Review"
SERVICE_SLUG_ZT_CISA = "Zero_Trust_Assessment_CISA"
SERVICE_SLUG_ZT_DOD = "Zero_Trust_Assessment_DoD"
SERVICE_SLUG_NIST_CSF = "NIST_CSF_2_0_Assessment"
SERVICE_SLUG_CSF_PLAYBOOK = "NIST_CSF_2_0_Playbook"
SERVICE_SLUG_ATTACK = "MITRE_ATTACK_Coverage"
SERVICE_SLUG_CONSULTATION = "Consultation_Request"
SERVICE_SLUG_ENGAGEMENT_SUMMARY = "Engagement_Summary"

SERVICE_SLUG_BY_KIND: dict[str, str] = {
    "tech_debt": SERVICE_SLUG_TECH_DEBT,
    "zero_trust_cisa": SERVICE_SLUG_ZT_CISA,
    "zero_trust_dod": SERVICE_SLUG_ZT_DOD,
    "nist_csf": SERVICE_SLUG_NIST_CSF,
    "attack_coverage": SERVICE_SLUG_ATTACK,
}
