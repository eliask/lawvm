from __future__ import annotations

from argparse import Namespace
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from lawvm.uk_legislation import effects as uk_legislation_effects
from lawvm.tools import uk_candidates
from lawvm.tools.uk_candidates import (
    _candidate_root_hits,
    _collect_malformed_residual_roots,
    _collect_residual_root_sides,
    _collect_residual_roots,
    _eid_branch_root,
    _effect_overlaps_residual,
    _budget_aware_frontier_status,
    _residual_candidate_inventory,
    _filtered_frontier,
    _frontier_status,
    _effective_comparison_class,
    _effective_core_benchmark,
    _format_candidate_source_status,
    _format_saved_bench_rejection_rules,
    _include_candidate_row,
    _matches_filters,
    _matching_frontier,
    _primary_frontier_score,
    _print_uk_candidates_text_summary,
    _replay_applicable_effects_with_budget,
    _summarize_effect_inventory,
    _triage_rule_id,
    _uk_candidate_row_jsonable,
    _uk_candidates_report_jsonable,
    _uk_candidates_filters_jsonable,
    _uk_replay_regime_kwargs_from_bench_row,
)


def test_primary_frontier_score_prefers_replay_commencement() -> None:
    row = SimpleNamespace(
        replay_commencement_score=0.81,
        replay_score=0.72,
        commencement_score=0.66,
        score=0.55,
        n_commenced_eids=4,
    )

    assert _primary_frontier_score(row) == 0.81


def test_format_candidate_source_status_preserves_saved_bench_source_state() -> None:
    row = SimpleNamespace(
        enacted_source_status="available",
        oracle_source_status="too_small",
        enacted_source_size=456,
        oracle_source_size=7,
        enacted_source_sha256="enacted-sha",
        oracle_source_sha256="oracle-sha",
        enacted_source_url="https://example.test/ukpga/2000/1/enacted/data.xml",
        oracle_source_url="https://example.test/ukpga/2000/1/data.xml",
    )

    assert (
        _format_candidate_source_status(row)
        == "enacted=available (456 bytes) oracle=too_small (7 bytes) "
        "enacted_url=https://example.test/ukpga/2000/1/enacted/data.xml "
        "oracle_url=https://example.test/ukpga/2000/1/data.xml "
        "enacted_sha256=enacted-sha oracle_sha256=oracle-sha"
    )


def test_format_saved_bench_rejection_rules_preserves_all_saved_rule_lanes() -> None:
    row = SimpleNamespace(
        source_parse_rejection_rule_counts={"uk_oracle_xml_parse_rejected": 1},
        source_parse_observation_rule_counts={"uk_oracle_xml_parse_rejected": 1},
        bench_exception_rule_counts={"uk_bench_unclassified_exception": 1},
        effect_source_pathology_counts={"missing_extracted_source": 4},
        manual_compile_status_counts={"manual_compile_candidate": 2},
        manual_compile_rule_counts={"uk_manual_frontier_repeal_table_candidate": 2},
        source_acquisition_rejection_rule_counts={"uk_affecting_act_xml_missing_rejected": 2},
        effect_feed_rejection_rule_counts={"uk_effect_feed_xml_parse_rejected": 1},
        effect_feed_observation_rule_counts={"uk_effect_feed_pages_absent_recorded": 2},
        uk_authority_rejection_rule_counts={"uk_authority_source_text_only_missing": 2},
        lowering_rejection_rule_counts={"uk_effect_payload_missing": 3},
        blocking_lowering_rejection_rule_counts={"uk_effect_payload_missing": 1},
    )

    assert _format_saved_bench_rejection_rules(row) == (
        "rejection_rules: "
        "feed_parse=uk_effect_feed_xml_parse_rejected=1 "
        "feed_observation=uk_effect_feed_pages_absent_recorded=2 "
        "source_parse=uk_oracle_xml_parse_rejected=1 "
        "source_parse_observation=uk_oracle_xml_parse_rejected=1 "
        "bench_exception=uk_bench_unclassified_exception=1 "
        "effect_source_pathology=missing_extracted_source=4 "
        "manual_compile_status=manual_compile_candidate=2 "
        "manual_compile_rule=uk_manual_frontier_repeal_table_candidate=2 "
        "source_acquisition=uk_affecting_act_xml_missing_rejected=2 "
        "bench_authority=uk_authority_source_text_only_missing=2 "
        "lowering_observation=uk_effect_payload_missing=3 "
        "lowering=uk_effect_payload_missing=3 "
        "blocking_lowering=uk_effect_payload_missing=1"
    )
    assert _format_saved_bench_rejection_rules(SimpleNamespace()) == ""


def test_uk_candidates_recovers_replay_regime_from_bench_row() -> None:
    row = SimpleNamespace(
        uk_metadata_backfill_enabled=False,
        uk_oracle_alignment_enabled="0",
        uk_metadata_only_effects_enabled="0",
        uk_applicability_mode="effective_date_only",
        uk_authority_mode="source_text_only",
    )

    assert _uk_replay_regime_kwargs_from_bench_row(row) == {
        "allow_metadata_backfill": False,
        "allow_oracle_alignment": False,
        "allow_metadata_only_effects": False,
        "applicability_mode": "effective_date_only",
        "authority_mode": "source_text_only",
    }

    legacy_row = SimpleNamespace()
    assert _uk_replay_regime_kwargs_from_bench_row(legacy_row) == {
        "allow_metadata_backfill": True,
        "allow_oracle_alignment": True,
        "allow_metadata_only_effects": True,
        "applicability_mode": "effective_date_plus_feed_applied",
        "authority_mode": "current_mixed",
    }


def test_primary_frontier_score_falls_back_to_replay_then_commencement_then_raw() -> None:
    row = SimpleNamespace(
        replay_commencement_score=-1.0,
        replay_score=0.72,
        commencement_score=0.66,
        score=0.55,
        n_commenced_eids=0,
    )
    assert _primary_frontier_score(row) == 0.72

    row = SimpleNamespace(
        replay_commencement_score=-1.0,
        replay_score=-1.0,
        commencement_score=0.66,
        score=0.55,
        n_commenced_eids=0,
    )
    assert _primary_frontier_score(row) == 0.66

    row = SimpleNamespace(
        replay_commencement_score=-1.0,
        replay_score=-1.0,
        commencement_score=-1.0,
        score=0.55,
        n_commenced_eids=0,
    )
    assert _primary_frontier_score(row) == 0.55


def test_primary_frontier_score_auto_ignores_zero_denominator_commencement_scores() -> None:
    row = SimpleNamespace(
        replay_commencement_score=0.0,
        replay_score=0.9897,
        commencement_score=0.0,
        score=0.4894,
        n_commenced_eids=0,
    )

    assert _primary_frontier_score(row) == 0.9897


def test_primary_frontier_score_replay_mode_prefers_replay_score() -> None:
    row = SimpleNamespace(
        replay_commencement_score=0.0015,
        replay_score=0.9151,
        commencement_score=0.0015,
        score=0.4846,
        n_commenced_eids=4,
    )

    assert _primary_frontier_score(row, score_mode="replay") == 0.9151
    assert _primary_frontier_score(row, score_mode="replay_commencement") == 0.0015


def test_effective_comparison_class_recomputes_for_stale_saved_rows() -> None:
    row = SimpleNamespace(
        comparison_class="",
        n_enacted_eids=78,
        n_oracle_eids=0,
        n_effects=9,
        score=0.0,
    )

    assert _effective_comparison_class(row) == "no_oracle_eids"
    assert _effective_core_benchmark(row) is False


def test_effective_comparison_class_respects_explicit_saved_class() -> None:
    row = SimpleNamespace(
        comparison_class="unapplied_oracle_expansion",
        n_enacted_eids=10,
        n_oracle_eids=100,
        n_effects=5,
        score=0.1,
    )

    assert _effective_comparison_class(row) == "unapplied_oracle_expansion"
    assert _effective_core_benchmark(row) is True


def test_matches_filters_applies_year_and_type_bounds() -> None:
    row = SimpleNamespace(year=2000, act_type="ukpga")

    assert _matches_filters(row, min_year=None, max_year=None, types=None) is True
    assert _matches_filters(row, min_year=1990, max_year=2005, types=None) is True
    assert _matches_filters(row, min_year=2001, max_year=None, types=None) is False
    assert _matches_filters(row, min_year=None, max_year=1999, types=None) is False
    assert _matches_filters(row, min_year=None, max_year=None, types={"ukpga"}) is True
    assert _matches_filters(row, min_year=None, max_year=None, types={"asp"}) is False


def test_filtered_frontier_keeps_only_core_filtered_rows_sorted_by_frontier_score() -> None:
    rows = [
        SimpleNamespace(
            statute_id="ukpga/2000/41",
            status="OK",
            year=2000,
            act_type="ukpga",
            replay_commencement_score=-1.0,
            replay_score=0.851,
            commencement_score=-1.0,
            score=0.851,
            n_commenced_eids=0,
            comparison_class="commensurable",
            n_enacted_eids=100,
            n_oracle_eids=100,
            n_effects=10,
        ),
        SimpleNamespace(
            statute_id="ukpga/2000/22",
            status="OK",
            year=2000,
            act_type="ukpga",
            replay_commencement_score=-1.0,
            replay_score=0.910,
            commencement_score=-1.0,
            score=0.910,
            n_commenced_eids=0,
            comparison_class="commensurable",
            n_enacted_eids=100,
            n_oracle_eids=100,
            n_effects=10,
        ),
        SimpleNamespace(
            statute_id="ukpga/2000/3",
            status="OK",
            year=2000,
            act_type="ukpga",
            replay_commencement_score=-1.0,
            replay_score=0.100,
            commencement_score=-1.0,
            score=0.100,
            n_commenced_eids=0,
            comparison_class="",
            n_enacted_eids=50,
            n_oracle_eids=0,
            n_effects=2,
        ),
        SimpleNamespace(
            statute_id="asp/2003/1",
            status="OK",
            year=2003,
            act_type="asp",
            replay_commencement_score=-1.0,
            replay_score=0.400,
            commencement_score=-1.0,
            score=0.400,
            n_commenced_eids=0,
            comparison_class="commensurable",
            n_enacted_eids=50,
            n_oracle_eids=50,
            n_effects=2,
        ),
    ]

    frontier = _filtered_frontier(
        rows,
        top=5,
        score_mode="auto",
        min_year=2000,
        max_year=None,
        types={"ukpga"},
    )

    assert [row.statute_id for row in frontier] == [
        "ukpga/2000/41",
        "ukpga/2000/22",
    ]
    matching = _matching_frontier(
        rows,
        score_mode="auto",
        min_year=2000,
        max_year=None,
        types={"ukpga"},
    )
    assert [row.statute_id for row in matching] == [
        "ukpga/2000/41",
        "ukpga/2000/22",
    ]


def test_effect_overlaps_residual_matches_exact_and_descendant_eids() -> None:
    assert _effect_overlaps_residual(
        ("section-3",),
        only_in_replayed=set(),
        only_in_oracle={"section-3-1", "section-3-1-a"},
    ) is True
    assert _effect_overlaps_residual(
        ("section-4-9",),
        only_in_replayed={"section-4-9"},
        only_in_oracle=set(),
    ) is True
    assert _effect_overlaps_residual(
        ("section-6-1",),
        only_in_replayed=set(),
        only_in_oracle={"section-3-1"},
    ) is False
    assert _effect_overlaps_residual(
        ("section-3-1",),
        only_in_replayed=set(),
        only_in_oracle={"section-3"},
    ) is True
    assert _effect_overlaps_residual(
        ("section-3-1",),
        only_in_replayed=set(),
        only_in_oracle={"section-30"},
    ) is False


def test_eid_branch_root_extracts_body_and_schedule_branch_roots() -> None:
    assert _eid_branch_root("section-3-2-a") == "section-3"
    assert _eid_branch_root("article-5-1") == "article-5"
    assert _eid_branch_root("schedule-3-part-2") == "schedule-3"
    assert _eid_branch_root("schedule-paragraph-6") == "schedule"
    assert _eid_branch_root("crossheading-removal-of-infringing-articles") == (
        "crossheading-removal-of-infringing-articles"
    )


def test_collect_residual_roots_and_candidate_hits_track_branch_level_overlap() -> None:
    residual_roots = _collect_residual_roots(
        only_in_replayed={"section-3-2-a", "section-4-1"},
        only_in_oracle={"crossheading-removal-of-infringing-articles"},
    )

    assert residual_roots == {
        "section-3",
        "section-4",
        "crossheading-removal-of-infringing-articles",
    }
    assert _candidate_root_hits(
        ("section-3-1", "section-6-1"),
        residual_roots=residual_roots,
    ) == {"section-3"}
    assert _collect_residual_root_sides(
        only_in_replayed={"section-3-2-a"},
        only_in_oracle={"section-4-1", "crossheading-removal-of-infringing-articles"},
    ) == (
        {"section-3"},
        {"section-4", "crossheading-removal-of-infringing-articles"},
    )
    assert _collect_malformed_residual_roots(
        only_in_replayed={"section-1.", "section-2"},
        only_in_oracle={"section-3,", "section-4-1"},
    ) == {"section-1.", "section-3,"}


def test_frontier_status_prioritizes_residual_counts() -> None:
    assert _frontier_status(candidate_count=2, residual_candidate_count=1) == "real residual frontier"
    assert _frontier_status(candidate_count=0, residual_candidate_count=0) == "classification-heavy"
    assert _frontier_status(candidate_count=3, residual_candidate_count=0) == "candidate-clean after residual overlap"
    assert _frontier_status(
        candidate_count=4,
        residual_candidate_count=0,
        residual_root_count=2,
        defeated_residual_root_count=2,
    ) == "residual branches defeated by no candidate overlap"
    assert _frontier_status(
        candidate_count=4,
        residual_candidate_count=0,
        residual_root_count=1,
        malformed_residual_root_count=1,
    ) == "malformed residual roots deferred"
    assert _frontier_status(
        candidate_count=4,
        residual_candidate_count=0,
        residual_root_count=2,
        defeated_residual_root_count=1,
        malformed_residual_root_count=1,
    ) == "residual branches include malformed roots"


def test_budget_aware_frontier_status_preserves_partial_effect_inventory() -> None:
    assert _budget_aware_frontier_status(
        candidate_count=0,
        residual_candidate_count=0,
        effect_inspection_truncated=True,
    ) == "effect inspection budget truncated"
    assert _budget_aware_frontier_status(
        candidate_count=3,
        residual_candidate_count=0,
        effect_inspection_truncated=True,
        residual_root_count=1,
        defeated_residual_root_count=1,
    ) == "effect inspection budget truncated"
    assert _budget_aware_frontier_status(
        candidate_count=3,
        residual_candidate_count=1,
        effect_inspection_truncated=True,
    ) == "real residual frontier"
    assert _triage_rule_id(
        "effect inspection budget truncated"
    ) == "uk_effect_inspection_budget_truncated"


def test_summarize_effect_inventory_counts_candidates_and_classifications() -> None:
    summaries = [
        SimpleNamespace(
            source_pathology="missing_extracted_source",
            compare_shape="",
            candidate=False,
            n_ops=0,
            source_acquisition_rejections=(
                {
                    "rule_id": "uk_affecting_act_xml_cached_recorded",
                    "blocking": False,
                    "strict_disposition": "record",
                },
                {"rule_id": "uk_affecting_act_xml_missing_rejected"},
            ),
            manual_compile_status="source_insufficient",
            manual_compile_rule_id="uk_manual_frontier_missing_payload_source_insufficient",
        ),
        SimpleNamespace(
            source_pathology="",
            compare_shape="collapsed_subtree_oracle_shape",
            candidate=False,
            n_ops=1,
            lowering_rejections=(
                {"rule_id": "uk_effect_payload_missing", "blocking": True},
                {"rule_id": "uk_effect_legacy_unmarked_rejected"},
                {"rule_id": "uk_effect_observation", "strict_disposition": "record"},
            ),
            source_acquisition_rejections=(),
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_heading_facet_candidate",
        ),
        SimpleNamespace(
            source_pathology="",
            compare_shape="",
            candidate=True,
            n_ops=2,
            lowering_rejections=(),
            source_acquisition_rejections=(),
            manual_compile_status="deterministic_frontend_supported",
            manual_compile_rule_id="uk_manual_frontier_deterministic_supported",
        ),
    ]
    summaries[0].lowering_rejections = ()

    inventory = _summarize_effect_inventory(summaries)

    assert inventory["source_counts"] == {"missing_extracted_source": 1}
    assert inventory["compare_counts"] == {"collapsed_subtree_oracle_shape": 1}
    assert inventory["candidate_source_counts"] == {}
    assert inventory["candidate_compare_counts"] == {}
    assert inventory["non_candidate_source_counts"] == {"missing_extracted_source": 1}
    assert inventory["non_candidate_compare_counts"] == {"collapsed_subtree_oracle_shape": 1}
    assert inventory["manual_compile_status_counts"] == {
        "deterministic_frontend_supported": 1,
        "manual_compile_candidate": 1,
        "source_insufficient": 1,
    }
    assert inventory["manual_compile_rule_counts"] == {
        "uk_manual_frontier_deterministic_supported": 1,
        "uk_manual_frontier_heading_facet_candidate": 1,
        "uk_manual_frontier_missing_payload_source_insufficient": 1,
    }
    assert inventory["lowering_observation_rule_counts"] == {
        "uk_effect_legacy_unmarked_rejected": 1,
        "uk_effect_observation": 1,
        "uk_effect_payload_missing": 1,
    }
    assert inventory["lowering_rejection_rule_counts"] == {
        "uk_effect_legacy_unmarked_rejected": 1,
        "uk_effect_payload_missing": 1,
    }
    assert inventory["blocking_lowering_rejection_rule_counts"] == {
        "uk_effect_legacy_unmarked_rejected": 1,
        "uk_effect_payload_missing": 1,
    }
    assert inventory["source_acquisition_observation_rule_counts"] == {
        "uk_affecting_act_xml_cached_recorded": 1,
        "uk_affecting_act_xml_missing_rejected": 1,
    }
    assert inventory["source_acquisition_rejection_rule_counts"] == {
        "uk_affecting_act_xml_missing_rejected": 1,
    }
    assert inventory["rows_with_source_acquisition_observations"] == 1
    assert inventory["rows_with_source_acquisition_rejections"] == 1
    assert inventory["rows_with_lowering_observations"] == 1
    assert inventory["rows_with_blocking_lowering_rejections"] == 1
    assert inventory["inspected_effect_count"] == 3
    assert inventory["candidate_count"] == 1
    assert inventory["candidate_ops"] == 2
    assert inventory["candidate_summaries"] == [summaries[2]]


