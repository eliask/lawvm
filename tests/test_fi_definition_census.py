"""Tests for Finland definition-entry differential census wiring."""

from __future__ import annotations

from lawvm.finland.legal_surface.definition_census import (
    DEFINITION_FAMILY,
    format_definition_census_report,
)
from lawvm.finland.legal_surface.family_census import FamilyCensusResult


def test_definition_family_id() -> None:
    assert DEFINITION_FAMILY == "definition_entry"


def test_format_definition_census_report_renders_partition() -> None:
    result = FamilyCensusResult(
        family=DEFINITION_FAMILY,
        statutes_scanned=1,
        in_scope_units=2,
        buckets={"match": 2, "superset": 0, "miss": 0, "decline": 0},
        totality_violations=0,
        miss_shape_counts={},
    )
    report = format_definition_census_report(result)
    assert "definition" in report.lower()
    assert "partition sum" in report
    assert result.is_partition()


def test_definition_census_json_payload_shape() -> None:
    from lawvm.tools.fi_definition_census import _result_to_json

    payload = _result_to_json(
        FamilyCensusResult(
            family=DEFINITION_FAMILY,
            statutes_scanned=1,
            in_scope_units=0,
            buckets={"match": 0, "superset": 0, "miss": 0, "decline": 0},
            totality_violations=0,
            miss_shape_counts={},
        )
    )
    assert payload["catalog_kind"] == "finland_definition_census"
    assert payload["family"] == DEFINITION_FAMILY
