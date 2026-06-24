"""Unit + witness tests for the DANGLING temporal-cause split.

The classification tests run on SYNTHETIC repeal-note XML and SYNTHETIC dangling
reports — NO corpus dependency. They exercise the tag-don't-guess discipline: a
covered repeal note (single OR range) is REPEALED with evidence; an absent /
uncovered note is UNDETERMINED, never a guessed never-existed cause; and the
cause report's totality guard refuses a split that does not sum.

One CORPUS-GATED witness test proves the range-aware matcher end-to-end on a real
Finnish act with a known range repeal note. It skips when the corpus is absent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.tools import dangling_temporal_cause as dc
from lawvm.tools.dangling_references import (
    REASON_DANGLING_ABSENT,
    STATUS_DANGLING,
    DanglingReferenceReport,
    DanglingReferenceRow,
)

_FINLEX_CORPUS_AVAILABLE = (
    Path(__file__).resolve().parents[1] / "data" / "finlex.farchive"
).exists()


# ---------------------------------------------------------------------------
# parse_repeal_notes + spec coverage (pure)
# ---------------------------------------------------------------------------


def test_parse_single_section_repeal_note() -> None:
    xml = '<p><i>84 § on kumottu L:lla <ref>16.4.1987/411</ref>.</i></p>'
    notes = dc.parse_repeal_notes(xml)
    assert len(notes) == 1
    assert notes[0].unit == "§"
    assert notes[0].amending_act == "16.4.1987/411"
    assert notes[0].spec == "84"


def test_parse_range_repeal_note() -> None:
    xml = '<p>67–84 § on kumottu L:lla <ref>16.4.1987/411</ref>.</p>'
    notes = dc.parse_repeal_notes(xml)
    assert len(notes) == 1
    assert notes[0].spec == "67–84"
    assert notes[0].amending_act == "16.4.1987/411"


def test_spec_covers_single() -> None:
    assert dc._spec_covers_section("84", "84")
    assert not dc._spec_covers_section("84", "85")


def test_spec_covers_range_numeric_and_letter() -> None:
    assert dc._spec_covers_section("67–84", "70")
    assert dc._spec_covers_section("67–84", "67")
    assert dc._spec_covers_section("67–84", "84")
    assert not dc._spec_covers_section("67–84", "85")
    assert not dc._spec_covers_section("67–84", "66")
    # letter-suffix range: "3 a–4" covers "3a", "4" but not "5"
    assert dc._spec_covers_section("3 a–4", "3a")
    assert dc._spec_covers_section("3 a–4", "4")
    assert not dc._spec_covers_section("3 a–4", "5")


def test_spec_covers_fails_closed_on_unparseable() -> None:
    assert not dc._spec_covers_section("foo", "4")
    assert not dc._spec_covers_section("4", "bar")


# ---------------------------------------------------------------------------
# RepealNoteCauseOracle (fake store)
# ---------------------------------------------------------------------------


class _FakeStore:
    def __init__(self, xml_by_sid: dict[str, bytes]):
        self._xml = xml_by_sid

    def read_oracle(self, sid: str) -> bytes | None:
        return self._xml.get(sid)


def test_oracle_repealed_via_range_note() -> None:
    store = _FakeStore(
        {"1929/234": b"<p>67-84 \xc2\xa7 on kumottu L:lla <ref>16.4.1987/411</ref>.</p>"}
    )
    # \xc2\xa7 is the UTF-8 for the section sign; the recognizer matches "§".
    oracle = dc.RepealNoteCauseOracle(store)
    cause, ev = oracle.classify("1929/234", "1929/234/70")
    assert cause == dc.CAUSE_REPEALED_TARGET
    assert ev is not None
    assert ev.amending_act == "16.4.1987/411"


def test_oracle_undetermined_when_no_note() -> None:
    store = _FakeStore({"1999/1": b"<p>some unrelated text</p>"})
    oracle = dc.RepealNoteCauseOracle(store)
    cause, ev = oracle.classify("1999/1", "1999/1/5")
    assert cause == dc.CAUSE_UNDETERMINED
    assert ev is None


def test_oracle_undetermined_when_note_does_not_cover() -> None:
    store = _FakeStore(
        {"1999/1": b"<p>10 \xc2\xa7 on kumottu L:lla <ref>2000/2</ref>.</p>"}
    )
    oracle = dc.RepealNoteCauseOracle(store)
    cause, ev = oracle.classify("1999/1", "1999/1/5")
    assert cause == dc.CAUSE_UNDETERMINED


def test_oracle_undetermined_when_act_absent() -> None:
    oracle = dc.RepealNoteCauseOracle(_FakeStore({}))
    cause, ev = oracle.classify("9999/9", "9999/9/1")
    assert cause == dc.CAUSE_UNDETERMINED


# ---------------------------------------------------------------------------
# classify_dangling_causes + report totality
# ---------------------------------------------------------------------------


def _dangling_row(target_id: str, target_ref: str, source_id: str = "2000/2") -> DanglingReferenceRow:
    return DanglingReferenceRow(
        source_statute_id=source_id,
        source_provision_ref_str=f"{source_id}/1",
        target_statute_id=target_id,
        target_provision_ref_str=target_ref,
        cite_confidence="exact",
        cite_kind="cross_statute",
        existence_status=STATUS_DANGLING,
        reason=REASON_DANGLING_ABSENT,
    )


def _report(rows: list[DanglingReferenceRow]) -> DanglingReferenceReport:
    return DanglingReferenceReport(
        total_rows=len(rows),
        resolved_checked=len(rows),
        excluded_non_resolved={},
        present=0,
        dangling=len(rows),
        existence_unknown=0,
        unknown_by_reason={},
        dangling_by_reason={REASON_DANGLING_ABSENT: len(rows)} if rows else {},
        dangling_rows=tuple(rows),
    )


def test_classify_splits_and_totals() -> None:
    rows = [
        _dangling_row("1929/234", "1929/234/70"),   # covered by range note
        _dangling_row("1999/1", "1999/1/5"),         # no note -> undetermined
        _dangling_row("1999/1", "1999/1/6"),         # no note -> undetermined
    ]
    store = _FakeStore(
        {"1929/234": b"<p>67-84 \xc2\xa7 on kumottu L:lla <ref>16.4.1987/411</ref>.</p>"}
    )
    cause_report = dc.classify_dangling_causes(_report(rows), dc.RepealNoteCauseOracle(store))
    assert cause_report.total_dangling == 3
    assert cause_report.repealed_target == 1
    assert cause_report.undetermined == 2
    assert len(cause_report.repealed_rows) == 1
    assert cause_report.repealed_rows[0].amending_act == "16.4.1987/411"
    assert cause_report.undetermined_targets == ("1999/1",)
    # totality invariant holds on serialization round-trip
    d = cause_report.to_canonical_dict()
    assert d["repealed_target"] + d["undetermined"] == d["total_dangling"]


def test_cause_row_rejects_repealed_without_evidence() -> None:
    with pytest.raises(dc.DanglingCauseError):
        dc.DanglingCauseRow(
            source_statute_id="a",
            source_provision_ref_str="a/1",
            target_statute_id="b",
            target_provision_ref_str="b/1",
            cause=dc.CAUSE_REPEALED_TARGET,
            amending_act=None,
        )


def test_cause_row_rejects_out_of_set_cause() -> None:
    with pytest.raises(dc.DanglingCauseError):
        dc.DanglingCauseRow(
            source_statute_id="a",
            source_provision_ref_str="a/1",
            target_statute_id="b",
            target_provision_ref_str="b/1",
            cause="DANGLING_NEVER_MATERIALIZED",  # not in the closed set
        )


def test_cause_report_totality_guard() -> None:
    with pytest.raises(dc.DanglingCauseError):
        dc.DanglingCauseReport(
            total_dangling=5,
            repealed_target=1,
            undetermined=1,  # 1+1 != 5
        )


# ---------------------------------------------------------------------------
# CORPUS-GATED witness: real range repeal note resolves to REPEALED
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus (data/finlex.farchive) not present"
)
def test_corpus_witness_range_repeal() -> None:
    from farchive import Farchive
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    oracle = dc.RepealNoteCauseOracle(store)
    # 1929/234 §§ 67–84 were repealed by 16.4.1987/411 (a range note in the
    # consolidated text). A citation to any section in that span must read
    # REPEALED with that amending act as evidence.
    cause, ev = oracle.classify("1929/234", "1929/234/70")
    assert cause == dc.CAUSE_REPEALED_TARGET
    assert ev is not None
    assert ev.amending_act == "16.4.1987/411"
