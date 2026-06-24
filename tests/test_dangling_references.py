"""Unit + witness tests for the corpus DANGLING-reference projection.

The classification + report-totality tests run on SYNTHETIC fi_refs rows and a
FAKE existence oracle — NO corpus dependency. They exercise the tag-don't-guess
discipline directly: an EXISTENCE_UNKNOWN row is never folded into DANGLING, the
three-way status set is closed (an out-of-set status raises), and the report's
totality guard refuses a partition that does not sum.

One CORPUS-GATED witness test proves the existence oracle end-to-end on a real
Finnish act: it asserts a known-absent cited section reads DANGLING and a
known-present section reads PRESENT, and that a contentAbsent (unmaterialized)
act reads EXISTENCE_UNKNOWN. It skips when the Finland corpus is not present.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lawvm.tools import dangling_references as dr

_FINLEX_CORPUS_AVAILABLE = (
    Path(__file__).resolve().parents[1] / "data" / "finlex.farchive"
).exists()


# ---------------------------------------------------------------------------
# Synthetic fixtures (corpus-free)
# ---------------------------------------------------------------------------


def _row(
    *,
    confidence: str,
    target_statute_id: str = "1999/1",
    target_ref: str = "1999/1/5",
    source_statute_id: str = "2000/2",
    cite_kind: str = "cross_statute",
) -> dict:
    return {
        "source_statute_id": source_statute_id,
        "source_provision_ref_str": f"{source_statute_id}/1",
        "target_statute_id": target_statute_id,
        "target_provision_ref_str": target_ref,
        "cite_confidence": confidence,
        "cite_kind": cite_kind,
        "valid_at_start": "2000-01-01",
        "valid_at_end": None,
    }


class _FakeOracle:
    """A scripted existence oracle: maps (statute, ref) -> (status, reason)."""

    def __init__(self, verdicts: dict[tuple[str, str], tuple[str, str]]):
        self._verdicts = verdicts

    def classify(self, target_statute_id: str, target_ref: str) -> tuple[str, str]:
        return self._verdicts[(target_statute_id, target_ref)]


def _write_jsonl(tmp_path: Path, rows: list[dict]) -> str:
    path = tmp_path / "fi_refs.jsonl"
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return str(path)


# ---------------------------------------------------------------------------
# Closed-set status (CLOSURE) — an out-of-set status is refused.
# ---------------------------------------------------------------------------


def test_status_set_is_closed_three_way():
    assert dr.DANGLING_STATUSES == frozenset(
        {"PRESENT", "DANGLING", "EXISTENCE_UNKNOWN"}
    )


def test_row_refuses_out_of_set_status():
    with pytest.raises(dr.DanglingReferenceError):
        dr.DanglingReferenceRow(
            source_statute_id="2000/2",
            source_provision_ref_str="2000/2/1",
            target_statute_id="1999/1",
            target_provision_ref_str="1999/1/5",
            cite_confidence="exact",
            cite_kind="cross_statute",
            existence_status="BROKEN_MAYBE",  # not in the closed set
            reason="x",
        )


def test_row_accepts_each_closed_status():
    for status in ("PRESENT", "DANGLING", "EXISTENCE_UNKNOWN"):
        row = dr.DanglingReferenceRow(
            source_statute_id="2000/2",
            source_provision_ref_str="2000/2/1",
            target_statute_id="1999/1",
            target_provision_ref_str="1999/1/5",
            cite_confidence="exact",
            cite_kind="cross_statute",
            existence_status=status,
            reason="r",
        )
        assert row.existence_status == status


# ---------------------------------------------------------------------------
# Scope — only RESOLVED confidences are checked; the rest are excluded.
# ---------------------------------------------------------------------------


def test_resolved_scope_matches_confidence_partition():
    assert dr.RESOLVED_CONFIDENCES == frozenset({"exact", "approximate"})
    # The two sets are disjoint (a confidence is either checked or excluded).
    assert not (dr.RESOLVED_CONFIDENCES & dr.NON_RESOLVED_CONFIDENCES)


def test_non_resolved_rows_excluded_not_checked(tmp_path):
    rows = [
        _row(confidence="statute_only"),
        _row(confidence="ambiguous"),
        _row(confidence="open"),
    ]
    path = _write_jsonl(tmp_path, rows)
    # Oracle should never be consulted for these — a KeyError would surface if it were.
    oracle = _FakeOracle({})
    report = dr.build_dangling_report(path, oracle)
    assert report.resolved_checked == 0
    assert report.present == report.dangling == report.existence_unknown == 0
    assert report.excluded_non_resolved == {
        "statute_only": 1,
        "ambiguous": 1,
        "open": 1,
    }


# ---------------------------------------------------------------------------
# TAG-DON'T-GUESS — EXISTENCE_UNKNOWN is never folded into DANGLING.
# ---------------------------------------------------------------------------


def test_existence_unknown_is_not_dangling(tmp_path):
    rows = [
        _row(confidence="exact", target_ref="1999/1/5"),  # present
        _row(confidence="exact", target_ref="1999/1/9"),  # dangling
        _row(confidence="exact", target_ref="1999/1/7"),  # unknown
    ]
    path = _write_jsonl(tmp_path, rows)
    oracle = _FakeOracle(
        {
            ("1999/1", "1999/1/5"): (dr.STATUS_PRESENT, dr.REASON_PRESENT),
            ("1999/1", "1999/1/9"): (
                dr.STATUS_DANGLING,
                dr.REASON_DANGLING_ABSENT,
            ),
            ("1999/1", "1999/1/7"): (
                dr.STATUS_EXISTENCE_UNKNOWN,
                dr.REASON_UNKNOWN_ACT_ABSENT,
            ),
        }
    )
    report = dr.build_dangling_report(path, oracle)
    assert report.resolved_checked == 3
    assert report.present == 1
    assert report.dangling == 1
    assert report.existence_unknown == 1
    # The UNKNOWN row is NOT a dangling witness.
    assert len(report.dangling_rows) == 1
    assert report.dangling_rows[0].target_provision_ref_str == "1999/1/9"
    assert report.dangling_rows[0].existence_status == "DANGLING"


# ---------------------------------------------------------------------------
# TOTALITY — present + dangling + existence_unknown == resolved_checked.
# ---------------------------------------------------------------------------


def test_report_totality_holds(tmp_path):
    rows = [_row(confidence="exact", target_ref=f"1999/1/{i}") for i in range(5)]
    path = _write_jsonl(tmp_path, rows)
    verdicts = {
        ("1999/1", "1999/1/0"): (dr.STATUS_PRESENT, dr.REASON_PRESENT),
        ("1999/1", "1999/1/1"): (dr.STATUS_PRESENT, dr.REASON_PRESENT),
        ("1999/1", "1999/1/2"): (dr.STATUS_DANGLING, dr.REASON_DANGLING_ABSENT),
        ("1999/1", "1999/1/3"): (
            dr.STATUS_EXISTENCE_UNKNOWN,
            dr.REASON_UNKNOWN_CONTENT_ABSENT,
        ),
        ("1999/1", "1999/1/4"): (
            dr.STATUS_EXISTENCE_UNKNOWN,
            dr.REASON_UNKNOWN_ACT_ABSENT,
        ),
    }
    report = dr.build_dangling_report(path, _FakeOracle(verdicts))
    assert (
        report.present + report.dangling + report.existence_unknown
        == report.resolved_checked
        == 5
    )
    assert report.total_rows == 5
    # to_canonical_dict round-trips the closed schema id.
    payload = report.to_canonical_dict()
    assert payload["schema"] == "lawvm.dangling_reference_report.v1"
    assert payload["present"] == 2
    assert payload["dangling"] == 1
    assert payload["existence_unknown"] == 2


def test_report_constructor_refuses_non_summing_partition():
    with pytest.raises(dr.DanglingReferenceError):
        dr.DanglingReferenceReport(
            total_rows=10,
            resolved_checked=10,  # but the three counts sum to 9 -> a dropped row
            excluded_non_resolved={},
            present=5,
            dangling=2,
            existence_unknown=2,
            unknown_by_reason={},
            dangling_by_reason={},
            dangling_rows=(),
        )


# ---------------------------------------------------------------------------
# The provision-ref -> locator parser handles the slash-bearing statute id.
# ---------------------------------------------------------------------------


def test_locator_strips_slash_bearing_statute_prefix():
    # statute id "1987/627" itself contains a slash; the section is "17".
    loc = dr._provision_ref_to_locator("1987/627", "1987/627/17")
    assert loc is not None
    assert len(loc.segments) == 1
    assert loc.segments[0].kind == "section"
    assert loc.segments[0].label == "17"


def test_locator_parses_embedded_chapter():
    loc = dr._provision_ref_to_locator("1889/39-001", "1889/39-001/ch17/1")
    assert loc is not None
    kinds = [(s.kind, s.label) for s in loc.segments]
    assert kinds == [("chapter", "17"), ("section", "1")]


def test_locator_act_level_only_is_none():
    # A ref that is just the statute id (no in-act provision) -> no locator.
    assert dr._provision_ref_to_locator("1972/41", "1972/41") is None


def test_locator_drops_subsection_and_kohta_tail():
    # momentti (bare int after section) and kohta (k-prefixed) are below section
    # granularity and are dropped — only the section survives.
    loc = dr._provision_ref_to_locator("1999/1", "1999/1/5/2/k3")
    assert loc is not None
    assert [(s.kind, s.label) for s in loc.segments] == [("section", "5")]


# ---------------------------------------------------------------------------
# CORPUS-GATED witness — the real existence oracle end-to-end.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus (finlex.farchive) not present"
)
def test_existence_oracle_real_corpus_witness():
    from farchive import Farchive
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    oracle = dr.CurrentStateExistenceOracle(store)

    # 1994/750 is materialized and has §44a then jumps to §52 — there is NO §46.
    status, reason = oracle.classify("1994/750", "1994/750/46")
    assert status == dr.STATUS_DANGLING, (status, reason)
    assert reason == dr.REASON_DANGLING_ABSENT

    # The same act DOES have §44 — present.
    status, reason = oracle.classify("1994/750", "1994/750/44")
    assert status == dr.STATUS_PRESENT, (status, reason)

    # 1972/41 (Huumausainelaki) is in the corpus but its body is a contentAbsent
    # placeholder -> EXISTENCE_UNKNOWN, never DANGLING.
    status, reason = oracle.classify("1972/41", "1972/41/3")
    assert status == dr.STATUS_EXISTENCE_UNKNOWN, (status, reason)
    assert reason == dr.REASON_UNKNOWN_CONTENT_ABSENT

    # An act with no XML in the corpus at all -> EXISTENCE_UNKNOWN (act absent),
    # never DANGLING.
    status, reason = oracle.classify("9999/99999", "9999/99999/1")
    assert status == dr.STATUS_EXISTENCE_UNKNOWN, (status, reason)
    assert reason == dr.REASON_UNKNOWN_ACT_ABSENT