def test_summarize_effect_inventory_counts_claim_template_statuses(monkeypatch) -> None:
    from lawvm.tools import uk_effects

    rows = (object(), object(), object())
    statuses = {
        rows[0]: "available",
        rows[1]: "not_available",
        rows[2]: "__not_actionable__",
    }

    def fake_actionable_claim_template_status(*, statute_id: str, row: object) -> str:
        assert statute_id == "ukpga/2000/1"
        return statuses[row]

    monkeypatch.setattr(
        uk_effects,
        "_actionable_claim_template_status",
        fake_actionable_claim_template_status,
    )

    inventory = _summarize_effect_inventory(
        (),
        effect_report_rows=rows,
        statute_id="ukpga/2000/1",
    )

    assert inventory["suggested_claim_template_status_counts"] == {
        "available": 1,
        "not_available": 1,
    }


def test_replay_applicable_effects_with_budget_preserves_truncation_evidence() -> None:
    seen_modes: list[str] = []

    def matches_mode(expected_mode: str):
        def _matches(*, applicability_mode: str) -> bool:
            seen_modes.append(applicability_mode)
            return applicability_mode == expected_mode

        return _matches

    effects = [
        SimpleNamespace(
            applied=True,
            metadata_only=False,
            effect_id="eff-1",
            is_applicable_for_replay=matches_mode("effective_date_only"),
        ),
        SimpleNamespace(
            applied=False,
            metadata_only=True,
            effect_id="eff-2",
            is_applicable_for_replay=matches_mode("effective_date_only"),
        ),
        SimpleNamespace(
            applied=False,
            metadata_only=False,
            effect_id="eff-3",
            is_applicable_for_replay=matches_mode("never"),
        ),
        SimpleNamespace(
            applied=True,
            metadata_only=False,
            effect_id="eff-4",
            is_applicable_for_replay=matches_mode("effective_date_only"),
        ),
    ]

    assert _replay_applicable_effects_with_budget(
        effects,
        effect_budget=None,
        applicability_mode="effective_date_only",
    ) == (
        [effects[0], effects[1], effects[3]],
        3,
        2,
        False,
    )
    assert _replay_applicable_effects_with_budget(
        effects,
        effect_budget=1,
        applicability_mode="effective_date_only",
    ) == (
        [effects[0]],
        3,
        2,
        True,
    )
    assert _replay_applicable_effects_with_budget(
        effects,
        effect_budget=None,
        applicability_mode="effective_date_only",
        allow_metadata_only_effects=False,
    ) == (
        [effects[0], effects[3]],
        2,
        2,
        False,
    )
    assert seen_modes == ["effective_date_only"] * 11

    selection_observations: list[dict[str, Any]] = []
    assert _replay_applicable_effects_with_budget(
        effects,
        effect_budget=1,
        applicability_mode="effective_date_only",
        allow_metadata_only_effects=False,
        selection_observations_out=selection_observations,
    ) == (
        [effects[0]],
        2,
        2,
        True,
    )
    assert [row["rule_id"] for row in selection_observations] == [
        "uk_effect_metadata_only_selection_rejected",
        "uk_effect_replay_applicability_selection_rejected",
        "uk_effect_inspection_budget_excluded",
    ]
    assert selection_observations[0]["effect_id"] == "eff-2"
    assert selection_observations[1]["effect_id"] == "eff-3"
    assert selection_observations[2]["skipped_effect_count"] == 1
    assert selection_observations[2]["skipped_effect_ids_sample"] == ["eff-4"]
    assert all(row["blocking"] is False for row in selection_observations)


def test_include_candidate_row_keeps_budget_skips_visible_under_residual_only() -> None:
    assert _include_candidate_row(
        residual_only=True,
        residual_candidate_count=0,
        residual_analysis_skipped=False,
        effect_inspection_truncated=False,
    ) is False
    assert _include_candidate_row(
        residual_only=True,
        residual_candidate_count=0,
        residual_analysis_skipped=True,
        effect_inspection_truncated=False,
    ) is True
    assert _include_candidate_row(
        residual_only=True,
        residual_candidate_count=0,
        residual_analysis_skipped=False,
        residual_analysis_unavailable=True,
        effect_inspection_truncated=False,
    ) is True
    assert _include_candidate_row(
        residual_only=True,
        residual_candidate_count=0,
        residual_analysis_skipped=False,
        effect_inspection_truncated=True,
    ) is True
    assert _include_candidate_row(
        residual_only=False,
        residual_candidate_count=0,
        residual_analysis_skipped=False,
        effect_inspection_truncated=False,
    ) is True


def test_residual_candidate_inventory_counts_only_overlapping_candidate_rows() -> None:
    candidate_summaries = [
        SimpleNamespace(
            resolver_eids=("section-3",),
            n_ops=2,
            effect_id="eff-3",
            effect_type="inserted",
            affected_provisions="s. 3",
            affecting_act_id="ukpga/2025/1",
            affecting_provisions="s. 10",
            effective_date="2025-01-01",
            source_pathology="",
            compare_shape="commensurable",
            replay_applicable=True,
            structural_for_replay=True,
        ),
        SimpleNamespace(resolver_eids=("section-6",), n_ops=1),
    ]

    inventory = _residual_candidate_inventory(
        candidate_summaries,
        residual_roots={"section-3", "section-4"},
        only_in_replayed={"section-3-1"},
        only_in_oracle={"section-4-1"},
    )

    assert inventory["residual_candidate_count"] == 1
    assert inventory["residual_candidate_ops"] == 2
    assert inventory["residual_root_hits"] == {"section-3"}
    assert inventory["residual_candidate_samples"] == [
        {
            "effect_id": "eff-3",
            "effect_type": "inserted",
            "affected_provisions": "s. 3",
            "affecting_act_id": "ukpga/2025/1",
            "affecting_provisions": "s. 10",
            "effective_date": "2025-01-01",
            "resolver_eids": ["section-3"],
            "overlapping_residual_roots": ["section-3"],
            "source_pathology": "",
            "compare_shape": "commensurable",
            "compiled_op_count": 2,
            "replay_applicable": True,
            "structural_for_replay": True,
        }
    ]
    assert inventory["residual_candidate_samples_omitted"] == 0


def test_residual_candidate_inventory_does_not_back_sibling_residual_branches() -> None:
    candidate_summaries = [
        SimpleNamespace(resolver_eids=("section-3-2",), n_ops=1),
    ]

    inventory = _residual_candidate_inventory(
        candidate_summaries,
        residual_roots={"section-3"},
        only_in_replayed=set(),
        only_in_oracle={"section-3-1"},
    )

    assert inventory["residual_candidate_count"] == 0
    assert inventory["residual_candidate_ops"] == 0
    assert inventory["residual_root_hits"] == set()
    assert inventory["residual_candidate_samples"] == []
    assert inventory["residual_candidate_samples_omitted"] == 0


def test_residual_candidate_inventory_only_hits_the_overlapping_resolver_eids() -> None:
    candidate_summaries = [
        SimpleNamespace(resolver_eids=("section-3-2", "section-4"), n_ops=2),
    ]

    inventory = _residual_candidate_inventory(
        candidate_summaries,
        residual_roots={"section-3", "section-4"},
        only_in_replayed=set(),
        only_in_oracle={"section-3-1", "section-4"},
    )

    assert inventory["residual_candidate_count"] == 1
    assert inventory["residual_candidate_ops"] == 2
    assert inventory["residual_root_hits"] == {"section-4"}
    assert inventory["residual_candidate_samples"][0]["overlapping_residual_roots"] == ["section-4"]


