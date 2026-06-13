"""Tests for ``lawvm self-consistency`` signal harvesting.

The pure helpers (category normalization, replay-log parsing) are tested
directly. The end-to-end projector row shape is exercised against a known
statute (1958/370) when a populated corpus is reachable, and skipped otherwise
so the suite stays runnable in a bare worktree.
"""
from __future__ import annotations

import pytest

import lawvm.tools.self_consistency as sc
from lawvm.tools.self_consistency import (
    ALL_SIGNAL_TYPES,
    _category,
    _classify_failed_reason,
    _parse_replay_log,
)


# ---------------------------------------------------------------------------
# _category normalization
# ---------------------------------------------------------------------------

def test_category_prefers_reason_code() -> None:
    assert _category("anything at all", "ELAB.REJECTED_OPERATION") == "ELAB.REJECTED_OPERATION"


def test_category_collapses_momentti_numbers() -> None:
    # "momentti 2 not found" and "momentti 5 not found" share one bucket, and
    # the digit must not eat the following word "not".
    a = _category("momentti 2 not found")
    b = _category("momentti 5 not found")
    assert a == b
    assert "not found" in a
    assert a == "momentti N not found"


def test_category_collapses_section_letter_suffix() -> None:
    assert _category("section 10a missing") == _category("section 17 missing")


def test_category_normalizes_kind_label_and_lists() -> None:
    cat = _category("uncovered kumotaan: ['10a (drop)', '122', '123']")
    # Concrete labels and list contents are placeholdered away.
    assert "'X'" not in cat or cat.count("'X'") <= 1
    assert _category("uncovered kumotaan: ['1']") == _category("uncovered kumotaan: ['999']")


# ---------------------------------------------------------------------------
# Failed-reason classification
# ---------------------------------------------------------------------------

def test_classify_failed_reason_not_found_is_target_absent() -> None:
    assert _classify_failed_reason("momentti 2 not found") == "target_absent"
    assert _classify_failed_reason("master section:111 not found") == "target_absent"


def test_classify_failed_reason_unhandled_is_unhandled_op() -> None:
    assert _classify_failed_reason("section not found or unhandled op") == "target_absent"
    assert _classify_failed_reason("unhandled non-section op") == "unhandled_op"


# ---------------------------------------------------------------------------
# Replay-log parsing (the silently-swallowed signals)
# ---------------------------------------------------------------------------

def test_parse_log_recovers_momentti_target_absent() -> None:
    log = "  [1977/604] REPEAL 111 § 2 mom → FAILED (momentti 2 not found)\n"
    rows = _parse_replay_log("1958/370", log)
    assert len(rows) == 1
    row = rows[0]
    assert row["statute_id"] == "1958/370"
    assert row["amendment_id"] == "1977/604"
    assert row["signal_type"] == "target_absent"
    assert row["category"] == "momentti N not found"
    assert "REPEAL 111" in row["description"]
    assert "[1977/604]" not in row["description"]


def test_parse_log_coverage_dropped_op() -> None:
    log = "  [1968/493] Coverage: 34 units, 32 claimed, 16 uncovered\n"
    rows = _parse_replay_log("1958/370", log)
    assert len(rows) == 1
    row = rows[0]
    assert row["signal_type"] == "coverage_gap"
    assert row["category"] == "claimed<units (dropped op?)"
    assert row["amendment_id"] == "1968/493"


def test_parse_log_coverage_clean_is_ignored() -> None:
    # claimed == units and zero uncovered is internally consistent: no signal.
    log = "  [1990/100] Coverage: 5 units, 5 claimed, 0 uncovered\n"
    assert _parse_replay_log("x/1", log) == []


def test_parse_log_skipped_amendment() -> None:
    log = "  [1999/123] not found in corpus — skipping\n"
    rows = _parse_replay_log("1958/370", log)
    assert len(rows) == 1
    assert rows[0]["signal_type"] == "skipped_amendment"
    assert rows[0]["amendment_id"] == "1999/123"


def test_parse_log_unhandled_op() -> None:
    log = "  140 § → FAILED (section not found or unhandled op)\n"
    rows = _parse_replay_log("x/1", log)
    assert len(rows) == 1
    assert rows[0]["signal_type"] == "target_absent"  # "not found" wins


def test_parse_log_arrow_ascii_variant() -> None:
    log = "  [2000/1] 5 § -> FAILED (unhandled non-section op)\n"
    rows = _parse_replay_log("x/1", log)
    assert len(rows) == 1
    assert rows[0]["signal_type"] == "unhandled_op"
    assert rows[0]["amendment_id"] == "2000/1"


def test_all_signal_types_is_complete() -> None:
    # Guard against a signal_type string drifting out of the canonical tuple.
    assert "target_absent" in ALL_SIGNAL_TYPES
    assert "coverage_gap" in ALL_SIGNAL_TYPES
    assert len(set(ALL_SIGNAL_TYPES)) == len(ALL_SIGNAL_TYPES)


# ---------------------------------------------------------------------------
# End-to-end projector row shape (corpus-backed; skipped without a corpus)
# ---------------------------------------------------------------------------

def _corpus_store_or_skip():
    from lawvm.finland.corpus import get_corpus_store

    try:
        store = get_corpus_store()
        if store.read_source("1958/370") is None:
            pytest.skip("1958/370 not present in reachable corpus")
        return store
    except Exception as exc:  # corpus archive missing in a bare worktree
        pytest.skip(f"corpus not reachable: {type(exc).__name__}")


def test_projector_row_shape_and_known_signals() -> None:
    store = _corpus_store_or_skip()
    rows, errors = sc._project_self_consistency("1958/370", store)
    assert errors == []
    assert rows, "expected self-consistency signals for 1958/370"

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
        assert row["statute_id"] == "1958/370"
        assert row["signal_type"] in ALL_SIGNAL_TYPES

    # The proof case: the 1977/604 momentti-2 repeal of an absent momentti must
    # surface as a target_absent signal even though cumulative replay swallows
    # it from failed_ops.
    target_absent = [r for r in rows if r["signal_type"] == "target_absent"]
    assert any(
        r["amendment_id"] == "1977/604" and "111" in r["description"]
        for r in target_absent
    ), "expected 1977/604 §111 momentti-not-found target_absent signal"

    # And the upstream 1968/493 dropped-op coverage gap.
    coverage = [r for r in rows if r["signal_type"] == "coverage_gap"]
    assert any(
        r["amendment_id"] == "1968/493" for r in coverage
    ), "expected 1968/493 coverage gap signal"
