"""USC annual-edition xhtml source-tree parsing (Title 11 §§361-362 fixture).

No network: parses a committed small xhtml fixture and round-trips one staged
edition through a tmp farchive via the USC import module.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.core.ir import LegalAddress
from lawvm.us_federal import source_tree
from lawvm.us_federal.source_tree import (
    UscSection,
    UscStatutoryParagraph,
    iter_section_notes,
    parse_usc_title_document,
    split_statutory_subsections,
    strip_replacement_section_catchline,
    summarize_indent_classes,
    synthetic_usc_section,
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


def test_parse_usc_title_document_cache_reuses_sections_with_fresh_report(
    fixture_bytes: bytes,
) -> None:
    source_tree._usc_title_document_cache.clear()
    first = parse_usc_title_document(fixture_bytes, year="2023", locator="fixture")
    second = parse_usc_title_document(fixture_bytes, year="2023", locator="fixture")

    assert first.sections is second.sections
    assert first.report is not second.report
    first.report.findings.append({"rule_id": "test_mutation", "reason": "local"})
    third = parse_usc_title_document(fixture_bytes, year="2023", locator="fixture")
    assert third.report.findings == []


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


def test_flush_hang_paragraphs_with_leading_markers_are_structural_nodes() -> None:
    """The OLRC uses negative-indent "flush/hang" CSS classes for paragraphs that are
    structurally children of a subsection (e.g., Title 28 §124 divisions (1)-(7)).
    The marker itself, not the visual indent, determines the structural level.
    """
    section = UscSection(
        title=28,
        section="124",
        heading="Texas judicial districts",
        address=LegalAddress(
            path=(
                ("title", "28"),
                ("section", "124"),
            )
        ),
        statutory_text=(
            "(a) The Western District comprises seven divisions. "
            "(1) The Austin Division comprises A. (2) The Pecos Division comprises B. "
            "Court for the Pecos Division shall be held at Pecos. "
            "(b) The Eastern District."
        ),
        source_credit_raw="",
        repealed=False,
        paragraphs=(
            UscStatutoryParagraph(
                indent_depth=0,
                css_class="statutory-body",
                text="(a) The Western District comprises seven divisions.",
            ),
            # These mimic the OLRC flush2/hang4 class: visually flush, but markers
            # make them paragraph-level structural children of (a).
            UscStatutoryParagraph(
                indent_depth=-1,
                css_class="statutory-body-flush2_hang4",
                text="(1) The Austin Division comprises A.",
            ),
            UscStatutoryParagraph(
                indent_depth=-1,
                css_class="statutory-body-flush2_hang4",
                text="(2) The Pecos Division comprises B.",
            ),
            UscStatutoryParagraph(
                indent_depth=-1,
                css_class="statutory-body-flush2_hang4",
                text="Court for the Pecos Division shall be held at Pecos.",
            ),
            UscStatutoryParagraph(
                indent_depth=0,
                css_class="statutory-body",
                text="(b) The Eastern District.",
            ),
        ),
        notes=(),
    )
    nodes, findings = split_statutory_subsections(section)
    by_segs = {n.address.path[2:]: n for n in nodes}

    # Flush/hang markers are parsed as paragraph nodes, not swallowed as continuation.
    assert (("subsection", "a"), ("paragraph", "1")) in by_segs
    assert (("subsection", "a"), ("paragraph", "2")) in by_segs
    para1 = by_segs[(("subsection", "a"), ("paragraph", "1"))]
    assert para1.text.startswith("(1) The Austin Division")
    para2 = by_segs[(("subsection", "a"), ("paragraph", "2"))]
    assert para2.text.startswith("(2) The Pecos Division")

    # The continuation line attaches to the open paragraph node.
    assert "Court for the Pecos Division shall be held at Pecos" in para2.text

    # No ambiguous findings for this clean shape.
    assert not findings


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


# ---------------------------------------------------------------------------
# Subsection-split node-coverage: digit-letter paragraph + frontier roman/letter
# disambiguation (the node-not-located residual classes).
# ---------------------------------------------------------------------------


# A digit-letter paragraph enumerator (``(4A)``) is the USC convention for a
# paragraph inserted between ``(4)`` and ``(5)``. The token is digit-rooted, so it
# is unambiguously a paragraph — never a letter level. Before this was handled the
# splitter flagged every such marker ``us_usc_subsection_parse_ambiguous`` and the
# stack desynchronised for the rest of the definition list.
_DIGIT_LETTER_HTM = b"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN"
"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
 <head>
<!-- AUTHORITIES-USC-TITLE-ENUM:11 -->
 </head>
 <body>
  <div>
<!-- expcite:TITLE 11-BANKRUPTCY!@!CHAPTER 1!@!Sec. 101 -->
<h3 class="section-head">&sect;101. Definitions</h3>
<!-- field-start:statute -->
<p class="statutory-body-1em">(4) The term "attorney" means attorney.</p>
<p class="statutory-body-1em">(4A) The term "bankruptcy assistance" means goods or services.</p>
<p class="statutory-body-1em">(5) The term "claim" means&mdash;</p>
<p class="statutory-body-2em">(A) right to payment; or</p>
<p class="statutory-body-2em">(B) right to an equitable remedy.</p>
<!-- field-end:statute -->
<!-- field-start:sourcecredit -->
<p class="source-credit">(Pub. L. 95&ndash;598.)</p>
<!-- field-end:sourcecredit -->
  </div>
 </body>
</html>
"""


