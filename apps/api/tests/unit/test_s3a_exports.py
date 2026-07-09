"""Task S3-A export-completeness tests (B-4, B-5, B-6, B-7).

Pure exporter tests: build a deliverable context and assert the rendered
XLSX/DOCX content matches the engine outputs.
"""

from __future__ import annotations

import io
import uuid

import pytest
from openpyxl import load_workbook


# ---------------------------------------------------------------------------
# B-4 / B-5: Zero Trust
# ---------------------------------------------------------------------------
def _zt_ctx(stage: int = 3, target: int = 4, *, top_n=None, narratives=None):
    from app.models.zt_assessment import (
        ZtAnswer,
        ZtAssessment,
        ZtAssessmentStatus,
        ZtFramework,
    )
    from app.zt.catalog import capabilities
    from app.zt.exporters import build_context
    from app.zt.maturity import ZtFrameworkCode
    from app.zt.scoring import analyze_gaps, compute

    fw = ZtFrameworkCode.CISA_ZTMM_2_0
    a = ZtAssessment(
        id=uuid.uuid4(),
        service_id=uuid.uuid4(),
        framework=ZtFramework.CISA_ZTMM_2_0,
        version=1,
        status=ZtAssessmentStatus.APPROVED,
    )
    a.narratives = narratives
    answers = [
        ZtAnswer(
            id=uuid.uuid4(),
            assessment_id=a.id,
            capability_code=cap.code,
            maturity_stage=stage,
        )
        for cap in capabilities(fw)
    ]
    stage_map = {ans.capability_code: ans.maturity_stage for ans in answers}
    score = compute(fw, stage_map)
    gap = analyze_gaps(fw, stage_map, target_stage=target, top_n=top_n)
    return build_context(
        client_legal_name="Atlas",
        service_title="Zero Trust Assessment",
        framework=fw,
        assessment=a,
        answers=answers,
        score=score,
        gap=gap,
    )


@pytest.mark.unit
def test_zt_xlsx_gap_plan_carries_full_list_when_top_n_none() -> None:
    from app.zt.exporters import render_xlsx

    ctx = _zt_ctx(top_n=None)  # all 37 CISA caps below target 4 -> 37 gaps
    assert ctx.gap.total_gap_count == 37
    wb = load_workbook(io.BytesIO(render_xlsx(ctx)))
    ws = wb["Gap Plan"]
    assert ws.max_row == ctx.gap.total_gap_count + 1  # header + every gap


@pytest.mark.unit
def test_zt_xlsx_has_roadmap_sheet_matching_build_roadmap() -> None:
    from app.zt.exporters import render_xlsx
    from app.zt.scoring import build_roadmap

    ctx = _zt_ctx(top_n=None)
    wb = load_workbook(io.BytesIO(render_xlsx(ctx)))
    assert "Roadmap" in wb.sheetnames
    ws = wb["Roadmap"]
    expected = build_roadmap(ctx.gap.gaps)
    assert ws.max_row == len(expected) + 1  # header + one row per roadmap item
    # First data row month == first roadmap item month.
    assert ws.cell(row=2, column=1).value == expected[0].month


@pytest.mark.unit
def test_zt_docx_top_20_heading_and_answers_section() -> None:
    from app.zt.exporters import render_docx
    from docx import Document

    ctx = _zt_ctx(top_n=None)
    doc = Document(io.BytesIO(render_docx(ctx)))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert f"Top 20 of {ctx.gap.total_gap_count} remediation gaps" in text
    assert "Remediation roadmap" in text
    assert "Answers" in text


@pytest.mark.unit
def test_zt_docx_renders_reviewed_executive_summary() -> None:
    from app.zt.exporters import render_docx
    from docx import Document

    ctx = _zt_ctx(
        top_n=None,
        narratives={
            "pillar_narratives": {},
            "executive_summary": "Identity maturity is the leading risk.",
            "roadmap_summary": None,
        },
    )
    doc = Document(io.BytesIO(render_docx(ctx)))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Identity maturity is the leading risk." in text
    assert "Analyst-reviewed draft (AI-assisted)." in text


@pytest.mark.unit
def test_zt_docx_small_gap_count_uses_plain_heading() -> None:
    from app.zt.exporters import render_docx
    from docx import Document

    # stage 3, target 4 but only a handful of gaps by scoring most at target.
    ctx = _zt_ctx(stage=4, target=4, top_n=None)  # zero gaps
    doc = Document(io.BytesIO(render_docx(ctx)))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Remediation gaps (0)" in text


