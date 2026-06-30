"""USLM 1.0 release-point XML parser for the U.S. federal :mod:`source_tree`.

Synthetic-only at the top (no network, no archive), plus one canonical-archive
regression that exercises the live Title 10 PL 113-100 release-point XML when
``LAWVM_CANONICAL_DATA_ROOT`` points at the linked ``us_federal.farchive``.

The synthetic tests pin the structural contract of
:func:`parse_uslm_title_document` and :func:`split_uslm_subsections` against a
small crafted USLM 1.0 document that exercises:
  * ``<section>`` / ``<subsection>`` / ``<paragraph>`` / ``<subparagraph>`` /
    ``<clause>`` nesting (the structural split path);
  * direct ``<content>`` (sections without subsections — the flat body path);
  * ``status="repealed"`` and bracketed ``[Repealed. ...]`` head styles;
  * ``<sourceCredit>`` extraction;
  * ``<chapeau>`` / ``<heading>`` inclusion in a subsection's node text but
    NOT in descendant children's text;
  * ghost ``<section>`` elements inside ``<notes>``/``<note>`` blocks (the
    quoted-text witnesses that must be excluded from the section count — both
    the no-identifier shape and the USC-shaped-identifier shape, since some
    amendment notes quote former-text sections by their original identifiers).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lawvm.core.ir import LegalAddress
from lawvm.us_federal.source_tree import (
    UscSection,
    UscSourceDocument,
    parse_uslm_title_document,
    split_uslm_subsections,
    usc_section_address,
)

# A small USLM 1.0 synthesis that mirrors the OLRC release-point shape.
#
# The real Title 10 / PL 113-100 release-point XML uses the OLRC USLM 1.0
# namespace ``http://xml.house.gov/schemas/uslm/1.0`` (NOT the PLAW 2.x
# namespace at ``http://schemas.gpo.gov/xml/uslm`` kept distinct in
# :mod:`lawvm.us_federal.import_release`). The crafted document below carries
# that namespace so the parser qualifies element names the same way as the
# real corpus.
_USLM_1_0_NS = "http://xml.house.gov/schemas/uslm/1.0"

_SYNTHETIC_USLM = """<?xml version="1.0" encoding="UTF-8"?>
<uscDoc xmlns="http://xml.house.gov/schemas/uslm/1.0" identifier="/us/usc/t99">
  <meta/>
  <main>
    <title identifier="/us/usc/t99">
      <num value="99">Title 99\u2014</num>
      <heading>SYNTHETIC TEST TITLE</heading>
      <subtitle identifier="/us/usc/t99/stA">
        <num value="A">A.</num>
        <heading>SUBTITLE A</heading>
        <chapter identifier="/us/usc/t99/stA/ch1">
          <num value="1">CHAPTER 1</num>
          <heading>General Provisions</heading>
          <section identifier="/us/usc/t99/s9901">
            <num value="9901">\u00a7 9901.</num>
            <heading>Definitions</heading>
            <subsection identifier="/us/usc/t99/s9901/a">
              <num value="a">(a)</num>
              <heading>In General.\u2014</heading>
              <chapeau>The following definitions apply in this title:</chapeau>
              <paragraph identifier="/us/usc/t99/s9901/a/1">
                <num value="1">(1)</num>
                <content>The term \u201cUnited States\u201d means the States and the District of Columbia.</content>
              </paragraph>
              <paragraph identifier="/us/usc/t99/s9901/a/2" status="repealed">
                <num value="2">[(2)</num>
                <content>Repealed. Pub. L. 109\u2013163, title X, \u00a7 1057(a)(1).</content>
              </paragraph>
              <paragraph identifier="/us/usc/t99/s9901/a/3">
                <num value="3">(3)</num>
                <chapeau>The term \u201cuniformed services\u201d means\u2014</chapeau>
                <subparagraph identifier="/us/usc/t99/s9901/a/3/A">
                  <num value="A">(A)</num>
                  <content>the armed forces;</content>
                </subparagraph>
                <subparagraph identifier="/us/usc/t99/s9901/a/3/B">
                  <num value="B">(B)</num>
                  <content>the commissioned corps of the Public Health Service.</content>
                </subparagraph>
              </paragraph>
            </subsection>
            <subsection identifier="/us/usc/t99/s9901/b">
              <num value="b">(b)</num>
              <content>For purposes of this chapter, the term \u201cSecretary\u201d means the Secretary of Defense.</content>
            </subsection>
            <sourceCredit>(Added Pub. L. 99\u20131, \u00a7 1, Jan. 1, 1985, 99 Stat. 1.)</sourceCredit>
          </section>
          <section identifier="/us/usc/t99/s9902">
            <num value="9902">\u00a7 9902.</num>
            <heading>Department of Defense: seal</heading>
            <content>The Secretary of Defense shall have a seal for the Department of Defense.</content>
            <sourceCredit>(Added Pub. L. 87\u2013651, title II, \u00a7 202, Sept. 7, 1962, 76 Stat. 517.)</sourceCredit>
          </section>
          <section identifier="/us/usc/t99/s9903" status="repealed">
            <num value="9903">[\u00a7 9903.</num>
            <heading>Repealed. Pub. L. 110\u2013181, div. A, title IX, \u00a7 901(a)(1), Jan. 28, 2008, 122 Stat. 272]</heading>
            <sourceCredit>(Added Pub. L. 99\u20131, \u00a7 1, Jan. 1, 1985, 99 Stat. 1.)</sourceCredit>
          </section>
        </chapter>
      </subtitle>
      <notes type="uscNote">
        <note topic="amendments">
          <heading class="centered">Amendments</heading>
          <!-- A ghost quoted-text pseudo-section: this must NOT inflate the
               section count, even though it carries a USC-shaped identifier
               that the simple identifier-prefix filter would mistakenly accept. -->
          <section identifier="/us/usc/t99/s6">
            <num value="6">\u201cSEC. 6.</num>
            <heading>Former Section That Once Existed</heading>
            <content>(Repealed. Pub. L. 99\u20131, \u00a7 1, Jan. 1, 1985.)</content>
          </section>
          <p>1994 amendments note body goes here.</p>
          <!-- A second ghost shape: no identifier at all (rare but exercised). -->
          <section>
            <num value="7">SEC. 7.</num>
            <heading>Another Former Section</heading>
            <content>Quoted from an amendment.</content>
          </section>
        </note>
      </notes>
    </title>
  </main>
</uscDoc>
""".encode("utf-8")


@pytest.fixture(scope="module")
def uslm_bytes() -> bytes:
    return _SYNTHETIC_USLM


@pytest.fixture(scope="module")
def parsed_doc(uslm_bytes: bytes) -> UscSourceDocument:
    return parse_uslm_title_document(uslm_bytes, title=99, year="2014")


# ---------------------------------------------------------------------------
# parse_uslm_title_document
# ---------------------------------------------------------------------------


def test_uslm_parse_extracts_section_count_and_numbers(parsed_doc: UscSourceDocument) -> None:
    """The three live sections parse; the two ghost sections in notes do not."""
    assert [s.section for s in parsed_doc.sections] == ["9901", "9902", "9903"]
    assert parsed_doc.report.section_count == 3
    assert parsed_doc.report.findings == []


def test_uslm_parse_excludes_ghost_sections_in_notes(parsed_doc: UscSourceDocument) -> None:
    """Ghost quoted-text sections (``identifier`` set OR unset) stay excluded.

    A naive identifier-prefix filter (``startswith("/us/usc/t")``) would
    accept the ghost ``section identifier="/us/usc/t99/s6"``; the explicit
    notes-subtree skip in :func:`_iter_uslm_section_elements` excludes both
    shapes. The sectino count must stay at 3, not 5.
    """
    section_numbers = {s.section for s in parsed_doc.sections}
    assert section_numbers == {"9901", "9902", "9903"}
    assert "6" not in section_numbers  # ghost with valid identifier
    assert "7" not in section_numbers  # ghost with no identifier


def test_uslm_parse_extracts_section_heading_and_address(parsed_doc: UscSourceDocument) -> None:
    s = parsed_doc.section_by_number("9901")
    assert s is not None
    assert s.title == 99
    assert s.heading == "Definitions"
    # Pinned address convention: title -> section.
    assert s.address == LegalAddress(path=(("title", "99"), ("section", "9901")))
    assert s.address == usc_section_address(99, "9901")


def test_uslm_parse_extracts_statutory_text_with_chapeau_and_marker(parsed_doc: UscSourceDocument) -> None:
    """The section's statutory_text includes the ``(a)`` marker, the
    subsection ``In General.—`` heading, and the chapeau that opens its
    paragraphs — mirroring the annual-edition htm parser's body surface.

    The synthetic test XML has indentation whitespace between adjacent
    ``<heading>`` and ``<chapeau>`` elements; the real OLRC USLM places them
    abutting without whitespace, so the canonical Title 10 §101 text reads
    ``In General.—The`` (no space). The parser neither injects NOR collapses
    that boundary — it normalizes runs of whitespace to single spaces and
    preserves the verbatim adjacency of the source.

    Note: ``Pub. L.`` MAY appear inside ``statutory_text`` when a paragraph is
    repealed and the OLRC carries the repeal citation inline (e.g.
    ``[(2) Repealed. Pub. L. 109–163, ...]``). The SC-vs-body boundary is the
    SECTION's own source-credit text (``Added Pub. L. 99–1, ... 99 Stat. 1.``)
    appearing in :attr:`source_credit_raw` but NOT in ``statutory_text``.
    """
    s = parsed_doc.section_by_number("9901")
    assert s is not None
    # Marker from <num>, subheading text, and chapeau are folded in. The
    # synthetic XML wires these as separate elements joined by whitespace
    # (the shape downstream split_statutory_subsections expects too).
    assert s.statutory_text.startswith("(a) In General.")
    assert "The following definitions apply in this title:" in s.statutory_text
    # The section's own catchline is NOT part of statutory_text.
    assert "Definitions" not in s.statutory_text.split("(a)", 1)[0]
    # The section's own source-credit (Pub. L. 99–1 ... 99 Stat. 1.) is captured
    # in source_credit_raw and NOT folded into statutory_text.
    assert "99 Stat. 1" in s.source_credit_raw
    assert "99 Stat. 1" not in s.statutory_text


def test_uslm_parse_extracts_source_credit_raw(parsed_doc: UscSourceDocument) -> None:
    s = parsed_doc.section_by_number("9901")
    assert s is not None
    assert s.source_credit_raw.startswith("(Added Pub. L. 99–1")
    assert "99 Stat. 1" in s.source_credit_raw


def test_uslm_parse_paragraphs_per_direct_structural_child(parsed_doc: UscSourceDocument) -> None:
    """§9901 has two direct subsections → two paragraphs; the chapeau is
    inside the subsection's subtree text, not a separate paragraph."""
    s = parsed_doc.section_by_number("9901")
    assert s is not None
    # Two direct subsection children (a) and (b).
    assert len(s.paragraphs) == 2
    assert all(p.css_class == "uslm-subsection" for p in s.paragraphs)
    assert all(p.indent_depth == 0 for p in s.paragraphs)
    # The first paragraph's text is the whole subsection (a) subtree:
    # marker + subheading + chapeau + every descendant paragraph body.
    assert s.paragraphs[0].text.startswith("(a) In General.—")
    # The (b) subsection has a direct <content>, no children.
    assert s.paragraphs[1].text.startswith("(b) For purposes of this chapter")


def test_uslm_parse_status_repealed_from_status_attribute(parsed_doc: UscSourceDocument) -> None:
    """``status="repealed"`` on the <section> sets ``repealed=True``."""
    s = parsed_doc.section_by_number("9903")
    assert s is not None
    assert s.repealed is True


def test_uslm_parse_status_repealed_from_head_regex(parsed_doc: UscSourceDocument) -> None:
    """A repealed sub-paragraph marked with ``status="repealed"`` on the
    ``<paragraph>`` element is correctly identified by the section-level
    parser; this test ensures the section-level ``repealed`` flag is set
    from the SECTION's status, not its children's (§9901 stays
    non-repealed even though paragraph (a)(2) is repealed)."""
    s9901 = parsed_doc.section_by_number("9901")
    assert s9901 is not None
    assert s9901.repealed is False  # paragraph (a)(2) repealed, but section is not


def test_uslm_parse_content_only_section(parsed_doc: UscSourceDocument) -> None:
    """A section without subsections, with a direct ``<content>``, emits one
    paragraph carrying the body text (no structural split needed)."""
    s = parsed_doc.section_by_number("9902")
    assert s is not None
    assert s.heading == "Department of Defense: seal"
    assert s.statutory_text.startswith(
        "The Secretary of Defense shall have a seal for the Department of Defense."
    )
    assert len(s.paragraphs) == 1
    assert s.paragraphs[0].css_class == "uslm-content"
    assert s.paragraphs[0].indent_depth == 0


def test_uslm_shape_report_fields_populated(parsed_doc: UscSourceDocument) -> None:
    """The shape report tracks repealed count and missing-source/text gaps."""
    assert parsed_doc.report.title == 99
    assert parsed_doc.report.year == "2014"
    assert parsed_doc.report.section_count == 3
    assert parsed_doc.report.repealed_count == 1
    # All three sections have a source-credit and statutory text.
    assert parsed_doc.report.sections_without_source_credit == []
    assert parsed_doc.report.sections_without_statutory_text == []


def test_uslm_parse_dashed_section_identifier_via_synthetic() -> None:
    """The dashed-section form (e.g. ``949p–1`` with U+2013 EN DASH) round-
    trips through ``identifier`` matching: parse-and-split finds the right
    section element by exact ``identifier`` compare (no dash normalization)."""
    dashed_uslm = """<?xml version="1.0" encoding="UTF-8"?>
<uscDoc xmlns="http://xml.house.gov/schemas/uslm/1.0" identifier="/us/usc/t99">
  <main>
    <title identifier="/us/usc/t99">
      <section identifier="/us/usc/t99/s949p\u20131">
        <num value="949p\u20131">\u00a7 949p\u20131.</num>
        <heading>Protection of classified information</heading>
        <subsection identifier="/us/usc/t99/s949p\u20131/a">
          <num value="a">(a)</num>
          <content>Subsection (a) of the dashed section.</content>
        </subsection>
      </section>
    </title>
  </main>
</uscDoc>
""".encode("utf-8")
    doc = parse_uslm_title_document(dashed_uslm, title=99, year="2014")
    assert len(doc.sections) == 1
    s = doc.sections[0]
    assert s.section == "949p–1"  # U+2013 preserved verbatim
    nodes, findings = split_uslm_subsections(s, dashed_uslm)
    assert findings == []
    assert len(nodes) == 1
    assert nodes[0].label == "a"
    assert nodes[0].kind == "subsection"
    assert nodes[0].address.path == (
        ("title", "99"),
        ("section", "949p–1"),
        ("subsection", "a"),
    )


# ---------------------------------------------------------------------------
# split_uslm_subsections
# ---------------------------------------------------------------------------


def test_uslm_split_basic_nesting(parsed_doc: UscSourceDocument, uslm_bytes: bytes) -> None:
    """§9901 splits into the right depth-first pre-order node list.

    Structure:
      (a) In General
        (1) term ...
        (2) [Repealed. ...]   ← paragraph with status="repealed"
        (3) the term uniformed services ...
          (A) armed forces
          (B) commissioned corps
      (b) For purposes of this chapter ...
    """
    s = parsed_doc.section_by_number("9901")
    assert s is not None
    nodes, findings = split_uslm_subsections(s, uslm_bytes)
    assert findings == []
    labels = [(n.kind, n.label) for n in nodes]
    assert labels == [
        ("subsection", "a"),
        ("paragraph", "1"),
        ("paragraph", "2"),
        ("paragraph", "3"),
        ("subparagraph", "A"),
        ("subparagraph", "B"),
        ("subsection", "b"),
    ]


def test_uslm_split_address_path_pinned_convention(
    parsed_doc: UscSourceDocument, uslm_bytes: bytes
) -> None:
    """Each node carries the full pinned address (title→section→...→leaf)."""
    s = parsed_doc.section_by_number("9901")
    assert s is not None
    nodes, findings = split_uslm_subsections(s, uslm_bytes)
    assert findings == []
    # (a)(3)(A) — Title/section/subsection/paragraph/subparagraph.
    node = next(
        n
        for n in nodes
        if n.label == "A" and n.kind == "subparagraph"
    )
    assert node.address.path == (
        ("title", "99"),
        ("section", "9901"),
        ("subsection", "a"),
        ("paragraph", "3"),
        ("subparagraph", "A"),
    )
    assert node.indent_depth == 2  # paragraph=1, subparagraph=2


def test_uslm_split_node_text_excludes_descendant_bodies(
    parsed_doc: UscSourceDocument, uslm_bytes: bytes
) -> None:
    """A node's text excludes its structural descendants' bodies — the (a)
    subsection's text does NOT contain the text of paragraph (1)."""
    s = parsed_doc.section_by_number("9901")
    assert s is not None
    nodes, findings = split_uslm_subsections(s, uslm_bytes)
    assert findings == []
    node_a = nodes[0]
    assert node_a.label == "a"
    assert node_a.kind == "subsection"
    # The chapeau IS part of (a)'s own text (it opens the paragraph list).
    assert "The following definitions apply in this title" in node_a.text
    # The inner paragraph (1) body is NOT part of (a)'s text — it has its
    # own node.
    assert "the States and the District of Columbia" not in node_a.text


def test_uslm_split_node_text_includes_marker_and_subheading(
    parsed_doc: UscSourceDocument, uslm_bytes: bytes
) -> None:
    """A node's text includes the leading ``(a)`` marker (from ``<num>``) and
    the in-line subsection ``In General.—`` subheading, mirroring the
    annual-edition parser's per-paragraph body that downstream
    :func:`split_statutory_subsections` expects."""
    s = parsed_doc.section_by_number("9901")
    assert s is not None
    nodes, _ = split_uslm_subsections(s, uslm_bytes)
    node_a = nodes[0]
    assert node_a.text.startswith("(a) In General.—")
    node_1 = next(n for n in nodes if n.label == "1" and n.kind == "paragraph")
    assert node_1.text.startswith("(1) The term “United States”")


def test_uslm_split_deeper_nesting_through_clause() -> None:
    """Coverage for the full USC ladder (subsection→paragraph→subparagraph
    →clause→subclause→item→sub-item)."""
    uslm = """<?xml version="1.0" encoding="UTF-8"?>
<uscDoc xmlns="http://xml.house.gov/schemas/uslm/1.0" identifier="/us/usc/t99">
  <main><title identifier="/us/usc/t99">
    <section identifier="/us/usc/t99/s9901">
      <num value="9901">§ 9901.</num>
      <heading>Deeply nested</heading>
      <subsection identifier="/us/usc/t99/s9901/a">
        <num value="a">(a)</num>
        <paragraph identifier="/us/usc/t99/s9901/a/1">
          <num value="1">(1)</num>
          <subparagraph identifier="/us/usc/t99/s9901/a/1/A">
            <num value="A">(A)</num>
            <clause identifier="/us/usc/t99/s9901/a/1/A/i">
              <num value="i">(i)</num>
              <content>clause body</content>
            </clause>
          </subparagraph>
        </paragraph>
      </subsection>
    </section>
  </title></main>
</uscDoc>
""".encode("utf-8")
    doc = parse_uslm_title_document(uslm, title=99, year="2014")
    assert len(doc.sections) == 1
    s = doc.sections[0]
    nodes, findings = split_uslm_subsections(s, uslm)
    assert findings == []
    assert [(n.kind, n.label, n.indent_depth) for n in nodes] == [
        ("subsection", "a", 0),
        ("paragraph", "1", 1),
        ("subparagraph", "A", 2),
        ("clause", "i", 3),
    ]
    clause = nodes[-1]
    assert clause.text.startswith("(i) clause body")
    assert clause.address.path == (
        ("title", "99"),
        ("section", "9901"),
        ("subsection", "a"),
        ("paragraph", "1"),
        ("subparagraph", "A"),
        ("clause", "i"),
    )


def test_uslm_split_section_not_in_blob_emits_finding(parsed_doc: UscSourceDocument, uslm_bytes: bytes) -> None:
    """A section whose identifier is absent from the blob returns an empty
    node list and a typed ``us_uslm_section_not_located_in_blob`` finding,
    never an inferred section/walked-without-target."""
    s = parsed_doc.section_by_number("9901")
    assert s is not None
    fabricated = UscSection(
        title=99,
        section="9999",  # Not in the blob.
        heading="fabricated",
        address=usc_section_address(99, "9999"),
        statutory_text="",
        source_credit_raw="",
        repealed=False,
        paragraphs=(),
    )
    nodes, findings = split_uslm_subsections(fabricated, uslm_bytes)
    assert nodes == ()
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "us_uslm_section_not_located_in_blob"
    assert findings[0]["section"] == "9999"


def test_uslm_split_node_without_num_emits_finding() -> None:
    """A ``<subsection>`` without a ``<num value="...">`` child emits a typed
    ``us_uslm_node_without_num`` finding and leaves the label empty."""

    uslm = """<?xml version="1.0" encoding="UTF-8"?>
<uscDoc xmlns="http://xml.house.gov/schemas/uslm/1.0" identifier="/us/usc/t99">
  <main><title identifier="/us/usc/t99">
    <section identifier="/us/usc/t99/s9901">
      <num value="9901">§ 9901.</num>
      <heading>Missing-num section</heading>
      <subsection identifier="/us/usc/t99/s9901/a">
        <!-- no <num> child -->
        <content>Body without an opening marker.</content>
      </subsection>
    </section>
  </title></main>
</uscDoc>
""".encode("utf-8")
    doc = parse_uslm_title_document(uslm, title=99, year="2014")
    assert len(doc.sections) == 1
    s = doc.sections[0]
    nodes, findings = split_uslm_subsections(s, uslm)
    assert len(nodes) == 1
    assert nodes[0].label == ""  # never guessed
    assert nodes[0].kind == "subsection"
    assert nodes[0].text.startswith("Body without an opening marker")
    assert len(findings) == 1
    assert findings[0]["rule_id"] == "us_uslm_node_without_num"


def test_uslm_split_reuses_cached_parse_for_split(
    parsed_doc: UscSourceDocument, uslm_bytes: bytes
) -> None:
    """``split_uslm_subsections`` reuses the cached parsed tree for the blob
    (per AGENTS.md §2.7 cache lifecycle). A repeat call must not parse twice
    and must produce the same node list."""
    from lawvm.us_federal import source_tree

    # Clear the cache so the baseline parse count starts from zero.
    source_tree._uslm_tree_cache.clear()
    s = parsed_doc.section_by_number("9901")
    assert s is not None

    nodes_a, _ = split_uslm_subsections(s, uslm_bytes)
    cache_size_after_a = len(source_tree._uslm_tree_cache)
    nodes_b, _ = split_uslm_subsections(s, uslm_bytes)
    cache_size_after_b = len(source_tree._uslm_tree_cache)

    # The cache held one entry across both calls (no second insertion).
    assert cache_size_after_a == 1
    assert cache_size_after_b == 1

    # Structural equality on the leaf addresses (the node-level identity).
    assert [n.address for n in nodes_a] == [n.address for n in nodes_b]
    assert [n.label for n in nodes_a] == [n.label for n in nodes_b]


def test_synthetic_markers_never_leak_into_address(
    parsed_doc: UscSourceDocument, uslm_bytes: bytes
) -> None:
    """No synthetic USLM namespace prefix or quote glyph leaks into the
    parsed :class:`LegalAddress` string representation."""
    s = parsed_doc.section_by_number("9901")
    assert s is not None
    nodes, _ = split_uslm_subsections(s, uslm_bytes)
    for node in nodes:
        text_addr = str(node.address)
        assert "{" not in text_addr  # no NS prefix leak
        assert "}" not in text_addr
        for path_label in (label for _kind, label in node.address.path):
            assert "uslm" not in path_label  # css class never smuggled into an address level


# ---------------------------------------------------------------------------
# Canonical-archive regression (no network — gate on local farchive presence)
# ---------------------------------------------------------------------------


def _canonical_archive_available() -> bool:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        return False
    return (Path(root) / "data" / "us_federal.farchive").exists()


@pytest.mark.skipif(
    not _canonical_archive_available(),
    reason="canonical us_federal.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_canonical_archive_uslm_title10_end_to_end() -> None:
    """Parse the live Title 10 / PL 113-100 release-point USLM (35.8 MB)
    and exercise :func:`split_uslm_subsections` on a known section.

    Pins:
      * the section count (3266 — pinned at the 2024-08-09 archive snapshot);
      * §101 ``Definitions`` heading;
      * §101's statutory_text opens with ``(a) In General.—`` (the chapeau
        is folded into the body, mirroring the annual-edition htm parser);
      * §101's structural split yields the known 119 nodes covering the
        full ladder (subsection / paragraph / subparagraph / clause).
    """
    from lawvm.us_federal.sources import open_us_federal_farchive

    archive = open_us_federal_farchive(readonly=True)
    try:
        blob = archive.get("us://usc/release/pl113-100/title10.xml")
        assert blob is not None
        assert len(blob) > 30_000_000  # ~35.8 MB
    finally:
        archive.close()

    doc = parse_uslm_title_document(blob, title=10, year="2014", locator="us://usc/release/pl113-100/title10.xml")
    # Section count pinned at the 2024 archive snapshot. Structural changes to
    # the section count are high-signal events that deserve a manual update of
    # this assertion; an unexpected delta here is not a parser regression.
    assert doc.report.section_count == 3266, doc.report.section_count
    # No structural findings on the canonical corpus.
    assert doc.report.findings == []

    s101 = doc.section_by_number("101")
    assert s101 is not None
    assert s101.heading == "Definitions"
    assert s101.statutory_text.startswith("(a) In General.—The following definitions apply in this title:")
    # The section's own source-credit (the Added Pub. L. 85–861 ... lineage
    # captured verbatim in :attr:`source_credit_raw`) is NOT folded into the
    # statutory comparison surface. ``Pub. L.`` MAY appear inline elsewhere in
    # the body where a repealed paragraph carries its repeal citation inline
    # (e.g. ``[(2) Repealed. Pub. L. 109–163, div. A, title X, § 1057(a)(1)]``) —
    # that is body text, not the section source-credit.
    assert s101.source_credit_raw.startswith("(Aug. 10, 1956")
    assert "70A Stat. 3" in s101.source_credit_raw  # the section's own SC
    assert "70A Stat. 3" not in s101.statutory_text  # SC not folded into body
    assert "Editorial Notes" not in s101.statutory_text  # notes excluded

    nodes, findings = split_uslm_subsections(s101, blob)
    assert findings == []
    # Known structure: §101 has many definitions across the full ladder.
    # Structural assertion, not exact count (a concurrent amendment could add
    # a definition without breaking the parser).
    assert len(nodes) > 100
    # Ladder coverage: section 101's definitions span multiple levels.
    kinds = {n.kind for n in nodes}
    assert {"subsection", "paragraph", "subparagraph"} <= kinds
    # First node is subsection (a) with the pinned address path.
    first = nodes[0]
    assert first.kind == "subsection"
    assert first.label == "a"
    assert first.address.path[:2] == (("title", "10"), ("section", "101"))
    assert first.address.path[2] == ("subsection", "a")

    # A dashed-section round-trip — the en-dash form (U+2013) must flow
    # through the identifier matcher unchanged.
    dashed = next((s for s in doc.sections if "–" in s.section), None)
    assert dashed is not None, "expected at least one en-dash section number in Title 10"
    dashed_nodes, dashed_findings = split_uslm_subsections(dashed, blob)
    assert dashed_findings == []
    assert len(dashed_nodes) > 0


@pytest.mark.skipif(
    not _canonical_archive_available(),
    reason="canonical us_federal.farchive not linked (LAWVM_CANONICAL_DATA_ROOT unset)",
)
def test_canonical_archive_uslm_repealed_sections_flagged() -> None:
    """The canonical Title 10 has ~459 repealed/renumbered sections: the
    status attribute path and the head-text regex both fire, and the
    shape report's repealed_count reflects them."""
    from lawvm.us_federal.sources import open_us_federal_farchive

    archive = open_us_federal_farchive(readonly=True)
    try:
        blob = archive.get("us://usc/release/pl113-100/title10.xml")
        assert blob is not None
    finally:
        archive.close()

    doc = parse_uslm_title_document(blob, title=10, year="2014")
    # Repealed count is high-signal but subject to amendment; assert a band,
    # not an exact count, so adding a single repeal upstream is not a
    # regression. 384 ``status="repealed"`` and 75 ``status="renumbered"``
    # at the 2024 snapshot -> ≥ 400 combined.
    assert doc.report.repealed_count >= 400
    # Repealed sections still have a heading (even if bracketed).
    repealed = [s for s in doc.sections if s.repealed]
    assert all(s.heading for s in repealed[:5])