def test_split_digit_letter_paragraph_is_a_paragraph_node() -> None:
    """``(4A)`` is a paragraph (digit-rooted insert), exposed with label ``4A`` and
    kind ``paragraph`` — never flagged ambiguous, and the following ``(5)``/``(A)``
    ladder stays correctly synchronised behind it."""
    doc = parse_usc_title_document(_DIGIT_LETTER_HTM, title=11, year="2018")
    section = doc.section_by_number("101")
    assert section is not None
    nodes, findings = split_statutory_subsections(section)
    assert findings == []
    by_segs = {n.address.path[2:]: n for n in nodes}
    para4a = by_segs[(("paragraph", "4A"),)]
    assert para4a.kind == "paragraph"
    assert para4a.label == "4A"
    assert para4a.text.startswith('(4A) The term "bankruptcy assistance"')
    # The digit-letter insert does not desync the rest: (5)(A) and (5)(B) follow.
    assert (("paragraph", "5"), ("subparagraph", "A")) in by_segs
    assert (("paragraph", "5"), ("subparagraph", "B")) in by_segs


# A deep ``(A)(i)(I)`` ladder opened UNDER a far-shallower subsection ``(h)``: the
# roman/letter ambiguity (``(i)`` is both the 9th subsection-letter and the clause
# roman) must resolve to the CLAUSE first-child of the open ``(A)``, descending the
# ladder, NOT reopen the shallow ``(h)`` as the 9th subsection. Likewise ``(I)`` is
# the subclause first-child, not subparagraph ``I``.
_DEEP_LADDER_HTM = b"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN"
"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
 <head>
<!-- AUTHORITIES-USC-TITLE-ENUM:38 -->
 </head>
 <body>
  <div>
<!-- expcite:TITLE 38-VETERANS!@!CHAPTER 1!@!Sec. 7253 -->
<h3 class="section-head">&sect;7253. Synthetic deep ladder</h3>
<!-- field-start:statute -->
<p class="statutory-body">(h) Temporary expansion.&mdash;(1) During the period the court is expanded.</p>
<p class="statutory-body">(2)(A) Of the additional judges&mdash;</p>
<p class="statutory-body-1em">(i) one may be appointed in 2002; and</p>
<p class="statutory-body-2em">(I) the first appointee serves a short term; and</p>
<p class="statutory-body-2em">(II) the second appointee serves a full term.</p>
<p class="statutory-body-1em">(ii) one may be appointed in 2003.</p>
<p class="statutory-body">(i) Additional expansion.&mdash;(1) Subject to paragraph (2), the court grows.</p>
<!-- field-end:statute -->
<!-- field-start:sourcecredit -->
<p class="source-credit">(Pub. L. 100&ndash;1.)</p>
<!-- field-end:sourcecredit -->
  </div>
 </body>
