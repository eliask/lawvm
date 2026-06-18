"""Tests for robust-only timeline invariant evidence promotion."""

from __future__ import annotations

from lawvm.core.timeline_invariants import (
    collect_apply_phase_shadow_paths,
    filter_promotable_timeline_invariant_rows,
    is_promotable_timeline_invariant_row,
)


def test_promotable_row_requires_robust_tier_when_present() -> None:
    assert is_promotable_timeline_invariant_row(
        {"kind": "content_mismatch", "tier": "robust"}
    )
    assert not is_promotable_timeline_invariant_row(
        {"kind": "timeline_without_ir", "tier": "materialization_variant"}
    )
    assert not is_promotable_timeline_invariant_row(
        {"kind": "duplicate_permanent_version_row", "tier": "materialization_variant"}
    )


def test_filter_promotable_rows_keeps_only_robust() -> None:
    rows = [
        {"kind": "content_mismatch", "tier": "robust"},
        {"kind": "timeline_without_ir", "tier": "materialization_variant"},
        {"kind": "same_source_descendant_shadow", "tier": "robust"},
    ]
    promotable = filter_promotable_timeline_invariant_rows(rows)
    assert [row["kind"] for row in promotable] == [
        "content_mismatch",
        "same_source_descendant_shadow",
    ]


def test_apply_phase_shadow_path_suppresses_timeline_shadow_promotion() -> None:
    row = {
        "kind": "same_source_descendant_shadow",
        "tier": "robust",
        "address_path": "section:5/subsection:2",
    }
    paths = frozenset({"section:5/subsection:2"})
    assert not is_promotable_timeline_invariant_row(
        row,
        apply_phase_shadow_paths=paths,
    )
    assert filter_promotable_timeline_invariant_rows(
        [row],
        apply_phase_shadow_paths=paths,
    ) == []


def test_collect_apply_phase_shadow_paths_reads_transition_detector_findings() -> None:
    findings = [
        {
            "kind": "REPLAY.TRANSITION_DETECTOR",
            "detail": {
                "detector": "same_source_descendant_snapshot_shadow",
                "path": "section:12/item:3",
            },
        },
        {
            "kind": "REPLAY.TRANSITION_DETECTOR",
            "detail": {
                "detector": "descendant_sibling_loss",
                "path": "section:1",
            },
        },
    ]
    assert collect_apply_phase_shadow_paths(findings) == frozenset({"section:12/item:3"})


def test_overlapping_permanent_is_robust_but_not_evidence_promotable() -> None:
    assert not is_promotable_timeline_invariant_row(
        {"kind": "overlapping_permanent", "tier": "robust"}
    )
    assert filter_promotable_timeline_invariant_rows(
        [{"kind": "overlapping_permanent", "tier": "robust"}]
    ) == []
