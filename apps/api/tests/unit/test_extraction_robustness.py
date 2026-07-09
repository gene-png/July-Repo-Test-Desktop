"""Unit tests for Sprint 2 extraction robustness (C-3, C-4, C-5, C-7).

These exercise the pure parsing / coercion / storage-mapping logic directly,
without the HTTP stack, so the matrix in the tasking is covered cheaply.
"""

from __future__ import annotations

import io

import pytest
from app.storage.base import StorageUnavailableError
from app.tech_debt.extract import (
    _coerce_item,
    _count_field,
    _leading_number,
    _money_field,
    _parse_response,
)
from app.tech_debt.parsers import parse_inventory

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx(sheets: list[tuple[str, list[list]]]) -> bytes:
    """Build a workbook from (sheet_title, rows) pairs."""
    from openpyxl import Workbook

    wb = Workbook()
    # Drop the default sheet so the first provided sheet is the workbook's first.
    default = wb.active
    for i, (title, rows) in enumerate(sheets):
        ws = default if i == 0 else wb.create_sheet()
        ws.title = title
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# C-3: multi-sheet, duplicate headers, ragged rows, BOM, ParseReport
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_picks_sheet_with_most_data_rows() -> None:
    data = _xlsx(
        [
            ("Cover", [["Confidential briefing"], []]),
            (
                "Inventory",
                [
                    ["Tool", "Vendor"],
                    ["Wiz", "Wiz Inc"],
                    ["Splunk", "Splunk"],
                    ["CrowdStrike", "CS"],
                ],
            ),
        ]
    )
    rows, report = parse_inventory(data, _XLSX_MIME)
    assert report.sheet_used == "Inventory"
    assert report.rows_parsed == 3
    assert rows[0] == {"Tool": "Wiz", "Vendor": "Wiz Inc"}


@pytest.mark.unit
def test_parse_uniquifies_duplicate_headers() -> None:
    data = _xlsx([("S", [["name", "name", "cost"], ["Wiz", "CNAPP", "100"]])])
    rows, _ = parse_inventory(data, _XLSX_MIME)
    assert rows[0] == {"name": "Wiz", "name_2": "CNAPP", "cost": "100"}


@pytest.mark.unit
def test_parse_keeps_overflow_cells_under_generated_headers() -> None:
    data = _xlsx([("S", [["a", "b"], ["1", "2", "3", "4"]])])
    rows, _ = parse_inventory(data, _XLSX_MIME)
    assert rows[0] == {"a": "1", "b": "2", "col_3": "3", "col_4": "4"}


@pytest.mark.unit
def test_parse_blank_header_cell_becomes_col_n() -> None:
    data = _xlsx([("S", [["a", None, "c"], ["1", "2", "3"]])])
    rows, _ = parse_inventory(data, _XLSX_MIME)
    assert rows[0] == {"a": "1", "col_2": "2", "c": "3"}


@pytest.mark.unit
def test_csv_bom_is_stripped_and_blank_rows_skipped() -> None:
    csv = "﻿Tool,Vendor\r\nWiz,Wiz Inc\r\n\r\nSplunk,Splunk\r\n"
    rows, report = parse_inventory(csv.encode("utf-8"), "text/csv")
    assert list(rows[0].keys())[0] == "Tool"  # BOM not glued to the header
    assert report.rows_parsed == 2
    assert report.rows_skipped == 1
    assert report.sheet_used is None


@pytest.mark.unit
def test_csv_ragged_rows_keep_overflow() -> None:
    csv = "a,b\r\n1,2,3\r\n"
    rows, _ = parse_inventory(csv.encode("utf-8"), "text/csv")
    assert rows[0] == {"a": "1", "b": "2", "col_3": "3"}


@pytest.mark.unit
def test_parse_report_flags_truncation() -> None:
    header = "Tool\n"
    body = "".join(f"Row {i}\n" for i in range(600))
    rows, report = parse_inventory((header + body).encode("utf-8"), "text/csv")
    assert report.truncated is True
    assert report.rows_parsed == 500
    assert len(rows) == 500


