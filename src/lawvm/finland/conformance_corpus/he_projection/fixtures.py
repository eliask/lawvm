"""Conformance corpus fixtures for HE corpus projection.

Per feature brief HE_CORPUS_PROJECTION.md §Verification regime.

Covers the five fixture cases required by the brief:
  1. FULL_AKN HE with multi-section body (HE 98/1996 pattern).
  2. PDF_WRAPPER HE — metadata only, no atoms/refs/signatures.
  3. HE with multiple TLCOrganization references.
  4. HE with inline <ref href="/akn/fi/act/..."> crosslinks.
  5. HE with bilingual fin@/swe@ coverage.

Each HEProjectionFixture contains:
  - fixture_id: str
  - description: str
  - xml_bytes: bytes (minimal valid AKN XML for one language variant)
  - expected_corpus_row: dict (partial assertions on fi_he_corpus row)
  - expected_atom_rows: list[dict] (partial assertions on fi_he_atoms rows)
  - expected_law_ref_rows: list[dict] (partial assertions on fi_he_law_refs rows)
  - expected_signature_rows: list[dict] (partial assertions on fi_he_signatures rows)

Assertions are partial: only listed keys must match. Extra keys allowed
(backward-compatible schema growth).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Fixture type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HEProjectionFixture:
    """One conformance corpus fixture for HE corpus projection."""

    fixture_id: str
    description: str
    he_year: int
    he_number: int
    lang: str
    xml_bytes: bytes
    expected_corpus_row: Dict[str, Any] = field(default_factory=dict)
    expected_atom_rows: List[Dict[str, Any]] = field(default_factory=list)
    expected_law_ref_rows: List[Dict[str, Any]] = field(default_factory=list)
    expected_signature_rows: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# AKN + Finlex namespace boilerplate
# ---------------------------------------------------------------------------

_AKN_OPEN = (
    b'<akomaNtoso '
    b'xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0" '
    b'xmlns:finlex="http://data.finlex.fi/schema/finlex">'
)
_AKN_CLOSE = b"</akomaNtoso>"


def _he_doc(
    year: int,
    number: int,
    lang: str,
    ministry_id: str,
    ministry_show_as: str,
    title: bytes,
    he_num_text: bytes,
    date_issued: str,
    finlex_state: str,
    body_xml: bytes,
    meta_extra: bytes = b"",
    extra_refs_block: bytes = b"",
    signatures_xml: bytes = b"",
) -> bytes:
    """Build a minimal valid AKN government-proposal document for an HE fixture."""
    frbr_uri = f"/akn/fi/doc/government-proposal/{year}/{number}"
    frbr_expr_uri = f"{frbr_uri}/{lang}@"
    ministry_ref = f"#{ministry_id}"

    refs_block = (
        b"<references source='#lawvm-test'>"
        + (
            f'<TLCOrganization eId="{ministry_id}" showAs="{ministry_show_as}" '
            f'href="/ontology/organization/fi/{ministry_id}"/>'
        ).encode("utf-8")
        + extra_refs_block
        + b"</references>"
    )

    meta = (
        b"<meta>"
        + (
            b"<identification source='#lawvm-test'>"
            b"<FRBRWork>"
            + f"<FRBRuri value='{frbr_uri}'/>".encode("utf-8")
            + b"<FRBRsubtype value='government-proposal'/>"
            + f"<FRBRdate name='dateIssued' date='{date_issued}'/>".encode("utf-8")
            + b"<FRBRauthor href='#government'/>"
            + b"</FRBRWork>"
            + b"<FRBRExpression>"
            + f"<FRBRuri value='{frbr_expr_uri}'/>".encode("utf-8")
            + f"<FRBRlanguage language='{lang}'/>".encode("utf-8")
            + b"</FRBRExpression>"
            + b"</identification>"
        )
        + (
            f"<finlex:administrativeBranch refersTo='{ministry_ref}' "
            f"source='#lawvm-test'/>".encode("utf-8")
        )
        + f"<finlex:state value='{finlex_state}' source='#lawvm-test'/>".encode("utf-8")
        + refs_block
        + meta_extra
        + b"</meta>"
    )

    preface = (
        b"<preface>"
        + b"<docNumber>" + he_num_text + b"</docNumber>"
        + b"<docTitle>" + title + b"</docTitle>"
        + b"</preface>"
    )

    main_body = b"<mainBody>" + body_xml + b"</mainBody>"

    conclusions_part = b""
    if signatures_xml:
        # Use the real Finlex HE structure: <hcontainer name="conclusions"> inside mainBody,
        # NOT a bare <conclusions> AKN element.  The bare element never appears in real
        # Finlex XML (verified across all 8438 HEs in corpus 2026-06-04).
        # This fixture must match the real structure so tests catch regressions
        # in the _extract_signatures_from_conclusions lookup path.
        conclusions_part = (
            b"<hcontainer name='conclusions'>"
            + signatures_xml
            + b"</hcontainer>"
        )

    doc_inner = (
        b"<doc FRBRsubtype='government-proposal'>"
        + meta
        + preface
        + main_body
        + conclusions_part
        + b"</doc>"
    )
    return _AKN_OPEN + doc_inner + _AKN_CLOSE


# ---------------------------------------------------------------------------
# Fixture 1: FULL_AKN HE with multi-section body (HE 98/1996 pattern)
# ---------------------------------------------------------------------------

_FULL_AKN_BODY = (
    b"<hcontainer name='rationale'>"
    b"<heading>1 Nyk\xc3\xb6ytila</heading>"
    b"<hcontainer name='section'>"
    b"<num>1.1</num>"
    b"<heading>Lains\xc3\xa4\xc3\xa4d\xc3\xa4nt\xc3\xb6</heading>"
    b"<content><p>Rikoslain 34 luvussa s\xc3\xa4\xc3\xa4det\xc3\xa4\xc3\xa4n...</p></content>"
    b"</hcontainer>"
    b"<hcontainer name='section'>"
    b"<num>1.2</num>"
    b"<heading>K\xc3\xa4yt\xc3\xa4nt\xc3\xb6</heading>"
    b"<content><p>K\xc3\xa4yt\xc3\xa4nn\xc3\xb6ss\xc3\xa4 on havaittu puutteita.</p></content>"
    b"</hcontainer>"
    b"</hcontainer>"
    b"<hcontainer name='rationale'>"
    b"<heading>2 Esityksen tavoitteet</heading>"
    b"<content><p>Tavoitteena on parantaa s\xc3\xa4\xc3\xa4ntely\xc3\xa4.</p></content>"
    b"</hcontainer>"
)

_FULL_AKN_SIGNATURES = (
    b"<blockContainer name='signatures'>"
    b"<signature><role>Tasavallan Presidentti</role><person>Martti Ahtisaari</person></signature>"
    b"<signature><role>Oikeusministeri</role><person>Sauli Niinist\xc3\xb6</person></signature>"
    b"</blockContainer>"
)

FULL_AKN_HE = HEProjectionFixture(
    fixture_id="full_akn_multi_section",
    description=(
        "FULL_AKN HE with multi-section rationale body (HE 98/1996 pattern). "
        "is_structured=True. Emits rows in fi_he_corpus and fi_he_atoms. "
        "Includes signature elements."
    ),
    he_year=1996,
    he_number=98,
    lang="fin",
    xml_bytes=_he_doc(
        year=1996,
        number=98,
        lang="fin",
        ministry_id="fi.ministry-of-justice",
        ministry_show_as="Oikeusministeri\xf6",
        title="Hallituksen esitys Eduskunnalle rikoslain muuttamisesta".encode("utf-8"),
        he_num_text="HE 98/1996 vp".encode("utf-8"),
        date_issued="1996-05-24",
        finlex_state="closed",
        body_xml=_FULL_AKN_BODY,
        signatures_xml=_FULL_AKN_SIGNATURES,
    ),
    expected_corpus_row={
        "he_id": "HE 98/1996 vp",
        "he_year": 1996,
        "he_number": 98,
        "he_uri": "/akn/fi/doc/government-proposal/1996/98",
        "ministry_canonical_id": "fi.ministry-of-justice",
        "structural_tier": "full_akn",
        "is_structured": True,
        "finlex_state": "closed",
    },
    expected_signature_rows=[
        {
            "he_year": 1996,
            "he_number": 98,
            "role": "Tasavallan Presidentti",
            "person": "Martti Ahtisaari",
            "signature_order": 0,
        },
        {
            "he_year": 1996,
            "he_number": 98,
            "role": "Oikeusministeri",
            "signature_order": 1,
        },
    ],
)

# ---------------------------------------------------------------------------
# Fixture 2: PDF_WRAPPER HE — metadata only
# ---------------------------------------------------------------------------

_PDF_WRAPPER_BODY = (
    b"<hcontainer name='contentAbsent'>"
    b"<componentRef src='main.pdf' alt='Katso PDF'/>"
    b"</hcontainer>"
)

PDF_WRAPPER_HE = HEProjectionFixture(
    fixture_id="pdf_wrapper_metadata_only",
    description=(
        "PDF_WRAPPER HE (HE 103/1996 pattern). main.xml is a stub pointing "
        "at main.pdf. Emits ONLY a fi_he_corpus row with is_structured=False. "
        "No atoms, no law_refs, no signatures (no extractable body)."
    ),
    he_year=1996,
    he_number=103,
    lang="fin",
    xml_bytes=_he_doc(
        year=1996,
        number=103,
        lang="fin",
        ministry_id="fi.ministry-of-finance",
        ministry_show_as="Valtiovarainministeri\xf6",
        title="Hallituksen esitys talousarviolaiksi".encode("utf-8"),
        he_num_text="HE 103/1996 vp".encode("utf-8"),
        date_issued="1996-06-07",
        finlex_state="closed",
        body_xml=_PDF_WRAPPER_BODY,
    ),
    expected_corpus_row={
        "he_year": 1996,
        "he_number": 103,
        "ministry_canonical_id": "fi.ministry-of-finance",
        "structural_tier": "pdf_wrapper",
        "is_structured": False,
    },
    expected_atom_rows=[],
    expected_law_ref_rows=[],
    expected_signature_rows=[],
)

# ---------------------------------------------------------------------------
# Fixture 3: HE with multiple TLCOrganization references
# ---------------------------------------------------------------------------

_MULTI_ORG_EXTRA_REFS = (
    b'<TLCOrganization eId="fi.ministry-of-social-affairs-and-health" '
    b'showAs="Sosiaali- ja terveysministeri\xc3\xb6" '
    b'href="/ontology/organization/fi/fi.ministry-of-social-affairs-and-health"/>'
    b'<TLCOrganization eId="fi.kela" '
    b'showAs="Kansanel\xc3\xa4kelaitos" '
    b'href="/ontology/organization/fi/fi.kela"/>'
)

_MULTI_ORG_BODY = (
    b"<hcontainer name='rationale'>"
    b"<heading>1 Tausta</heading>"
    b"<content><p>Kela ja STM ovat yhteisty\xc3\xb6ss\xc3\xa4...</p></content>"
    b"</hcontainer>"
)

MULTI_ORG_HE = HEProjectionFixture(
    fixture_id="multi_tlc_organization",
    description=(
        "HE with multiple TLCOrganization references in the AKN references block. "
        "Tests cross-substrate reuse verification: ministry extraction picks the "
        "correct one (finlex:administrativeBranch target)."
    ),
    he_year=2001,
    he_number=55,
    lang="fin",
    xml_bytes=_he_doc(
        year=2001,
        number=55,
        lang="fin",
        ministry_id="fi.ministry-of-social-affairs-and-health",
        ministry_show_as="Sosiaali- ja terveysministeri\xf6",
        title="Hallituksen esitys Kela-lain muuttamisesta".encode("utf-8"),
        he_num_text="HE 55/2001 vp".encode("utf-8"),
        date_issued="2001-04-13",
        finlex_state="closed",
        body_xml=_MULTI_ORG_BODY,
        extra_refs_block=_MULTI_ORG_EXTRA_REFS,
    ),
    expected_corpus_row={
        "he_year": 2001,
        "he_number": 55,
        "ministry_canonical_id": "fi.ministry-of-social-affairs-and-health",
        "structural_tier": "full_akn",
        "is_structured": True,
    },
)

# ---------------------------------------------------------------------------
# Fixture 4: HE with inline <ref> crosslinks
# ---------------------------------------------------------------------------

_REF_CROSSLINKS_BODY = (
    b"<hcontainer name='rationale'>"
    b"<heading>1 Nykytila</heading>"
    b"<content><p>"
    b"Voimassa olevan "
    b'<ref href="/akn/fi/act/statute-consolidated/2003/314#sec_5">'
    b"ymp\xc3\xa4rist\xc3\xb6nsuojelulain 5 \xc2\xa7:n</ref>"
    b" mukaan..."
    b"</p></content>"
    b"</hcontainer>"
    b"<hcontainer name='rationale'>"
    b"<heading>2 Ehdotuksen p\xc3\xa4\xc3\xa4asiallinen sis\xc3\xa4lt\xc3\xb6</heading>"
    b"<content><p>"
    b"Muutetaan "
    b'<ref href="/akn/fi/act/statute-consolidated/1996/86">'
    b"vesilakia</ref>"
    b"."
    b"</p></content>"
    b"</hcontainer>"
)

REF_CROSSLINKS_HE = HEProjectionFixture(
    fixture_id="inline_ref_crosslinks",
    description=(
        "HE with inline <ref href='/akn/fi/act/...'> crosslinks to enacted statutes. "
        "Tests ReferenceMention extractor reuse (unchanged from #1)."
    ),
    he_year=2004,
    he_number=227,
    lang="fin",
    xml_bytes=_he_doc(
        year=2004,
        number=227,
        lang="fin",
        ministry_id="fi.ministry-of-the-environment",
        ministry_show_as="Ymp\xe4rist\xf6ministeri\xf6",
        title="Hallituksen esitys ymp\xc3\xa4rist\xc3\xb6nsuojelulain muuttamisesta".encode("utf-8"),
        he_num_text="HE 227/2004 vp".encode("utf-8"),
        date_issued="2004-10-22",
        finlex_state="closed",
        body_xml=_REF_CROSSLINKS_BODY,
    ),
    expected_corpus_row={
        "he_year": 2004,
        "he_number": 227,
        "ministry_canonical_id": "fi.ministry-of-the-environment",
        "structural_tier": "full_akn",
        "is_structured": True,
    },
    expected_law_ref_rows=[
        {
            "target_statute_id": "2003/314",
            "cite_kind": "cross_statute",
            "cite_confidence": "exact",
            "phrase_lemma": "ref_element",
        },
        {
            "target_statute_id": "1996/86",
            "cite_kind": "cross_statute",
            # Bare-act <ref href=".../1996/86"> ("vesilakia") with no #sec_N
            # provision fragment: the act is known but the in-act provision is
            # pending, so the resolution-status-driven confidence is
            # STATUTE_ONLY, not a guessed whole-act EXACT (catalogue §0.1).
            "cite_confidence": "statute_only",
            "phrase_lemma": "ref_element",
        },
    ],
)

# ---------------------------------------------------------------------------
# Fixture 5: Bilingual HE (fin@ variant)
# ---------------------------------------------------------------------------

BILINGUAL_HE_FIN = HEProjectionFixture(
    fixture_id="bilingual_fin_variant",
    description=(
        "Finnish (fin@) variant of a bilingual HE. "
        "The corpus row must carry lang='fin'."
    ),
    he_year=1999,
    he_number=14,
    lang="fin",
    xml_bytes=_he_doc(
        year=1999,
        number=14,
        lang="fin",
        ministry_id="fi.ministry-of-justice",
        ministry_show_as="Oikeusministeri\xf6",
        title="Hallituksen esitys laiksi oikeudenkäymiskaaren muuttamisesta".encode("utf-8"),
        he_num_text="HE 14/1999 vp".encode("utf-8"),
        date_issued="1999-02-05",
        finlex_state="closed",
        body_xml=(
            b"<hcontainer name='rationale'>"
            b"<heading>1 Tausta</heading>"
            b"<content><p>Oikeudenk\xc3\xa4ymiskaarta muutetaan.</p></content>"
            b"</hcontainer>"
        ),
    ),
    expected_corpus_row={
        "he_year": 1999,
        "he_number": 14,
        "lang": "fin",
        "structural_tier": "full_akn",
        "is_structured": True,
    },
)

# ---------------------------------------------------------------------------
# Fixture 6: Missing ministry — typed observation expected
# ---------------------------------------------------------------------------

_NO_MINISTRY_XML = (
    _AKN_OPEN
    + b"<doc FRBRsubtype='government-proposal'>"
    + b"<meta>"
    + b"<identification source='#lawvm-test'>"
    + b"<FRBRWork>"
    + b"<FRBRuri value='/akn/fi/doc/government-proposal/2000/999'/>"
    + b"<FRBRsubtype value='government-proposal'/>"
    + b"<FRBRdate name='dateIssued' date='2000-03-15'/>"
    + b"<FRBRauthor href='#government'/>"
    + b"</FRBRWork>"
    + b"<FRBRExpression>"
    + b"<FRBRuri value='/akn/fi/doc/government-proposal/2000/999/fin@'/>"
    + b"<FRBRlanguage language='fin'/>"
    + b"</FRBRExpression>"
    + b"</identification>"
    + b"<finlex:state value='closed' source='#lawvm-test'/>"
    + b"<references source='#lawvm-test'/>"
    + b"</meta>"
    + b"<preface>"
    + b"<docNumber>HE 999/2000 vp</docNumber>"
    + b"<docTitle>Hallituksen esitys laiksi X:n muuttamisesta</docTitle>"
    + b"</preface>"
    + b"<mainBody>"
    + b"<hcontainer name='rationale'>"
    + b"<heading>1 Tausta</heading>"
    + b"<content><p>Yksityinen esitys.</p></content>"
    + b"</hcontainer>"
    + b"</mainBody>"
    + b"</doc>"
    + _AKN_CLOSE
)

MISSING_MINISTRY_HE = HEProjectionFixture(
    fixture_id="missing_ministry",
    description=(
        "HE without finlex:administrativeBranch. ministry_canonical_id is empty. "
        "Per AGENTS.md §1.6, must emit HEMissingMinistryObservation. "
        "The corpus row IS emitted (no silent drop per §1.8)."
    ),
    he_year=2000,
    he_number=999,
    lang="fin",
    xml_bytes=_NO_MINISTRY_XML,
    expected_corpus_row={
        "he_year": 2000,
        "he_number": 999,
        "ministry_canonical_id": "",
        "structural_tier": "full_akn",
        "is_structured": True,
    },
)

# ---------------------------------------------------------------------------
# Non-HE document — must be rejected with typed error
# ---------------------------------------------------------------------------

_NON_HE_XML = (
    _AKN_OPEN
    + b"<act>"
    + b"<meta>"
    + b"<identification source='#lawvm-test'>"
    + b"<FRBRWork>"
    + b"<FRBRuri value='/akn/fi/act/statute-consolidated/2003/314'/>"
    + b"<FRBRsubtype value='statute-consolidated'/>"
    + b"<FRBRdate name='dateIssued' date='2003-05-28'/>"
    + b"</FRBRWork>"
    + b"<FRBRExpression>"
    + b"<FRBRuri value='/akn/fi/act/statute-consolidated/2003/314/fin@'/>"
    + b"<FRBRlanguage language='fin'/>"
    + b"</FRBRExpression>"
    + b"</identification>"
    + b"</meta>"
    + b"<body><section eId='sec_1'><num>1 \xc2\xa7</num></section></body>"
    + b"</act>"
    + _AKN_CLOSE
)

NON_HE_STATUTE = HEProjectionFixture(
    fixture_id="non_he_statute_rejected",
    description=(
        "A statute-consolidated AKN document (not a government-proposal). "
        "The HE projection must reject it with HEProjectionFailure "
        "rule_id='HE_PROJ.WRONG_FRBR_SUBTYPE'. No corpus row emitted."
    ),
    he_year=2003,
    he_number=314,
    lang="fin",
    xml_bytes=_NON_HE_XML,
    expected_corpus_row={},
)

# ---------------------------------------------------------------------------
# All fixtures, indexed by fixture_id
# ---------------------------------------------------------------------------

ALL_FIXTURES: dict[str, HEProjectionFixture] = {
    f.fixture_id: f
    for f in [
        FULL_AKN_HE,
        PDF_WRAPPER_HE,
        MULTI_ORG_HE,
        REF_CROSSLINKS_HE,
        BILINGUAL_HE_FIN,
        MISSING_MINISTRY_HE,
        NON_HE_STATUTE,
    ]
}
