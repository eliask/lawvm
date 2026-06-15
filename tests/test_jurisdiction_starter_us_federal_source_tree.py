"""USC annual-edition xhtml source-tree parsing (Title 11 §§361-362 fixture).

No network: parses a committed small xhtml fixture and round-trips one staged
edition through a tmp farchive via the USC import module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.core.ir import LegalAddress
from lawvm.us_federal.source_tree import (
    iter_section_notes,
    parse_usc_title_document,
    split_statutory_subsections,
    strip_replacement_section_catchline,
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


_NOTES_HTM = b"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN"
"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
 <head>
<!-- AUTHORITIES-USC-TITLE-ENUM:99 -->
 </head>
 <body>
  <div>
<!-- expcite:TITLE 99-SYNTHETIC!@!CHAPTER 1!@!Sec. 10 -->
<h3 class="section-head">&sect;10. Section with temporal notes</h3>
<!-- field-start:statute -->
<p class="statutory-body">The applicable debt limit is $250,000.</p>
<!-- field-end:statute -->
<!-- field-start:sourcecredit -->
<p class="source-credit">(Pub. L. 99&ndash;1.)</p>
<!-- field-end:sourcecredit -->
<!-- field-start:notes -->
<h3 class="note-head">Amendments</h3>
<p class="note-body">2022 &mdash; Pub. L. 117&ndash;151 amended this section to read as it read on the day before June 21, 2022.</p>
<h3 class="note-head">Effective Date of 2022 Amendment</h3>
<p class="note-body">the amendment is effective on the date that is 2 years after June 21, 2022.</p>
<!-- field-end:notes -->
  </div>
 </body>
</html>
"""


def test_section_notes_extracted_without_polluting_statutory_text() -> None:
    doc = parse_usc_title_document(_NOTES_HTM, title=99, year="2024")
    section = doc.section_by_number("10")
    assert section is not None
    # The statutory comparison surface is the statute body only; note text must
    # NOT leak into it.
    assert section.statutory_text == "The applicable debt limit is $250,000."
    assert "June 21, 2022" not in section.statutory_text
    # The note blocks are exposed as (head, bodies) pairs in document order.
    notes = list(iter_section_notes(section))
    heads = [n.head for n in notes]
    assert heads == ["Amendments", "Effective Date of 2022 Amendment"]
    assert "to read as it read on the day before" in notes[0].bodies[0]
    assert "2 years after June 21, 2022" in notes[1].text


_FOOTNOTE_REF_HTM = b"""<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
 <head><title>T99</title><!-- AUTHORITIES-USC-TITLE-ENUM:99 --></head>
 <body>
  <div>
<!-- expcite:TITLE 99!@!CHAPTER 1!@!Sec. 10 -->
<!-- field-start:head -->
<h3 class="section-head">&sect;10. Footnote demo</h3>
<!-- field-end:head -->
<!-- field-start:statute -->
<p class="statutory-body">The provisions shall not apply to number&#160;<sup><a href="#99_1_target" name="99_1">1</a></sup> of officers after that date.</p>
<!-- field-end:statute -->
<!-- field-start:sourcecredit -->
<p class="source-credit">(Pub. L. 99&ndash;1.)</p>
<!-- field-end:sourcecredit -->
  </div>
 </body>
</html>
"""


def test_footnote_reference_superscript_is_stripped_from_statutory_text() -> None:
    # The OLRC tags an editorial footnote reference (``So in original. Probably
    # should be 'the number'``) as a ``<sup><a href="#X_target">N</a></sup>`` whose
    # visible glyph is a bare digit. That digit is NOT statutory text — folding it in
    # turns ``to number of`` into ``to number 1 of`` and manufactures a spurious
    # before/after divergence. The parser must drop the digit but keep the tail.
    doc = parse_usc_title_document(_FOOTNOTE_REF_HTM, title=99, year="2018")
    section = doc.section_by_number("10")
    assert section is not None
    assert section.statutory_text == (
        "The provisions shall not apply to number of officers after that date."
    )
    # The stray footnote digit never reaches the statutory comparison surface.
    assert "number 1 of" not in section.statutory_text