</html>
"""


def test_split_deep_roman_ladder_descends_not_reopen_shallow_subsection() -> None:
    """Under an open ``(h)(2)(A)`` the ``(i)``/``(ii)`` are CLAUSES (children of A),
    and ``(I)``/``(II)`` are SUBCLAUSES — not a 9th subsection reopening ``(h)`` nor
    a subparagraph ``I``. The trailing run-in ``(i)`` subsection (after the (2)(A)
    subtree closes, with paragraph the frontier) is the real 9th subsection."""
    doc = parse_usc_title_document(_DEEP_LADDER_HTM, title=38, year="2018")
    section = doc.section_by_number("7253")
    assert section is not None
    nodes, findings = split_statutory_subsections(section)
    assert findings == []
    segs = {n.address.path[2:] for n in nodes}
    base = (("subsection", "h"), ("paragraph", "2"), ("subparagraph", "A"))
    assert base + (("clause", "i"),) in segs
    assert base + (("clause", "i"), ("subclause", "I")) in segs
    assert base + (("clause", "i"), ("subclause", "II")) in segs
    assert base + (("clause", "ii"),) in segs
    # The (2)(A) subtree never mis-opened a 9th subsection (i) or a subparagraph I.
    by_segs = {n.address.path[2:]: n for n in nodes}
    deep_i = by_segs[base + (("clause", "i"),)]
    assert deep_i.kind == "clause"
    # The trailing ``(i) Additional expansion.—(1) ...`` IS the 9th subsection
    # (it closes the (h)(2)(A) subtree): a top-level subsection sibling of (h), not
    # a clause buried under the prior subparagraph. The ``(1)`` here is prose-
    # separated (not abutting), so it stays part of the subsection's run-in text.
    assert (("subsection", "i"),) in segs
    subsec_i = by_segs[(("subsection", "i"),)]
    assert subsec_i.kind == "subsection"
    assert subsec_i.text.startswith("(i) Additional expansion")


_DASH_RUNIN_ROMAN_AMBIGUOUS_HTM = b"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN"
"http://www.w3.org/TR/xhtml/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
 <head>
<!-- AUTHORITIES-USC-TITLE-ENUM:38 -->
 </head>
 <body>
  <div>
<!-- expcite:TITLE 38-VETERANS!@!CHAPTER 73!@!Sec. 7309 -->
<h3 class="section-head">&sect;7309. Chiefs and assistant chiefs</h3>
<!-- field-start:statute -->
<p class="statutory-body">(a) In general. The Secretary may appoint chiefs.</p>
<p class="statutory-body">(b) Chief Officer.&mdash;(1) There is in the Veterans Health Administration the position of Chief Officer.</p>
<p class="statutory-body">(2) The Chief Officer is the principal adviser.</p>
<p class="statutory-body">(c) Structure.&mdash;(1) The Service is organized in directorates.</p>
<p class="statutory-body">(2) The Service provides counseling.</p>
<!-- field-end:statute -->
<!-- field-start:sourcecredit -->
<p class="source-credit">(Pub. L. 95&ndash;598.)</p>
<!-- field-end:sourcecredit -->
  </div>
 </body>
</html>
"""


