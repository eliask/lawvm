"""Tests for UK structural review dump (lawvm structural-review -j uk --dump).

Unit tests (no archive required):
  - Per-EID classifier on synthetic {eid:text} maps
  - Bucket grouping
  - Compact mode rendering
  - Section filter

Integration tests (require data/uk_legislation.farchive):
  - dump_uk_statute for ukpga/1978/30 returns non-empty output with expected
    structure (statute id in header, at least one divergence marker, no 'same'
    nodes when compact=True)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.tools.uk_structural_review import (
    _CLASS_ONLY_ORACLE,
    _CLASS_ONLY_REPLAY,
    _CLASS_SAME,
    _CLASS_TEXT_DIFF,
    _bucket_eid,
    _classify_eids,
    _normalize_text,
    _render_diff,
)

_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "uk_legislation.farchive"

# ---------------------------------------------------------------------------
# Unit: _normalize_text
# ---------------------------------------------------------------------------


def test_normalize_text_lowercases_and_strips_punctuation() -> None:
    assert _normalize_text("Hello, World!") == "hello world"
    assert _normalize_text("  foo   bar  ") == "foo bar"
    assert _normalize_text("section 1(2)(a)") == "section 12a"


def test_normalize_text_empty() -> None:
    assert _normalize_text("") == ""


# ---------------------------------------------------------------------------
# Unit: _bucket_eid
# ---------------------------------------------------------------------------


def test_bucket_eid_section_family() -> None:
    assert _bucket_eid("section-1") == "section-1"
    assert _bucket_eid("section-1-2-a") == "section-1"
    assert _bucket_eid("section-23a-3") == "section-23a"


def test_bucket_eid_schedule() -> None:
    assert _bucket_eid("schedule-1-paragraph-2") == "schedule-1"
    assert _bucket_eid("schedule-1") == "schedule-1"


def test_bucket_eid_bare_type() -> None:
    assert _bucket_eid("section") == "section"


def test_bucket_eid_empty() -> None:
    assert _bucket_eid("") == ""


# ---------------------------------------------------------------------------
# Unit: _classify_eids
# ---------------------------------------------------------------------------


def _make_classify(
    replay_raw_texts: dict[str, str],
    oracle_norm_text_map: dict[str, str],
    replay_eids: set[str],
    oracle_eids: set[str],
) -> dict[str, dict]:
    """Helper: build a simple classification without normalization complexity."""
    # For unit tests: normalized == raw (we pick simple lowercase EIDs)
    return _classify_eids(
        replay_raw_texts=replay_raw_texts,
        oracle_norm_text_map=oracle_norm_text_map,
        replay_norm_set=frozenset(replay_eids),
        oracle_norm_set=frozenset(oracle_eids),
        replay_norm_to_raw={eid: eid for eid in replay_eids},
    )


def test_classify_only_replay() -> None:
    classified = _make_classify(
        replay_raw_texts={"section-1": "text one"},
        oracle_norm_text_map={},
        replay_eids={"section-1"},
        oracle_eids=set(),
    )
    assert classified["section-1"]["kind"] == _CLASS_ONLY_REPLAY
    assert classified["section-1"]["replay_text"] == "text one"
    assert classified["section-1"]["oracle_text"] == ""


def test_classify_only_oracle() -> None:
    classified = _make_classify(
        replay_raw_texts={},
        oracle_norm_text_map={"section-2": "oracle text two"},
        replay_eids=set(),
        oracle_eids={"section-2"},
    )
    assert classified["section-2"]["kind"] == _CLASS_ONLY_ORACLE
    assert classified["section-2"]["replay_text"] == ""
    assert classified["section-2"]["oracle_text"] == "oracle text two"


def test_classify_same() -> None:
    # replay raw text normalizes to same as oracle norm text
    classified = _make_classify(
        replay_raw_texts={"section-3": "Hello, World!"},
        # oracle stores already-normalized text: "hello world"
        oracle_norm_text_map={"section-3": "hello world"},
        replay_eids={"section-3"},
        oracle_eids={"section-3"},
    )
    assert classified["section-3"]["kind"] == _CLASS_SAME


def test_classify_text_diff() -> None:
    classified = _make_classify(
        replay_raw_texts={"section-4": "version A"},
        oracle_norm_text_map={"section-4": "version b"},
        replay_eids={"section-4"},
        oracle_eids={"section-4"},
    )
    assert classified["section-4"]["kind"] == _CLASS_TEXT_DIFF


def test_classify_mixed() -> None:
    classified = _make_classify(
        replay_raw_texts={
            "section-1": "only in replay",
            "section-3": "same text",
            "section-4": "replay version",
        },
        oracle_norm_text_map={
            "section-2": "only in oracle",
            "section-3": "same text",
            "section-4": "oracle version",
        },
        replay_eids={"section-1", "section-3", "section-4"},
        oracle_eids={"section-2", "section-3", "section-4"},
    )
    assert classified["section-1"]["kind"] == _CLASS_ONLY_REPLAY
    assert classified["section-2"]["kind"] == _CLASS_ONLY_ORACLE
    assert classified["section-3"]["kind"] == _CLASS_SAME
    assert classified["section-4"]["kind"] == _CLASS_TEXT_DIFF


# ---------------------------------------------------------------------------
# Unit: _render_diff -- compact mode
# ---------------------------------------------------------------------------


def test_render_diff_compact_omits_same() -> None:
    classified = {
        "section-1": {"kind": _CLASS_ONLY_REPLAY, "replay_text": "some text", "oracle_text": ""},
        "section-2": {"kind": _CLASS_SAME, "replay_text": "same", "oracle_text": "same"},
    }
    lines = _render_diff(classified, compact=True, section_filter=None)
    rendered = "\n".join(lines)
    assert "section-1" in rendered
    assert "+REPLAY" in rendered
    # compact mode: same node must not appear
    assert "section-2" not in rendered
    assert "=SAME" not in rendered


def test_render_diff_non_compact_includes_same() -> None:
    classified = {
        "section-1": {"kind": _CLASS_ONLY_REPLAY, "replay_text": "some text", "oracle_text": ""},
        "section-2": {"kind": _CLASS_SAME, "replay_text": "same", "oracle_text": "same"},
    }
    lines = _render_diff(classified, compact=False, section_filter=None)
    rendered = "\n".join(lines)
    assert "section-1" in rendered
    assert "section-2" in rendered
    assert "=SAME" in rendered


def test_render_diff_section_filter() -> None:
    classified = {
        "section-1": {"kind": _CLASS_ONLY_REPLAY, "replay_text": "text", "oracle_text": ""},
        "schedule-1-paragraph-1": {"kind": _CLASS_ONLY_ORACLE, "replay_text": "", "oracle_text": "oracle"},
    }
    lines = _render_diff(classified, compact=True, section_filter="schedule")
    rendered = "\n".join(lines)
    assert "schedule-1" in rendered
    assert "section-1" not in rendered


def test_render_diff_markers() -> None:
    classified = {
        "section-10": {"kind": _CLASS_ONLY_REPLAY, "replay_text": "replay text", "oracle_text": ""},
        "section-11": {"kind": _CLASS_ONLY_ORACLE, "replay_text": "", "oracle_text": "oracle text"},
        "section-12": {"kind": _CLASS_TEXT_DIFF, "replay_text": "replay v", "oracle_text": "oracle v"},
    }
    lines = _render_diff(classified, compact=True, section_filter=None)
    rendered = "\n".join(lines)
    assert "+REPLAY" in rendered
    assert "+ORACLE" in rendered
    assert "~DIFF" in rendered


# ---------------------------------------------------------------------------
# Integration: dump_uk_statute requires archive
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _DB_PATH.exists(),
    reason="uk_legislation.farchive not present — skipping live pipeline test",
)
def test_dump_uk_statute_basic_ukpga_1978_30() -> None:
    """dump_uk_statute for ukpga/1978/30 produces non-empty output with required markers."""
    from lawvm.tools.uk_structural_review import dump_uk_statute

    result = dump_uk_statute("ukpga/1978/30", compact=True, db_path=_DB_PATH)
    assert isinstance(result, str)
    assert len(result) > 0, "expected non-empty output"
    assert "ukpga/1978/30" in result, "statute id must appear in header"


@pytest.mark.skipif(
    not _DB_PATH.exists(),
    reason="uk_legislation.farchive not present — skipping live pipeline test",
)
def test_dump_uk_statute_contains_divergence_marker_ukpga_1978_30() -> None:
    """dump_uk_statute compact output must contain at least one divergence."""
    from lawvm.tools.uk_structural_review import dump_uk_statute

    result = dump_uk_statute("ukpga/1978/30", compact=True, db_path=_DB_PATH)
    has_divergence = (
        "+REPLAY" in result
        or "+ORACLE" in result
        or "~DIFF" in result
    )
    assert has_divergence, (
        "expected at least one divergence marker (+REPLAY, +ORACLE, or ~DIFF) "
        f"in compact dump for ukpga/1978/30; first 500 chars:\n{result[:500]}"
    )


@pytest.mark.skipif(
    not _DB_PATH.exists(),
    reason="uk_legislation.farchive not present — skipping live pipeline test",
)
def test_dump_uk_statute_compact_has_no_same_nodes_ukpga_1978_30() -> None:
    """Compact mode must not contain '=SAME' (identical nodes are omitted)."""
    from lawvm.tools.uk_structural_review import dump_uk_statute

    result = dump_uk_statute("ukpga/1978/30", compact=True, db_path=_DB_PATH)
    assert "=SAME" not in result, (
        "compact mode must omit identical/same nodes but '=SAME' appeared in output"
    )
