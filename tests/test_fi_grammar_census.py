"""Tests for unified Finland construction-grammar differential census."""

from __future__ import annotations

import pytest

from lawvm.finland.legal_surface.family_census import FamilyCensusResult
from lawvm.finland.legal_surface.grammar_census import (
    GRAMMAR_CENSUS_FAMILIES,
    format_grammar_census_report,
    run_grammar_census,
)
from lawvm.finland.legal_surface.sentence_census import SentenceCensusResult


def test_grammar_census_family_catalog() -> None:
    assert GRAMMAR_CENSUS_FAMILIES == (
        "scope_carrier",
        "temporal_applicability",
        "citation_sentence",
        "definition_entry",
    )


def test_run_grammar_census_rejects_unknown_family() -> None:
    with pytest.raises(ValueError, match="unknown grammar census families"):
        run_grammar_census(families=("not_a_family",), limit=0)


def test_format_grammar_census_report_renders_families(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_scope(**_kwargs: object) -> FamilyCensusResult:
        return FamilyCensusResult(
            family="scope_carrier",
            statutes_scanned=2,
            in_scope_units=3,
            buckets={"match": 3, "superset": 0, "miss": 0, "decline": 0},
            totality_violations=0,
            miss_shape_counts={},
        )

    def _fake_sentence(**_kwargs: object) -> SentenceCensusResult:
        return SentenceCensusResult(
            statutes_scanned=2,
            segments_total=1,
            in_scope_segments=1,
            buckets={"match": 1, "superset": 0, "miss": 0, "decline": 0},
            totality_violations=0,
            miss_shape_counts={},
        )

    monkeypatch.setattr(
        "lawvm.finland.legal_surface.grammar_census.run_scope_carrier_census",
        _fake_scope,
    )
    monkeypatch.setattr(
        "lawvm.finland.legal_surface.grammar_census.run_sentence_census",
        _fake_sentence,
    )

    result = run_grammar_census(families=("scope_carrier", "citation_sentence"))
    report = format_grammar_census_report(result)
    assert "scope_carrier" in report
    assert "citation_sentence" in report
    assert result.all_partitions_ok()
    assert result.total_in_scope_units == 4


def test_grammar_census_json_payload_shape() -> None:
    from lawvm.finland.legal_surface.grammar_census import GrammarCensusResult, GrammarFamilySummary
    from lawvm.tools.fi_grammar_census import _result_to_json

    payload = _result_to_json(
        GrammarCensusResult(
            statutes_scanned=1,
            families=(
                GrammarFamilySummary(
                    family_id="scope_carrier",
                    statutes_scanned=1,
                    in_scope_units=2,
                    buckets={"match": 2, "superset": 0, "miss": 0, "decline": 0},
                    totality_violations=0,
                    miss_shape_counts={},
                    partition_ok=True,
                    distance_from_miss_zero=0,
                ),
            ),
        )
    )
    assert payload["catalog_kind"] == "finland_grammar_census"
    assert payload["all_partitions_ok"] is True
    assert payload["families"][0]["family_id"] == "scope_carrier"