def test_split_dash_runin_handles_ambiguous_letter_parent() -> None:
    """A dash-separated run-in child after an ambiguous lowercase letter (``c`` is
    both the 3rd subsection and the roman clause 100) must still be split when the
    letter resolves as a subsection in context.  Both unambiguous ``(b)`` and
    roman-ambiguous ``(c)`` expose ``paragraph:1``; the following ``(2)``
    paragraphs are siblings under that paragraph."""
    doc = parse_usc_title_document(_DASH_RUNIN_ROMAN_AMBIGUOUS_HTM, title=38, year="2014")
    section = doc.section_by_number("7309")
    assert section is not None
    nodes, findings = split_statutory_subsections(section)
    segs = {n.address.path[2:] for n in nodes}
    assert findings == []
    assert (("subsection", "b"),) in segs
    assert (("subsection", "b"), ("paragraph", "1")) in segs
    assert (("subsection", "c"),) in segs
    assert (("subsection", "c"), ("paragraph", "1")) in segs
    assert (("subsection", "c"), ("paragraph", "2")) in segs
    # The dash child shares the same source span as its parent.
    assert any(
        n.address.path[2:] == (("subsection", "c"), ("paragraph", "1"))
        and "(c) Structure" in n.text
        for n in nodes
    )


def test_split_genuinely_ambiguous_marker_stays_flagged_never_guessed() -> None:
    """A bare ``(i)`` opening with NO disambiguating ancestor stack (no open ``(A)``
    to make it a clause, no ``(h)`` to make it the 9th subsection) is genuinely
    ambiguous between subsection and clause: it must be flagged, never guessed onto
    one level. Guards the refuse-on-ambiguity contract the locator relies on."""
    htm = b"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN"
"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
 <head>
<!-- AUTHORITIES-USC-TITLE-ENUM:11 -->
 </head>
 <body>
  <div>
<!-- expcite:TITLE 11!@!Sec. 9 -->
<h3 class="section-head">&sect;9. Synthetic ambiguous opener</h3>
<!-- field-start:statute -->
<p class="statutory-body">(i) the program shall be carried out.</p>
<!-- field-end:statute -->
  </div>
 </body>
</html>
"""
    doc = parse_usc_title_document(htm, title=11, year="2018")
    section = doc.section_by_number("9")
    assert section is not None
    _nodes, findings = split_statutory_subsections(section)
    # ``(i)`` with an empty stack ties subsection (1st sibling of nothing) against
    # clause: the resolver refuses rather than pinning a level.
    assert any(
        f["rule_id"] == "us_usc_subsection_parse_ambiguous" for f in findings
    )


# A synthetic title reproducing the OLRC dashed-suffix section family. The OLRC
# renders the VISIBLE section head with an EN-DASH (``&ndash;``, U+2013) for an
# insert section numbered between two parents (``§49c–1`` between ``§49c`` and
# ``§49d``), while the structural ``itempath``/``expcite``/``href`` carry an ASCII
# hyphen. The section KEY must be the bare dashed token (``49c–1``), preserving the
# en-dash exactly as the head renders it — that is the form the USLM ``href`` the
# amendatory side pins (``/us/usc/t.../s49c–1``), so the oracle key and the lowered
# op address are byte-identical strings and the op can match. Before the fix the
# whole ``§49c–1. <heading>`` string leaked in as the section number, polluting the
# witness denominator and blocking the match.
_DASHED_SUFFIX_HTM = b"""<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN"
"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
 <head>
<!-- AUTHORITIES-USC-TITLE-ENUM:29 -->
 </head>
 <body>
  <div>