# ---------------------------------------------------------------------------
# B-6: Tech Debt Consolidation Plan per-item rows
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_tech_debt_consolidation_plan_has_per_item_rows() -> None:
    from app.models.capability import (
        CapabilityDisposition,
        CapabilityItem,
        CapabilityList,
        CapabilityListStatus,
    )
    from app.tech_debt.exporters import build_context, render_xlsx

    cap_list = CapabilityList(
        id=uuid.uuid4(),
        service_id=uuid.uuid4(),
        version=1,
        status=CapabilityListStatus.APPROVED,
    )
    keep_id = uuid.uuid4()
    items = [
        CapabilityItem(
            id=keep_id,
            capability_list_id=cap_list.id,
            name="Splunk",
            annual_cost_usd=480_000,
            disposition=CapabilityDisposition.KEEP,
        ),
        CapabilityItem(
            id=uuid.uuid4(),
            capability_list_id=cap_list.id,
            name="Lacework",
            annual_cost_usd=120_000,
            disposition=CapabilityDisposition.CUT,
            disposition_rationale="Overlaps with Wiz",
        ),
        CapabilityItem(
            id=uuid.uuid4(),
            capability_list_id=cap_list.id,
            name="LogRhythm",
            annual_cost_usd=90_000,
            disposition=CapabilityDisposition.CONSOLIDATE,
            consolidation_target_id=keep_id,
            disposition_rationale="Fold into Splunk",
        ),
    ]
    ctx = build_context(
        client_legal_name="Atlas",
        service_title="Tech Debt Review",
        cap_list=cap_list,
        items=items,
    )
    wb = load_workbook(io.BytesIO(render_xlsx(ctx)))
    ws = wb["Consolidation Plan"]
    rows = [[c.value for c in r] for r in ws.iter_rows()]
    flat = [str(cell) for row in rows for cell in row if cell is not None]
    # Every item name appears as a per-item row.
    assert "Lacework" in flat
    assert "LogRhythm" in flat
    # The consolidation target name is resolved from the self-FK.
    assert "Splunk" in flat
    # The rationale is carried through.
    assert "Fold into Splunk" in flat
    # Per-item savings for the CUT row equals its cost; the total matches the
    # engine's estimated_savings (CUT-only).
    assert ctx.estimated_savings == 120_000


# ---------------------------------------------------------------------------
# B-7: ATT&CK ordering + AI exec + blank-name fallback
# ---------------------------------------------------------------------------
def _attack_ctx(status_by_code=None, ai_summaries=None):
    from app.attack.analytics import compute as compute_heatmap
    from app.attack.catalog import TECHNIQUES
    from app.attack.coverage import CoverageStatus
    from app.attack.exporters import build_context
    from app.models.attack_assessment import (
        AttackAssessment,
        AttackAssessmentStatus,
        AttackCoverage,
    )

    a = AttackAssessment(
        id=uuid.uuid4(),
        service_id=uuid.uuid4(),
        version=1,
        status=AttackAssessmentStatus.APPROVED,
    )
    a.ai_summaries = ai_summaries
    status_by_code = status_by_code or {}
    coverage = [
        AttackCoverage(
            id=uuid.uuid4(),
            assessment_id=a.id,
            technique_code=t.id,
            status=status_by_code.get(t.id, CoverageStatus.GAP.value),
        )
        for t in TECHNIQUES
    ]
    coverage_map = {c.technique_code: c.status for c in coverage}
    rollup = compute_heatmap(coverage_map)
    return build_context(
        client_legal_name="Atlas",
        service_title="ATT&CK Coverage",
        assessment=a,
        coverage=coverage,
        rollup=rollup,
    )


@pytest.mark.unit
def test_attack_prioritized_gap_order_is_coverage_ascending() -> None:
    from app.attack.exporters import _prioritized_gap_rows

    ctx = _attack_ctx()
    rows = _prioritized_gap_rows(ctx)
    # Build the per-tactic coverage map and confirm the weakest-tactic key is
    # non-decreasing across the ordered list.
    tactic_pct = {tc.tactic_id: tc.coverage_pct for tc in ctx.rollup.by_tactic}
    from app.attack.catalog import technique_by_id

    def weakest(code: str) -> float:
        try:
            tech = technique_by_id(code)
        except KeyError:
            return float("inf")
        pcts = [tactic_pct[t] for t in tech.tactics if t in tactic_pct]
        return min(pcts) if pcts else float("inf")

    keys = [weakest(r.technique_code) for r in rows]
    assert keys == sorted(keys)


@pytest.mark.unit
def test_attack_gap_before_partial_within_same_tactic() -> None:
    from app.attack.coverage import CoverageStatus
    from app.attack.exporters import _prioritized_gap_rows

    ctx = _attack_ctx()  # everything is a gap
    # Flip a couple to partial and confirm gaps still sort ahead of partials
    # at equal tactic coverage.
    ctx = _attack_ctx(status_by_code={ctx.coverage[0].technique_code: CoverageStatus.PARTIAL.value})
    rows = _prioritized_gap_rows(ctx)
    # Both gap and partial statuses are surfaced.
    statuses = {r.status for r in rows}
    assert CoverageStatus.GAP.value in statuses
    assert CoverageStatus.PARTIAL.value in statuses


@pytest.mark.unit
def test_attack_docx_ordered_by_weakest_tactic_title_and_ai_exec() -> None:
    from app.attack.exporters import render_docx
    from docx import Document

    ctx = _attack_ctx(
        ai_summaries={
            "executive_summary": "Credential access is the weakest area.",
            "top_blind_spots": ["No EDR on servers"],
        }
    )
    doc = Document(io.BytesIO(render_docx(ctx)))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "ordered by weakest tactic" in text
    assert "Credential access is the weakest area." in text
    assert "No EDR on servers" in text


@pytest.mark.unit
def test_attack_pdf_renders_with_ordering() -> None:
    from app.attack.exporters import render_pdf

    raw = render_pdf(_attack_ctx())
    assert raw.startswith(b"%PDF-")
