"""USC annual-edition xhtml source-tree parsing (Title 11 §§361-362 fixture).

No network: parses a committed small xhtml fixture and round-trips one staged
edition through a tmp farchive via the USC import module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.core.ir import LegalAddress
from lawvm.us_federal.source_tree import (
    parse_usc_title_document,
    split_statutory_subsections,
    summarize_indent_classes,
    usc_section_address,
)

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "us_federal"
    / "USCODE-2023-title11-ch3-subchIV-fixture.htm"
)


@pytest.fixture(scope="module")
def fixture_bytes() -> bytes:
    return FIXTURE.read_bytes()


def test_fixture_section_count_and_numbers(fixture_bytes: bytes) -> None:
    doc = parse_usc_title_document(fixture_bytes, year="2023", locator="fixture")
    assert doc.title == 11
    assert [s.section for s in doc.sections] == ["361", "362"]
    assert doc.report.section_count == 2
    assert doc.report.repealed_count == 0
    # Clean ladder: no shape findings, both sections carry a source-credit.
    assert doc.report.findings == []
    assert doc.report.sections_without_source_credit == []
    assert doc.report.sections_without_statutory_text == []


def test_section_address_is_title_section_pinned_convention(fixture_bytes: bytes) -> None:
    doc = parse_usc_title_document(fixture_bytes, year="2023")
    s362 = doc.section_by_number("362")
    assert s362 is not None
    assert s362.address == LegalAddress(
        path=(("title", "11"), ("section", "362"))
    )
    assert s362.address == usc_section_address(11, "362")
    assert s362.heading == "Automatic stay"
    # Chapter/subchapter are structural containers, not part of the address.
    assert s362.chapter == "3"
    assert s362.subchapter == "IV"


def test_known_section_statutory_text_and_credit(fixture_bytes: bytes) -> None:
    doc = parse_usc_title_document(fixture_bytes, year="2023")
    s361 = doc.section_by_number("361")
    assert s361 is not None
    assert s361.heading == "Adequate protection"
    # Statutory body present and editorial notes excluded.
    assert s361.statutory_text.startswith(
        "When adequate protection is required under section 362"
    )
    assert "Editorial Notes" not in s361.statutory_text
    assert "Pub. L." not in s361.statutory_text  # source-credit not folded in
    # Raw source-credit captured verbatim (en-dash entity decoded).
    assert s361.source_credit_raw.startswith("(Pub. L. 95–598")
    assert "98 Stat. 370" in s361.source_credit_raw


def test_section_oracle_rows_shape(fixture_bytes: bytes) -> None:
    from lawvm.us_federal.source_tree import iter_section_oracle_rows

    doc = parse_usc_title_document(fixture_bytes, year="2023")
    rows = list(iter_section_oracle_rows(doc))
    assert len(rows) == 2
    for address, text, credit in rows:
        assert isinstance(address, LegalAddress)
        assert address.path[0] == ("title", "11")
        assert text
        assert credit.startswith("(Pub. L.")


def test_subsection_split_maps_pinned_convention(fixture_bytes: bytes) -> None:
    doc = parse_usc_title_document(fixture_bytes, year="2023")
    s362 = doc.section_by_number("362")
    assert s362 is not None
    nodes, findings = split_statutory_subsections(s362)
    assert findings == []  # clean ladder for §362
    assert len(nodes) > 50
    first = nodes[0]
    assert first.kind == "subsection"
    assert first.label == "a"
    assert first.address.path[:2] == (("title", "11"), ("section", "362"))
    assert first.address.path[2] == ("subsection", "a")
    # A nested paragraph under (a): title/section/subsection:a/paragraph:1.
    para = next(
        n
        for n in nodes
        if n.address.path[2:] == (("subsection", "a"), ("paragraph", "1"))
    )
    assert para.kind == "paragraph"
    # A deeper clause exists somewhere in §362(b)(2)(A)(i)...
    kinds = {n.kind for n in nodes}
    assert {"subsection", "paragraph", "subparagraph", "clause"} <= kinds


def test_indent_class_histogram_excludes_editorial(fixture_bytes: bytes) -> None:
    doc = parse_usc_title_document(fixture_bytes, year="2023")
    classes = summarize_indent_classes(doc)
    assert all(c.startswith("statutory-body") for c in classes)
    assert classes["statutory-body"] > 0


def test_import_usc_roundtrip_tmp_farchive(tmp_path: Path) -> None:
    """Import the fixture into a tmp farchive and round-trip a get."""
    from lawvm.us_federal.import_usc import import_usc_sources
    from lawvm.us_federal.sources import (
        UscAnnualIdentity,
        content_digest,
        open_us_federal_farchive,
        read_usc_annual,
    )

    db_path = tmp_path / "us_federal.farchive"
    identity = UscAnnualIdentity(year=2023, title=11)
    report = import_usc_sources(
        [(FIXTURE, identity)], db_path=db_path
    )
    assert report.total_imported == 1
    assert report.total_errors == 0
    assert report.imported_locators == ["us://usc/2023/title11.htm"]

    archive = open_us_federal_farchive(db_path, readonly=True)
    try:
        data = read_usc_annual(archive, 2023, 11)
        assert data is not None
        assert data == FIXTURE.read_bytes()
        span = archive.resolve("us://usc/2023/title11.htm")
        assert span is not None
        md = dict(span.last_metadata or {})
        assert md["year"] == "2023"
        assert md["title"] == "11"
        assert md["sha256"] == content_digest(data)
        assert md["laws_enacted_through"] == "20240103"
    finally:
        archive.close()


def test_import_usc_skips_unrecognized_member(tmp_path: Path) -> None:
    from lawvm.us_federal.import_usc import import_usc_sources

    bad = tmp_path / "not-a-uscode-file.htm"
    bad.write_bytes(b"<html></html>")
    db_path = tmp_path / "us_federal.farchive"
    report = import_usc_sources([(bad, None)], db_path=db_path)
    assert report.total_imported == 0
    assert report.total_skipped == 1
    assert report.skipped_entries[0]["rule_id"] == "us_usc_import_unrecognized_member"