def test_candidate_row_jsonable_records_defeated_residual_roots() -> None:
    row = SimpleNamespace(
        statute_id="ukpga/2003/30",
        score=0.6,
        replay_score=0.9,
        commencement_score=-1.0,
        replay_commencement_score=-1.0,
        n_commenced_eids=0,
        n_effects=12,
        n_effect_feed_pages=12,
        n_effect_rows=9,
        comparison_class="commensurable",
        n_enacted_eids=100,
        n_oracle_eids=99,
        enacted_source_status="available",
        oracle_source_status="too_small",
        enacted_source_size=456,
        oracle_source_size=7,
        enacted_source_sha256="enacted-sha",
        oracle_source_sha256="oracle-sha",
        enacted_source_url="https://example.test/ukpga/2003/30/enacted/data.xml",
        oracle_source_url="https://example.test/ukpga/2003/30/data.xml",
        uk_metadata_backfill_enabled=False,
        uk_oracle_alignment_enabled=False,
        uk_metadata_only_effects_enabled=False,
        uk_applicability_mode="effective_date_only",
        uk_authority_mode="source_text_only",
        uk_authority_rejection_count=2,
        uk_authority_rejection_rule_counts={"uk_authority_source_text_only_missing": 2},
    )

    payload = _uk_candidate_row_jsonable(
        row,
        score_mode="auto",
        source_counts={"missing_extracted_source": 2},
        compare_counts={"collapsed_subtree_oracle_shape": 1},
        candidate_source_counts={},
        candidate_compare_counts={},
        non_candidate_source_counts={"missing_extracted_source": 2},
        non_candidate_compare_counts={"collapsed_subtree_oracle_shape": 1},
        manual_compile_status_counts={"manual_compile_candidate": 2},
        manual_compile_rule_counts={"uk_manual_frontier_heading_facet_candidate": 2},
        suggested_claim_template_status_counts={"available": 1, "not_available": 1},
        lowering_rejection_rule_counts={"uk_effect_payload_missing": 2},
        blocking_lowering_rejection_rule_counts={"uk_effect_payload_missing": 1},
        source_acquisition_observation_rule_counts={
            "uk_affecting_act_xml_cached_recorded": 1,
            "uk_affecting_act_xml_missing_rejected": 1,
        },
        rows_with_source_acquisition_observations=2,
        source_acquisition_rejection_rule_counts={"uk_affecting_act_xml_missing_rejected": 1},
        rows_with_source_acquisition_rejections=1,
        rows_with_blocking_lowering_rejections=1,
        inspected_effect_count=6,
        available_replay_applicable_effect_count=11,
        available_applied_effect_count=10,
        effect_inspection_truncated=True,
        residual_analysis_skipped=True,
        residual_analysis_unavailable=True,
        residual_analysis_unavailable_reason="oracle_missing_or_empty",
        residual_analysis_enacted_missing=False,
        residual_analysis_oracle_missing=True,
        candidate_count=4,
        candidate_ops=7,
        residual_candidate_count=0,
        residual_candidate_ops=0,
        residual_roots={"section-3", "section-4"},
        replayed_residual_roots={"section-3"},
        oracle_residual_roots={"section-4"},
        malformed_residual_roots={"section-4."},
        residual_root_hits={"section-3"},
        defeated_residual_roots={"section-4"},
        status="residual branches defeated by no candidate overlap",
        effect_feed_parse_rejections=(
            {
                "rule_id": "uk_effect_feed_xml_parse_rejected",
                "phase": "parse",
                "feed_locator": "https://example.test/feed",
                "blocking": True,
            },
            {
                "rule_id": "uk_effect_feed_pages_absent_recorded",
                "phase": "acquisition",
                "feed_locator": "https://example.test/missing-feed",
                "strict_disposition": "record",
            },
        ),
        effect_selection_observations=(
            {
                "rule_id": "uk_effect_metadata_only_selection_rejected",
                "phase": "candidate_selection",
                "effect_id": "eff-metadata",
                "blocking": False,
            },
            {
                "rule_id": "uk_effect_replay_applicability_selection_rejected",
                "phase": "candidate_selection",
                "effect_id": "eff-unapplied",
                "blocking": False,
            },
        ),
        residual_effect_feed_parse_rejections=(
            {
                "rule_id": "uk_effect_feed_locator_payload_missing_rejected",
                "phase": "parse",
            },
            {
                "rule_id": "uk_effect_feed_pages_absent_recorded",
                "phase": "acquisition",
                "blocking": False,
            },
        ),
        residual_effect_source_pathology_observations=(
            {
                "rule_id": "uk_effect_source_pathology_classified",
                "phase": "lowering",
                "source_pathology": "missing_extracted_source",
                "strict_disposition": "record",
            },
        ),
        residual_source_acquisition_rejections=(
            {
                "rule_id": "uk_affecting_act_xml_missing_rejected",
                "phase": "acquisition",
                "blocking": True,
            },
            {
                "rule_id": "uk_affecting_act_xml_cached_recorded",
                "phase": "acquisition",
                "blocking": False,
                "strict_disposition": "record",
            },
        ),
        residual_lowering_rejections=(
            {
                "rule_id": "uk_effect_payload_missing",
                "phase": "lowering",
            },
        ),
        residual_authority_rejections=(
            {
                "rule_id": "uk_effect_authority_filter_rejected",
                "phase": "lowering",
            },
        ),
        residual_candidate_samples=(
            {
                "effect_id": "eff-1",
                "resolver_eids": ["section-3"],
                "overlapping_residual_roots": ["section-3"],
            },
        ),
        residual_candidate_samples_omitted=2,
    )

    assert payload["statute_id"] == "ukpga/2003/30"
    assert payload["score_mode"] == "auto"
    assert payload["frontier_score"] == 0.9
    assert payload["effect_count"] == 12
    assert payload["effect_row_count"] == 9
    assert payload["effect_feed_page_count"] == 12
    assert payload["enacted_source_status"] == "available"
    assert payload["oracle_source_status"] == "too_small"
    assert payload["enacted_source_size"] == 456
    assert payload["oracle_source_size"] == 7
    assert payload["enacted_source_sha256"] == "enacted-sha"
    assert payload["oracle_source_sha256"] == "oracle-sha"
    assert payload["enacted_source_url"] == "https://example.test/ukpga/2003/30/enacted/data.xml"
    assert payload["oracle_source_url"] == "https://example.test/ukpga/2003/30/data.xml"
    assert payload["core_benchmark"] is True
    assert payload["uk_replay_regime"] == {
        "allow_metadata_backfill": False,
        "allow_oracle_alignment": False,
        "allow_metadata_only_effects": False,
        "applicability_mode": "effective_date_only",
        "authority_mode": "source_text_only",
    }
    assert payload["bench_authority_rejection_count"] == 2
    assert payload["bench_authority_rejection_rule_counts"] == {
        "uk_authority_source_text_only_missing": 2,
    }
    assert payload["inspected_effect_count"] == 6
    assert payload["inspected_replay_applicable_effect_count"] == 6
    assert payload["available_replay_applicable_effect_count"] == 11
    assert payload["available_applied_effect_count"] == 10
    assert payload["effect_inspection_truncated"] is True
    assert payload["residual_analysis_skipped"] is True
    assert payload["residual_analysis_unavailable"] is True
    assert payload["residual_analysis_unavailable_reason"] == "oracle_missing_or_empty"
    assert payload["residual_analysis_enacted_missing"] is False
    assert payload["residual_analysis_oracle_missing"] is True
    assert payload["source_counts"] == {"missing_extracted_source": 2}
    assert payload["compare_counts"] == {"collapsed_subtree_oracle_shape": 1}
    assert payload["candidate_source_counts"] == {}
    assert payload["candidate_compare_counts"] == {}
    assert payload["non_candidate_source_counts"] == {"missing_extracted_source": 2}
    assert payload["non_candidate_compare_counts"] == {"collapsed_subtree_oracle_shape": 1}
    assert payload["manual_compile_status_counts"] == {"manual_compile_candidate": 2}
    assert payload["manual_compile_rule_counts"] == {
        "uk_manual_frontier_heading_facet_candidate": 2,
    }
    assert payload["suggested_claim_template_status_counts"] == {
        "available": 1,
        "not_available": 1,
    }
    assert payload["lowering_observation_rule_counts"] == {"uk_effect_payload_missing": 2}
    assert payload["rows_with_lowering_observations"] == 0
    assert payload["lowering_rejection_rule_counts"] == {"uk_effect_payload_missing": 2}
    assert payload["blocking_lowering_rejection_rule_counts"] == {"uk_effect_payload_missing": 1}
    assert payload["source_acquisition_observation_rule_counts"] == {
        "uk_affecting_act_xml_cached_recorded": 1,
        "uk_affecting_act_xml_missing_rejected": 1,
    }
    assert payload["rows_with_source_acquisition_observations"] == 2
    assert payload["source_acquisition_rejection_rule_counts"] == {
        "uk_affecting_act_xml_missing_rejected": 1,
    }
    assert payload["rows_with_source_acquisition_rejections"] == 1
    assert payload["rows_with_blocking_lowering_rejections"] == 1
    assert payload["effect_feed_parse_rejection_count"] == 1
    assert payload["effect_feed_parse_rejection_rule_counts"] == {
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert payload["effect_feed_observation_count"] == 2
    assert payload["effect_feed_observation_rule_counts"] == {
        "uk_effect_feed_pages_absent_recorded": 1,
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert payload["effect_selection_observation_count"] == 2
    assert payload["effect_selection_observation_rule_counts"] == {
        "uk_effect_metadata_only_selection_rejected": 1,
        "uk_effect_replay_applicability_selection_rejected": 1,
    }
    assert payload["effect_selection_rejection_count"] == 0
    assert payload["effect_selection_rejection_rule_counts"] == {}
    assert payload["effect_selection_rejections"] == []
    assert payload["effect_selection_observations"] == [
        {
            "rule_id": "uk_effect_metadata_only_selection_rejected",
            "phase": "candidate_selection",
            "effect_id": "eff-metadata",
            "blocking": False,
        },
        {
            "rule_id": "uk_effect_replay_applicability_selection_rejected",
            "phase": "candidate_selection",
            "effect_id": "eff-unapplied",
            "blocking": False,
        },
    ]
    assert payload["residual_compile_observation_count"] == 7
    assert payload["residual_compile_observation_rule_counts"] == {
        "uk_affecting_act_xml_cached_recorded": 1,
        "uk_affecting_act_xml_missing_rejected": 1,
        "uk_effect_authority_filter_rejected": 1,
        "uk_effect_feed_locator_payload_missing_rejected": 1,
        "uk_effect_feed_pages_absent_recorded": 1,
        "uk_effect_payload_missing": 1,
        "uk_effect_source_pathology_classified": 1,
    }
    assert payload["residual_compile_rejection_count"] == 4
    assert payload["residual_compile_rejection_rule_counts"] == {
        "uk_affecting_act_xml_missing_rejected": 1,
        "uk_effect_authority_filter_rejected": 1,
        "uk_effect_feed_locator_payload_missing_rejected": 1,
        "uk_effect_payload_missing": 1,
    }
    assert payload["residual_compile_rejections"]["effect_feed_parse"][0]["rule_id"] == (
        "uk_effect_feed_locator_payload_missing_rejected"
    )
    assert len(payload["residual_compile_observations"]["effect_feed_parse"]) == 2
    assert len(payload["residual_compile_observations"]["effect_source_pathology"]) == 1
    assert len(payload["residual_compile_observations"]["source_acquisition"]) == 2
    assert len(payload["residual_compile_rejections"]["effect_feed_parse"]) == 1
    assert payload["residual_compile_rejections"]["effect_source_pathology"] == []
    assert len(payload["residual_compile_rejections"]["source_acquisition"]) == 1
    assert payload["residual_compile_rejections"]["source_acquisition"][0]["rule_id"] == (
        "uk_affecting_act_xml_missing_rejected"
    )
    assert payload["residual_compile_rejections"]["lowering"][0]["rule_id"] == (
        "uk_effect_payload_missing"
    )
    assert payload["residual_compile_rejections"]["authority"][0]["rule_id"] == (
        "uk_effect_authority_filter_rejected"
    )
    assert payload["effect_feed_parse_rejections"] == [
        {
            "rule_id": "uk_effect_feed_xml_parse_rejected",
            "phase": "parse",
            "feed_locator": "https://example.test/feed",
            "blocking": True,
        },
    ]
    assert payload["effect_feed_observations"] == [
        {
            "rule_id": "uk_effect_feed_xml_parse_rejected",
            "phase": "parse",
            "feed_locator": "https://example.test/feed",
            "blocking": True,
        },
        {
            "rule_id": "uk_effect_feed_pages_absent_recorded",
            "phase": "acquisition",
            "feed_locator": "https://example.test/missing-feed",
            "strict_disposition": "record",
        },
    ]
    assert payload["candidate_effect_count"] == 4
    assert payload["candidate_op_count"] == 7
    assert payload["residual_candidate_samples"] == [
        {
            "effect_id": "eff-1",
            "resolver_eids": ["section-3"],
            "overlapping_residual_roots": ["section-3"],
        }
    ]
    assert payload["residual_candidate_samples_omitted"] == 2
    assert payload["residual_roots"] == ["section-3", "section-4"]
    assert payload["replayed_residual_roots"] == ["section-3"]
    assert payload["oracle_residual_roots"] == ["section-4"]
    assert payload["malformed_residual_roots"] == ["section-4."]
    assert payload["backed_residual_roots"] == ["section-3"]
    assert payload["defeated_residual_roots"] == ["section-4"]
    assert payload["triage_rule_id"] == "uk_residual_claim_defeated_no_candidate_overlap"


def test_candidates_report_jsonable_records_summary_and_filters() -> None:
    filters = _uk_candidates_filters_jsonable(
        top=5,
        score_mode="replay",
        residual_only=True,
        fast=False,
        effect_budget=25,
        residual_budget=3,
        min_year=2000,
        max_year=2005,
        types={"ukpga", "asp"},
    )
    report = _uk_candidates_report_jsonable(
        label="uk_frontier",
        rows=[
            {
                "status": "real residual frontier",
                "comparison_class": "commensurable",
                "core_benchmark": True,
                "enacted_source_status": "available",
                "oracle_source_status": "available",
                "uk_replay_regime": {
                    "allow_metadata_backfill": True,
                    "allow_oracle_alignment": True,
                    "applicability_mode": "effective_date_plus_feed_applied",
                    "authority_mode": "current_mixed",
                },
                "inspected_effect_count": 3,
                "available_replay_applicable_effect_count": 6,
                "available_applied_effect_count": 5,
                "effect_count": 4,
                "effect_row_count": 3,
                "effect_feed_page_count": 1,
                "effect_inspection_truncated": True,
                "residual_analysis_skipped": False,
                "residual_analysis_unavailable": False,
                "candidate_effect_count": 2,
                "candidate_op_count": 4,
                "residual_candidate_effect_count": 1,
                "residual_candidate_op_count": 2,
                "residual_roots": ["section-1", "section-2"],
                "replayed_residual_roots": ["section-1"],
                "oracle_residual_roots": ["section-2"],
                "malformed_residual_roots": [],
                "backed_residual_roots": ["section-1"],
                "defeated_residual_roots": ["section-2"],
                "source_counts": {"missing_extracted_source": 2},
                "compare_counts": {"oracle_missing_live_branch": 1},
                "candidate_source_counts": {},
                "candidate_compare_counts": {"commensurable": 1},
                "non_candidate_source_counts": {"missing_extracted_source": 2},
                "non_candidate_compare_counts": {"oracle_missing_live_branch": 1},
                "suggested_claim_template_status_counts": {"available": 1},
                "rows_with_source_acquisition_rejections": 1,
                "source_acquisition_rejection_rule_counts": {
                    "uk_affecting_act_xml_missing_rejected": 1,
                },
                "bench_authority_rejection_count": 0,
                "bench_authority_rejection_rule_counts": {},
                "rows_with_blocking_lowering_rejections": 1,
                "effect_feed_parse_rejection_count": 2,
                "effect_feed_parse_rejection_rule_counts": {
                    "uk_effect_feed_xml_parse_rejected": 2,
                },
                "effect_feed_observation_count": 2,
                "effect_feed_observation_rule_counts": {
                    "uk_effect_feed_xml_parse_rejected": 2,
                },
                "effect_selection_observation_count": 2,
                "effect_selection_observation_rule_counts": {
                    "uk_effect_inspection_budget_excluded": 1,
                    "uk_effect_replay_applicability_selection_rejected": 1,
                },
                "effect_selection_rejection_count": 0,
                "effect_selection_rejection_rule_counts": {},
                "residual_compile_rejection_count": 1,
                "residual_compile_rejection_rule_counts": {
                    "uk_effect_payload_missing": 1,
                },
                "residual_compile_observation_count": 2,
                "residual_compile_observation_rule_counts": {
                    "uk_effect_payload_missing": 1,
                    "uk_residual_compile_note": 1,
                },
                "lowering_rejection_rule_counts": {"rule-a": 2},
                "blocking_lowering_rejection_rule_counts": {"rule-a": 1},
            },
            {
                "status": "real residual frontier",
                "comparison_class": "unapplied_oracle_expansion",
                "core_benchmark": True,
                "enacted_source_status": "available",
                "oracle_source_status": "too_small",
                "uk_replay_regime": {
                    "allow_metadata_backfill": False,
                    "allow_oracle_alignment": False,
                    "applicability_mode": "effective_date_only",
                    "authority_mode": "source_text_only",
                },
                "inspected_effect_count": 2,
                "available_replay_applicable_effect_count": 3,
                "available_applied_effect_count": 2,
                "effect_count": 5,
                "effect_row_count": 2,
                "effect_feed_page_count": 3,
                "effect_inspection_truncated": False,
                "residual_analysis_skipped": True,
                "residual_analysis_unavailable": False,
                "candidate_effect_count": 1,
                "candidate_op_count": 1,
                "residual_candidate_effect_count": 1,
                "residual_candidate_op_count": 1,
                "residual_roots": ["section-3."],
                "replayed_residual_roots": [],
                "oracle_residual_roots": ["section-3."],
                "malformed_residual_roots": ["section-3."],
                "backed_residual_roots": [],
                "defeated_residual_roots": [],
                "source_counts": {"uncovered_body": 1},
                "compare_counts": {"collapsed_subtree_oracle_shape": 2},
                "candidate_source_counts": {"uncovered_body": 1},
                "candidate_compare_counts": {"collapsed_subtree_oracle_shape": 2},
                "non_candidate_source_counts": {},
                "non_candidate_compare_counts": {},
                "suggested_claim_template_status_counts": {"not_available": 1},
                "rows_with_source_acquisition_rejections": 0,
                "source_acquisition_rejection_rule_counts": {},
                "bench_authority_rejection_count": 2,
                "bench_authority_rejection_rule_counts": {
                    "uk_authority_source_text_only_missing": 2,
                },
                "rows_with_blocking_lowering_rejections": 0,
                "effect_feed_parse_rejection_count": 1,
                "effect_feed_parse_rejection_rule_counts": {
                    "uk_effect_feed_locator_payload_missing_rejected": 1,
                },
                "effect_feed_observation_count": 1,
                "effect_feed_observation_rule_counts": {
                    "uk_effect_feed_locator_payload_missing_rejected": 1,
                },
                "effect_selection_observation_count": 1,
                "effect_selection_observation_rule_counts": {
                    "uk_effect_metadata_only_selection_rejected": 1,
                },
                "effect_selection_rejection_count": 0,
                "effect_selection_rejection_rule_counts": {},
                "residual_compile_rejection_count": 2,
                "residual_compile_rejection_rule_counts": {
                    "uk_effect_payload_missing": 1,
                    "uk_effect_authority_filter_rejected": 1,
                },
                "residual_compile_observation_count": 2,
                "residual_compile_observation_rule_counts": {
                    "uk_effect_payload_missing": 1,
                    "uk_effect_authority_filter_rejected": 1,
                },
                "lowering_rejection_rule_counts": {"rule-b": 3},
                "blocking_lowering_rejection_rule_counts": {},
            },
            {
                "status": "classification-heavy",
                "comparison_class": "collapsed_subtree_oracle_shape",
                "core_benchmark": False,
                "enacted_source_status": "unknown",
                "oracle_source_status": "absent",
                "uk_replay_regime": {
                    "allow_metadata_backfill": False,
                    "allow_oracle_alignment": False,
                    "applicability_mode": "effective_date_only",
                    "authority_mode": "source_text_only",
                },
                "inspected_effect_count": 1,
                "available_replay_applicable_effect_count": 1,
                "available_applied_effect_count": 1,
                "effect_count": 6,
                "effect_row_count": 1,
                "effect_feed_page_count": 4,
                "effect_inspection_truncated": False,
                "residual_analysis_skipped": False,
                "residual_analysis_unavailable": True,
                "residual_analysis_unavailable_reason": "oracle_missing_or_empty",
                "candidate_effect_count": 0,
                "candidate_op_count": 0,
                "residual_candidate_effect_count": 0,
                "residual_candidate_op_count": 0,
                "rows_with_source_acquisition_rejections": 0,
                "source_acquisition_rejection_rule_counts": {},
                "bench_authority_rejection_count": 1,
                "bench_authority_rejection_rule_counts": {
                    "uk_authority_source_text_only_missing": 1,
                },
                "rows_with_blocking_lowering_rejections": 0,
                "effect_feed_parse_rejection_count": 0,
                "effect_feed_parse_rejection_rule_counts": {},
                "effect_feed_observation_count": 0,
                "effect_feed_observation_rule_counts": {},
                "residual_compile_rejection_count": 0,
                "residual_compile_rejection_rule_counts": {},
                "residual_compile_observation_count": 0,
                "residual_compile_observation_rule_counts": {},
                "lowering_rejection_rule_counts": {},
                "blocking_lowering_rejection_rule_counts": {},
            },
        ],
        filters=filters,
        inspected_count=5,
    )

    assert report["report_kind"] == "uk_candidates_frontier_report"
    assert report["label"] == "uk_frontier"
    assert report["filters"]["types"] == ["asp", "ukpga"]
    assert report["filters"]["effect_budget"] == 25
    assert report["filters"]["residual_budget"] == 3
    assert report["summary"] == {
        "configured_top": 5,
        "configured_score_mode": "replay",
        "configured_effect_budget": 25,
        "configured_residual_budget": 3,
        "pre_replay_adjudication_filter_frontier_count": 5,
        "replay_adjudication_filter_excluded_count": 0,
        "inspected_frontier_count": 5,
        "matched_frontier_count": 5,
        "frontier_truncated": False,
        "emitted_row_count": 3,
        "status_counts": {
            "classification-heavy": 1,
            "real residual frontier": 2,
        },
        "inspected_effect_count": 6,
        "inspected_replay_applicable_effect_count": 6,
        "saved_legacy_effect_count": 15,
        "saved_effect_row_count": 6,
        "saved_effect_feed_page_count": 8,
        "available_replay_applicable_effect_count": 10,
        "available_applied_effect_count": 8,
        "rows_with_effect_inspection_truncated": 1,
        "rows_with_residual_analysis_skipped": 1,
        "rows_with_residual_analysis_unavailable": 1,
        "rows_with_candidate_analysis_skipped": 0,
        "candidate_effect_count": 3,
        "candidate_op_count": 5,
        "residual_candidate_effect_count": 2,
        "residual_candidate_op_count": 3,
        "residual_root_count": 3,
        "replayed_residual_root_count": 1,
        "oracle_residual_root_count": 2,
        "malformed_residual_root_count": 1,
        "backed_residual_root_count": 1,
        "defeated_residual_root_count": 1,
        "rows_with_source_acquisition_rejections": 1,
        "rows_with_source_acquisition_observations": 0,
        "source_acquisition_observation_rule_counts": {},
        "source_acquisition_rejection_rule_counts": {
            "uk_affecting_act_xml_missing_rejected": 1,
        },
        "bench_authority_observation_count": 0,
        "bench_authority_observation_rule_counts": {},
        "bench_authority_rejection_count": 3,
        "bench_authority_rejection_rule_counts": {
            "uk_authority_source_text_only_missing": 3,
        },
        "bench_effect_source_pathology_counts": {},
        "bench_manual_compile_status_counts": {},
        "bench_manual_compile_rule_counts": {},
        "bench_source_acquisition_rejection_count": 0,
        "rows_with_bench_source_acquisition_rejections": 0,
        "bench_source_acquisition_rejection_rule_counts": {},
        "rows_with_lowering_observations": 2,
        "rows_with_blocking_lowering_rejections": 1,
        "effect_feed_parse_rejection_count": 3,
        "rows_with_effect_feed_parse_rejections": 2,
        "effect_feed_parse_rejection_rule_counts": {
            "uk_effect_feed_locator_payload_missing_rejected": 1,
            "uk_effect_feed_xml_parse_rejected": 2,
        },
        "rows_with_effect_feed_count_errors": 0,
        "effect_feed_observation_count": 3,
        "rows_with_effect_feed_observations": 2,
        "effect_feed_observation_rule_counts": {
            "uk_effect_feed_locator_payload_missing_rejected": 1,
            "uk_effect_feed_xml_parse_rejected": 2,
        },
        "effect_selection_observation_count": 3,
        "rows_with_effect_selection_observations": 2,
        "effect_selection_observation_rule_counts": {
            "uk_effect_inspection_budget_excluded": 1,
            "uk_effect_metadata_only_selection_rejected": 1,
            "uk_effect_replay_applicability_selection_rejected": 1,
        },
        "effect_selection_rejection_count": 0,
        "rows_with_effect_selection_rejections": 0,
        "effect_selection_rejection_rule_counts": {},
        "source_parse_rejection_count": 0,
        "rows_with_source_parse_rejections": 0,
        "source_parse_rejection_rule_counts": {},
        "source_parse_observation_count": 0,
        "rows_with_source_parse_observations": 0,
        "source_parse_observation_rule_counts": {},
        "bench_exception_count": 0,
        "rows_with_bench_exceptions": 0,
        "bench_exception_rule_counts": {},
        "saved_bench_diagnostic_count": 0,
        "rows_with_saved_bench_diagnostics": 0,
        "saved_bench_diagnostic_rule_counts": {},
        "saved_bench_diagnostic_lane_counts": {},
        "replay_adjudication_count": 0,
        "rows_with_replay_adjudications": 0,
        "replay_adjudication_kind_counts": {},
        "replay_adjudication_bucket_counts": {},
        "replay_adjudication_sample_count": 0,
        "replay_adjudication_samples_omitted": 0,
        "uk_residual_claim_tier_counts": {},
        "uk_residual_claim_kind_counts": {},
        "rows_with_residual_section_claims": 0,
        "residual_claim_only_in_replayed_count": 0,
        "residual_claim_only_in_oracle_count": 0,
        "residual_compile_observation_count": 4,
        "rows_with_residual_compile_observations": 2,
        "residual_compile_observation_rule_counts": {
            "uk_effect_authority_filter_rejected": 1,
            "uk_effect_payload_missing": 2,
            "uk_residual_compile_note": 1,
        },
        "residual_compile_rejection_count": 3,
        "rows_with_residual_compile_rejections": 2,
        "residual_compile_rejection_rule_counts": {
            "uk_effect_authority_filter_rejected": 1,
            "uk_effect_payload_missing": 2,
        },
        "source_counts": {
            "missing_extracted_source": 2,
            "uncovered_body": 1,
        },
        "compare_counts": {
            "collapsed_subtree_oracle_shape": 2,
            "oracle_missing_live_branch": 1,
        },
        "enacted_source_status_counts": {
            "available": 2,
            "unknown": 1,
        },
        "oracle_source_status_counts": {
            "absent": 1,
            "available": 1,
            "too_small": 1,
        },
        "uk_replay_regime_counts": {
            (
                "metadata_backfill=0;oracle_alignment=0;"
                "metadata_only_effects=1;"
                "applicability=effective_date_only;authority=source_text_only"
            ): 2,
            (
                "metadata_backfill=1;oracle_alignment=1;"
                "metadata_only_effects=1;"
                "applicability=effective_date_plus_feed_applied;authority=current_mixed"
            ): 1,
        },
        "uk_source_purity_lane_counts": {},
        "rows_with_source_semantics_clean": 0,
        "rows_with_source_first_candidate": 0,
        "uk_source_first_candidate_reason_counts": {},
        "comparison_class_counts": {
            "collapsed_subtree_oracle_shape": 1,
            "commensurable": 1,
            "unapplied_oracle_expansion": 1,
        },
        "core_benchmark_counts": {
            "core": 2,
            "non_core": 1,
        },
        "candidate_source_counts": {"uncovered_body": 1},
        "candidate_compare_counts": {
            "collapsed_subtree_oracle_shape": 2,
            "commensurable": 1,
        },
        "non_candidate_source_counts": {"missing_extracted_source": 2},
        "non_candidate_compare_counts": {"oracle_missing_live_branch": 1},
        "manual_compile_status_counts": {},
        "manual_compile_rule_counts": {},
        "suggested_claim_template_status_counts": {
            "available": 1,
            "not_available": 1,
        },
        "lowering_observation_rule_counts": {"rule-a": 2, "rule-b": 3},
        "lowering_rejection_rule_counts": {"rule-a": 2, "rule-b": 3},
        "blocking_lowering_rejection_rule_counts": {"rule-a": 1},
    }


def test_candidates_report_jsonable_can_omit_rows_for_summary_only() -> None:
    report = _uk_candidates_report_jsonable(
        label="uk_frontier",
        rows=[
            {
                "status": "frontier prefilter only",
                "effect_count": 10,
                "effect_row_count": 7,
                "effect_feed_page_count": 3,
                "inspected_effect_count": 0,
                "available_replay_applicable_effect_count": 0,
                "available_applied_effect_count": 0,
                "effect_inspection_truncated": False,
                "residual_analysis_skipped": False,
                "residual_analysis_unavailable": False,
            }
        ],
        filters=_uk_candidates_filters_jsonable(
            top=1,
            score_mode="auto",
            residual_only=False,
            fast=True,
            effect_budget=None,
            residual_budget=None,
            min_year=None,
            max_year=None,
            types=None,
        ),
        inspected_count=1,
        summary_only=True,
    )

    assert report["report_kind"] == "uk_candidates_frontier_report"
    assert report["summary"]["matched_frontier_count"] == 1
    assert report["summary"]["emitted_row_count"] == 1
    assert report["summary"]["configured_top"] == 1
    assert report["summary"]["configured_effect_budget"] is None
    assert report["summary"]["configured_residual_budget"] is None
    assert report["summary"]["saved_legacy_effect_count"] == 10
    assert report["summary"]["saved_effect_row_count"] == 7
    assert report["summary"]["saved_effect_feed_page_count"] == 3
    assert "rows" not in report


def test_candidates_report_jsonable_records_frontier_truncation() -> None:
    report = _uk_candidates_report_jsonable(
        label="uk_frontier",
        rows=[],
        filters={},
        inspected_count=0,
        matched_frontier_count=3,
        summary_only=True,
    )

    assert report["summary"]["matched_frontier_count"] == 3
    assert report["summary"]["inspected_frontier_count"] == 0
    assert report["summary"]["frontier_truncated"] is True
    assert report["summary"]["configured_top"] is None


def test_print_uk_candidates_text_summary_uses_report_summary(capsys) -> None:
    report = _uk_candidates_report_jsonable(
        label="uk_frontier",
        rows=[
            {
                "status": "real residual frontier",
                "comparison_class": "commensurable",
                "core_benchmark": True,
                "enacted_source_status": "available",
                "oracle_source_status": "too_small",
                "source_counts": {"missing_extracted_source": 1},
                "candidate_source_counts": {"missing_extracted_source": 1},
                "non_candidate_source_counts": {},
                "compare_counts": {"commensurable": 1},
                "candidate_compare_counts": {"commensurable": 1},
                "non_candidate_compare_counts": {},
                "uk_replay_regime": {
                    "allow_metadata_backfill": False,
                    "allow_oracle_alignment": False,
                    "applicability_mode": "effective_date_only",
                    "authority_mode": "source_text_only",
                },
                "candidate_effect_count": 2,
                "candidate_op_count": 3,
                "residual_candidate_effect_count": 1,
                "residual_candidate_op_count": 2,
                "effect_count": 9,
                "effect_row_count": 4,
                "effect_feed_page_count": 5,
                "inspected_effect_count": 4,
                "available_replay_applicable_effect_count": 6,
                "available_applied_effect_count": 5,
                "effect_inspection_truncated": True,
                "residual_analysis_skipped": True,
                "residual_analysis_unavailable": True,
                "effect_feed_parse_rejection_count": 1,
                "effect_feed_parse_rejection_rule_counts": {
                    "uk_effect_feed_xml_parse_rejected": 1,
                },
                "effect_feed_observation_count": 1,
                "effect_feed_observation_rule_counts": {
                    "uk_effect_feed_xml_parse_rejected": 1,
                },
                "effect_selection_observation_count": 2,
                "effect_selection_observation_rule_counts": {
                    "uk_effect_inspection_budget_excluded": 1,
                    "uk_effect_replay_applicability_selection_rejected": 1,
                },
                "effect_selection_rejection_count": 0,
                "effect_selection_rejection_rule_counts": {},
                "effect_feed_count_error": "ValueError: bad effect feed",
                "source_parse_rejection_count": 1,
                "source_parse_rejection_rule_counts": {
                    "uk_oracle_xml_parse_rejected": 1,
                },
                "source_parse_observation_count": 1,
                "source_parse_observation_rule_counts": {
                    "uk_oracle_xml_parse_rejected": 1,
                },
                "bench_exception_count": 1,
                "bench_exception_rule_counts": {
                    "uk_bench_unclassified_exception": 1,
                },
                "residual_compile_rejection_count": 1,
                "residual_compile_rejection_rule_counts": {
                    "uk_effect_payload_missing": 1,
                },
                "residual_compile_observation_count": 1,
                "residual_compile_observation_rule_counts": {
                    "uk_effect_payload_missing": 1,
                },
                "rows_with_source_acquisition_rejections": 1,
                "source_acquisition_rejection_rule_counts": {
                    "uk_affecting_act_xml_missing_rejected": 1,
                },
                "bench_authority_rejection_count": 2,
                "bench_authority_rejection_rule_counts": {
                    "uk_authority_source_text_only_missing": 2,
                },
                "bench_effect_source_pathology_counts": {
                    "missing_extracted_source": 3,
                },
                "bench_manual_compile_status_counts": {
                    "manual_compile_candidate": 2,
                },
                "bench_manual_compile_rule_counts": {
                    "uk_manual_frontier_heading_facet_candidate": 2,
                },
                "bench_source_acquisition_rejection_count": 2,
                "bench_source_acquisition_rejection_rule_counts": {
                    "uk_affecting_act_xml_missing_rejected": 2,
                },
                "lowering_rejection_rule_counts": {
                    "uk_effect_payload_missing": 2,
                },
                "blocking_lowering_rejection_rule_counts": {
                    "uk_effect_payload_missing": 1,
                },
                "residual_roots": ["section-1", "section-2"],
                "backed_residual_roots": ["section-1"],
                "defeated_residual_roots": ["section-2"],
                "malformed_residual_roots": [],
            }
        ],
        filters={},
        inspected_count=1,
        matched_frontier_count=3,
        summary_only=True,
    )

    _print_uk_candidates_text_summary(report)

    out = capsys.readouterr().out
    assert "Summary:" in out
    assert "frontier: matched=3 inspected=1 emitted=1 truncated=true" in out
    assert "configured: top=None score_mode=None effect_budget=None residual_budget=None" in out
    assert "candidates: effects=2 ops=3 residual_effects=1 residual_ops=2" in out
    assert "saved_effect_inventory: legacy=9 rows=4 pages=5" in out
    assert "inspected_effects: inspected=4 available_replay_applicable=6 available_applied=5" in out
    assert (
        "budgets: effect_truncated_rows=1 residual_skipped_rows=1 "
        "residual_unavailable_rows=1 candidate_analysis_skipped_rows=0 "
        "feed_parse_observation_rows=1 "
        "feed_parse_observations=1 "
        "feed_parse_rejection_rows=1 "
        "feed_parse_rejections=1 "
        "effect_selection_observation_rows=1 "
        "effect_selection_observations=2 "
        "effect_selection_rejection_rows=0 "
        "effect_selection_rejections=0 "
        "feed_count_error_rows=1 "
        "source_parse_observation_rows=1 "
        "source_parse_observations=1 "
        "source_parse_rejection_rows=1 "
        "source_parse_rejections=1 "
        "bench_exception_rows=1 "
        "bench_exceptions=1 "
        "saved_bench_diagnostic_rows=0 "
        "saved_bench_diagnostics=0 "
        "replay_adjudication_rows=0 "
        "replay_adjudications=0 "
        "residual_compile_observation_rows=1 "
        "residual_compile_observations=1 "
        "residual_compile_rejection_rows=1 "
        "residual_compile_rejections=1 "
        "bench_authority_observations=0 "
        "bench_authority_rejections=2 "
        "bench_source_acquisition_rejection_rows=1 "
        "bench_source_acquisition_rejections=2 "
        "lowering_observation_rows=1 "
        "source_acquisition_observation_rows=0 "
        "source_acquisition_rejection_rows=1"
    ) in out
    assert "residual_roots: total=2 backed=1 defeated=1 malformed=0" in out
    assert "source_status: enacted=available=1 oracle=too_small=1" in out
    assert "classes: comparison=commensurable=1 core=core=1" in out
    assert "lowering_observation: uk_effect_payload_missing=2" in out
    assert (
        "replay_regimes: "
        "metadata_backfill=0;oracle_alignment=0;"
        "metadata_only_effects=1;"
        "applicability=effective_date_only;authority=source_text_only=1"
    ) in out
    assert (
        "source_evidence: all=missing_extracted_source=1 "
        "candidate=missing_extracted_source=1 non_candidate={}"
    ) in out
    assert "compare_evidence: all=commensurable=1 candidate=commensurable=1 non_candidate={}" in out
    assert "rejection_rules:" in out
    assert "feed_parse: uk_effect_feed_xml_parse_rejected=1" in out
    assert "feed_observation: uk_effect_feed_xml_parse_rejected=1" in out
    assert (
        "effect_selection_observation: "
        "uk_effect_inspection_budget_excluded=1, "
        "uk_effect_replay_applicability_selection_rejected=1"
    ) in out
    assert "effect_selection_rejection: {}" in out
    assert "source_parse_observation: uk_oracle_xml_parse_rejected=1" in out
    assert "source_parse: uk_oracle_xml_parse_rejected=1" in out
    assert "bench_exception: uk_bench_unclassified_exception=1" in out
    assert "residual_compile_observation: uk_effect_payload_missing=1" in out
    assert "residual_compile: uk_effect_payload_missing=1" in out
    assert "source_acquisition: uk_affecting_act_xml_missing_rejected=1" in out
    assert "bench_authority: uk_authority_source_text_only_missing=2" in out
    assert "bench_effect_source_pathology: missing_extracted_source=3" in out
    assert "bench_manual_compile_status: manual_compile_candidate=2" in out
    assert "bench_manual_compile_rule: uk_manual_frontier_heading_facet_candidate=2" in out
    assert "bench_source_acquisition: uk_affecting_act_xml_missing_rejected=2" in out
    assert "lowering: uk_effect_payload_missing=2" in out
    assert "blocking_lowering: uk_effect_payload_missing=1" in out


def test_uk_candidates_top_zero_json_summary_preserves_matched_frontier(monkeypatch, capsys) -> None:
    rows = [
        SimpleNamespace(
            statute_id="ukpga/2000/1",
            status="OK",
            year=2000,
            act_type="ukpga",
            replay_commencement_score=-1.0,
            replay_score=0.8,
            commencement_score=-1.0,
            score=0.8,
            n_commenced_eids=0,
            comparison_class="commensurable",
            n_enacted_eids=10,
            n_oracle_eids=12,
            n_effects=2,
        ),
        SimpleNamespace(
            statute_id="ukpga/2000/2",
            status="OK",
            year=2000,
            act_type="ukpga",
            replay_commencement_score=-1.0,
            replay_score=0.9,
            commencement_score=-1.0,
            score=0.9,
            n_commenced_eids=0,
            comparison_class="commensurable",
            n_enacted_eids=10,
            n_oracle_eids=12,
            n_effects=2,
        ),
    ]
    monkeypatch.setattr("lawvm.tools.uk_bench._load_run", lambda label: rows)

    uk_candidates.main(
        Namespace(
            label="demo",
            top=0,
            fast=True,
            effect_budget=None,
            residual_budget=None,
            score_mode="auto",
            residual_only=False,
            json=True,
            summary_only=True,
            min_year=None,
            max_year=None,
            types=None,
            db=None,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["matched_frontier_count"] == 2
    assert payload["summary"]["inspected_frontier_count"] == 0
    assert payload["summary"]["frontier_truncated"] is True
    assert payload["summary"]["emitted_row_count"] == 0
    assert "rows" not in payload


def test_uk_candidates_fast_prefilter_preserves_saved_source_surface(monkeypatch, capsys) -> None:
    rows = [
        SimpleNamespace(
            statute_id="ukpga/2000/1",
            status="OK",
            year=2000,
            act_type="ukpga",
            replay_commencement_score=-1.0,
            replay_score=0.8,
            commencement_score=-1.0,
            score=0.8,
            n_commenced_eids=0,
            comparison_class="commensurable",
            n_enacted_eids=10,
            n_oracle_eids=12,
            n_effects=2,
            n_effect_feed_pages=2,
            n_effect_rows=1,
            enacted_source_status="available",
            oracle_source_status="too_small",
            enacted_source_size=456,
            oracle_source_size=7,
            enacted_source_sha256="enacted-sha",
            oracle_source_sha256="oracle-sha",
            enacted_source_url="https://example.test/ukpga/2000/1/enacted/data.xml",
            oracle_source_url="https://example.test/ukpga/2000/1/data.xml",
            source_parse_rejection_count=1,
            source_parse_rejection_rule_counts={"uk_oracle_xml_parse_rejected": 1},
            source_parse_observation_count=1,
            source_parse_observation_rule_counts={"uk_oracle_xml_parse_rejected": 1},
            source_parse_observations=(
                {
                    "rule_id": "uk_oracle_xml_parse_rejected",
                    "phase": "parse",
                    "blocking": True,
                },
            ),
            bench_exception_count=1,
            bench_exception_rule_counts={"uk_bench_unclassified_exception": 1},
            bench_exception_observations=(
                {
                    "rule_id": "uk_bench_unclassified_exception",
                    "phase": "benchmark",
                    "exception_type": "RuntimeError",
                    "exception_message": "archive backend failed",
                    "strict_disposition": "block",
                },
            ),
            effect_diagnostics=(
                {
                    "rule_id": "uk_affecting_act_xml_missing_rejected",
                    "phase": "acquisition",
                    "blocking": True,
                },
                {
                    "rule_id": "uk_effect_source_pathology_classified",
                    "source_pathology": "missing_extracted_source",
                    "blocking": False,
                },
                {
                    "rule_id": "uk_manual_compile_frontier_classified",
                    "phase": "lowering",
                    "manual_compile_status": "unclassified_frontier",
                    "manual_compile_rule_id": "uk_manual_frontier_unclassified",
                    "blocking": False,
                },
            ),
            uk_metadata_backfill_enabled=False,
            uk_oracle_alignment_enabled=False,
            uk_applicability_mode="effective_date_only",
            uk_authority_mode="source_text_only",
            uk_authority_observation_count=1,
            uk_authority_observation_rule_counts={
                "uk_authority_source_text_only_observed": 1,
            },
            uk_authority_rejection_count=2,
            uk_authority_rejection_rule_counts={
                "uk_authority_source_text_only_missing": 2,
            },
            uk_authority_observations=(
                {
                    "rule_id": "uk_authority_source_text_only_missing",
                    "phase": "lowering",
                    "blocking": True,
                },
            ),
            effect_source_pathology_counts={"missing_extracted_source": 4},
            manual_compile_status_counts={"unclassified_frontier": 1},
            manual_compile_rule_counts={"uk_manual_frontier_unclassified": 1},
            source_acquisition_rejection_count=2,
            source_acquisition_rejection_rule_counts={"uk_affecting_act_xml_missing_rejected": 2},
            effect_feed_rejection_count=1,
            effect_feed_rejection_rule_counts={
                "uk_effect_feed_xml_parse_rejected": 1,
            },
            effect_feed_count_error="ValueError: bad effect feed",
            effect_feed_observation_count=3,
            effect_feed_observation_rule_counts={
                "uk_effect_feed_pages_absent_recorded": 2,
                "uk_effect_feed_xml_parse_rejected": 1,
            },
            effect_feed_observations=(
                {
                    "rule_id": "uk_effect_feed_xml_parse_rejected",
                    "phase": "parse",
                    "blocking": True,
                },
                {
                    "rule_id": "uk_effect_feed_pages_absent_recorded",
                    "phase": "acquisition",
                    "strict_disposition": "record",
                },
                {
                    "rule_id": "uk_effect_feed_pages_absent_recorded",
                    "phase": "acquisition",
                    "blocking": False,
                },
            ),
            lowering_rejection_count=2,
            lowering_rejection_rule_counts={"uk_effect_payload_missing": 2},
            blocking_lowering_rejection_count=1,
            blocking_lowering_rejection_rule_counts={"uk_effect_payload_missing": 1},
            lowering_rejections=(
                {
                    "rule_id": "uk_effect_payload_missing",
                    "phase": "lowering",
                    "blocking": True,
                },
            ),
            replay_adjudications=(
                {
                    "kind": "uk_replay_target_not_found",
                    "message": "target missing",
                    "blocking": False,
                },
            ),
        )
    ]
    monkeypatch.setattr("lawvm.tools.uk_bench._load_run", lambda label: rows)

    uk_candidates.main(
        Namespace(
            label="demo",
            top=1,
            fast=True,
            effect_budget=None,
            residual_budget=None,
            score_mode="auto",
            residual_only=False,
            json=True,
            summary_only=False,
            min_year=None,
            max_year=None,
            types=None,
            db=None,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    row = payload["rows"][0]
    assert row["enacted_source_status"] == "available"
    assert row["oracle_source_status"] == "too_small"
    assert row["enacted_source_size"] == 456
    assert row["oracle_source_size"] == 7
    assert row["enacted_source_sha256"] == "enacted-sha"
    assert row["oracle_source_sha256"] == "oracle-sha"
    assert row["enacted_source_url"] == "https://example.test/ukpga/2000/1/enacted/data.xml"
    assert row["oracle_source_url"] == "https://example.test/ukpga/2000/1/data.xml"
    assert row["source_parse_rejection_count"] == 1
    assert row["source_parse_rejection_rule_counts"] == {
        "uk_oracle_xml_parse_rejected": 1,
    }
    assert row["source_parse_observation_count"] == 1
    assert row["source_parse_observation_rule_counts"] == {
        "uk_oracle_xml_parse_rejected": 1,
    }
    assert row["bench_exception_count"] == 1
    assert row["bench_exception_rule_counts"] == {"uk_bench_unclassified_exception": 1}
    assert row["bench_exception_observations"] == [
        {
            "rule_id": "uk_bench_unclassified_exception",
            "phase": "benchmark",
            "exception_type": "RuntimeError",
            "exception_message": "archive backend failed",
            "strict_disposition": "block",
        }
    ]
    assert row["saved_bench_diagnostic_count"] == 11
    assert row["saved_bench_diagnostic_rule_counts"] == {
        "uk_affecting_act_xml_missing_rejected": 1,
        "uk_bench_unclassified_exception": 1,
        "uk_effect_feed_pages_absent_recorded": 2,
        "uk_effect_feed_xml_parse_rejected": 1,
        "uk_manual_compile_frontier_classified": 1,
        "uk_effect_payload_missing": 1,
        "uk_effect_source_pathology_classified": 1,
        "uk_oracle_xml_parse_rejected": 1,
        "uk_authority_source_text_only_missing": 1,
        "uk_replay_target_not_found": 1,
    }
    assert row["saved_bench_diagnostic_lane_counts"] == {
        "authority": 1,
        "bench_exception": 1,
        "effect_feed": 3,
        "effect_source_pathology": 1,
        "lowering": 1,
        "manual_compile_frontier": 1,
        "replay_adjudication": 1,
        "source_acquisition": 1,
        "source_parse": 1,
    }
    assert [entry["diagnostic_lane"] for entry in row["saved_bench_diagnostics"]] == [
        "source_parse",
        "effect_feed",
        "effect_feed",
        "effect_feed",
        "source_acquisition",
        "effect_source_pathology",
        "manual_compile_frontier",
        "authority",
        "lowering",
        "replay_adjudication",
        "bench_exception",
    ]
    manual_diagnostic = row["saved_bench_diagnostics"][6]
    assert manual_diagnostic["record"]["manual_compile_status"] == "unclassified_frontier"
    assert manual_diagnostic["record"]["manual_compile_rule_id"] == (
        "uk_manual_frontier_unclassified"
    )
    assert row["core_benchmark"] is True
    assert row["bench_authority_observation_count"] == 1
    assert row["bench_authority_observation_rule_counts"] == {
        "uk_authority_source_text_only_observed": 1,
    }
    assert row["bench_authority_rejection_count"] == 2
    assert row["bench_authority_rejection_rule_counts"] == {
        "uk_authority_source_text_only_missing": 2,
    }
    assert row["bench_effect_source_pathology_counts"] == {
        "missing_extracted_source": 4,
    }
    assert row["bench_manual_compile_status_counts"] == {"unclassified_frontier": 1}
    assert row["bench_manual_compile_rule_counts"] == {
        "uk_manual_frontier_unclassified": 1,
    }
    assert row["bench_source_acquisition_rejection_count"] == 2
    assert row["bench_source_acquisition_rejection_rule_counts"] == {
        "uk_affecting_act_xml_missing_rejected": 2,
    }
    assert row["effect_feed_parse_rejection_count"] == 1
    assert row["effect_feed_parse_rejection_rule_counts"] == {
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert row["effect_feed_parse_rejections"] == [
        {
            "rule_id": "uk_effect_feed_xml_parse_rejected",
            "phase": "parse",
            "blocking": True,
        }
    ]
    assert row["effect_feed_count_error"] == "ValueError: bad effect feed"
    assert row["effect_feed_observation_count"] == 3
    assert row["effect_feed_observation_rule_counts"] == {
        "uk_effect_feed_pages_absent_recorded": 2,
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert row["effect_feed_observations"] == [
        {
            "rule_id": "uk_effect_feed_xml_parse_rejected",
            "phase": "parse",
            "blocking": True,
        },
        {
            "rule_id": "uk_effect_feed_pages_absent_recorded",
            "phase": "acquisition",
            "strict_disposition": "record",
        },
        {
            "rule_id": "uk_effect_feed_pages_absent_recorded",
            "phase": "acquisition",
            "blocking": False,
        },
    ]
    assert row["lowering_observation_count"] == 2
    assert row["lowering_observation_rule_counts"] == {"uk_effect_payload_missing": 2}
    assert row["rows_with_lowering_observations"] == 1
    assert row["lowering_rejection_count"] == 2
    assert row["lowering_rejection_rule_counts"] == {"uk_effect_payload_missing": 2}
    assert row["blocking_lowering_rejection_count"] == 1
    assert row["blocking_lowering_rejection_rule_counts"] == {"uk_effect_payload_missing": 1}
    assert payload["summary"]["enacted_source_status_counts"] == {"available": 1}
    assert payload["summary"]["oracle_source_status_counts"] == {"too_small": 1}
    assert payload["summary"]["comparison_class_counts"] == {"commensurable": 1}
    assert payload["summary"]["core_benchmark_counts"] == {"core": 1}
    assert payload["summary"]["rows_with_candidate_analysis_skipped"] == 1
    assert payload["summary"]["bench_authority_observation_count"] == 1
    assert payload["summary"]["bench_authority_observation_rule_counts"] == {
        "uk_authority_source_text_only_observed": 1,
    }
    assert payload["summary"]["bench_authority_rejection_count"] == 2
    assert payload["summary"]["bench_authority_rejection_rule_counts"] == {
        "uk_authority_source_text_only_missing": 2,
    }
    assert payload["summary"]["bench_effect_source_pathology_counts"] == {
        "missing_extracted_source": 4,
    }
    assert payload["summary"]["bench_manual_compile_status_counts"] == {
        "unclassified_frontier": 1
    }
    assert payload["summary"]["bench_manual_compile_rule_counts"] == {
        "uk_manual_frontier_unclassified": 1,
    }
    assert payload["summary"]["bench_source_acquisition_rejection_count"] == 2
    assert payload["summary"]["rows_with_bench_source_acquisition_rejections"] == 1
    assert payload["summary"]["bench_source_acquisition_rejection_rule_counts"] == {
        "uk_affecting_act_xml_missing_rejected": 2,
    }
    assert payload["summary"]["source_parse_rejection_count"] == 1
    assert payload["summary"]["rows_with_source_parse_rejections"] == 1
    assert payload["summary"]["source_parse_rejection_rule_counts"] == {
        "uk_oracle_xml_parse_rejected": 1,
    }
    assert payload["summary"]["source_parse_observation_count"] == 1
    assert payload["summary"]["rows_with_source_parse_observations"] == 1
    assert payload["summary"]["source_parse_observation_rule_counts"] == {
        "uk_oracle_xml_parse_rejected": 1,
    }
    assert payload["summary"]["bench_exception_count"] == 1
    assert payload["summary"]["rows_with_bench_exceptions"] == 1
    assert payload["summary"]["bench_exception_rule_counts"] == {
        "uk_bench_unclassified_exception": 1,
    }
    assert payload["summary"]["saved_bench_diagnostic_count"] == 11
    assert payload["summary"]["rows_with_saved_bench_diagnostics"] == 1
    assert payload["summary"]["saved_bench_diagnostic_rule_counts"] == {
        "uk_affecting_act_xml_missing_rejected": 1,
        "uk_bench_unclassified_exception": 1,
        "uk_effect_feed_pages_absent_recorded": 2,
        "uk_effect_feed_xml_parse_rejected": 1,
        "uk_manual_compile_frontier_classified": 1,
        "uk_effect_payload_missing": 1,
        "uk_effect_source_pathology_classified": 1,
        "uk_oracle_xml_parse_rejected": 1,
        "uk_authority_source_text_only_missing": 1,
        "uk_replay_target_not_found": 1,
    }
    assert payload["summary"]["saved_bench_diagnostic_lane_counts"] == {
        "authority": 1,
        "bench_exception": 1,
        "effect_feed": 3,
        "effect_source_pathology": 1,
        "lowering": 1,
        "manual_compile_frontier": 1,
        "replay_adjudication": 1,
        "source_acquisition": 1,
        "source_parse": 1,
    }
    assert payload["summary"]["effect_feed_parse_rejection_count"] == 1
    assert payload["summary"]["effect_feed_parse_rejection_rule_counts"] == {
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert payload["summary"]["rows_with_effect_feed_count_errors"] == 1
    assert payload["summary"]["effect_feed_observation_count"] == 3
    assert payload["summary"]["rows_with_effect_feed_observations"] == 1
    assert payload["summary"]["effect_feed_observation_rule_counts"] == {
        "uk_effect_feed_pages_absent_recorded": 2,
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert payload["summary"]["lowering_observation_rule_counts"] == {
        "uk_effect_payload_missing": 2,
    }
    assert payload["summary"]["rows_with_lowering_observations"] == 1
    assert payload["summary"]["lowering_rejection_rule_counts"] == {
        "uk_effect_payload_missing": 2,
    }
    assert payload["summary"]["blocking_lowering_rejection_rule_counts"] == {
        "uk_effect_payload_missing": 1,
    }
    assert payload["summary"]["rows_with_blocking_lowering_rejections"] == 1
    assert payload["summary"]["uk_replay_regime_counts"] == {
        "metadata_backfill=0;oracle_alignment=0;metadata_only_effects=1;applicability=effective_date_only;authority=source_text_only": 1
    }


def test_uk_candidates_fast_text_reports_saved_rejection_rules(monkeypatch, capsys) -> None:
    rows = [
        SimpleNamespace(
            statute_id="ukpga/2000/1",
            status="OK",
            year=2000,
            act_type="ukpga",
            replay_commencement_score=-1.0,
            replay_score=0.8,
            commencement_score=-1.0,
            score=0.8,
            n_commenced_eids=0,
            comparison_class="commensurable",
            n_enacted_eids=10,
            n_oracle_eids=12,
            n_effects=2,
            n_effect_rows=1,
            enacted_source_status="available",
            oracle_source_status="available",
            source_parse_rejection_rule_counts={
                "uk_oracle_xml_parse_rejected": 1,
            },
            source_parse_observation_rule_counts={
                "uk_oracle_xml_parse_rejected": 1,
            },
            source_parse_observations=(
                {"rule_id": "uk_oracle_xml_parse_rejected", "phase": "source_parse"},
            ),
            effect_feed_rejection_rule_counts={
                "uk_effect_feed_xml_parse_rejected": 1,
            },
            effect_feed_observation_rule_counts={
                "uk_effect_feed_pages_absent_recorded": 2,
            },
            effect_diagnostics=(
                {"rule_id": "uk_effect_source_pathology_classified", "blocking": False},
            ),
            effect_feed_count_error="ValueError: bad effect feed",
            effect_source_pathology_counts={"missing_extracted_source": 4},
            source_acquisition_rejection_rule_counts={"uk_affecting_act_xml_missing_rejected": 2},
            uk_authority_observation_rule_counts={
                "uk_authority_source_text_only_observed": 1,
            },
            uk_authority_rejection_rule_counts={
                "uk_authority_source_text_only_missing": 2,
            },
            uk_authority_observations=(
                {"rule_id": "uk_authority_source_text_only_missing", "blocking": True},
            ),
            lowering_rejection_rule_counts={"uk_effect_payload_missing": 3},
            blocking_lowering_rejection_rule_counts={"uk_effect_payload_missing": 1},
            lowering_rejections=(
                {"rule_id": "uk_effect_payload_missing", "blocking": True},
            ),
            replay_adjudications=(
                {"kind": "uk_replay_target_not_found", "blocking": True},
            ),
            bench_exception_observations=(
                {"rule_id": "uk_bench_unclassified_exception", "blocking": True},
            ),
        )
    ]
    monkeypatch.setattr("lawvm.tools.uk_bench._load_run", lambda label: rows)

    uk_candidates.main(
        Namespace(
            label="demo",
            top=1,
            fast=True,
            effect_budget=None,
            residual_budget=None,
            score_mode="auto",
            residual_only=False,
            json=False,
            summary_only=False,
            min_year=None,
            max_year=None,
            types=None,
            db=None,
        )
    )

    out = capsys.readouterr().out
    assert "effect_rows=   1 effect_pages=   2" in out
    assert "effects=   2" not in out
    assert "rejection_rules:" in out
    assert "feed_parse=uk_effect_feed_xml_parse_rejected=1" in out
    assert "feed_observation=uk_effect_feed_pages_absent_recorded=2" in out
    assert "source_parse=uk_oracle_xml_parse_rejected=1" in out
    assert "source_parse_observation=uk_oracle_xml_parse_rejected=1" in out
    assert "effect_source_pathology=missing_extracted_source=4" in out
    assert "source_acquisition=uk_affecting_act_xml_missing_rejected=2" in out
    assert "bench_authority_observation=uk_authority_source_text_only_observed=1" in out
    assert "bench_authority=uk_authority_source_text_only_missing=2" in out
    assert "lowering_observation=uk_effect_payload_missing=3" in out
    assert "lowering=uk_effect_payload_missing=3" in out
    assert "blocking_lowering=uk_effect_payload_missing=1" in out
    assert (
        "saved_bench_diagnostic_rules="
        "uk_authority_source_text_only_missing=1, "
        "uk_bench_unclassified_exception=1, "
        "uk_effect_payload_missing=1, "
        "uk_effect_source_pathology_classified=1, "
        "uk_oracle_xml_parse_rejected=1, "
        "uk_replay_target_not_found=1"
    ) in out
    assert (
        "saved_bench_diagnostic_lanes="
        "authority=1, bench_exception=1, effect_source_pathology=1, "
        "lowering=1, replay_adjudication=1, source_parse=1"
    ) in out
    assert "saved_bench_feed_count_error: ValueError: bad effect feed" in out


def test_uk_candidates_full_text_reports_saved_bench_rejection_rules(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    import farchive
    from lawvm.tools import uk_effects

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    row = SimpleNamespace(
        statute_id="ukpga/2000/1",
        status="OK",
        year=2000,
        act_type="ukpga",
        replay_commencement_score=-1.0,
        replay_score=0.8,
        commencement_score=-1.0,
        score=0.8,
        n_commenced_eids=0,
        comparison_class="commensurable",
        n_enacted_eids=10,
        n_oracle_eids=12,
        n_effects=2,
        n_effect_rows=1,
        enacted_source_status="available",
        oracle_source_status="available",
        source_parse_rejection_rule_counts={"uk_oracle_xml_parse_rejected": 1},
        effect_feed_rejection_rule_counts={"uk_effect_feed_xml_parse_rejected": 1},
        effect_feed_count_error="ValueError: bad effect feed",
        effect_source_pathology_counts={"missing_extracted_source": 4},
        manual_compile_status_counts={"manual_compile_candidate": 2},
        manual_compile_rule_counts={"uk_manual_frontier_repeal_table_candidate": 2},
        source_acquisition_rejection_rule_counts={"uk_affecting_act_xml_missing_rejected": 2},
        uk_authority_rejection_rule_counts={"uk_authority_source_text_only_missing": 2},
        lowering_rejection_rule_counts={"uk_effect_payload_missing": 3},
        blocking_lowering_rejection_rule_counts={"uk_effect_payload_missing": 1},
    )

    class FakeArchive:
        def __init__(self, _path: str) -> None:
            pass

        def __enter__(self) -> "FakeArchive":
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr("lawvm.tools.uk_bench._load_run", lambda _label: [row])
    monkeypatch.setattr(
        uk_legislation_effects,
        "load_effects_for_statute_from_archive",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        uk_effects,
        "build_uk_effect_summary_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            enacted_ir=None,
            oracle_eids=set(),
            enacted_missing=True,
            oracle_missing=False,
        ),
    )

    uk_candidates.main(
        Namespace(
            label="demo",
            top=1,
            fast=False,
            effect_budget=None,
            residual_budget=None,
            score_mode="auto",
            residual_only=False,
            json=False,
            summary_only=False,
            min_year=None,
            max_year=None,
            types=None,
            db=str(db_path),
        )
    )

    out = capsys.readouterr().out
    assert "saved_bench_rejection_rules:" in out
    assert "feed_parse=uk_effect_feed_xml_parse_rejected=1" in out
    assert "source_parse=uk_oracle_xml_parse_rejected=1" in out
    assert "effect_source_pathology=missing_extracted_source=4" in out
    assert "manual_compile_status=manual_compile_candidate=2" in out
    assert "manual_compile_rule=uk_manual_frontier_repeal_table_candidate=2" in out
    assert "source_acquisition=uk_affecting_act_xml_missing_rejected=2" in out
    assert "bench_authority=uk_authority_source_text_only_missing=2" in out
    assert "lowering_observation=uk_effect_payload_missing=3" in out
    assert "lowering=uk_effect_payload_missing=3" in out
    assert "blocking_lowering=uk_effect_payload_missing=1" in out
    assert "saved_bench_feed_count_error: ValueError: bad effect feed" in out


def test_uk_candidates_full_mode_exports_manual_compile_evidence_jsonl(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    import farchive
    from lawvm.tools import uk_effects
    from lawvm.uk_legislation.uk_amendment_replay import UKEffectRecord

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    out_path = tmp_path / "manual" / "uk-manual.jsonl"
    bench_row = SimpleNamespace(
        statute_id="ukpga/2000/1",
        status="OK",
        year=2000,
        act_type="ukpga",
        replay_commencement_score=-1.0,
        replay_score=0.8,
        commencement_score=-1.0,
        score=0.8,
        n_commenced_eids=0,
        comparison_class="commensurable",
        n_enacted_eids=10,
        n_oracle_eids=12,
        n_effects=1,
        n_effect_rows=1,
        enacted_source_status="available",
        oracle_source_status="available",
        uk_metadata_backfill_enabled=False,
        uk_oracle_alignment_enabled=False,
        uk_metadata_only_effects_enabled=False,
        uk_applicability_mode="effective_date_only",
        uk_authority_mode="source_text_only",
    )
    effect = UKEffectRecord(
        effect_id="eff-heading",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2000/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2000",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri="/id/ukpga/2025/1",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Test Act",
    )

    class FakeArchive:
        def __init__(self, _path: str) -> None:
            pass

        def __enter__(self) -> "FakeArchive":
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    context = uk_effects._EffectSummaryContext(
        statute_id="ukpga/2000/1",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
        archive_path=str(db_path),
        enacted_url="https://example.test/enacted.xml",
        oracle_url="https://example.test/current.xml",
        enacted_source_status="available",
        oracle_source_status="available",
    )

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr("lawvm.tools.uk_bench._load_run", lambda _label: [bench_row])
    monkeypatch.setattr(
        uk_legislation_effects,
        "load_effects_for_statute_from_archive",
        lambda *_args, **_kwargs: [effect],
    )
    monkeypatch.setattr(
        uk_effects,
        "build_uk_effect_summary_context",
        lambda *_args, **_kwargs: context,
    )
    monkeypatch.setattr(
        uk_effects,
        "summarize_uk_effect",
        lambda *_args, **_kwargs: uk_effects._EffectSummary(
            source_pathology="unhandled_instruction_text",
            compare_shape="commensurable",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {"rule_id": "uk_effect_heading_only_ref_rejected", "blocking": True},
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview='In the title, for "old" substitute "new".',
            affecting_source_status="available",
            affecting_source_size=17,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_heading_facet_candidate",
            manual_compile_reason="Heading facet requires manual compile.",
        ),
    )

    uk_candidates.main(
        Namespace(
            label="demo",
            top=1,
            fast=False,
            effect_budget=None,
            residual_budget=None,
            score_mode="auto",
            residual_only=False,
            json=True,
            summary_only=False,
            min_year=None,
            max_year=None,
            types=None,
            db=str(db_path),
            manual_compile_evidence_jsonl=str(out_path),
        )
    )

    payload = json.loads(capsys.readouterr().out)
    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines()]
    assert payload["manual_compile_evidence_jsonl"] == {
        "path": str(out_path),
        "rows": 1,
        "statuses": ["manual_compile_candidate"],
    }
    assert rows[0]["statute_id"] == "ukpga/2000/1"
    assert rows[0]["effect_id"] == "eff-heading"
    assert rows[0]["work_item_kind"] == "semantic_compile_candidate"
    assert rows[0]["claim_kind"] == "semantic_compile"
    assert rows[0]["claim_status"] == "unresolved_work_item"
    assert rows[0]["validator_status"] == "not_validated"
    assert rows[0]["work_item_id"].startswith("uk-manual-frontier-")
    assert rows[0]["affected_uri"] == "/id/ukpga/2000/1"
    assert rows[0]["affecting_uri"] == "/id/ukpga/2025/1"
    assert rows[0]["affecting_source_witness"] == {
        "affecting_act_id": "ukpga/2025/1",
        "affecting_provisions": "s. 2",
        "source_status": "available",
        "source_size": 17,
        "source_sha256": "affecting-sha",
    }
    assert rows[0]["manual_compile_status"] == "manual_compile_candidate"
    assert rows[0]["manual_compile_rule_id"] == "uk_manual_frontier_heading_facet_candidate"
    assert rows[0]["source"]["text_preview_sha256"]
    assert rows[0]["target_context"] == {
        "surface": "effect_feed_affected_provisions",
        "affected_provisions": "s. 1",
        "resolver_eids": [],
        "compare_shape": "commensurable",
    }
    assert rows[0]["lowering_rejections"] == [
        {"rule_id": "uk_effect_heading_only_ref_rejected", "blocking": True},
    ]
    assert rows[0]["source_witness"]["archive_path"] == str(db_path)
    assert rows[0]["replay_regime"] == {
        "allow_metadata_backfill": False,
        "allow_metadata_only_effects": False,
        "allow_oracle_alignment": False,
        "applicability_mode": "effective_date_only",
        "authority_mode": "source_text_only",
    }


def test_uk_candidates_manual_compile_evidence_jsonl_can_export_frontend_candidates(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    import farchive
    from lawvm.tools import uk_effects
    from lawvm.uk_legislation.uk_amendment_replay import UKEffectRecord

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    out_path = tmp_path / "manual" / "uk-frontier.jsonl"
    bench_row = SimpleNamespace(
        statute_id="asp/2001/2",
        status="OK",
        year=2001,
        act_type="asp",
        replay_commencement_score=-1.0,
        replay_score=0.8,
        commencement_score=-1.0,
        score=0.8,
        n_commenced_eids=0,
        comparison_class="commensurable",
        n_enacted_eids=10,
        n_oracle_eids=12,
        n_effects=2,
        n_effect_rows=2,
        enacted_source_status="available",
        oracle_source_status="available",
        uk_metadata_backfill_enabled=False,
        uk_oracle_alignment_enabled=False,
        uk_metadata_only_effects_enabled=False,
        uk_applicability_mode="effective_date_only",
        uk_authority_mode="source_text_only",
    )
    manual_effect = UKEffectRecord(
        effect_id="eff-manual",
        effect_type="words substituted",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/asp/2001/2",
        affected_class="ScottishAct",
        affected_year="2001",
        affected_number="2",
        affected_provisions="s. 1",
        affecting_uri="/id/asp/2025/1",
        affecting_class="ScottishAct",
        affecting_year="2025",
        affecting_number="1",
        affecting_provisions="s. 2",
        affecting_title="Manual Act",
    )
    frontend_effect = replace(
        manual_effect,
        effect_id="eff-frontend",
        effect_type="words inserted",
        affecting_provisions="art. 6(2)(b)",
        affecting_title="Frontend Act",
    )

    class FakeArchive:
        def __init__(self, _path: str) -> None:
            pass

        def __enter__(self) -> "FakeArchive":
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    context = uk_effects._EffectSummaryContext(
        statute_id="asp/2001/2",
        enacted_ir=None,
        oracle_ir=None,
        base_eids=set(),
        oracle_eids=set(),
        base_text_map={},
        oracle_eid_map={},
        oracle_text_map={},
        resolver=None,
        affecting_xml_cache={},
        archive_path=str(db_path),
        enacted_url="https://example.test/enacted.xml",
        oracle_url="https://example.test/current.xml",
        enacted_source_status="available",
        oracle_source_status="available",
    )

    def fake_summary(effect: UKEffectRecord, *_args: object, **_kwargs: object) -> uk_effects._EffectSummary:
        if effect.effect_id == "eff-frontend":
            return uk_effects._EffectSummary(
                source_pathology="structural_sibling_insert_unsupported",
                compare_shape="commensurable",
                n_ops=0,
                candidate=False,
                resolver_eids=(),
                lowering_rejections=(
                    {"rule_id": "uk_effect_overlap_substitution_unlowered", "blocking": True},
                ),
                replay_applicable=True,
                structural_for_replay=True,
                source_extracted=True,
                source_extracted_tag="P3",
                source_extracted_text_preview="after that paragraph, insert...",
                affecting_source_status="available",
                affecting_source_size=17,
                affecting_source_sha256="affecting-sha",
                manual_compile_status="deterministic_frontend_candidate",
                manual_compile_rule_id="uk_manual_frontier_structural_sibling_insert_candidate",
                manual_compile_reason="Deterministic frontend lowering candidate.",
            )
        return uk_effects._EffectSummary(
            source_pathology="unhandled_instruction_text",
            compare_shape="commensurable",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {"rule_id": "uk_effect_heading_only_ref_rejected", "blocking": True},
            ),
            replay_applicable=True,
            structural_for_replay=True,
            source_extracted=True,
            source_extracted_tag="P1",
            source_extracted_text_preview='In the title, for "old" substitute "new".',
            affecting_source_status="available",
            affecting_source_size=17,
            affecting_source_sha256="affecting-sha",
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_heading_facet_candidate",
            manual_compile_reason="Heading facet requires manual compile.",
        )

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr("lawvm.tools.uk_bench._load_run", lambda _label: [bench_row])
    monkeypatch.setattr(
        uk_legislation_effects,
        "load_effects_for_statute_from_archive",
        lambda *_args, **_kwargs: [manual_effect, frontend_effect],
    )
    monkeypatch.setattr(
        uk_effects,
        "build_uk_effect_summary_context",
        lambda *_args, **_kwargs: context,
    )
    monkeypatch.setattr(uk_effects, "summarize_uk_effect", fake_summary)

    uk_candidates.main(
        Namespace(
            label="demo",
            top=1,
            fast=False,
            effect_budget=None,
            residual_budget=None,
            score_mode="auto",
            residual_only=False,
            json=True,
            summary_only=False,
            min_year=None,
            max_year=None,
            types=None,
            db=str(db_path),
            manual_compile_evidence_jsonl=str(out_path),
            manual_compile_evidence_status=["deterministic_frontend_candidate"],
        )
    )

    payload = json.loads(capsys.readouterr().out)
    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines()]
    assert payload["manual_compile_evidence_jsonl"] == {
        "path": str(out_path),
        "rows": 1,
        "statuses": ["deterministic_frontend_candidate"],
    }
    assert rows[0]["effect_id"] == "eff-frontend"
    assert rows[0]["manual_compile_status"] == "deterministic_frontend_candidate"
    assert rows[0]["manual_compile_rule_id"] == (
        "uk_manual_frontier_structural_sibling_insert_candidate"
    )
    assert rows[0]["source_pathology"] == "structural_sibling_insert_unsupported"
    assert rows[0]["lowering_rejections"] == [
        {"rule_id": "uk_effect_overlap_substitution_unlowered", "blocking": True},
    ]


def test_uk_candidates_manual_compile_evidence_jsonl_writes_empty_frontier(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    out_path = tmp_path / "manual" / "empty.jsonl"

    monkeypatch.setattr("lawvm.tools.uk_bench._load_run", lambda _label: [])

    uk_candidates.main(
        Namespace(
            label="empty",
            top=10,
            fast=False,
            effect_budget=None,
            residual_budget=None,
            score_mode="auto",
            residual_only=False,
            json=True,
            summary_only=False,
            min_year=None,
            max_year=None,
            types=None,
            db=str(db_path),
            manual_compile_evidence_jsonl=str(out_path),
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["manual_compile_evidence_jsonl"] == {
        "path": str(out_path),
        "rows": 0,
        "statuses": ["manual_compile_candidate"],
    }
    assert out_path.read_text(encoding="utf-8") == ""


def test_uk_candidates_manual_compile_evidence_jsonl_rejects_fast(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        uk_candidates.main(
            Namespace(
                label="demo",
                top=1,
                fast=True,
                effect_budget=None,
                residual_budget=None,
                score_mode="auto",
                residual_only=False,
                json=False,
                summary_only=False,
                min_year=None,
                max_year=None,
                types=None,
                db=None,
                manual_compile_evidence_jsonl=".tmp/manual.jsonl",
            )
        )

    assert excinfo.value.code == 2
    assert "requires archive-backed mode" in capsys.readouterr().err


def test_triage_rule_id_is_stable_for_known_statuses() -> None:
    assert _triage_rule_id("real residual frontier") == "uk_residual_claim_backed_by_candidate_overlap"
    assert _triage_rule_id("frontier prefilter only") == "uk_frontier_prefilter_only"
    assert _triage_rule_id("residual analysis budget skipped") == "uk_residual_analysis_budget_skipped"
    assert (
        _triage_rule_id("residual comparison source unavailable")
        == "uk_residual_analysis_source_unavailable"
    )
    assert (
        _triage_rule_id("malformed residual roots deferred")
        == "uk_residual_claim_deferred_malformed_eid_root"
    )
    assert (
        _triage_rule_id("residual branches include malformed roots")
        == "uk_residual_claim_partially_deferred_malformed_eid_root"
    )


def test_uk_candidates_fast_residual_only_requires_archive(capsys, tmp_path) -> None:
    args = Namespace(
        label="missing-run-is-not-loaded",
        top=5,
        fast=True,
        effect_budget=None,
        residual_budget=None,
        score_mode="auto",
        residual_only=True,
        json=False,
        min_year=None,
        max_year=None,
        types=None,
        db=str(tmp_path / "missing.farchive"),
    )

    with pytest.raises(SystemExit) as excinfo:
        uk_candidates.main(args)

    assert excinfo.value.code == 2
    assert "--fast --residual-only requires an archive DB" in capsys.readouterr().err


def test_uk_candidates_fast_residual_only_keeps_source_unavailable_rows(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    import farchive
    from lawvm.tools import uk_effects

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    bench_row = SimpleNamespace(
        statute_id="ukpga/2000/1",
        status="OK",
        year=2000,
        act_type="ukpga",
        replay_commencement_score=-1.0,
        replay_score=0.75,
        commencement_score=-1.0,
        score=0.7,
        n_commenced_eids=0,
        comparison_class="commensurable",
        n_enacted_eids=10,
        n_oracle_eids=12,
        n_effects=3,
        n_effect_feed_pages=2,
        n_effect_rows=1,
    )

    class FakeArchive:
        def __init__(self, _path: str) -> None:
            pass

        def __enter__(self) -> "FakeArchive":
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    class FakeEffect:
        applied = True

        def is_applicable_for_replay(self, *, applicability_mode: str) -> bool:
            assert applicability_mode == "effective_date_plus_feed_applied"
            return True

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr("lawvm.tools.uk_bench._load_run", lambda _label: [bench_row])
    monkeypatch.setattr(
        uk_legislation_effects,
        "load_effects_for_statute_from_archive",
        lambda *_args, **_kwargs: [FakeEffect()],
    )
    monkeypatch.setattr(
        uk_effects,
        "build_uk_effect_summary_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            enacted_ir=None,
            oracle_eids=set(),
            oracle_eid_map={},
            oracle_text_map={},
            enacted_missing=True,
            oracle_missing=False,
        ),
    )
    monkeypatch.setattr(uk_effects, "summarize_uk_effect", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        uk_candidates,
        "_summarize_effect_inventory",
        lambda _summaries, **_kwargs: {
            "source_counts": {},
            "compare_counts": {},
            "candidate_source_counts": {},
            "candidate_compare_counts": {},
            "non_candidate_source_counts": {},
            "non_candidate_compare_counts": {},
            "lowering_rejection_rule_counts": {},
            "blocking_lowering_rejection_rule_counts": {},
            "source_acquisition_rejection_rule_counts": {},
            "rows_with_source_acquisition_rejections": 0,
            "rows_with_blocking_lowering_rejections": 0,
            "inspected_effect_count": 1,
            "candidate_count": 1,
            "candidate_ops": 2,
            "candidate_summaries": [],
        },
    )

    uk_candidates.main(
        Namespace(
            label="demo",
            top=1,
            fast=True,
            effect_budget=None,
            residual_budget=None,
            score_mode="auto",
            residual_only=True,
            json=True,
            summary_only=False,
            min_year=None,
            max_year=None,
            types=None,
            db=str(db_path),
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["emitted_row_count"] == 1
    row = payload["rows"][0]
    assert row["status"] == "residual comparison source unavailable"
    assert row["triage_rule_id"] == "uk_residual_analysis_source_unavailable"
    assert row["residual_analysis_unavailable"] is True
    assert row["residual_analysis_unavailable_reason"] == "enacted_missing"
    assert row["residual_analysis_enacted_missing"] is True
    assert row["candidate_effect_count"] == 1
    assert row["candidate_op_count"] == 2


def test_uk_candidates_fast_residual_only_keeps_source_unavailable_without_candidates(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    import farchive
    from lawvm.tools import uk_effects

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    bench_row = SimpleNamespace(
        statute_id="ukpga/2000/1",
        status="OK",
        year=2000,
        act_type="ukpga",
        replay_commencement_score=-1.0,
        replay_score=0.75,
        commencement_score=-1.0,
        score=0.7,
        n_commenced_eids=0,
        comparison_class="commensurable",
        n_enacted_eids=10,
        n_oracle_eids=12,
        n_effects=3,
        n_effect_feed_pages=2,
        n_effect_rows=1,
    )

    class FakeArchive:
        def __init__(self, _path: str) -> None:
            pass

        def __enter__(self) -> "FakeArchive":
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr("lawvm.tools.uk_bench._load_run", lambda _label: [bench_row])
    monkeypatch.setattr(
        uk_legislation_effects,
        "load_effects_for_statute_from_archive",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        uk_effects,
        "build_uk_effect_summary_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            enacted_ir=None,
            oracle_eids=set(),
            oracle_eid_map={},
            oracle_text_map={},
            enacted_missing=True,
            oracle_missing=True,
        ),
    )
    monkeypatch.setattr(
        uk_candidates,
        "_summarize_effect_inventory",
        lambda _summaries, **_kwargs: {
            "source_counts": {},
            "compare_counts": {},
            "candidate_source_counts": {},
            "candidate_compare_counts": {},
            "non_candidate_source_counts": {},
            "non_candidate_compare_counts": {},
            "lowering_rejection_rule_counts": {},
            "blocking_lowering_rejection_rule_counts": {},
            "source_acquisition_rejection_rule_counts": {},
            "rows_with_source_acquisition_rejections": 0,
            "rows_with_blocking_lowering_rejections": 0,
            "inspected_effect_count": 0,
            "candidate_count": 0,
            "candidate_ops": 0,
            "candidate_summaries": [],
        },
    )

    uk_candidates.main(
        Namespace(
            label="demo",
            top=1,
            fast=True,
            effect_budget=None,
            residual_budget=None,
            score_mode="auto",
            residual_only=True,
            json=True,
            summary_only=False,
            min_year=None,
            max_year=None,
            types=None,
            db=str(db_path),
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["emitted_row_count"] == 1
    row = payload["rows"][0]
    assert row["status"] == "residual comparison source unavailable"
    assert row["triage_rule_id"] == "uk_residual_analysis_source_unavailable"
    assert row["residual_analysis_unavailable"] is True
    assert row["residual_analysis_unavailable_reason"] == "enacted_missing"
    assert row["candidate_effect_count"] == 0
    assert row["candidate_op_count"] == 0


def test_uk_candidates_fast_residual_only_keeps_budget_skipped_rows_without_candidates(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    import farchive
    from lawvm.tools import uk_effects

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    bench_row = SimpleNamespace(
        statute_id="ukpga/2000/1",
        status="OK",
        year=2000,
        act_type="ukpga",
        replay_commencement_score=-1.0,
        replay_score=0.75,
        commencement_score=-1.0,
        score=0.7,
        n_commenced_eids=0,
        comparison_class="commensurable",
        n_enacted_eids=10,
        n_oracle_eids=12,
        n_effects=3,
        n_effect_feed_pages=2,
        n_effect_rows=1,
    )

    class FakeArchive:
        def __init__(self, _path: str) -> None:
            pass

        def __enter__(self) -> "FakeArchive":
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr("lawvm.tools.uk_bench._load_run", lambda _label: [bench_row])
    monkeypatch.setattr(
        uk_legislation_effects,
        "load_effects_for_statute_from_archive",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        uk_effects,
        "build_uk_effect_summary_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            enacted_ir=object(),
            oracle_eids={"section-1"},
            oracle_eid_map={},
            oracle_text_map={},
            enacted_missing=False,
            oracle_missing=False,
        ),
    )
    monkeypatch.setattr(
        uk_candidates,
        "_summarize_effect_inventory",
        lambda _summaries, **_kwargs: {
            "source_counts": {},
            "compare_counts": {},
            "candidate_source_counts": {},
            "candidate_compare_counts": {},
            "non_candidate_source_counts": {},
            "non_candidate_compare_counts": {},
            "lowering_rejection_rule_counts": {},
            "blocking_lowering_rejection_rule_counts": {},
            "source_acquisition_rejection_rule_counts": {},
            "rows_with_source_acquisition_rejections": 0,
            "rows_with_blocking_lowering_rejections": 0,
            "inspected_effect_count": 0,
            "candidate_count": 0,
            "candidate_ops": 0,
            "candidate_summaries": [],
        },
    )

    uk_candidates.main(
        Namespace(
            label="demo",
            top=1,
            fast=True,
            effect_budget=None,
            residual_budget=0,
            score_mode="auto",
            residual_only=True,
            json=True,
            summary_only=False,
            min_year=None,
            max_year=None,
            types=None,
            db=str(db_path),
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["emitted_row_count"] == 1
    assert payload["summary"]["rows_with_residual_analysis_skipped"] == 1
    row = payload["rows"][0]
    assert row["status"] == "residual analysis budget skipped"
    assert row["triage_rule_id"] == "uk_residual_analysis_budget_skipped"
    assert row["residual_analysis_skipped"] is True
    assert row["residual_analysis_unavailable"] is False
    assert row["candidate_effect_count"] == 0
    assert row["candidate_op_count"] == 0


def test_uk_candidates_residual_analysis_uses_saved_replay_regime(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    import farchive
    from lawvm.tools import uk_effect
    from lawvm.tools import uk_effects
    from lawvm.uk_legislation import uk_amendment_replay
    from lawvm.tools.uk_effects import _EffectSummary

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    bench_row = SimpleNamespace(
        statute_id="ukpga/2000/1",
        status="OK",
        year=2000,
        act_type="ukpga",
        replay_commencement_score=-1.0,
        replay_score=0.75,
        commencement_score=-1.0,
        score=0.7,
        n_commenced_eids=0,
        comparison_class="commensurable",
        n_enacted_eids=10,
        n_oracle_eids=12,
        n_effects=3,
        n_effect_feed_pages=2,
        n_effect_rows=1,
        uk_metadata_backfill_enabled=False,
        uk_oracle_alignment_enabled=False,
        uk_metadata_only_effects_enabled=False,
        uk_applicability_mode="effective_date_only",
        uk_authority_mode="source_text_only",
    )
    seen: dict[str, object] = {}
    effect_modes: list[str] = []

    class FakeArchive:
        def __init__(self, _path: str) -> None:
            pass

        def __enter__(self) -> "FakeArchive":
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    class FakeEffect:
        applied = False

        def is_applicable_for_replay(self, *, applicability_mode: str) -> bool:
            effect_modes.append(applicability_mode)
            return applicability_mode == "effective_date_only"

    class FakePipeline:
        def __init__(self, _repo_root: Path) -> None:
            pass

        def compile_ops_for_statute(self, statute_id: str, **kwargs: object) -> list[object]:
            seen["compile_statute_id"] = statute_id
            seen["allow_metadata_backfill"] = kwargs["allow_metadata_backfill"]
            seen["allow_metadata_only_effects"] = kwargs["allow_metadata_only_effects"]
            seen["applicability_mode"] = kwargs["applicability_mode"]
            seen["authority_mode"] = kwargs["authority_mode"]
            effect_diagnostics = cast(list[dict[str, object]], kwargs["effect_diagnostics_out"])
            lowering_rejections = cast(list[dict[str, object]], kwargs["lowering_rejections_out"])
            authority_rejections = cast(list[dict[str, object]], kwargs["authority_rejections_out"])
            effect_diagnostics.append(
                {
                    "rule_id": "uk_effect_source_pathology_classified",
                    "source_pathology": "missing_extracted_source",
                    "blocking": False,
                }
            )
            effect_diagnostics.append(
                {
                    "rule_id": "uk_affecting_act_xml_missing_rejected",
                    "phase": "acquisition",
                    "blocking": True,
                }
            )
            lowering_rejections.append({"rule_id": "uk_effect_payload_missing"})
            authority_rejections.append({"rule_id": "uk_effect_authority_filter_rejected"})
            return ["op"]

        def apply_ops(self, enacted_ir: object, ops: list[object], **kwargs: object) -> object:
            seen["apply_enacted_ir"] = enacted_ir
            seen["apply_ops"] = ops
            seen["allow_oracle_alignment"] = kwargs["allow_oracle_alignment"]
            return SimpleNamespace(kind="replayed")

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr("lawvm.tools.uk_bench._load_run", lambda _label: [bench_row])
    monkeypatch.setattr(
        uk_legislation_effects,
        "load_effects_for_statute_from_archive",
        lambda *_args, **_kwargs: [FakeEffect()],
    )
    monkeypatch.setattr(uk_amendment_replay, "UKReplayPipeline", FakePipeline)
    monkeypatch.setattr(uk_effect, "_collect_statute_eids", lambda _statute: {"section-3"})
    monkeypatch.setattr(
        uk_effects,
        "build_uk_effect_summary_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            enacted_ir=SimpleNamespace(kind="enacted"),
            oracle_eids={"section-2"},
            oracle_eid_map={},
            oracle_text_map={},
            enacted_missing=False,
            oracle_missing=False,
        ),
    )

    def fake_summarize(effect: object, **kwargs: object) -> _EffectSummary:
        seen["summary_applicability_mode"] = kwargs["applicability_mode"]
        return _EffectSummary(
            source_pathology="",
            compare_shape="commensurable",
            n_ops=1,
            candidate=True,
            resolver_eids=("section-2",),
            lowering_rejections=(),
            effect_id="eff-1",
            replay_applicable=True,
            structural_for_replay=True,
            applicability_mode=str(kwargs["applicability_mode"]),
        )

    monkeypatch.setattr(uk_effects, "summarize_uk_effect", fake_summarize)

    uk_candidates.main(
        Namespace(
            label="demo",
            top=1,
            fast=True,
            effect_budget=None,
            residual_budget=None,
            score_mode="auto",
            residual_only=True,
            json=True,
            summary_only=False,
            min_year=None,
            max_year=None,
            types=None,
            db=str(db_path),
        )
    )

    payload = json.loads(capsys.readouterr().out)
    row = payload["rows"][0]
    assert row["status"] == "real residual frontier"
    assert row["residual_analysis_unavailable"] is False
    assert row["residual_candidate_effect_count"] == 1
    assert effect_modes == ["effective_date_only"]
    assert seen["summary_applicability_mode"] == "effective_date_only"
    assert seen["allow_metadata_backfill"] is False
    assert seen["allow_metadata_only_effects"] is False
    assert seen["applicability_mode"] == "effective_date_only"
    assert seen["authority_mode"] == "source_text_only"
    assert seen["allow_oracle_alignment"] is False
    assert row["residual_compile_observation_count"] == 4
    assert row["residual_compile_observation_rule_counts"] == {
        "uk_affecting_act_xml_missing_rejected": 1,
        "uk_effect_authority_filter_rejected": 1,
        "uk_effect_payload_missing": 1,
        "uk_effect_source_pathology_classified": 1,
    }
    assert row["residual_compile_rejection_count"] == 3
    assert row["residual_compile_rejection_rule_counts"] == {
        "uk_affecting_act_xml_missing_rejected": 1,
        "uk_effect_authority_filter_rejected": 1,
        "uk_effect_payload_missing": 1,
    }
    assert row["residual_compile_observations"]["effect_source_pathology"][0][
        "source_pathology"
    ] == "missing_extracted_source"
    assert row["residual_compile_rejections"]["source_acquisition"][0]["rule_id"] == (
        "uk_affecting_act_xml_missing_rejected"
    )


@pytest.mark.parametrize(
    ("phase", "expected_reason", "expected_rule"),
    [
        ("compile", "compile_exception:ValueError", "uk_residual_compile_exception_recorded"),
        ("apply", "apply_exception:RuntimeError", "uk_residual_apply_exception_recorded"),
    ],
)
def test_uk_candidates_residual_analysis_exceptions_are_row_local(
    monkeypatch,
    tmp_path: Path,
    capsys,
    phase: str,
    expected_reason: str,
    expected_rule: str,
) -> None:
    import farchive
    from lawvm.tools import uk_effect
    from lawvm.tools import uk_effects
    from lawvm.tools.uk_effects import _EffectSummary
    from lawvm.uk_legislation import uk_amendment_replay

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    bench_row = SimpleNamespace(
        statute_id="ukpga/2000/1",
        status="OK",
        year=2000,
        act_type="ukpga",
        replay_commencement_score=-1.0,
        replay_score=0.75,
        commencement_score=-1.0,
        score=0.7,
        n_commenced_eids=0,
        comparison_class="commensurable",
        n_enacted_eids=10,
        n_oracle_eids=12,
        n_effects=1,
        n_effect_feed_pages=1,
        n_effect_rows=1,
        uk_metadata_backfill_enabled=False,
        uk_oracle_alignment_enabled=False,
        uk_applicability_mode="effective_date_only",
        uk_authority_mode="source_text_only",
    )

    class FakeArchive:
        def __init__(self, _path: str) -> None:
            pass

        def __enter__(self) -> "FakeArchive":
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    class FakeEffect:
        applied = False

        def is_applicable_for_replay(self, *, applicability_mode: str) -> bool:
            return applicability_mode == "effective_date_only"

    class FakePipeline:
        def __init__(self, _repo_root: Path) -> None:
            pass

        def compile_ops_for_statute(self, _statute_id: str, **_kwargs: object) -> list[object]:
            if phase == "compile":
                raise ValueError("compile boom")
            return ["op"]

        def apply_ops(self, _enacted_ir: object, _ops: list[object], **_kwargs: object) -> object:
            if phase == "apply":
                raise RuntimeError("apply boom")
            return SimpleNamespace(kind="replayed")

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr("lawvm.tools.uk_bench._load_run", lambda _label: [bench_row])
    monkeypatch.setattr(
        uk_legislation_effects,
        "load_effects_for_statute_from_archive",
        lambda *_args, **_kwargs: [FakeEffect()],
    )
    monkeypatch.setattr(uk_amendment_replay, "UKReplayPipeline", FakePipeline)
    monkeypatch.setattr(uk_effect, "_collect_statute_eids", lambda _statute: {"section-3"})
    monkeypatch.setattr(
        uk_effects,
        "build_uk_effect_summary_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            enacted_ir=SimpleNamespace(kind="enacted"),
            oracle_eids={"section-2"},
            oracle_eid_map={},
            oracle_text_map={},
            enacted_missing=False,
            oracle_missing=False,
        ),
    )

    def fake_summarize(_effect: object, **kwargs: object) -> _EffectSummary:
        return _EffectSummary(
            source_pathology="",
            compare_shape="commensurable",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(),
            effect_id="eff-1",
            replay_applicable=True,
            structural_for_replay=False,
            applicability_mode=str(kwargs["applicability_mode"]),
        )

    monkeypatch.setattr(uk_effects, "summarize_uk_effect", fake_summarize)

    uk_candidates.main(
        Namespace(
            label="demo",
            top=1,
            fast=True,
            effect_budget=None,
            residual_budget=None,
            score_mode="auto",
            residual_only=True,
            json=True,
            summary_only=False,
            min_year=None,
            max_year=None,
            types=None,
            db=str(db_path),
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["emitted_row_count"] == 1
    assert payload["summary"]["rows_with_residual_analysis_unavailable"] == 1
    assert payload["summary"]["residual_compile_rejection_rule_counts"] == {expected_rule: 1}
    row = payload["rows"][0]
    assert row["status"] == "residual comparison execution unavailable"
    assert row["triage_rule_id"] == "uk_residual_analysis_execution_unavailable"
    assert row["residual_analysis_unavailable"] is True
    assert row["residual_analysis_unavailable_reason"] == expected_reason
    assert row["candidate_effect_count"] == 0
    assert row["residual_compile_observation_rule_counts"] == {expected_rule: 1}
    assert row["residual_compile_rejection_rule_counts"] == {expected_rule: 1}
    execution_observations = row["residual_compile_observations"]["execution"]
    assert execution_observations[0]["rule_id"] == expected_rule
    assert execution_observations[0]["blocking"] is True
    assert row["residual_compile_rejections"]["execution"][0]["rule_id"] == expected_rule


def test_uk_candidates_summary_only_requires_json(capsys) -> None:
    args = Namespace(
        label="unused",
        top=5,
        fast=True,
        effect_budget=None,
        residual_budget=None,
        score_mode="auto",
        residual_only=False,
        json=False,
        summary_only=True,
        min_year=None,
        max_year=None,
        types=None,
        db=None,
    )

    with pytest.raises(SystemExit) as excinfo:
        uk_candidates.main(args)

    assert excinfo.value.code == 2
    assert "--summary-only requires --json" in capsys.readouterr().err


def test_uk_candidates_rejects_nonpositive_effect_budget(capsys) -> None:
    args = Namespace(
        label="unused",
        top=5,
        fast=True,
        effect_budget=0,
        residual_budget=None,
        score_mode="auto",
        residual_only=False,
        json=True,
        summary_only=False,
        min_year=None,
        max_year=None,
        types=None,
        db=None,
    )

    with pytest.raises(SystemExit) as excinfo:
        uk_candidates.main(args)

    assert excinfo.value.code == 2
    assert "--effect-budget must be a positive integer" in capsys.readouterr().err


def test_uk_candidates_rejects_negative_top(capsys) -> None:
    args = Namespace(
        label="unused",
        top=-1,
        fast=True,
        effect_budget=None,
        residual_budget=None,
        score_mode="auto",
        residual_only=False,
        json=True,
        summary_only=False,
        min_year=None,
        max_year=None,
        types=None,
        db=None,
    )

    with pytest.raises(SystemExit) as excinfo:
        uk_candidates.main(args)

    assert excinfo.value.code == 2
    assert "--top must be zero or a positive integer" in capsys.readouterr().err


def test_uk_candidates_rejects_negative_residual_budget(capsys) -> None:
    args = Namespace(
        label="unused",
        top=5,
        fast=True,
        effect_budget=None,
        residual_budget=-1,
        score_mode="auto",
        residual_only=False,
        json=True,
        summary_only=False,
        min_year=None,
        max_year=None,
        types=None,
        db=None,
    )

    with pytest.raises(SystemExit) as excinfo:
        uk_candidates.main(args)

    assert excinfo.value.code == 2
    assert "--residual-budget must be zero or a positive integer" in capsys.readouterr().err


def test_uk_candidates_fast_json_filters_replay_adjudication_samples(
    monkeypatch,
    capsys,
) -> None:
    rows = [
        SimpleNamespace(
            statute_id="ukpga/2000/1",
            status="OK",
            year=2000,
            act_type="ukpga",
            score=0.8,
            replay_score=0.75,
            commencement_score=-1.0,
            replay_commencement_score=-1.0,
            n_commenced_eids=0,
            comparison_class="commensurable",
            n_effects=2,
            n_effect_rows=2,
            n_effect_feed_pages=1,
            uk_source_purity_lane="source_backed_effects_assisted",
            uk_source_semantics_clean=True,
            uk_source_first_candidate=True,
            uk_source_first_candidate_reasons=(),
            replay_adjudication_count=3,
            replay_adjudication_kind_counts={
                "uk_replay_text_match_missing": 2,
                "uk_replay_repealed_target_gap": 1,
            },
            uk_residual_claim_tier="UNRESOLVED",
            uk_residual_claim_kind="uk_text_match_missing_mixed_residual_eids",
            uk_residual_claim_comparison_class="commensurable",
            uk_residual_claim_core_comparison=True,
            uk_residual_only_in_replayed_count=4,
            uk_residual_only_in_oracle_count=5,
            uk_residual_section_claim_count=0,
            uk_residual_section_claim_emitted=False,
            replay_adjudications=(
                {
                    "kind": "uk_replay_text_match_missing",
                    "message": "text match missing",
                    "source_statute": "ukpga/2001/2",
                    "op_id": "op-1",
                    "detail": {
                        "target": "section:1/subsection:2",
                        "target_granularity": "subsection",
                        "text_match": "old words",
                        "replacement_text": "new words",
                    },
                },
                {
                    "kind": "uk_replay_text_match_missing",
                    "message": "second missing",
                    "source_statute": "ukpga/2002/3",
                    "op_id": "op-2",
                    "detail": {"target": "section:3"},
                },
                {
                    "kind": "uk_replay_repealed_target_gap",
                    "message": "not requested",
                    "source_statute": "ukpga/2003/4",
                    "op_id": "op-3",
                    "detail": {"target": "section:4"},
                },
            ),
        ),
        SimpleNamespace(
            statute_id="ukpga/2000/2",
            status="OK",
            year=2000,
            act_type="ukpga",
            score=0.7,
            replay_score=0.7,
            commencement_score=-1.0,
            replay_commencement_score=-1.0,
            n_commenced_eids=0,
            comparison_class="commensurable",
            uk_source_purity_lane="source_backed_with_oracle_adapter",
            uk_source_semantics_clean=False,
            uk_source_first_candidate=False,
            uk_source_first_candidate_reasons=("oracle_alignment_adapter_active",),
            replay_adjudication_count=1,
            replay_adjudication_kind_counts={"uk_replay_repealed_target_gap": 1},
            uk_residual_claim_tier="UNRESOLVED",
            uk_residual_claim_kind="uk_repealed_target_gap",
            uk_residual_claim_comparison_class="commensurable",
            uk_residual_claim_core_comparison=True,
            uk_residual_only_in_replayed_count=1,
            uk_residual_only_in_oracle_count=0,
            uk_residual_section_claim_count=0,
            uk_residual_section_claim_emitted=False,
            replay_adjudications=(
                {
                    "kind": "uk_replay_repealed_target_gap",
                    "message": "not requested",
                    "source_statute": "ukpga/2003/4",
                    "op_id": "op-4",
                    "detail": {"target": "section:4"},
                },
            ),
        ),
    ]
    monkeypatch.setattr("lawvm.tools.uk_bench._load_run", lambda label: rows)

    uk_candidates.main(
        Namespace(
            label="demo",
            top=10,
            fast=True,
            effect_budget=None,
            residual_budget=None,
            score_mode="auto",
            residual_only=False,
            json=True,
            summary_only=False,
            min_year=None,
            max_year=None,
            types=None,
            db=None,
            replay_adjudication_kind=["uk_replay_text_match_missing"],
            replay_adjudication_sample_limit=1,
        )
    )

    payload = json.loads(capsys.readouterr().out)

    assert payload["filters"]["replay_adjudication_kinds"] == [
        "uk_replay_text_match_missing"
    ]
    assert payload["summary"]["pre_replay_adjudication_filter_frontier_count"] == 2
    assert payload["summary"]["replay_adjudication_filter_excluded_count"] == 1
    assert payload["summary"]["matched_frontier_count"] == 1
    assert payload["summary"]["replay_adjudication_count"] == 3
    assert payload["summary"]["replay_adjudication_kind_counts"] == {
        "uk_replay_repealed_target_gap": 1,
        "uk_replay_text_match_missing": 2,
    }
    assert payload["summary"]["replay_adjudication_bucket_counts"] == {
        "source_shape": 1,
        "text_surface": 2,
    }
    assert payload["summary"]["uk_residual_claim_tier_counts"] == {
        "UNRESOLVED": 1,
    }
    assert payload["summary"]["uk_residual_claim_kind_counts"] == {
        "uk_text_match_missing_mixed_residual_eids": 1,
    }
    assert payload["summary"]["residual_claim_only_in_replayed_count"] == 4
    assert payload["summary"]["residual_claim_only_in_oracle_count"] == 5
    assert payload["summary"]["uk_source_purity_lane_counts"] == {
        "source_backed_effects_assisted": 1,
    }
    assert payload["summary"]["rows_with_source_semantics_clean"] == 1
    assert payload["summary"]["rows_with_source_first_candidate"] == 1
    assert payload["summary"]["uk_source_first_candidate_reason_counts"] == {}
    assert payload["summary"]["replay_adjudication_sample_count"] == 1
    assert payload["summary"]["replay_adjudication_samples_omitted"] == 1
    assert [row["statute_id"] for row in payload["rows"]] == ["ukpga/2000/1"]
    row = payload["rows"][0]
    assert row["replay_adjudication_bucket_counts"] == {
        "source_shape": 1,
        "text_surface": 2,
    }
    assert row["uk_replay_regime_claim"] == {
        "source_purity_lane": "source_backed_effects_assisted",
        "source_semantics_clean": True,
        "source_first_candidate": True,
        "source_first_candidate_reasons": [],
    }
    assert row["uk_residual_claim"] == {
        "selected_tier": "UNRESOLVED",
        "selected_kind": "uk_text_match_missing_mixed_residual_eids",
        "comparison_class": "commensurable",
        "core_comparison": True,
        "only_in_replayed_count": 4,
        "only_in_oracle_count": 5,
        "section_claim_count": 0,
        "section_claim_emitted": False,
    }
    assert row["replay_adjudication_samples_omitted"] == 1
    assert row["replay_adjudication_samples"] == [
        {
            "kind": "uk_replay_text_match_missing",
            "message": "text match missing",
            "source_statute": "ukpga/2001/2",
            "op_id": "op-1",
            "target": "section:1/subsection:2",
            "target_granularity": "subsection",
            "text_match": "old words",
            "replacement_text": "new words",
            "source_shape": "",
        }
    ]


def test_uk_candidates_fast_json_exposes_duplication_warning_sample_detail(
    monkeypatch,
    capsys,
) -> None:
    rows = [
        SimpleNamespace(
            statute_id="ukpga/2000/1",
            status="OK",
            year=2000,
            act_type="ukpga",
            score=0.8,
            replay_score=0.8,
            commencement_score=-1.0,
            replay_commencement_score=-1.0,
            n_commenced_eids=0,
            comparison_class="commensurable",
            n_effects=1,
            n_effect_rows=1,
            n_effect_feed_pages=1,
            uk_source_purity_lane="source_backed_effects_assisted",
            uk_source_semantics_clean=True,
            uk_source_first_candidate=True,
            uk_source_first_candidate_reasons=(),
            replay_adjudication_count=1,
            replay_adjudication_kind_counts={"text_duplication_warning": 1},
            uk_residual_claim_tier="UNRESOLVED",
            uk_residual_claim_kind="uk_mixed_residual_eids",
            uk_residual_claim_comparison_class="commensurable",
            uk_residual_claim_core_comparison=True,
            uk_residual_only_in_replayed_count=0,
            uk_residual_only_in_oracle_count=0,
            uk_residual_section_claim_count=0,
            uk_residual_section_claim_emitted=False,
            replay_adjudications=(
                {
                    "kind": "text_duplication_warning",
                    "message": "Replay output contains a suspicious duplicated text tract.",
                    "source_statute": "ukpga/2000/1",
                    "op_id": "",
                    "detail": {
                        "blocking": False,
                        "kind": "duplicate_suffix_text",
                        "path": "schedule/paragraph:1",
                        "left": "paragraph:1",
                        "right": "paragraph:2",
                        "shared_token_count": 19,
                        "excerpt": (
                            "duplicated words in the replay output that identify "
                            "the duplicated tract"
                        ),
                    },
                },
            ),
        ),
    ]
    monkeypatch.setattr("lawvm.tools.uk_bench._load_run", lambda label: rows)

    uk_candidates.main(
        Namespace(
            label="demo",
            top=10,
            fast=True,
            effect_budget=None,
            residual_budget=None,
            score_mode="auto",
            residual_only=False,
            json=True,
            summary_only=False,
            min_year=None,
            max_year=None,
            types=None,
            db=None,
            replay_adjudication_kind=["text_duplication_warning"],
            replay_adjudication_sample_limit=1,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    sample = payload["rows"][0]["replay_adjudication_samples"][0]

    assert sample["kind"] == "text_duplication_warning"
    assert sample["duplicate_kind"] == "duplicate_suffix_text"
    assert sample["path"] == "schedule/paragraph:1"
    assert "root" not in sample
    assert sample["left"] == "paragraph:1"
    assert sample["right"] == "paragraph:2"
    assert sample["shared_token_count"] == "19"
    assert sample["excerpt"].startswith("duplicated words in the replay output")


def test_uk_candidates_fast_json_exports_replay_adjudication_evidence_jsonl(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    out_path = tmp_path / "replay" / "adjudications.jsonl"
    rows = [
        SimpleNamespace(
            statute_id="ukpga/2000/1",
            status="OK",
            year=2000,
            act_type="ukpga",
            score=0.8,
            replay_score=0.8,
            commencement_score=-1.0,
            replay_commencement_score=-1.0,
            n_commenced_eids=0,
            comparison_class="commensurable",
            n_enacted_eids=10,
            n_oracle_eids=12,
            n_effects=1,
            n_effect_rows=1,
            n_effect_feed_pages=1,
            enacted_source_status="available",
            enacted_source_size=123,
            enacted_source_sha256="enacted-sha",
            enacted_source_url="https://example.test/enacted.xml",
            oracle_source_status="available",
            oracle_source_size=456,
            oracle_source_sha256="oracle-sha",
            oracle_source_url="https://example.test/current.xml",
            uk_metadata_backfill_enabled=False,
            uk_oracle_alignment_enabled=False,
            uk_metadata_only_effects_enabled=False,
            uk_applicability_mode="effective_date_only",
            uk_authority_mode="source_text_only",
            uk_source_purity_lane="source_backed_effects_assisted",
            uk_source_semantics_clean=True,
            uk_source_first_candidate=True,
            uk_source_first_candidate_reasons=(),
            replay_adjudication_count=2,
            replay_adjudication_kind_counts={
                "text_duplication_warning": 1,
                "uk_replay_target_not_found": 1,
            },
            uk_residual_claim_tier="UNRESOLVED",
            uk_residual_claim_kind="uk_mixed_residual_eids",
            uk_residual_claim_comparison_class="commensurable",
            uk_residual_claim_core_comparison=True,
            uk_residual_only_in_replayed_count=0,
            uk_residual_only_in_oracle_count=0,
            uk_residual_section_claim_count=0,
            uk_residual_section_claim_emitted=False,
            replay_adjudications=(
                {
                    "kind": "text_duplication_warning",
                    "message": "Replay output contains a suspicious duplicated text tract.",
                    "source_statute": "ukpga/2000/1",
                    "op_id": "",
                    "detail": {
                        "blocking": False,
                        "kind": "duplicate_suffix_text",
                        "path": "body/section:1",
                        "left": "subsection:1",
                        "right": "subsection:2",
                        "shared_token_count": 19,
                        "excerpt": "duplicated words in replay output",
                    },
                },
                {
                    "kind": "uk_replay_target_not_found",
                    "message": "target not found",
                    "source_statute": "ukpga/2001/2",
                    "op_id": "op-1",
                    "detail": {"blocking": True, "target": "section:99"},
                },
            ),
        ),
    ]
    monkeypatch.setattr("lawvm.tools.uk_bench._load_run", lambda label: rows)

    uk_candidates.main(
        Namespace(
            label="demo",
            top=10,
            fast=True,
            effect_budget=None,
            residual_budget=None,
            score_mode="auto",
            residual_only=False,
            json=True,
            summary_only=False,
            min_year=None,
            max_year=None,
            types=None,
            db=None,
            replay_adjudication_kind=["text_duplication_warning"],
            replay_adjudication_sample_limit=1,
            replay_adjudication_evidence_jsonl=str(out_path),
        )
    )

    payload = json.loads(capsys.readouterr().out)
    evidence_rows = [
        json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines()
    ]

    assert payload["replay_adjudication_evidence_jsonl"] == {
        "path": str(out_path),
        "rows": 1,
        "kinds": ["text_duplication_warning"],
    }
    assert evidence_rows[0]["schema"] == "lawvm.uk_replay_adjudication_frontier.v1"
    assert evidence_rows[0]["rule_id"] == "uk_replay_adjudication_frontier_workqueue"
    assert evidence_rows[0]["work_item_kind"] == "replay_adjudication_review"
    assert evidence_rows[0]["claim_status"] == "unresolved_work_item"
    assert evidence_rows[0]["validator_status"] == "not_validated"
    assert evidence_rows[0]["work_item_id"].startswith("uk-replay-adjudication-")
    assert evidence_rows[0]["bench_label"] == "demo"
    assert evidence_rows[0]["statute_id"] == "ukpga/2000/1"
    assert evidence_rows[0]["adjudication_kind"] == "text_duplication_warning"
    assert evidence_rows[0]["adjudication_bucket"] == "nonblocking_observation"
    assert evidence_rows[0]["blocking"] is False
    assert evidence_rows[0]["detail"]["path"] == "body/section:1"
    assert evidence_rows[0]["uk_replay_regime"] == {
        "allow_metadata_backfill": False,
        "allow_metadata_only_effects": False,
        "allow_oracle_alignment": False,
        "applicability_mode": "effective_date_only",
        "authority_mode": "source_text_only",
    }
    assert evidence_rows[0]["enacted_source"]["sha256"] == "enacted-sha"
    assert evidence_rows[0]["oracle_source"]["sha256"] == "oracle-sha"


def test_uk_candidates_fast_json_exports_residual_claim_evidence_jsonl(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    out_path = tmp_path / "residual" / "claims.jsonl"
    rows = [
        SimpleNamespace(
            statute_id="asc/2024/6",
            status="OK",
            year=2024,
            act_type="asc",
            score=0.99,
            replay_score=0.877315,
            commencement_score=-1.0,
            replay_commencement_score=0.996951,
            n_commenced_eids=10,
            comparison_class="commensurable",
            n_enacted_eids=10,
            n_oracle_eids=11,
            n_effects=2,
            n_effect_rows=2,
            n_effect_feed_pages=1,
            enacted_source_status="available",
            enacted_source_size=123,
            enacted_source_sha256="enacted-sha",
            enacted_source_url="https://example.test/asc/2024/6/enacted.xml",
            oracle_source_status="available",
            oracle_source_size=456,
            oracle_source_sha256="oracle-sha",
            oracle_source_url="https://example.test/asc/2024/6/current.xml",
            uk_metadata_backfill_enabled=False,
            uk_oracle_alignment_enabled=False,
            uk_metadata_only_effects_enabled=False,
            uk_applicability_mode="effective_date_only",
            uk_authority_mode="source_text_only",
            uk_source_purity_lane="source_backed_effects_assisted",
            uk_source_semantics_clean=True,
            uk_source_first_candidate=True,
            uk_source_first_candidate_reasons=(),
            replay_adjudication_count=0,
            replay_adjudication_kind_counts={},
            replay_adjudications=(),
            uk_residual_claim_tier="UNRESOLVED",
            uk_residual_claim_kind="uk_source_backed_renumber_oracle_branch_mixed_residual_eids",
            uk_residual_claim_comparison_class="commensurable",
            uk_residual_claim_core_comparison=True,
            uk_residual_only_in_replayed_count=53,
            uk_residual_only_in_oracle_count=1,
            uk_residual_section_claim_count=0,
            uk_residual_section_claim_emitted=False,
        ),
        SimpleNamespace(
            statute_id="ukpga/2000/1",
            status="OK",
            year=2000,
            act_type="ukpga",
            score=0.9,
            replay_score=0.9,
            commencement_score=-1.0,
            replay_commencement_score=-1.0,
            n_commenced_eids=0,
            comparison_class="commensurable",
            uk_source_purity_lane="unknown",
            uk_source_semantics_clean=False,
            uk_source_first_candidate=False,
            uk_source_first_candidate_reasons=(),
            replay_adjudication_count=0,
            replay_adjudication_kind_counts={},
            replay_adjudications=(),
            uk_residual_claim_tier="UNRESOLVED",
            uk_residual_claim_kind="no_strong_claim",
            uk_residual_claim_comparison_class="commensurable",
            uk_residual_claim_core_comparison=True,
            uk_residual_only_in_replayed_count=0,
            uk_residual_only_in_oracle_count=0,
            uk_residual_section_claim_count=0,
            uk_residual_section_claim_emitted=False,
        ),
    ]
    monkeypatch.setattr("lawvm.tools.uk_bench._load_run", lambda label: rows)

    uk_candidates.main(
        Namespace(
            label="demo",
            top=10,
            fast=True,
            effect_budget=None,
            residual_budget=None,
            score_mode="auto",
            residual_only=False,
            json=True,
            summary_only=False,
            min_year=None,
            max_year=None,
            types=None,
            db=None,
            residual_claim_evidence_jsonl=str(out_path),
        )
    )

    payload = json.loads(capsys.readouterr().out)
    evidence_rows = [
        json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines()
    ]

    assert payload["residual_claim_evidence_jsonl"] == {
        "path": str(out_path),
        "rows": 1,
    }
    assert evidence_rows[0]["schema"] == "lawvm.uk_residual_claim_frontier.v1"
    assert evidence_rows[0]["rule_id"] == "uk_residual_claim_frontier_workqueue"
    assert evidence_rows[0]["work_item_kind"] == "residual_claim_review"
    assert evidence_rows[0]["claim_status"] == "UNRESOLVED"
    assert evidence_rows[0]["claim_kind"] == (
        "uk_source_backed_renumber_oracle_branch_mixed_residual_eids"
    )
    assert evidence_rows[0]["validator_status"] == "not_validated"
    assert evidence_rows[0]["work_item_id"].startswith("uk-residual-claim-")
    assert evidence_rows[0]["bench_label"] == "demo"
    assert evidence_rows[0]["statute_id"] == "asc/2024/6"
    assert evidence_rows[0]["uk_residual_claim"]["only_in_replayed_count"] == 53
    assert evidence_rows[0]["uk_residual_claim"]["only_in_oracle_count"] == 1
    assert evidence_rows[0]["uk_replay_regime"] == {
        "allow_metadata_backfill": False,
        "allow_metadata_only_effects": False,
        "allow_oracle_alignment": False,
        "applicability_mode": "effective_date_only",
        "authority_mode": "source_text_only",
    }
    assert evidence_rows[0]["enacted_source"]["sha256"] == "enacted-sha"
    assert evidence_rows[0]["oracle_source"]["sha256"] == "oracle-sha"


def test_uk_candidates_fast_json_infers_body_root_for_old_duplication_samples(
    monkeypatch,
    capsys,
) -> None:
    rows = [
        SimpleNamespace(
            statute_id="ukpga/2000/1",
            status="OK",
            year=2000,
            act_type="ukpga",
            score=0.8,
            replay_score=0.8,
            commencement_score=-1.0,
            replay_commencement_score=-1.0,
            n_commenced_eids=0,
            comparison_class="commensurable",
            n_effects=1,
            n_effect_rows=1,
            n_effect_feed_pages=1,
            uk_source_purity_lane="source_backed_effects_assisted",
            uk_source_semantics_clean=True,
            uk_source_first_candidate=True,
            uk_source_first_candidate_reasons=(),
            replay_adjudication_count=1,
            replay_adjudication_kind_counts={"text_duplication_warning": 1},
            uk_residual_claim_tier="UNRESOLVED",
            uk_residual_claim_kind="uk_mixed_residual_eids",
            uk_residual_claim_comparison_class="commensurable",
            uk_residual_claim_core_comparison=True,
            uk_residual_only_in_replayed_count=0,
            uk_residual_only_in_oracle_count=0,
            uk_residual_section_claim_count=0,
            uk_residual_section_claim_emitted=False,
            replay_adjudications=(
                {
                    "kind": "text_duplication_warning",
                    "message": "Replay output contains a suspicious duplicated text tract.",
                    "source_statute": "ukpga/2000/1",
                    "op_id": "",
                    "detail": {
                        "blocking": False,
                        "kind": "duplicate_suffix_text",
                        "path": "body/part:Part 1/section:1",
                        "left": "subsection:1",
                        "right": "subsection:2",
                        "shared_token_count": 19,
                    },
                },
            ),
        ),
    ]
    monkeypatch.setattr("lawvm.tools.uk_bench._load_run", lambda label: rows)

    uk_candidates.main(
        Namespace(
            label="demo",
            top=10,
            fast=True,
            effect_budget=None,
            residual_budget=None,
            score_mode="auto",
            residual_only=False,
            json=True,
            summary_only=False,
            min_year=None,
            max_year=None,
            types=None,
            db=None,
            replay_adjudication_kind=["text_duplication_warning"],
            replay_adjudication_sample_limit=1,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    sample = payload["rows"][0]["replay_adjudication_samples"][0]

    assert sample["path"] == "body/part:Part 1/section:1"
    assert sample["root"] == "body"


def test_format_replay_adjudication_sample_includes_duplication_context() -> None:
    rendered = uk_candidates._format_replay_adjudication_sample(
        {
            "kind": "text_duplication_warning",
            "source_statute": "ukpga/2000/1",
            "duplicate_kind": "duplicate_suffix_text",
            "path": "schedule/paragraph:1",
            "root": "schedule:SCHEDULE 1",
            "left": "paragraph:1",
            "right": "paragraph:2",
            "shared_token_count": "19",
            "excerpt": "duplicated words in replay output",
        }
    )

    assert "kind=text_duplication_warning" in rendered
    assert "duplicate_kind=duplicate_suffix_text" in rendered
    assert "path=schedule/paragraph:1" in rendered
    assert "root=schedule:SCHEDULE 1" in rendered
    assert "left=paragraph:1" in rendered
    assert "right=paragraph:2" in rendered
    assert "shared_token_count=19" in rendered
    assert "excerpt=duplicated words in replay output" in rendered
