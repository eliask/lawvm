"""Finland bench comparator → unified contract BenchUnitResult."""

from __future__ import annotations

from collections import Counter

import pytest

from lawvm.core.bench_comparator_registry import (
    get_bench_comparator,
    has_bench_comparator,
)
from lawvm.core.bench_contract import (
    BenchStatus,
    check_residue_reconciliation,
)
from lawvm.tools import bench


class _DummyReplay:
    def serialize_text(self) -> str:
        return "foo"


def _patch_replay(monkeypatch) -> None:
    monkeypatch.setattr(
        bench,
        "_run_replay_with_bench_warning_capture",
        lambda *a, **k: (_DummyReplay(), Counter()),
    )
    monkeypatch.setattr(bench, "is_known_missing_source", lambda _sid: False)


def test_fi_comparator_registered() -> None:
    assert has_bench_comparator("fi")
    assert get_bench_comparator("fi") is bench.fi_bench_unit_result


def test_fi_adapter_scored_maps_axes_and_residue(monkeypatch) -> None:
    _patch_replay(monkeypatch)
    monkeypatch.setattr(
        bench,
        "_semantic_section_score",
        lambda *a, **k: bench._BenchSemanticScore(
            structural_similarity=0.8,
            adjusted_levenshtein_similarity=0.95,
            event_counts=Counter({"unit_missing_left": 3, "facet_removed": 1}),
            penalized_event_counts=Counter({"unit_missing_left": 2}),
        ),
    )

    result = bench.fi_bench_unit_result("2000/1")

    assert result.bench_unit_status is BenchStatus.SCORED
    assert result.structural_err == pytest.approx(0.2)
    assert result.text_err == pytest.approx(0.05)
    # Residue comes from the PENALIZED events, not all events.
    assert dict(result.residue_buckets) == {"unit_missing_left": 2}
    assert result.headline_error() == pytest.approx(0.2)  # worst-of
    check_residue_reconciliation(result)


def test_fi_adapter_perfect_has_empty_residue(monkeypatch) -> None:
    _patch_replay(monkeypatch)
    monkeypatch.setattr(
        bench,
        "_semantic_section_score",
        lambda *a, **k: bench._BenchSemanticScore(
            structural_similarity=1.0,
            adjusted_levenshtein_similarity=1.0,
            event_counts=Counter({"facet_removed": 1}),  # neutralized — not penalized
            penalized_event_counts=Counter(),
        ),
    )

    result = bench.fi_bench_unit_result("2000/1")
    assert result.structural_err == 0.0
    assert dict(result.residue_buckets) == {}
    check_residue_reconciliation(result)  # no phantom residue


def test_fi_adapter_no_text_score_leaves_text_axis_unattempted(monkeypatch) -> None:
    _patch_replay(monkeypatch)
    monkeypatch.setattr(
        bench,
        "_semantic_section_score",
        lambda *a, **k: bench._BenchSemanticScore(
            structural_similarity=0.9,
            adjusted_levenshtein_similarity=-1.0,  # not computed
            event_counts=Counter(),
            penalized_event_counts=Counter({"wording_text_changed": 1}),
        ),
    )

    result = bench.fi_bench_unit_result("2000/1")
    assert result.structural_err == pytest.approx(0.1)
    assert result.text_err is None
    assert result.headline_error() == pytest.approx(0.1)


def test_fi_adapter_fast_path_has_no_structural_axis(monkeypatch) -> None:
    _patch_replay(monkeypatch)
    monkeypatch.setattr(bench, "_lev_sim_fast", lambda _sid, _master: 0.9)

    result = bench.fi_bench_unit_result("2000/1", fast=True)
    assert result.bench_unit_status is BenchStatus.SCORED
    assert result.structural_err is None
    assert result.text_err == pytest.approx(0.1)


def test_fi_adapter_no_truth_is_non_scored(monkeypatch) -> None:
    _patch_replay(monkeypatch)
    monkeypatch.setattr(
        bench,
        "_semantic_section_score",
        lambda *a, **k: bench._BenchSemanticScore(
            structural_similarity=-1.0,
            adjusted_levenshtein_similarity=-1.0,
            event_counts=Counter(),
            penalized_event_counts=Counter(),
        ),
    )
    result = bench.fi_bench_unit_result("2000/1")
    assert result.bench_unit_status is BenchStatus.NO_TRUTH
    assert result.structural_err is None


def test_fi_adapter_source_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(bench, "is_known_missing_source", lambda _sid: True)
    result = bench.fi_bench_unit_result("1987/182")
    assert result.bench_unit_status is BenchStatus.SOURCE_UNAVAILABLE


def test_fi_adapter_crash_is_failure(monkeypatch) -> None:
    monkeypatch.setattr(bench, "is_known_missing_source", lambda _sid: False)

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(bench, "_run_replay_with_bench_warning_capture", boom)
    result = bench.fi_bench_unit_result("2000/1")
    assert result.bench_unit_status is BenchStatus.CRASH
    assert result.is_failure
    assert result.witnesses == ("boom",)


def test_fi_adapter_section_diff_flag_residue(monkeypatch) -> None:
    """A penalized section with no events still gets typed residue (no silent error)."""
    _patch_replay(monkeypatch)
    monkeypatch.setattr(
        bench,
        "_semantic_section_score",
        lambda *a, **k: bench._BenchSemanticScore(
            structural_similarity=0.5,
            adjusted_levenshtein_similarity=-1.0,
            event_counts=Counter(),
            penalized_event_counts=Counter({"section_diff_structural": 1}),
        ),
    )
    result = bench.fi_bench_unit_result("2000/1")
    assert result.structural_err == pytest.approx(0.5)
    assert dict(result.residue_buckets) == {"section_diff_structural": 1}
    check_residue_reconciliation(result)
