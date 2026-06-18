"""Tests for scope-carrier family census."""
from __future__ import annotations

from lawvm.finland.legal_surface.family_census import CENSUS_BUCKETS, classify
from lawvm.finland.legal_surface.scope_carrier_census import (
    SCOPE_CARRIER_FAMILY,
    _scope_carrier_oracle_keys,
    _scope_carrier_projection_keys,
    _scope_carrier_segment_selector,
)


def test_scope_carrier_classify_match_when_keys_agree() -> None:
    bucket = classify({"scope:1"}, {"scope:1"}, declined=False)
    assert bucket == "match"


def test_scope_carrier_classify_decline_bucket() -> None:
    assert classify(set(), {"scope:1"}, declined=True) == "decline"


def test_scope_carrier_segment_selector_finds_luvun_cue() -> None:
    units = list(
        _scope_carrier_segment_selector(
            "1991/3",
            "Johdanto\nmuutetaan 2 luvun 5 §",
        )
    )
    assert len(units) == 1
    assert units[0].parser_lane == "scope_carrier_grammar"
    assert "luvun" in units[0].declared_marker.lower()


def test_scope_carrier_projection_and_oracle_are_callable() -> None:
    from lawvm.finland.legal_surface.family_census import CensusUnit

    unit = CensusUnit(
        text="muutetaan 2 luvun 5 §",
        parser_lane="scope_carrier_grammar",
        declared_marker="2 luvun",
    )
    assert isinstance(_scope_carrier_projection_keys(unit, "1991/3"), set)
    assert isinstance(_scope_carrier_oracle_keys(unit, None), set)


def test_scope_carrier_family_id_is_stable() -> None:
    assert SCOPE_CARRIER_FAMILY == "scope_carrier"


def test_family_census_buckets_are_closed_set() -> None:
    assert CENSUS_BUCKETS == ("match", "superset", "miss", "decline")


def test_format_scope_carrier_census_report_renders_partition() -> None:
    from lawvm.finland.legal_surface.family_census import FamilyCensusResult
    from lawvm.finland.legal_surface.scope_carrier_census import (
        format_scope_carrier_census_report,
    )

    result = FamilyCensusResult(
        family="scope_carrier",
        statutes_scanned=2,
        in_scope_units=4,
        buckets={"match": 3, "superset": 0, "miss": 1, "decline": 0},
        totality_violations=0,
        miss_shape_counts={"missing_chapter_scope": 1},
    )
    report = format_scope_carrier_census_report(result)
    assert "scope-carrier" in report.lower()
    assert "partition sum" in report
    assert result.is_partition()


def test_scope_carrier_census_json_payload_shape() -> None:
    from lawvm.finland.legal_surface.family_census import FamilyCensusResult
    from lawvm.tools.fi_scope_carrier_census import _result_to_json

    payload = _result_to_json(
        FamilyCensusResult(
            family="scope_carrier",
            statutes_scanned=1,
            in_scope_units=0,
            buckets={"match": 0, "superset": 0, "miss": 0, "decline": 0},
            totality_violations=0,
            miss_shape_counts={},
        )
    )
    assert payload["catalog_kind"] == "finland_scope_carrier_census"
    assert payload["is_partition"] is True
