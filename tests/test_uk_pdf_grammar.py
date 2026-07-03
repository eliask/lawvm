"""Tests for the UK PDF-only-Act grammar prototype.

Pins two net-new modules from the first PDF-import increment:

  1. ``lawvm.uk_legislation.pdf_grammar`` — the UK section/Schedule token grammar
     and eId normaliser (the analogue of Finland's ``attachment_ir`` grammar).
  2. ``lawvm.uk_legislation.pdf_acquire`` — the PDF-URL extractor + PDF-lane
     locator scheme (pure, no-network parts).

Grammar coverage: UK ``1.—(1)`` section+subsection, bare ``(2)`` subsection,
``(a)`` item, ``PART I`` division, ``SCHEDULE`` + ``Part I`` + schedule
paragraphs, ``CHAPTER`` banner. A synthetic fixture act exercises every arm and
asserts the expected spine + legislation.gov.uk-style eIds.
"""

from __future__ import annotations

import textwrap

import pytest

from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation import pdf_acquire, pdf_grammar


# ---------------------------------------------------------------------------
# Grammar: per-arm recognition
# ---------------------------------------------------------------------------


def test_section_with_inline_subsection_recognised() -> None:
    m = pdf_grammar._SECTION_SUBSEC_RE.match("1.—(1) The coming into force")
    assert m is not None
    assert m.group(1) == "1"
    assert m.group(2) == "1"
    # Letter-suffixed inserted section (e.g. inserted 27A).
    m2 = pdf_grammar._SECTION_SUBSEC_RE.match("27A.—(1) Where the tribunal")
    assert m2 is not None and m2.group(1) == "27A" and m2.group(2) == "1"


def test_bare_subsection_and_item_recognised() -> None:
    assert pdf_grammar._SUBSEC_RE.match("(2) An application shall not")
    assert pdf_grammar._SUBSEC_RE.match("(12) Something")
    assert pdf_grammar._ITEM_ALPHA_RE.match("(a) the licensing body")
    # A subsection number must NOT be read as an alpha item, and vice versa.
    assert pdf_grammar._ITEM_ALPHA_RE.match("(3) text") is None
    assert pdf_grammar._SUBSEC_RE.match("(a) text") is None


def test_part_schedule_chapter_recognised() -> None:
    m_part_i = pdf_grammar._PART_RE.match("PART I")
    assert m_part_i is not None and m_part_i.group(1) == "I"
    m_part_2 = pdf_grammar._PART_RE.match("PART 2")
    assert m_part_2 is not None and m_part_2.group(1) == "2"
    assert pdf_grammar._SCHEDULE_RE.match("SCHEDULE")
    assert pdf_grammar._SCHEDULE_RE.match("THE SCHEDULE")
    m_snum = pdf_grammar._SCHEDULE_NUM_RE.search("SCHEDULE 3")
    assert m_snum is not None and m_snum.group(1) == "3"
    m_chap = pdf_grammar._CHAPTER_BANNER_RE.match("1975 CHAPTER 4")
    assert m_chap is not None and m_chap.group(2) == "4"
    assert pdf_grammar._ordinal_schedule_number("SECOND SCHEDULE") == 2


# ---------------------------------------------------------------------------
# eId normalisation (legislation.gov.uk conventions)
# ---------------------------------------------------------------------------


def test_eid_conventions() -> None:
    assert pdf_grammar.section_eid("1") == "section-1"
    assert pdf_grammar.section_eid("27A") == "section-27A"
    assert pdf_grammar.subsection_eid("1", "2") == "section-1-2"
    assert pdf_grammar.part_eid("I") == "part-I"
    assert pdf_grammar.schedule_eid(1) == "schedule-1"
    assert pdf_grammar.schedule_paragraph_eid(1, "2") == "schedule-1-paragraph-2"


# ---------------------------------------------------------------------------
# Fixture act → expected spine
# ---------------------------------------------------------------------------


_FIXTURE_ACT = textwrap.dedent(
    """\
    1975 CHAPTER 4

    An Act to make provision for things.

    PART I
    Preliminary

    1.—(1) The first substantive provision has effect.
    (a) one thing, and
    (b) another thing.
    (2) A second subsection follows.

    2. A plain section with no inline subsection.

    SCHEDULE
    Part I
    1. First schedule paragraph.
    2. Second schedule paragraph.
    (a) a schedule item.
    """
)


