"""Conformance corpus fixtures for InlineCitation extraction (feature #12).

Covers the test scenarios per INLINE_CITATION_SWEEP.md §Tests + conformance:

  1. Statute body with inline court reference (KKO + KHO in enacted-statute body)
  2. HE perustelut with KKO + KHO + EOA + plain-text statute citation
  3. HE->HE policy-coordination citation in HE body prose
  4. VTV-report citation in HE perustelut
  5. EK (modern parliament kirjelma) in preliminaryWork — cross-feature with #11
     (EK should be typed here; other patterns in prelim should be suppressed)
  6. Negative: bare \\d{4}/\\d+ not in recognized context (e.g. date) → no row emitted
  7. Composition: <ref>-marked citations are SKIPPED (deferred to #1)

Each fixture is an InlineCorpusFixture with:
  - fixture_id: str
  - description: str
  - source_doc_id: str
  - source_doc_kind: str ('statute' or 'he')
  - xml_bytes: bytes  (minimal valid AKN XML)
  - expected_citations: list[dict]   (column-level assertions; partial match)
  - expected_pattern_matches: list[dict]  (InlineCitationPatternMatch assertions)
  - expected_absent_kinds: list[str]  (kinds that must NOT appear in citations)

Assertions are partial: only the keys listed must match.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Fixture type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InlineCorpusFixture:
    """One conformance corpus fixture for InlineCitation extraction."""

    fixture_id: str
    description: str
    source_doc_id: str
    source_doc_kind: str  # 'statute' or 'he'
    xml_bytes: bytes
    expected_citations: List[Dict[str, Any]] = field(default_factory=list)
    expected_pattern_matches: List[Dict[str, Any]] = field(default_factory=list)
    expected_absent_kinds: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# AKN namespace boilerplate
# ---------------------------------------------------------------------------

_AKN_OPEN = (
    b'<akomaNtoso '
    b'xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0" '
    b'xmlns:finlex="http://data.finlex.fi/schema/finlex">'
)
_AKN_CLOSE = b"</akomaNtoso>"


def _wrap_statute_body(body_p_content: bytes) -> bytes:
    """Wrap content in a minimal valid AKN statute body (no preliminaryWork)."""
    return (
        _AKN_OPEN
        + b"<act><meta/><body>"
        + b"<section><num>1 \xc2\xa7</num>"
        + b"<paragraph><content>"
        + b"<p>" + body_p_content + b"</p>"
        + b"</content></paragraph>"
        + b"</section>"
        + b"</body></act>"
        + _AKN_CLOSE
    )


def _wrap_he_body(rationale_p_content: bytes) -> bytes:
    """Wrap content in a minimal valid AKN HE mainBody with perustelut hcontainer."""
    return (
        _AKN_OPEN
        + b"<doc><meta/><mainBody>"
        + b'<hcontainer name="perustelut">'
        + b"<content><p>" + rationale_p_content + b"</p></content>"
        + b"</hcontainer>"
        + b"</mainBody></doc>"
        + _AKN_CLOSE
    )


def _wrap_he_body_multi_p(*p_contents: bytes) -> bytes:
    """Wrap multiple <p> elements in a minimal HE mainBody perustelut."""
    p_tags = b"".join(b"<p>" + c + b"</p>" for c in p_contents)
    return (
        _AKN_OPEN
        + b"<doc><meta/><mainBody>"
        + b'<hcontainer name="perustelut">'
        + b"<content>"
        + p_tags
        + b"</content>"
        + b"</hcontainer>"
        + b"</mainBody></doc>"
        + _AKN_CLOSE
    )


def _wrap_he_body_with_prelim(he_p_content: bytes, prelim_p_content: bytes) -> bytes:
    """HE mainBody with a perustelut section + a preliminaryWork block."""
    return (
        _AKN_OPEN
        + b"<doc><meta/><mainBody>"
        + b'<hcontainer name="perustelut">'
        + b"<content><p>" + he_p_content + b"</p></content>"
        + b"</hcontainer>"
        + b'<hcontainer name="conclusions">'
        + b'<hcontainer name="preliminaryWork">'
        + b"<content><p>" + prelim_p_content + b"</p></content>"
        + b"</hcontainer>"
        + b"</hcontainer>"
        + b"</mainBody></doc>"
        + _AKN_CLOSE
    )


# ---------------------------------------------------------------------------
# Fixture 1: Statute body with inline court references
#
# An enacted statute body paragraph containing "KKO 2018:45" and "KHO 2020:87".
# Both should be extracted as ENACTED_STATUTE_BODY context citations.
# ---------------------------------------------------------------------------

STATUTE_BODY_COURT_REFS = InlineCorpusFixture(
    fixture_id="statute_body_court_refs",
    description="Enacted statute body with KKO and KHO inline citations",
    source_doc_id="711/2022",
    source_doc_kind="statute",
    xml_bytes=_wrap_statute_body(
        b"Asiaa k\xc3\xa4siteltiin my\xc3\xb6s ratkaisussa KKO 2018:45 "
        b"sek\xc3\xa4 KHO 2020:87."
    ),
    expected_citations=[
        {
            "source_doc_id": "711/2022",
            "source_doc_kind": "statute",
            "kind": "court_kko",
            "canonical_id": "fi.court.kko.2018.45",
            "raw_text": "KKO 2018:45",
            "case_year": 2018,
            "case_number": 45,
            "context": "enacted_statute_body",
        },
        {
            "source_doc_id": "711/2022",
            "source_doc_kind": "statute",
            "kind": "court_kho",
            "canonical_id": "fi.court.kho.2020.87",
            "raw_text": "KHO 2020:87",
            "case_year": 2020,
            "case_number": 87,
            "context": "enacted_statute_body",
        },
    ],
    expected_pattern_matches=[],
    expected_absent_kinds=["he_inline", "statute_inline"],
)


# ---------------------------------------------------------------------------
# Fixture 2: HE perustelut with KKO + KHO + EOA + statute citation
#
# An HE body paragraph containing a mix of citation types.
# ---------------------------------------------------------------------------

HE_PERUSTELUT_MIXED_CITATIONS = InlineCorpusFixture(
    fixture_id="he_perustelut_mixed_citations",
    description="HE perustelut paragraph with KKO, KHO, EOA, and statute citation",
    source_doc_id="184/2024",
    source_doc_kind="he",
    xml_bytes=_wrap_he_body(
        b"Korkein oikeus k\xc3\xa4sitteli asiaa ratkaisussaan KKO 2019:3 "
        b"ja KHO 2021:55. "
        b"Oikeusasiamies on antanut ratkaisun EOAK/1234/2022. "
        b"Arvonlis\xc3\xa4verolain (1501/1993) mukaisesti."
    ),
    expected_citations=[
        {
            "source_doc_id": "184/2024",
            "source_doc_kind": "he",
            "kind": "court_kko",
            "canonical_id": "fi.court.kko.2019.3",
            "case_year": 2019,
            "case_number": 3,
            "context": "he_rationale",
        },
        {
            "source_doc_id": "184/2024",
            "source_doc_kind": "he",
            "kind": "court_kho",
            "canonical_id": "fi.court.kho.2021.55",
            "case_year": 2021,
            "case_number": 55,
            "context": "he_rationale",
        },
        {
            "source_doc_id": "184/2024",
            "source_doc_kind": "he",
            "kind": "ombudsman_eoa",
            "canonical_id": "fi.eoa.1234.2022",
            "case_year": 2022,
            "case_number": 1234,
            "context": "he_rationale",
        },
        {
            "source_doc_id": "184/2024",
            "source_doc_kind": "he",
            "kind": "statute_inline",
            "canonical_id": "1501/1993",
            "context": "he_rationale",
        },
    ],
    expected_pattern_matches=[],
)


# ---------------------------------------------------------------------------
# Fixture 3: HE->HE policy-coordination citation in HE prose
#
# HE 184/2024 referring to HE 116/2024 in its perustelut.
# Only extracted when doc_kind='he' (statute bodies don't emit HE_INLINE).
# ---------------------------------------------------------------------------

HE_INLINE_CITATION = InlineCorpusFixture(
    fixture_id="he_inline_citation",
    description="HE->HE policy-coordination citation in HE perustelut prose",
    source_doc_id="184/2024",
    source_doc_kind="he",
    xml_bytes=_wrap_he_body(
        b"T\xc3\xa4ss\xc3\xa4 yhteydess\xc3\xa4 viitataan my\xc3\xb6s hallituksen esitykseen "
        b"HE 116/2024, jossa k\xc3\xa4siteltiin samaa asiaa."
    ),
    expected_citations=[
        {
            "source_doc_id": "184/2024",
            "source_doc_kind": "he",
            "kind": "he_inline",
            "canonical_id": "he/2024/116",
            "raw_text": "HE 116/2024",
            "case_year": 2024,
            "case_number": 116,
            "context": "he_rationale",
        },
    ],
    expected_pattern_matches=[],
    expected_absent_kinds=["court_kko", "court_kho"],
)


# ---------------------------------------------------------------------------
# Fixture 4: VTV report citation in HE perustelut
# ---------------------------------------------------------------------------

HE_VTV_CITATION = InlineCorpusFixture(
    fixture_id="he_vtv_citation",
    description="VTV audit report citation in HE perustelut",
    source_doc_id="200/2023",
    source_doc_kind="he",
    xml_bytes=_wrap_he_body(
        b"Valtiontalouden tarkastusviraston kertomus VTV 5/2022 osoitti puutteet."
    ),
    expected_citations=[
        {
            "source_doc_id": "200/2023",
            "source_doc_kind": "he",
            "kind": "vtv_report",
            "canonical_id": "fi.vtv.5.2022",
            "raw_text": "VTV 5/2022",
            "case_year": 2022,
            "case_number": 5,
            "context": "he_rationale",
        },
    ],
    expected_pattern_matches=[],
)


# ---------------------------------------------------------------------------
# Fixture 5: EK in preliminaryWork — cross-feature composition with #11
#
# EK 42/2023 appears in a preliminaryWork block.
# - EK: emitted by THIS extractor (closes #11 UNRESOLVED gap).
# - Other patterns (HE, committee, etc.) in the same paragraph are suppressed
#   (they belong to #11).
# The HE perustelut text "HE 100/2023" should NOT be extracted from prelim.
# ---------------------------------------------------------------------------

EK_IN_PRELIMINARY_WORK = InlineCorpusFixture(
    fixture_id="ek_in_preliminary_work",
    description="EK (parliament kirjelma) in preliminaryWork block — closes #11 gap",
    source_doc_id="500/2023",
    source_doc_kind="statute",
    # The perustelut text is in an hcontainer outside prelim
    xml_bytes=_wrap_he_body_with_prelim(
        he_p_content=b"Laki on valmisteltu.",
        # In prelim: EK should fire, but HE N/YYYY (if present) should not from this extractor
        prelim_p_content=b"EK 42/2023",
    ),
    expected_citations=[
        {
            "source_doc_id": "500/2023",
            "source_doc_kind": "statute",
            "kind": "parliament_kirjelma",
            "canonical_id": "fi.ek.42.2023",
            "raw_text": "EK 42/2023",
            "case_year": 2023,
            "case_number": 42,
            "context": "preliminary_work",
        },
    ],
    expected_pattern_matches=[],
    # HE_INLINE must not fire in preliminaryWork (belongs to #11)
    expected_absent_kinds=["he_inline"],
)


# ---------------------------------------------------------------------------
# Fixture 6: Negative — bare YYYY/N not in recognized context → no row emitted
#
# A body paragraph with dates like "1.1.2024" and statute-like forms embedded
# in other contexts that should NOT trigger the statute_inline recognizer.
# The pattern "jätevesilain (2024/3)" would match, but "3.5.2024" would not.
# ---------------------------------------------------------------------------

NEGATIVE_BARE_NUMBERS = InlineCorpusFixture(
    fixture_id="negative_bare_numbers",
    description="Bare NNNN/N in date context — no citation row emitted",
    source_doc_id="99/2019",
    source_doc_kind="statute",
    xml_bytes=_wrap_statute_body(
        b"Laki tuli voimaan 15.3.2024. Asetus on p\xc3\xa4iv\xc3\xa4tty 2024/3."
    ),
    expected_citations=[],
    expected_pattern_matches=[],
)


# ---------------------------------------------------------------------------
# Fixture 7: Ref-markup composition — <ref>-tagged citations skipped
#
# A paragraph containing:
#   - <ref href="/akn/fi/act/statute/2022/711">lannoitelain</ref>
#     — this is inside a <ref> element; text should be excluded (deferred to #1)
#   - "KKO 2020:1" in plain text outside the ref — should be extracted.
# ---------------------------------------------------------------------------

REF_MARKUP_DEFERRED = InlineCorpusFixture(
    fixture_id="ref_markup_deferred",
    description="<ref>-markup citations skipped; only plain-text KKO extracted",
    source_doc_id="711/2022",
    source_doc_kind="statute",
    xml_bytes=(
        _AKN_OPEN
        + b"<act><meta/><body>"
        + b"<section><num>1 \xc2\xa7</num>"
        + b"<paragraph><content>"
        + b"<p>"
        + b'Ks. <ref href="/akn/fi/act/statute/2022/711">lannoitelain (711/2022)</ref> '
        + b"sek\xc3\xa4 ratkaisua KKO 2020:1."
        + b"</p>"
        + b"</content></paragraph>"
        + b"</section>"
        + b"</body></act>"
        + _AKN_CLOSE
    ),
    expected_citations=[
        {
            "source_doc_id": "711/2022",
            "source_doc_kind": "statute",
            "kind": "court_kko",
            "canonical_id": "fi.court.kko.2020.1",
            "raw_text": "KKO 2020:1",
            "context": "enacted_statute_body",
        },
    ],
    expected_pattern_matches=[],
    # statute_inline must NOT fire on the ref content
    expected_absent_kinds=["statute_inline"],
)


# ---------------------------------------------------------------------------
# All fixtures for parametric testing
# ---------------------------------------------------------------------------

ALL_FIXTURES = (
    STATUTE_BODY_COURT_REFS,
    HE_PERUSTELUT_MIXED_CITATIONS,
    HE_INLINE_CITATION,
    HE_VTV_CITATION,
    EK_IN_PRELIMINARY_WORK,
    NEGATIVE_BARE_NUMBERS,
    REF_MARKUP_DEFERRED,
)