# ---------------------------------------------------------------------------
# C-4: tolerant money / license parsing + confidence clamp
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$1,200,000", 1200000.0),
        ("1,200,000", 1200000.0),
        ("120000.50", 120000.5),
        ("500 seats", 500.0),
        (350000, 350000.0),
        ("EUR 120k", None),
        ("1.2M", None),
        ("50%", None),
        ("", None),
        (None, None),
    ],
)
def test_leading_number_matrix(raw, expected) -> None:
    assert _leading_number(raw) == expected


@pytest.mark.unit
def test_money_field_unparseable_returns_note() -> None:
    val, note = _money_field("EUR 120k")
    assert val is None
    assert note == "cost: 'EUR 120k'"


@pytest.mark.unit
def test_count_field_unparseable_returns_note() -> None:
    val, note = _count_field("a bunch")
    assert val is None
    assert note == "licenses: 'a bunch'"


@pytest.mark.unit
def test_coerce_item_pushes_unparseable_values_to_notes() -> None:
    item = _coerce_item(
        {
            "name": "Mystery",
            "annual_cost_usd": "1.2M",
            "license_count": "lots",
            "notes": "seen in export",
        }
    )
    assert item.annual_cost_usd is None
    assert item.license_count is None
    assert "seen in export" in item.notes
    assert "cost: '1.2M'" in item.notes
    assert "licenses: 'lots'" in item.notes


@pytest.mark.unit
def test_coerce_item_clamps_confidence() -> None:
    assert _coerce_item({"name": "x", "confidence_pct": 150}).confidence_pct == 100
    assert _coerce_item({"name": "x", "confidence_pct": -10}).confidence_pct == 0
    assert _coerce_item({"name": "x", "confidence_pct": 80}).confidence_pct == 80


# ---------------------------------------------------------------------------
# C-5: response-shape validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_response_rejects_list_shape() -> None:
    with pytest.raises(ValueError):
        _parse_response('[{"name": "Wiz"}]')


@pytest.mark.unit
def test_parse_response_rejects_wrong_key() -> None:
    with pytest.raises(ValueError):
        _parse_response('{"capabilities": [{"name": "Wiz"}]}')


@pytest.mark.unit
def test_parse_response_rejects_empty_items_for_nonempty_input() -> None:
    with pytest.raises(ValueError):
        _parse_response('{"items": []}', input_row_count=5)


@pytest.mark.unit
def test_parse_response_allows_empty_items_without_input_count() -> None:
    # The registry parser has no row count; the empty-vs-nonempty check happens
    # in extract_capabilities where the count is known.
    assert _parse_response('{"items": []}') == []


@pytest.mark.unit
def test_parse_response_happy_path() -> None:
    items = _parse_response('{"items": [{"name": "Wiz", "annual_cost_usd": "$100"}]}')
    assert len(items) == 1
    assert items[0].name == "Wiz"
    assert items[0].annual_cost_usd == 100.0


# ---------------------------------------------------------------------------
# C-7: S3 error mapping (missing key vs outage)
# ---------------------------------------------------------------------------


class _FakeS3Client:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def get_object(self, **_kwargs):
        raise self._exc


def _s3_with(exc: Exception):
    from app.storage.s3 import S3Storage

    store = S3Storage.__new__(S3Storage)
    store._bucket = "b"  # type: ignore[attr-defined]
    store._client = _FakeS3Client(exc)  # type: ignore[attr-defined]
    return store


@pytest.mark.unit
def test_s3_missing_key_maps_to_filenotfound() -> None:
    from botocore.exceptions import ClientError

    exc = ClientError(
        {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
        "GetObject",
    )
    with pytest.raises(FileNotFoundError):
        _s3_with(exc).get("missing")


@pytest.mark.unit
def test_s3_auth_error_maps_to_storage_unavailable() -> None:
    from botocore.exceptions import ClientError

    exc = ClientError(
        {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 403}},
        "GetObject",
    )
    with pytest.raises(StorageUnavailableError):
        _s3_with(exc).get("k")


@pytest.mark.unit
def test_s3_connection_error_maps_to_storage_unavailable() -> None:
    from botocore.exceptions import EndpointConnectionError

    with pytest.raises(StorageUnavailableError):
        _s3_with(EndpointConnectionError(endpoint_url="http://x")).get("k")