def test_fixture_act_spine() -> None:
    ir = pdf_grammar.pdf_text_to_uk_ir(_FIXTURE_ACT, source_ref="ukpga/1975/4")
    assert ir.kind is IRNodeKind.BODY
    assert ir.attrs.get("source_lane") == "pdf"
    assert ir.attrs.get("chapter_year") == "1975"
    assert ir.attrs.get("chapter_number") == "4"

    summary = pdf_grammar.spine_summary(ir)
    counts = summary["counts"]
    assert counts["part"] == 2  # body PART I + schedule Part I
    assert counts["section"] == 2  # section 1 (inline subsec) + section 2 (plain)
    assert counts["subsection"] == 2  # (1) inline + (2) bare
    assert counts["schedule"] == 1
    assert counts["paragraph"] == 2  # two schedule paragraphs
    assert summary["section_labels"] == ["1", "2"]


def test_fixture_act_eids_and_nesting() -> None:
    ir = pdf_grammar.pdf_text_to_uk_ir(_FIXTURE_ACT, source_ref="ukpga/1975/4")

    # Body PART I contains section 1 with the expected eIds.
    part = next(c for c in ir.children if c.kind is IRNodeKind.PART and c.label == "I")
    assert part.attrs.get("eId") == "part-I"
    sec1 = next(c for c in part.children if c.kind is IRNodeKind.SECTION)
    assert sec1.label == "1"
    assert sec1.attrs.get("eId") == "section-1"

    sub1 = next(c for c in sec1.children if c.kind is IRNodeKind.SUBSECTION)
    assert sub1.attrs.get("eId") == "section-1-1"
    items = [c for c in sub1.children if c.kind is IRNodeKind.ITEM]
    assert [i.label for i in items] == ["a", "b"]

    # Schedule with a Part and paragraphs bearing schedule-scoped eIds.
    sched = next(c for c in ir.children if c.kind is IRNodeKind.SCHEDULE)
    assert sched.attrs.get("eId") == "schedule-1"
    sched_part = next(c for c in sched.children if c.kind is IRNodeKind.PART)
    paras = [c for c in sched_part.children if c.kind is IRNodeKind.PARAGRAPH]
    assert [p.label for p in paras] == ["1", "2"]
    assert paras[0].attrs.get("eId") == "schedule-1-paragraph-1"
    assert paras[1].attrs.get("eId") == "schedule-1-paragraph-2"


def test_empty_input_yields_empty_body() -> None:
    ir = pdf_grammar.pdf_text_to_uk_ir("")
    assert ir.kind is IRNodeKind.BODY
    assert ir.children == ()


# ---------------------------------------------------------------------------
# Acquisition: PDF-URL extraction + lane locator (pure, no network)
# ---------------------------------------------------------------------------


_STUB_XML = (
    b'<Legislation xmlns="http://www.legislation.gov.uk/namespaces/legislation">'
    b'<ukm:Metadata xmlns:atom="http://www.w3.org/2005/Atom"'
    b' xmlns:ukm="http://www.legislation.gov.uk/namespaces/metadata">'
    b'<atom:link rel="alternate" type="application/pdf"'
    b' href="http://www.legislation.gov.uk/ukpga/1983/38/pdfs/ukpga_19830038_en.pdf"/>'
    b'<ukm:Alternatives><ukm:Alternative Date="2025-03-20" Size="2296521"'
    b' URI="http://www.legislation.gov.uk/ukpga/1983/38/pdfs/ukpga_19830038_en.pdf"'
    b' Print="true"/></ukm:Alternatives>'
    b'</ukm:Metadata></Legislation>'
)


def test_extract_pdf_url_from_stub() -> None:
    alt = pdf_acquire.extract_pdf_url_from_stub(_STUB_XML)
    assert alt is not None
    assert alt.url.endswith("/pdfs/ukpga_19830038_en.pdf")
    assert alt.size_bytes == 2296521  # from the preferred ukm:Alternative
    assert alt.date == "2025-03-20"


def test_extract_pdf_url_atom_fallback() -> None:
    # A stub with only the atom:link PDF form (no ukm:Alternatives).
    stub = (
        b'<Legislation xmlns:atom="http://www.w3.org/2005/Atom">'
        b'<atom:link rel="alternate" type="application/pdf"'
        b' href="http://www.legislation.gov.uk/ukpga/1971/4/pdfs/ukpga_19710004_en.pdf"/>'
        b"</Legislation>"
    )
    alt = pdf_acquire.extract_pdf_url_from_stub(stub)
    assert alt is not None
    assert alt.url.endswith("/pdfs/ukpga_19710004_en.pdf")
    assert alt.size_bytes is None


def test_extract_pdf_url_none_when_absent() -> None:
    assert pdf_acquire.extract_pdf_url_from_stub(b"<Legislation/>") is None
    assert pdf_acquire.extract_pdf_url_from_stub(b"not xml") is None


