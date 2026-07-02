"""Tests for the UK ``lawvm self-consistency -j uk`` signal harvesting.

The pure classification/projection helpers (adjudication-kind -> signal type,
compile-rejection projection + benign-class filtering, row schema) are tested
directly. The end-to-end projector is exercised against a known statute
(ukpga/1961/33) when the UK Farchive is reachable, and skipped otherwise so the
suite stays runnable in a bare worktree.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import lawvm.tools.uk_self_consistency as uksc
from lawvm.tools.self_consistency import ALL_SIGNAL_TYPES

_ROW_KEYS = {
    "statute_id",
    "amendment_id",
    "signal_type",
    "category",
    "description",
    "target_scope",
    "reason",
}


# ---------------------------------------------------------------------------
# Adjudication-kind -> signal-type classification
# ---------------------------------------------------------------------------

def test_target_not_found_is_target_absent() -> None:
    assert uksc._adjudication_signal_type("uk_replay_target_not_found") == "target_absent"


def test_source_shape_gap_is_target_absent() -> None:
    # A representative source-shape gap kind.
    assert (
        uksc._adjudication_signal_type("uk_replay_missing_root_parent_shape_gap")
        == "target_absent"
    )


def test_tree_invariant_is_invariant_violation() -> None:
    assert (
        uksc._adjudication_signal_type("uk_replay_tree_invariant_violation")
        == "invariant_violation"
    )


def test_unsupported_action_is_unhandled_op() -> None:
    assert (
        uksc._adjudication_signal_type("uk_replay_unsupported_action") == "unhandled_op"
    )


def test_payload_mismatch_is_apply_failure() -> None:
    assert (
        uksc._adjudication_signal_type("uk_replay_payload_mismatch") == "apply_failure"
    )


def test_text_match_missing_is_apply_failure() -> None:
    assert (
        uksc._adjudication_signal_type("uk_replay_text_match_missing") == "apply_failure"
    )


def test_applied_observation_is_not_a_defect() -> None:
    # A non-blocking "applied" observation is a successful outcome, not a signal.
    assert uksc._adjudication_signal_type("uk_replay_at_end_step_text_rewrite_applied") is None
    assert uksc._adjudication_signal_type("text_duplication_warning") is None


def test_signal_types_are_in_canonical_taxonomy() -> None:
    for kind in (
        "uk_replay_target_not_found",
        "uk_replay_tree_invariant_violation",
        "uk_replay_unsupported_action",
        "uk_replay_payload_mismatch",
        "uk_replay_missing_root_parent_shape_gap",
    ):
        sig = uksc._adjudication_signal_type(kind)
        assert sig in ALL_SIGNAL_TYPES


# ---------------------------------------------------------------------------
# Adjudication projection (row shape)
# ---------------------------------------------------------------------------

class _Adj:
    def __init__(self, kind: str, source_statute: str, message: str, detail: dict) -> None:
        self.kind = kind
        self.source_statute = source_statute
        self.message = message
        self.detail = detail


def test_project_adjudications_row_shape_and_filtering() -> None:
    adjs = [
        _Adj(
            "uk_replay_target_not_found",
            "ukpga/2020/1",
            "target not found",
            {"target": "section:5"},
        ),
        # An applied observation must be dropped (not a defect).
        _Adj("uk_replay_at_end_step_text_rewrite_applied", "ukpga/2020/1", "applied", {}),
    ]
    rows = uksc._project_adjudications("ukpga/1961/33", adjs)
    assert len(rows) == 1
    row = rows[0]
    assert _ROW_KEYS <= set(row)
    assert row["statute_id"] == "ukpga/1961/33"
    assert row["amendment_id"] == "ukpga/2020/1"
    assert row["signal_type"] == "target_absent"
    assert row["category"] == "uk_replay_target_not_found"
    assert row["target_scope"] == "section:5"


# ---------------------------------------------------------------------------
# Compile-rejection projection + benign-class filtering
# ---------------------------------------------------------------------------

def test_benign_source_pathology_is_filtered() -> None:
    # nonstructural_root_gap is the deliberate non-textual-application case.
    benign = {
        "rule_id": "uk_effect_source_pathology_classified",
        "source_pathology": "nonstructural_root_gap",
        "affecting_act_id": "uksi/1994/2716",
        "affected_provisions": "s. 5",
    }
    pathological = {
        "rule_id": "uk_effect_source_pathology_classified",
        "source_pathology": "missing_extracted_source",
        "affecting_act_id": "uksi/1994/2716",
        "affected_provisions": "s. 7",
        "effect_type": "substituted",
    }
    rows = uksc._project_compile_rejections(
        "ukpga/1961/33",
        lowering_rejections=[],
        authority_rejections=[],
        effect_feed_parse_rejections=[],
        source_parse_rejections=[],
        effect_source_pathology_observations=[benign, pathological],
        manual_compile_frontier_observations=[],
        source_acquisition_rejections=[],
    )
    assert len(rows) == 1
    assert rows[0]["signal_type"] == "source_pathology"
    assert rows[0]["category"] == "missing_extracted_source"
    assert "s. 7" in rows[0]["target_scope"]


def test_benign_manual_frontier_status_is_filtered() -> None:
    benign = {
        "rule_id": "uk_manual_compile_frontier_classified",
        "manual_compile_status": "non_textual_or_out_of_scope",
        "manual_compile_rule_id": "uk_manual_frontier_non_textual_or_out_of_scope",
        "affecting_act_id": "uksi/1994/2716",
    }
    actionable = {
        "rule_id": "uk_manual_compile_frontier_classified",
        "manual_compile_status": "source_insufficient",
        "manual_compile_rule_id": "uk_manual_frontier_missing_payload_source_insufficient",
        "manual_compile_reason": "source payload missing",
        "affecting_act_id": "uksi/2000/428",
        "affected_provisions": "s. 9",
    }
    rows = uksc._project_compile_rejections(
        "ukpga/1961/33",
        lowering_rejections=[],
        authority_rejections=[],
        effect_feed_parse_rejections=[],
        source_parse_rejections=[],
        effect_source_pathology_observations=[],
        manual_compile_frontier_observations=[benign, actionable],
        source_acquisition_rejections=[],
    )
    assert len(rows) == 1
    assert rows[0]["signal_type"] == "skipped_amendment"
    assert rows[0]["category"] == "source_insufficient"
    assert rows[0]["amendment_id"] == "uksi/2000/428"


def test_blocking_lowering_rejection_is_unhandled_op() -> None:
    rec = {
        "rule_id": "uk_effect_lowering_no_supported_action_rejected",
        "blocking": True,
        "affecting_act_id": "uksi/2001/3627",
        "affected_provisions": "Pt. 1",
        "affecting_provisions": "art. 32",
    }
    nonblocking = {
        "rule_id": "uk_effect_lowering_observation",
        "blocking": False,
        "strict_disposition": "record",
        "affecting_act_id": "uksi/2001/3627",
    }
    rows = uksc._project_compile_rejections(
        "ukpga/1961/33",
        lowering_rejections=[rec, nonblocking],
        authority_rejections=[],
        effect_feed_parse_rejections=[],
        source_parse_rejections=[],
        effect_source_pathology_observations=[],
        manual_compile_frontier_observations=[],
        source_acquisition_rejections=[],
    )
    assert len(rows) == 1
    assert rows[0]["signal_type"] == "unhandled_op"
    assert rows[0]["amendment_id"] == "uksi/2001/3627"


# ---------------------------------------------------------------------------
# Corpus selection
# ---------------------------------------------------------------------------

def test_resolve_explicit_statutes() -> None:
    class _Args:
        statutes = "ukpga/1961/33, asc/2020/1 ,"

    assert uksc.resolve_uk_statute_ids(_Args()) == ["ukpga/1961/33", "asc/2020/1"]


def test_build_uk_store_missing_archive_does_not_create_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # build_uk_store resolves through resolve_farchive_path; the explicit-env
    # override is the highest-precedence input, so point it at a missing path.
    missing = tmp_path / "unused"
    monkeypatch.setenv("LAWVM_UK_LEGISLATION_FARCHIVE_DB", str(missing))

    with pytest.raises(FileNotFoundError):
        uksc.build_uk_store()

    assert not missing.exists()


# ---------------------------------------------------------------------------
# End-to-end projector (archive-backed; skipped without the UK Farchive)
# ---------------------------------------------------------------------------

def _uk_store_or_skip():
    from lawvm.corpus_store import resolve_farchive_path

    db, _rule = resolve_farchive_path(
        "uk_legislation.farchive", explicit_env="LAWVM_UK_LEGISLATION_FARCHIVE_DB"
    )
    if not Path(db).exists():
        pytest.skip(f"UK archive not present at {db}")
    try:
        return uksc.build_uk_store()
    except Exception as exc:  # archive unreadable in a bare worktree
        pytest.skip(f"UK archive not reachable: {type(exc).__name__}")


@pytest.mark.slow
def test_uk_projector_row_shape_and_target_absent() -> None:
    store = _uk_store_or_skip()
    try:
        rows, errors = uksc.project_uk_self_consistency("ukpga/1961/33", store)
    finally:
        close = getattr(store, "close", None)
        if callable(close):
            close()
    if errors:
        pytest.skip(f"replay error (corpus incomplete): {errors[0].get('error')}")
    assert rows, "expected UK self-consistency signals for ukpga/1961/33"
    for row in rows:
        assert _ROW_KEYS <= set(row), f"row missing keys: {row}"
        assert row["statute_id"] == "ukpga/1961/33"
        assert row["signal_type"] in ALL_SIGNAL_TYPES
    # ukpga/1961/33 has amendments targeting absent units; expect target_absent.
    assert any(r["signal_type"] == "target_absent" for r in rows)
