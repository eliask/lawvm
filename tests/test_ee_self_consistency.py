"""Tests for ``lawvm -j ee self-consistency`` signal classification.

The pure classifier (adjudication-kind → signal type) is tested directly. The
end-to-end projector is exercised against a known RT pair when the populated
Riigi Teataja Farchive is reachable, and skipped otherwise so the suite stays
runnable in a bare worktree.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import lawvm.tools.ee_self_consistency as ee_sc
from lawvm.tools.ee_self_consistency import (
    EE_SIGNAL_TYPES,
    _classify_adjudication_kind,
)


# ---------------------------------------------------------------------------
# Adjudication-kind classification
# ---------------------------------------------------------------------------

def test_target_not_found_is_target_absent() -> None:
    assert _classify_adjudication_kind("ee_replay_target_not_found", {}) == "target_absent"


def test_unsupported_action_is_unhandled_op() -> None:
    assert _classify_adjudication_kind("ee_replay_unsupported_action", {}) == "unhandled_op"
    assert _classify_adjudication_kind("ee_replay_meta_non_body_skipped", {}) == "unhandled_op"


def test_parser_rejection_is_unhandled_op() -> None:
    # A ``*_rejected`` lowering refused to emit an executable op.
    assert _classify_adjudication_kind("ee_parse_new_format_op_text_rejected", {}) == "unhandled_op"
    assert _classify_adjudication_kind("ee_parse_constitutional_review_rejected", {}) == "unhandled_op"


def test_unsupported_source_lane_family_is_unhandled_not_pathology() -> None:
    # An unparsed-META rejection carries family ``unsupported_source_lane``: it is
    # an unhandled operation surface, NOT a source-acquisition pathology.
    detail = {"family": "unsupported_source_lane", "blocking": True}
    assert _classify_adjudication_kind("ee_parse_old_format_unparsed_meta_rejected", detail) == "unhandled_op"


def test_fetch_failed_is_source_pathology() -> None:
    detail = {"family": "source_lane_failure"}
    assert _classify_adjudication_kind("ee_amendment_source_fetch_failed", detail) == "source_pathology"
    assert _classify_adjudication_kind("ee_temporal_source_scan_failed", detail) == "source_pathology"


def test_rt_xml_metadata_pathology_is_source_pathology() -> None:
    assert _classify_adjudication_kind("ee_rt_xml_muutmismarge_missing_aktviide", {}) == "source_pathology"


def test_cancelled_pending_ref_is_skipped_amendment() -> None:
    # The explicit kind override wins over its recovery family.
    detail = {"family": "pending_amendment_cancellation_filter"}
    assert (
        _classify_adjudication_kind("ee_cancelled_pending_amendment_ref_filtered", detail)
        == "skipped_amendment"
    )


def test_ref_slice_filter_is_not_a_signal() -> None:
    # Deliberate temporal windowing (op belongs to a different effective slice).
    detail = {"family": "ref_slice_filter"}
    assert _classify_adjudication_kind("ee_ref_slice_operation_filtered", detail) is None


def test_recovery_family_is_not_a_signal() -> None:
    detail = {"family": "target_resolution_recovery"}
    assert _classify_adjudication_kind("ee_some_recovery_rule", detail) is None


def test_signal_types_are_distinct() -> None:
    assert len(set(EE_SIGNAL_TYPES)) == len(EE_SIGNAL_TYPES)
    assert "target_absent" in EE_SIGNAL_TYPES
    assert "coverage_gap" not in EE_SIGNAL_TYPES  # EE has no oracle-free coverage


def test_classified_signals_are_in_taxonomy() -> None:
    for kind, detail in [
        ("ee_replay_target_not_found", {}),
        ("ee_replay_unsupported_action", {}),
        ("ee_parse_constitutional_review_rejected", {}),
        ("ee_temporal_source_scan_failed", {"family": "source_lane_failure"}),
        ("ee_cancelled_pending_amendment_ref_filtered", {}),
    ]:
        signal = _classify_adjudication_kind(kind, detail)
        assert signal is None or signal in EE_SIGNAL_TYPES


# ---------------------------------------------------------------------------
# End-to-end projector row shape (archive-backed; skipped without the archive)
# ---------------------------------------------------------------------------

def _archive_or_skip():
    from lawvm.estonia.fetch import open_rt_archive

    db = ee_sc._DEFAULT_DB
    if not Path(db).exists():
        pytest.skip(f"EE archive not reachable: {db}")
    try:
        return open_rt_archive(Path(db))
    except Exception as exc:
        pytest.skip(f"EE archive not openable: {type(exc).__name__}")


def test_projector_row_shape_known_pair() -> None:
    archive = _archive_or_skip()
    try:
        # Ehitisregister: a high-amendment law that replays cleanly through ops.
        rows, errors = ee_sc._project_ee_pair(
            "155895", "119062012020", "128092014004", archive
        )
    finally:
        close = getattr(archive, "close", None)
        if callable(close):
            close()

    assert errors == []
    required_keys = {
        "statute_id",
        "amendment_id",
        "signal_type",
        "category",
        "description",
        "target_scope",
        "reason",
    }
    for row in rows:
        assert required_keys <= set(row), f"row missing keys: {row}"
        assert row["statute_id"] == "155895"
        assert row["signal_type"] in EE_SIGNAL_TYPES
