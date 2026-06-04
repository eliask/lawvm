"""Conformance corpus fixtures for fi_sections_text projection.

Covers the 5 required scenarios per SECTIONS_TEXT_PROJECTION.md §Verification:

  1. MULTI_SECTION      — standard statute with multiple <section> elements.
  2. NESTED_CHAPTER     — chapter/section nested structure (chapter:1/section:5).
  3. EMPTY_SECTION      — section with heading only, no body paragraphs.
  4. INLINE_REF         — section with inline <ref> markup; display text kept.
  5. AMENDMENT_REJECTED — amendment AKN URI (FRBRsubtype != 'statute-consolidated')
                          is rejected and produces zero SectionText rows.

Each SectionTextFixture carries:
  - fixture_id:          stable identifier
  - description:         human-readable test description
  - statute_id:          simulated statute_id for the extraction call
  - xml_bytes:           minimal valid Akoma Ntoso XML
  - expected_sections:   list of partial-dict assertions per row
  - expected_zero_rows:  True when no SectionText rows are expected
  - expected_diag_rule_ids: rule IDs that must appear in diagnostics
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Fixture type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SectionTextFixture:
    """One conformance corpus fixture for fi_sections_text extraction."""

    fixture_id: str
    description: str
    statute_id: str
    xml_bytes: bytes
    expected_sections: List[Dict[str, Any]] = field(default_factory=list)
    expected_zero_rows: bool = False
    expected_diag_rule_ids: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# AKN boilerplate helpers
# ---------------------------------------------------------------------------

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
_AKN_OPEN = (
    b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"'
    b' xmlns:finlex="http://data.finlex.fi/schema/finlex">'
)
_AKN_CLOSE = b"</akomaNtoso>"


def _wrap_act(meta_inner: bytes, body_inner: bytes) -> bytes:
    """Wrap meta + body in a minimal AKN act element."""
    meta = b"<meta>" + meta_inner + b"</meta>"
    return (
        _AKN_OPEN
        + b"<act>"
        + meta
        + b"<body>"
        + body_inner
        + b"</body>"
        + b"</act>"
        + _AKN_CLOSE
    )


def _consolidated_meta(subtype: str = "statute-consolidated", date_str: str = "2024-01-15") -> bytes:
    """Minimal FRBR meta block for a consolidated statute."""
    return (
        b"<identification source='#org'>"
        b"<FRBRWork><FRBRthis value='/akn/fi/act/statute-consolidated/2003/314/!main'/>"
        b"<FRBRsubtype value='" + subtype.encode() + b"'/></FRBRWork>"
        b"<FRBRExpression>"
        b"<FRBRdate date='" + date_str.encode() + b"' name='dateConsolidated'/>"
        b"</FRBRExpression>"
        b"</identification>"
    )


def _section_xml(eid: bytes, num: bytes, heading: bytes = b"", body: bytes = b"") -> bytes:
    """Build a <section> element with num, optional heading, optional body."""
    h = b"<heading>" + heading + b"</heading>" if heading else b""
    b_inner = (
        b"<subsection><content><p>" + body + b"</p></content></subsection>"
        if body else b""
    )
    return (
        b"<section eId='" + eid + b"'>"
        + b"<num>" + num + b"</num>"
        + h
        + b_inner
        + b"</section>"
    )


# ---------------------------------------------------------------------------
# Fixture 1: MULTI_SECTION — standard statute, multiple sections, flat
# ---------------------------------------------------------------------------

MULTI_SECTION = SectionTextFixture(
    fixture_id="multi_section",
    description=(
        "Standard statute with three top-level <section> elements "
        "(eId=sec_1, sec_2, sec_3). Verifies basic extraction, label, "
        "heading, body_text, char_count."
    ),
    statute_id="2003/314",
    xml_bytes=_wrap_act(
        meta_inner=_consolidated_meta(),
        body_inner=(
            _section_xml(
                eid=b"sec_1",
                num=b"1 \xc2\xa7",
                heading=b"Tarkoitus",
                body=b"T\xc3\xa4m\xc3\xa4 laki koskee hallintoa.",
            )
            + _section_xml(
                eid=b"sec_2",
                num=b"2 \xc2\xa7",
                heading=b"Soveltamisala",
                body=b"Lakia sovelletaan viranomaisiin.",
            )
            + _section_xml(
                eid=b"sec_3",
                num=b"3 \xc2\xa7",
                heading=b"M\xc3\xa4\xc3\xa4ritelm\xc3\xa4t",
                body=b"T\xc3\xa4ss\xc3\xa4 laissa tarkoitetaan.",
            )
        ),
    ),
    expected_sections=[
        {
            "statute_id": "2003/314",
            "section_key": "section:1",
            "section_label": "1 §",
            "heading_text": "Tarkoitus",
        },
        {
            "statute_id": "2003/314",
            "section_key": "section:2",
            "section_label": "2 §",
            "heading_text": "Soveltamisala",
        },
        {
            "statute_id": "2003/314",
            "section_key": "section:3",
            "section_label": "3 §",
        },
    ],
)

# ---------------------------------------------------------------------------
# Fixture 2: NESTED_CHAPTER — chapter/section structure
# ---------------------------------------------------------------------------

NESTED_CHAPTER = SectionTextFixture(
    fixture_id="nested_chapter",
    description=(
        "Statute with chapter/section nesting. Sections have eIds like "
        "'chp_1__sec_5'. Verifies section_key = 'chapter:1/section:5'."
    ),
    statute_id="1999/731",
    xml_bytes=_wrap_act(
        meta_inner=_consolidated_meta(),
        body_inner=(
            b"<chapter eId='chp_1'><num>1 luku</num><heading>Yleis\xc3\xa4\xc3\xa4</heading>"
            + _section_xml(
                eid=b"chp_1__sec_1",
                num=b"1 \xc2\xa7",
                heading=b"Perusoikeudet",
                body=b"Suomen kansalaisilla on perusoikeudet.",
            )
            + _section_xml(
                eid=b"chp_1__sec_5",
                num=b"5 \xc2\xa7",
                heading=b"Kansalaisuus",
                body=b"Suomen kansalainen on syntypera\xcc\x88inen.",
            )
            + b"</chapter>"
            + b"<chapter eId='chp_2'><num>2 luku</num>"
            + _section_xml(
                eid=b"chp_2__sec_9",
                num=b"9 \xc2\xa7",
                heading=b"Liikkumisvapaus",
                body=b"Jokaisella on oikeus liikkua.",
            )
            + b"</chapter>"
        ),
    ),
    expected_sections=[
        {
            "statute_id": "1999/731",
            "section_key": "chapter:1/section:1",
            "section_label": "1 §",
            "heading_text": "Perusoikeudet",
        },
        {
            "statute_id": "1999/731",
            "section_key": "chapter:1/section:5",
            "section_label": "5 §",
            "heading_text": "Kansalaisuus",
        },
        {
            "statute_id": "1999/731",
            "section_key": "chapter:2/section:9",
        },
    ],
)

# ---------------------------------------------------------------------------
# Fixture 3: EMPTY_SECTION — heading only, no body paragraphs
# ---------------------------------------------------------------------------

EMPTY_SECTION = SectionTextFixture(
    fixture_id="empty_section",
    description=(
        "Section with <num> and <heading> but no body paragraphs. "
        "Verifies body_text='' and char_count=0 for that section."
    ),
    statute_id="2006/417",
    xml_bytes=_wrap_act(
        meta_inner=_consolidated_meta(),
        body_inner=(
            # Section with heading only — no subsection/paragraph
            b"<section eId='sec_1'>"
            b"<num>1 \xc2\xa7</num>"
            b"<heading>Otsikko</heading>"
            b"</section>"
            + _section_xml(
                eid=b"sec_2",
                num=b"2 \xc2\xa7",
                heading=b"",
                body=b"Jotain sis\xc3\xa4lt\xc3\xb6\xc3\xa4.",
            )
        ),
    ),
    expected_sections=[
        {
            "statute_id": "2006/417",
            "section_key": "section:1",
            "section_label": "1 §",
            "heading_text": "Otsikko",
            "body_text": "",
            "char_count": 0,
        },
        {
            "statute_id": "2006/417",
            "section_key": "section:2",
        },
    ],
)

# ---------------------------------------------------------------------------
# Fixture 4: INLINE_REF — section with <ref> markup in body
# ---------------------------------------------------------------------------

INLINE_REF = SectionTextFixture(
    fixture_id="inline_ref",
    description=(
        "Section body contains <ref href='...'> markup. "
        "Verifies that body_text contains the displayed text of the ref "
        "but not the href attribute value."
    ),
    statute_id="2003/434",
    xml_bytes=_wrap_act(
        meta_inner=_consolidated_meta(),
        body_inner=(
            b"<section eId='sec_4'>"
            b"<num>4 \xc2\xa7</num>"
            b"<heading>Soveltamisalan rajaukset</heading>"
            b"<subsection><content><p>"
            b"T\xc3\xa4t\xc3\xa4 lakia ei sovelleta "
            b'<ref href="/akn/fi/act/statute-consolidated/2022/711#sec_7">'
            b"lannoitelakiin</ref>"
            b" eik\xc3\xa4 "
            b'<ref href="/akn/fi/act/statute-consolidated/1978/404#sec_1">'
            b"kemikaalilakiin</ref>"
            b"."
            b"</p></content></subsection>"
            b"</section>"
        ),
    ),
    expected_sections=[
        {
            "statute_id": "2003/434",
            "section_key": "section:4",
            "section_label": "4 §",
            "heading_text": "Soveltamisalan rajaukset",
        },
    ],
    # body_text must contain displayed text, not href
    # tested directly in test_fi_sections_text.py
)

# ---------------------------------------------------------------------------
# Fixture 5: AMENDMENT_REJECTED — non-consolidated FRBRsubtype rejected
# ---------------------------------------------------------------------------

AMENDMENT_REJECTED = SectionTextFixture(
    fixture_id="amendment_rejected",
    description=(
        "Amendment statute AKN with FRBRsubtype='statute' (not "
        "'statute-consolidated'). Extractor rejects it and returns "
        "zero SectionText rows with a blocking diagnostic."
    ),
    statute_id="2020/854",
    xml_bytes=(
        b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        b"<act>"
        b"<meta><identification source='#org'>"
        b"<FRBRWork><FRBRthis value='/akn/fi/act/statute/2020/854/!main'/>"
        b"<FRBRsubtype value='statute'/>"  # NOT statute-consolidated
        b"</FRBRWork></identification></meta>"
        b"<body>"
        b"<section eId='sec_1'><num>1 \xc2\xa7</num>"
        b"<subsection><content><p>Muutetaan.</p></content></subsection>"
        b"</section>"
        b"</body>"
        b"</act>"
        b"</akomaNtoso>"
    ),
    expected_zero_rows=True,
    expected_diag_rule_ids=["fi_sections_text_wrong_frbr_subtype"],
)


# ---------------------------------------------------------------------------
# All fixtures indexed by fixture_id
# ---------------------------------------------------------------------------

ALL_FIXTURES: dict[str, SectionTextFixture] = {
    f.fixture_id: f
    for f in [
        MULTI_SECTION,
        NESTED_CHAPTER,
        EMPTY_SECTION,
        INLINE_REF,
        AMENDMENT_REJECTED,
    ]
}
