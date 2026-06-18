"""Tests for Finland temporal/applicability differential census wiring."""

from __future__ import annotations

from lawvm.finland.legal_surface.family_census import CENSUS_BUCKETS, classify
from lawvm.finland.legal_surface.temporal_census import (
    TEMPORAL_FAMILY,
    format_temporal_census_report,
)


def test_temporal_family_id() -> None:
    assert TEMPORAL_FAMILY == "temporal_applicability"


def test_family_census_buckets_are_closed_set() -> None:
    assert set(CENSUS_BUCKETS) == {"match", "superset", "miss", "decline"}


def test_format_temporal_census_report_renders_partition() -> None:
    from lawvm.finland.legal_surface.family_census import FamilyCensusResult

    result = FamilyCensusResult(
        family=TEMPORAL_FAMILY,
        statutes_scanned=1,
        in_scope_units=2,
        buckets={"match": 2, "superset": 0, "miss": 0, "decline": 0},
        totality_violations=0,
        miss_shape_counts={},
    )
    report = format_temporal_census_report(result)
    assert "temporal" in report.lower()
    assert "partition sum" in report
    assert result.is_partition()


def test_temporal_census_json_payload_shape() -> None:
    from lawvm.finland.legal_surface.family_census import FamilyCensusResult
    from lawvm.tools.fi_temporal_census import _result_to_json

    result = FamilyCensusResult(
        family=TEMPORAL_FAMILY,
        statutes_scanned=3,
        in_scope_units=1,
        buckets={"match": 1, "superset": 0, "miss": 0, "decline": 0},
        totality_violations=0,
        miss_shape_counts={},
    )
    payload = _result_to_json(result)
    assert payload["catalog_kind"] == "finland_temporal_census"
    assert payload["family"] == TEMPORAL_FAMILY
    assert payload["is_partition"] is True


def test_classify_match_on_identical_key_sets() -> None:
    keys = {"commencement:2016-01-01"}
    assert classify(keys, keys, declined=False) == "match"
