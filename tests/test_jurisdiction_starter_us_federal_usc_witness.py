"""USC ``source-credit`` witness extraction (Title 11 §§361-362 fixture)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.us_federal.source_tree import parse_usc_title_document
from lawvm.us_federal.usc_witness import (
    extract_title_witnesses,
    parse_source_credit_witnesses,
    section_public_law_witnesses,
    witness_congress_histogram,
)

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "us_federal"
    / "USCODE-2023-title11-ch3-subchIV-fixture.htm"
)


@pytest.fixture(scope="module")
def document():
    return parse_usc_title_document(FIXTURE.read_bytes(), year="2023", locator="fixture")


def test_parse_single_credit_witness_fields() -> None:
    credit = (
        "(Pub. L. 116–260, div. FF, title X, §1001, Dec. 27, 2020, "
        "134 Stat. 2145.)"
    )
    witnesses, unparsed = parse_source_credit_witnesses(credit, section="999")
    assert unparsed == []
    assert len(witnesses) == 1
    w = witnesses[0]
    assert w.congress == 116
    assert w.law_number == 260
    assert w.public_law_label == "Public Law 116-260"
    assert "1001" in w.pinpoints
    assert w.date_iso == "2020-12-27"
    assert w.statutes_at_large == "134 Stat. 2145"


def test_section_361_two_enactment_witnesses(document) -> None:
    s361 = document.section_by_number("361")
    assert s361 is not None
    witnesses = section_public_law_witnesses(s361)
    keys = [(w.congress, w.law_number) for w in witnesses]
    assert keys == [(95, 598), (98, 353)]
    # Original enactment credit has no amending pinpoint §.
    assert witnesses[0].pinpoints == ()
    # The 1984 amendment cites §440.
    assert "440" in witnesses[1].pinpoints
    assert witnesses[0].date_iso == "1978-11-06"


def test_section_362_lineage_and_window(document) -> None:
    s362 = document.section_by_number("362")
    assert s362 is not None
    witnesses = section_public_law_witnesses(s362)
    # §362 has a long lineage; the original enactment plus many amendments.
    assert len(witnesses) >= 10
    keys = {(w.congress, w.law_number) for w in witnesses}
    assert (95, 598) in keys  # original enactment
    assert (109, 8) in keys  # BAPCPA 2005
    assert (116, 189) in keys  # 2020 amendment


def test_title_witness_report_aggregation(document) -> None:
    report = extract_title_witnesses(document)
    assert report.title == 11
    assert report.section_count == 2
    assert report.unparsed == []
    # (section, PL) pairs cover both sections.
    pairs = report.public_law_pairs()
    assert ("361", (95, 598)) in pairs
    assert ("362", (109, 8)) in pairs
    # The original 1978 enactment (Congress 95) is shared by both sections.
    assert report.count_in_window(congress=95) == 2


def test_window_counter_bounds(document) -> None:
    report = extract_title_witnesses(document)
    # Modern Congresses 113-118 window on §362's lineage.
    n_recent = report.count_in_window(min_congress=113, max_congress=118)
    n_all = report.count_in_window()
    assert 0 < n_recent < n_all
    # A window with no laws yields zero, not an error.
    assert report.count_in_window(min_congress=200, max_congress=300) == 0


def test_congress_histogram(document) -> None:
    report = extract_title_witnesses(document)
    hist = witness_congress_histogram(report)
    assert hist[95] == 2  # both sections enacted by PL 95-598
    assert all(isinstance(k, int) and v > 0 for k, v in hist.items())


def test_unparsed_public_law_is_typed_not_dropped() -> None:
    # A malformed segment lacking a clean congress-number head is a typed finding.
    credit = "(Pub. L. ZZ, no number, Dec. 1, 2020, 134 Stat. 1.)"
    witnesses, unparsed = parse_source_credit_witnesses(credit, section="42")
    assert witnesses == ()
    assert len(unparsed) == 1
    assert unparsed[0]["rule_id"] == "us_usc_source_credit_unparsed_public_law"
    assert unparsed[0]["section"] == "42"