<!-- expcite:TITLE 29-LABOR!@!CHAPTER 4B!@!Sec. 49c -->
<h3 class="section-head">&sect;49c. Acceptance by States</h3>
<!-- field-start:statute -->
<p class="statutory-body">A State may accept the provisions of this chapter.</p>
<!-- field-end:statute -->
<!-- field-start:sourcecredit -->
<p class="source-credit">(June 6, 1933, ch. 49.)</p>
<!-- field-end:sourcecredit -->
<!-- expcite:TITLE 29-LABOR!@!CHAPTER 4B!@!Sec. 49c-1 -->
<h3 class="section-head">&sect;49c&ndash;1. Transfer to States of property</h3>
<!-- field-start:statute -->
<p class="statutory-body">Property used by the Employment Service may be transferred.</p>
<!-- field-end:statute -->
<!-- field-start:sourcecredit -->
<p class="source-credit">(Pub. L. 97&ndash;300.)</p>
<!-- field-end:sourcecredit -->
<!-- expcite:TITLE 29-LABOR!@!CHAPTER 4B!@!Sec. 49c-2 -->
<h3 class="section-head">&sect;49c&ndash;2. Omitted</h3>
<!-- field-start:statute -->
<p class="statutory-body">This section concerned a separate program.</p>
<!-- field-end:statute -->
<!-- field-start:sourcecredit -->
<p class="source-credit">(Pub. L. 97&ndash;301.)</p>
<!-- field-end:sourcecredit -->
<!-- expcite:TITLE 29-LABOR!@!CHAPTER 4B!@!Sec. 1715z-13a -->
<h3 class="section-head">&sect;1715z&ndash;13a. Loan guarantees for Indian housing</h3>
<!-- field-start:statute -->
<p class="statutory-body">The Secretary may guarantee loans under this section.</p>
<!-- field-end:statute -->
<!-- field-start:sourcecredit -->
<p class="source-credit">(Pub. L. 102&ndash;550.)</p>
<!-- field-end:sourcecredit -->
<!-- expcite:TITLE 29-LABOR!@!CHAPTER 4B!@!Sec. 58a-2 -->
<h3 class="section-head">&sect;58a-2. ASCII hyphen control</h3>
<!-- field-start:statute -->
<p class="statutory-body">A section head rendered with a plain ASCII hyphen.</p>
<!-- field-end:statute -->
<!-- field-start:sourcecredit -->
<p class="source-credit">(Pub. L. 99&ndash;1.)</p>
<!-- field-end:sourcecredit -->
  </div>
 </body>