# A synthetic section reproducing the OLRC "run-in + flattened-depth" shape that
# the CSS-indent-only splitter mis-addressed (the §1325(b) family): subsection (b)
# opens RUN-IN with its first paragraph on one ``statutory-body`` line (``(b)(1)``),
# and the FOLLOWING paragraphs (2)/(3) are flattened to the same ``statutory-body``
# depth as a subsection — yet they are paragraphs UNDER (b), distinguished only by
# the enumerator token TYPE (digit ⇒ paragraph, not a new lettered subsection).
_RUNIN_HTM = b"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN"
"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
 <head>
<!-- AUTHORITIES-USC-TITLE-ENUM:11 -->
 </head>
 <body>
  <div>
<!-- expcite:TITLE 11-BANKRUPTCY!@!CHAPTER 13!@!Sec. 1325 -->
<h3 class="section-head">&sect;1325. Synthetic run-in confirmation</h3>
<!-- field-start:statute -->
<p class="statutory-body">(a) The court shall confirm a plan if&mdash;</p>
<p class="statutory-body-1em">(1) the plan complies with this chapter;</p>
<p class="statutory-body-1em">(2) any required fee has been paid; and</p>
<p class="statutory-body">(b)(1) If the trustee objects to confirmation, the court may not approve unless&mdash;</p>
<p class="statutory-body-1em">(A) the value distributed is not less than the claim; or</p>
<p class="statutory-body-1em">(B) the plan provides all projected disposable income.</p>
<p class="statutory-body">(2) For purposes of this subsection, the term "disposable income" means current monthly income.</p>
<p class="statutory-body-1em">(A)(i) for the support of the debtor; and</p>
<p class="statutory-body-1em">(ii) for charitable contributions.</p>
<p class="statutory-body">(3) Amounts reasonably necessary shall be determined.</p>
<p class="statutory-body">(c) After confirmation, the court may order an entity to pay.</p>
<!-- field-end:statute -->
<!-- field-start:sourcecredit -->
<p class="source-credit">(Pub. L. 95&ndash;598.)</p>
<!-- field-end:sourcecredit -->
  </div>
 </body>