def test_pdf_lane_locator_normalises_scheme_and_host() -> None:
    http = "http://www.legislation.gov.uk/ukpga/1983/38/pdfs/ukpga_19830038_en.pdf"
    https = "https://www.legislation.gov.uk/ukpga/1983/38/pdfs/ukpga_19830038_en.pdf"
    expected = "leg://pdf/ukpga/1983/38/pdfs/ukpga_19830038_en.pdf"
    assert pdf_acquire.pdf_lane_locator(http) == expected
    assert pdf_acquire.pdf_lane_locator(https) == expected  # scheme-agnostic


def test_looks_like_pdf_guard() -> None:
    assert pdf_acquire._looks_like_pdf(b"%PDF-1.4\n...")
    assert not pdf_acquire._looks_like_pdf(b"<!DOCTYPE html><html>error</html>")


def test_enacted_stub_url() -> None:
    assert (
        pdf_acquire.enacted_stub_url("ukpga/1983/38")
        == "https://www.legislation.gov.uk/ukpga/1983/38/enacted/data.xml"
    )


# ---------------------------------------------------------------------------
# _parse_statute_id — modern calendar-year AND 4-part regnal citations (#177)
# ---------------------------------------------------------------------------


def test_parse_statute_id_modern_calendar_year() -> None:
    assert pdf_acquire._parse_statute_id("ukpga/2020/17") == ("ukpga", "2020", "17")
    assert pdf_acquire._parse_statute_id("asp/2010/5") == ("asp", "2010", "5")
    # leading/trailing slashes are stripped
    assert pdf_acquire._parse_statute_id("/ukpga/1983/38/") == ("ukpga", "1983", "38")


def test_parse_statute_id_regnal_citations() -> None:
    # The three C19 acts whose segmentation was proven in #177.
    assert pdf_acquire._parse_statute_id("ukpga/Vict/45-46/61") == (
        "ukpga",
        "Vict/45-46",
        "61",
    )  # Bills of Exchange Act 1882
    assert pdf_acquire._parse_statute_id("ukpga/Vict/53-54/39") == (
        "ukpga",
        "Vict/53-54",
        "39",
    )  # Partnership Act 1890
    assert pdf_acquire._parse_statute_id("ukpga/Vict/38-39/90") == (
        "ukpga",
        "Vict/38-39",
        "90",
    )  # Public Health Act 1875


def test_parse_statute_id_regnal_single_year_and_other_monarchs() -> None:
    # Single (non-spanning) regnal year.
    assert pdf_acquire._parse_statute_id("ukpga/Geo5/1/28") == (
        "ukpga",
        "Geo5/1",
        "28",
    )
    # Other monarch abbreviations with a numeric ordinal.
    assert pdf_acquire._parse_statute_id("ukpga/Edw7/7/12") == (
        "ukpga",
        "Edw7/7",
        "12",
    )
    assert pdf_acquire._parse_statute_id("ukpga/Will4/1-2/76") == (
        "ukpga",
        "Will4/1-2",
        "76",
    )


def test_enacted_stub_url_regnal() -> None:
    assert (
        pdf_acquire.enacted_stub_url("ukpga/Vict/45-46/61")
        == "https://www.legislation.gov.uk/ukpga/Vict/45-46/61/enacted/data.xml"
    )


def test_parse_statute_id_invalid_forms_raise() -> None:
    # Too few parts.
    with pytest.raises(ValueError):
        pdf_acquire._parse_statute_id("ukpga/2020")
    with pytest.raises(ValueError):
        pdf_acquire._parse_statute_id("bad")
    # Too many parts.
    with pytest.raises(ValueError):
        pdf_acquire._parse_statute_id("ukpga/Vict/45-46/61/extra")
    # 4-part but not a regnal citation (a lowercase / numeric "monarch").
    with pytest.raises(ValueError):
        pdf_acquire._parse_statute_id("ukpga/2020/17/1")
    with pytest.raises(ValueError):
        pdf_acquire._parse_statute_id("ukpga/vict/45-46/61")
    # Empty path segment.
    with pytest.raises(ValueError):
        pdf_acquire._parse_statute_id("ukpga//45-46/61")


def test_tier1_regnal_batch_is_parseable() -> None:
    # Every id in the bounded tier-1 batch must parse (and be a regnal citation).
    for sid in pdf_acquire.TIER1_REGNAL_BATCH:
        act_type, year, number = pdf_acquire._parse_statute_id(sid)
        assert act_type == "ukpga"
        assert "/" in year  # regnal middle carries monarch/regnal-years
        assert number.isdigit()