</html>
"""


def test_endash_dashed_suffix_section_keys_to_bare_token() -> None:
    # The dashed-suffix sections key to the BARE dashed token with the en-dash
    # preserved verbatim — not the whole ``§<num>. <heading>`` string, and not a
    # hyphen-normalized form. ``1715z–13a`` keeps both its letter suffix and its
    # trailing letter after the dash.
    doc = parse_usc_title_document(_DASHED_SUFFIX_HTM, title=29, year="2020")
    numbers = [s.section for s in doc.sections]
    assert numbers == ["49c", "49c–1", "49c–2", "1715z–13a", "58a-2"]
    # No leaked heading text: a section number never contains a space or a § sign.
    for n in numbers:
        assert " " not in n and "§" not in n
    # The dashed section's heading is parsed out of the number, as for plain heads.
    s = doc.section_by_number("49c–1")
    assert s is not None
    assert s.heading == "Transfer to States of property"
    assert s.address == usc_section_address(29, "49c–1")
    # The address string the dry-run keys on carries the en-dash, not a hyphen.
    assert str(s.address) == "title:29/section:49c–1"


def test_ascii_hyphen_section_head_stays_correct() -> None:
    # A head rendered with a plain ASCII hyphen (the form some OLRC heads and the
    # structural identifiers use) keys to the hyphen token verbatim — the dash glyph
    # is preserved, never substituted, so the key matches whichever dash the
    # matching USLM href carries.
    doc = parse_usc_title_document(_DASHED_SUFFIX_HTM, title=29, year="2020")
    s = doc.section_by_number("58a-2")
    assert s is not None
    assert s.heading == "ASCII hyphen control"
    assert s.address == usc_section_address(29, "58a-2")
    # The en-dash and the hyphen forms are DISTINCT keys: no key collapses a
    # hyphen into an en-dash or vice versa.
    assert doc.section_by_number("58a–2") is None


def test_dashed_suffix_sections_are_distinct_not_merged() -> None:
    # ``49c``, ``49c–1`` and ``49c–2`` are three DISTINCT sections — the dashed
    # suffix must not collapse them onto the bare parent ``49c`` nor onto each other.
    doc = parse_usc_title_document(_DASHED_SUFFIX_HTM, title=29, year="2020")
    keys = [s.section for s in doc.sections]
    assert len(keys) == len(set(keys))  # no merge / duplicate
    parent = doc.section_by_number("49c")
    child1 = doc.section_by_number("49c–1")
    child2 = doc.section_by_number("49c–2")
    assert parent is not None and child1 is not None and child2 is not None
    # Distinct addresses and distinct statutory bodies — proof they did not merge.
    assert parent.address != child1.address != child2.address
    assert parent.statutory_text != child1.statutory_text != child2.statutory_text
    # No spurious duplicate-section-number finding from the dashed family.
    assert doc.report.findings == []


def test_replacement_catchline_strip_handles_endash_section() -> None:
    # A whole-section "amend to read as follows" payload for a dashed-suffix section
    # opens with that section's own ``§ <num>. <heading>`` catchline (en-dash and
    # all). The strip keys on the SAME dashed token, so it removes this section's own
    # catchline up to the quotedText body marker, exactly as for a plain section.
    payload = "§ 1715z–13a. Loan guarantees for Indian housing “(a) The Secretary may guarantee."
    stripped = strip_replacement_section_catchline(payload, "1715z–13a")
    assert stripped == "“(a) The Secretary may guarantee."
    # A mismatched (hyphen) number does NOT strip the en-dash catchline: the dash
    # glyph is part of the key identity, never normalized away.
    assert strip_replacement_section_catchline(payload, "1715z-13a") is None


# ---------------------------------------------------------------------------
# Synthetic-section construction for newly-inserted sections (§1182 SBRA family)
# ---------------------------------------------------------------------------


def test_synthetic_usc_section_splits_quote_wrapped_payload() -> None:
    """A replacement payload for a new section wraps structural units in nested
    curly quotes (``“In this subchapter:“(1) Debtor...``). The synthetic section
    must still expose paragraph-level nodes so later sub-section ops can locate
    them, and the trailing quote boundary must not be absorbed into the node
    text (otherwise replacing paragraph (1) would swallow the separator before
    paragraph (2))."""
    payload = (
        "“In this subchapter:“"
        "(1) Debtor.—The term ‘debtor’ means a small business debtor. "
        "“(2) Debtor in possession.—The term ‘debtor in possession’ means "
        "the debtor, unless removed as debtor in possession under section 1185(a) "
        "of this title. “"
    )
    section = synthetic_usc_section(title=11, section="1182", text=payload)
    nodes, findings = split_statutory_subsections(section)
    by_addr = {n.address.path[2:]: n for n in nodes}
    assert (("paragraph", "1"),) in by_addr
    assert (("paragraph", "2"),) in by_addr
    para1 = by_addr[(("paragraph", "1"),)]
    # The node text ends with the statutory period, not the boundary quote that
    # precedes paragraph (2).
    assert para1.text.endswith("small business debtor.")
    assert not para1.text.endswith("“")
    assert para1.text in section.statutory_text


def test_synthetic_usc_section_trailing_quote_boundary_stays_on_adjacent_node() -> None:
    """When a structural marker is preceded by a quote boundary, the quote must be
    stripped from the end of the preceding node but retained in the section text
    as a separator, so ``before_text.replace(node_text, replacement)`` does not
    delete the next node's wrapper."""
    payload = (
        "(1) First.—Text one. "
        "“(2) Second.—Text two. “"
    )
    section = synthetic_usc_section(title=11, section="500", text=payload)
    nodes, _findings = split_statutory_subsections(section)
    by_addr = {n.address.path[2:]: n for n in nodes}
    first = by_addr[(("paragraph", "1"),)]
    second = by_addr[(("paragraph", "2"),)]
    # Boundary quote separates the two units in the section text.
    assert "“" in section.statutory_text
    # But the first node does not own it.
    assert not first.text.endswith("“")
    # The second node still begins with its marker.
    assert second.text.startswith("(2) Second")
    # Both nodes are substrings of the whole text.
    assert first.text in section.statutory_text
    assert second.text in section.statutory_text