</html>
"""


def _runin_section():
    doc = parse_usc_title_document(_RUNIN_HTM, title=11, year="2018")
    section = doc.section_by_number("1325")
    assert section is not None
    return section


def test_split_runin_marker_addresses_paragraph_under_subsection() -> None:
    """A run-in ``(b)(1)`` line opens BOTH subsection (b) and its paragraph (1);
    the following flattened ``(2)``/``(3)`` are paragraphs under (b), not new
    subsections. The CSS-indent-only splitter addressed them as subsection (2)."""
    section = _runin_section()
    nodes, _findings = split_statutory_subsections(section)
    by_segs = {
        n.address.path[2:]: n for n in nodes
    }

    # The run-in line is reachable as BOTH the container subsection (b) and the
    # run-in paragraph (b)(1); both anchor on the same span.
    assert (("subsection", "b"),) in by_segs
    assert (("subsection", "b"), ("paragraph", "1")) in by_segs
    runin = by_segs[(("subsection", "b"), ("paragraph", "1"))]
    assert runin.text.startswith("(b)(1) If the trustee objects")

    # The flattened depth-0 ``(2)`` is paragraph (b)(2), NOT subsection (2).
    assert (("subsection", "b"), ("paragraph", "2")) in by_segs
    assert (("subsection", "2"),) not in by_segs
    para2 = by_segs[(("subsection", "b"), ("paragraph", "2"))]
    assert para2.kind == "paragraph"
    assert para2.text.startswith("(2) For purposes of this subsection")

    # (b)(3) likewise a paragraph under (b); (c) reopens the subsection level.
    assert (("subsection", "b"), ("paragraph", "3")) in by_segs
    assert (("subsection", "c"),) in by_segs


def test_split_runin_nested_subparagraph_clause_ladder() -> None:
    """A run-in ``(A)(i)`` under (b)(2) nests subparagraph then clause; the
    following ``(ii)`` is a clause sibling, not a new top-level letter."""
    section = _runin_section()
    nodes, _findings = split_statutory_subsections(section)
    segs = {n.address.path[2:] for n in nodes}
    assert (
        ("subsection", "b"),
        ("paragraph", "2"),
        ("subparagraph", "A"),
    ) in segs
    assert (
        ("subsection", "b"),
        ("paragraph", "2"),
        ("subparagraph", "A"),
        ("clause", "i"),
    ) in segs
    assert (
        ("subsection", "b"),
        ("paragraph", "2"),
        ("subparagraph", "A"),
        ("clause", "ii"),
    ) in segs


def test_locate_subsection_text_finds_runin_paragraph() -> None:
    """``_locate_subsection_text`` resolves the (b)(2) node the dry-run kernel
    needs (the formerly node-not-located residual class)."""
    from lawvm.us_federal.dry_run import _locate_subsection_text

    section = _runin_section()
    target = LegalAddress(
        path=(
            ("title", "11"),
            ("section", "1325"),
            ("subsection", "b"),
            ("paragraph", "2"),
        )
    )
    located = _locate_subsection_text(section, target)
    assert located is not None
    assert located.startswith("(2) For purposes of this subsection")
    # And it is a faithful substring of the section body (anchorable for replay).
    assert located in section.statutory_text


def test_locate_subsection_text_absent_node_stays_unlocated() -> None:
    """A genuinely-absent sub-section node returns None (the dry-run kernel keeps
    a typed residual) — never a fuzzy match onto a present sibling."""
    from lawvm.us_federal.dry_run import _locate_subsection_text

    section = _runin_section()
    # (b)(99) does not exist; the splitter must not hand back a sibling paragraph.
    absent = LegalAddress(
        path=(
            ("title", "11"),
            ("section", "1325"),
            ("subsection", "b"),
            ("paragraph", "99"),
        )
    )
    assert _locate_subsection_text(section, absent) is None
    # A subsection letter past the end of the ladder likewise stays unlocated.
    absent_subsec = LegalAddress(
        path=(("title", "11"), ("section", "1325"), ("subsection", "z"))
    )
    assert _locate_subsection_text(section, absent_subsec) is None


# ---------------------------------------------------------------------------
# Replacement-payload catchline projection (amend-to-read whole-section)
# ---------------------------------------------------------------------------


def test_strip_replacement_section_catchline_drops_own_catchline_to_body() -> None:
    """An amend-to-read payload opens with the section's own ``§ <num>. <heading>``
    catchline before the first quoted body unit; the body-only oracle surface
    carries the catchline in the heading, so projecting the payload onto that
    surface drops the catchline. The cut ends exactly at the first body quote —
    even when the heading itself contains internal periods (``Art. 10. ...``)."""
    payload = (
        "§ 2196. Manufacturing engineering education program"
        "“(a) Establishment.—(1) The Secretary shall establish a program."
    )
    body = strip_replacement_section_catchline(payload, "2196")
    assert body == "“(a) Establishment.—(1) The Secretary shall establish a program."

    # A UCMJ-style heading with internal periods is cut at the body quote, not the
    # first period (which would mangle ``Art. 10.``).
    ucmj = "§ 810. Art. 10. Restraint of persons charged“(a) In General.—Subject to x."
    assert (
        strip_replacement_section_catchline(ucmj, "810")
        == "“(a) In General.—Subject to x."
    )


def test_strip_replacement_section_catchline_refuses_when_unsafe() -> None:
    """The stripper returns None (caller keeps the payload verbatim) when it cannot
    delimit the catchline safely: a mismatched section number (a leading reference,
    not this section's own catchline) or no curly-quote body marker to cut at."""
    # Number mismatch: a leading cross-reference that starts with §, NOT our
    # section's catchline — must never be stripped (it would delete real text).
    assert (
        strip_replacement_section_catchline("§ 999. Other heading“(a) x.", "2196")
        is None
    )
    # No body-marker quote: a bare renamed-heading payload cannot be delimited
    # without risking a cut into a period-bearing heading — keep it verbatim.
    assert (
        strip_replacement_section_catchline("§ 3084. Chief of Veterinary Corps", "3084")
        is None
    )
    # Plain body (no catchline at all) is left untouched.
    assert strip_replacement_section_catchline("(a) The program shall.", "100") is None
