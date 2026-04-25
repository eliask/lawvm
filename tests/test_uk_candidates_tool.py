from __future__ import annotations

from types import SimpleNamespace

from lawvm.tools.uk_candidates import (
    _candidate_root_hits,
    _collect_residual_roots,
    _eid_branch_root,
    _effect_overlaps_residual,
    _residual_candidate_inventory,
    _filtered_frontier,
    _frontier_status,
    _effective_comparison_class,
    _effective_core_benchmark,
    _matches_filters,
    _primary_frontier_score,
    _summarize_effect_inventory,
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


def test_summarize_effect_inventory_counts_candidates_and_classifications() -> None:
    summaries = [
        SimpleNamespace(source_pathology="missing_extracted_source", compare_shape="", candidate=False, n_ops=0),
        SimpleNamespace(source_pathology="", compare_shape="collapsed_subtree_oracle_shape", candidate=False, n_ops=1),
        SimpleNamespace(source_pathology="", compare_shape="", candidate=True, n_ops=2),
    ]

    inventory = _summarize_effect_inventory(summaries)

    assert inventory["source_counts"] == {"missing_extracted_source": 1}
    assert inventory["compare_counts"] == {"collapsed_subtree_oracle_shape": 1}
    assert inventory["candidate_count"] == 1
    assert inventory["candidate_ops"] == 2
    assert inventory["candidate_summaries"] == [summaries[2]]


def test_residual_candidate_inventory_counts_only_overlapping_candidate_rows() -> None:
    candidate_summaries = [
        SimpleNamespace(resolver_eids=("section-3",), n_ops=2),
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
