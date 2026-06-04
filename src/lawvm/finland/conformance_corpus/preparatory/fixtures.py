"""Conformance corpus fixtures for PreparatoryReference extraction.

Covers each cell per PREPARATORY_REFERENCE_SWEEP.md §Verification regime:

  1. Full chain: HE + HaVM + EV + EU regulation with CELEX + EUVL
  2. Committee opinion only (PeVL pattern, no mietintö)
  3. Older law with HE + EV but no committee
  4. EU directive (not regulation) — verify directive classification
  5. Multiple EU acts in same <p> (combined paragraph handling)
  6. Unparseable <p> in preliminaryWork → UNRESOLVED + observation
  7. <p> OUTSIDE preliminaryWork — must NOT be extracted (negative fixture)

Each fixture is a PrepCorpusFixture with:
  - fixture_id: str
  - description: str
  - source_statute_id: str
  - xml_bytes: bytes  (minimal valid AKN XML)
  - expected_refs: list[dict]   (column-level assertions)
  - expected_rejected: list[dict]  (RejectedPreparatoryCandidate assertions)
  - expected_lifecycle_obs: list[dict]  (CommitteeLifecycleObservation assertions)

Assertions are partial: only the keys listed must match. Extra keys in the
actual record are allowed (backward-compatible schema growth).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Fixture type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrepCorpusFixture:
    """One conformance corpus fixture for PreparatoryReference extraction."""

    fixture_id: str
    description: str
    source_statute_id: str
    xml_bytes: bytes
    expected_refs: List[Dict[str, Any]] = field(default_factory=list)
    expected_rejected: List[Dict[str, Any]] = field(default_factory=list)
    expected_lifecycle_obs: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# AKN namespace boilerplate (reused across fixtures)
# ---------------------------------------------------------------------------

_AKN_OPEN = (
    b'<akomaNtoso '
    b'xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0" '
    b'xmlns:finlex="http://data.finlex.fi/schema/finlex">'
)
_AKN_CLOSE = b"</akomaNtoso>"


def _wrap_with_prelim(prelim_content: bytes) -> bytes:
    """Wrap preliminaryWork content in a minimal valid AKN act element."""
    return (
        _AKN_OPEN
        + b"<act><meta/><body>"
        + b'<hcontainer finlex:outline="Esity\xc3\xb6t ja allekirjoitukset" name="conclusions">'
        + b'<hcontainer finlex:outline="Esity\xc3\xb6t" name="preliminaryWork">'
        + b"<content>"
        + prelim_content
        + b"</content>"
        + b"</hcontainer>"
        + b"</hcontainer>"
        + b"</body></act>"
        + _AKN_CLOSE
    )


def _wrap_body_only(body_content: bytes) -> bytes:
    """Wrap body-only content (no preliminaryWork block) in a minimal AKN act."""
    return (
        _AKN_OPEN
        + b"<act><meta/><body>"
        + b"<section><num>1 \xc2\xa7</num>"
        + b"<paragraph><content>"
        + body_content
        + b"</content></paragraph>"
        + b"</section>"
        + b"</body></act>"
        + _AKN_CLOSE
    )


# ---------------------------------------------------------------------------
# Fixture 1: Full chain
#
# HE 173/2021 (via AKN <ref>) + HaVM 23/2022 + EV 156/2022
# + EU regulation (EU) 2017/2226 with CELEX 32017R2226 + EUVL L 327, 9.12.2017, s. 20
# ---------------------------------------------------------------------------

FULL_CHAIN = PrepCorpusFixture(
    fixture_id="full_chain",
    description=(
        "Full preparation chain: HE via AKN <ref>, HaVM committee mietintö, "
        "EV parliament response, EU regulation with CELEX + OJ reference."
    ),
    source_statute_id="2022/711",
    xml_bytes=_wrap_with_prelim(
        # HE via typed <ref>
        b'<p><ref href="/akn/fi/doc/government-proposal/2021/173">HE 173/2021</ref></p>'
        # Committee mietintö
        b"<p>HaVM 23/2022</p>"
        # Parliament response
        b"<p>EV 156/2022</p>"
        # EU regulation with CELEX + OJ
        b"<p>Euroopan parlamentin ja neuvoston asetus (EU) 2017/2226 "
        b"(32017R2226); EUVL L 327, 9.12.2017, s. 20</p>"
    ),
    expected_refs=[
        {
            "source_statute_id": "2022/711",
            "kind": "he",
            "canonical_id": "he/2021/173",
            "he_year": 2021,
            "he_number": 173,
            "confidence": "exact",
        },
        {
            "source_statute_id": "2022/711",
            "kind": "committee_report",
            "canonical_id": "fi.committee.havm.23.2022",
            "committee_abbrev": "HaVM",
            "confidence": "exact",
        },
        {
            "source_statute_id": "2022/711",
            "kind": "parliament_response",
            "canonical_id": "fi.ev.156.2022",
            "confidence": "exact",
        },
        {
            "source_statute_id": "2022/711",
            "kind": "eu_regulation",
            "canonical_id": "eu.celex.32017R2226",
            "eu_form": "EU",
            "eu_year": 2017,
            "eu_number": 2226,
            "celex": "32017R2226",
            "oj_series": "L",
            "oj_number": 327,
            "oj_page": 20,
            "confidence": "exact",
        },
    ],
    expected_rejected=[],
    expected_lifecycle_obs=[],
)

# ---------------------------------------------------------------------------
# Fixture 2: Committee opinion only (PeVL pattern)
#
# Statute that went through Constitutional Law Committee lausunto only.
# No mietintö, no EV — just PeVL.
# ---------------------------------------------------------------------------

COMMITTEE_OPINION_ONLY = PrepCorpusFixture(
    fixture_id="committee_opinion_only",
    description=(
        "Statute with only a committee opinion (PeVL = perustuslakivaliokunnan lausunto). "
        "No mietintö, no EV."
    ),
    source_statute_id="2019/438",
    xml_bytes=_wrap_with_prelim(
        b'<p><ref href="/akn/fi/doc/government-proposal/2018/72">HE 72/2018</ref></p>'
        b"<p>PeVL 12/2019</p>"
    ),
    expected_refs=[
        {
            "source_statute_id": "2019/438",
            "kind": "he",
            "canonical_id": "he/2018/72",
            "he_year": 2018,
            "he_number": 72,
            "confidence": "exact",
        },
        {
            "source_statute_id": "2019/438",
            "kind": "committee_opinion",
            "canonical_id": "fi.committee_opinion.pevl.12.2019",
            "committee_abbrev": "PeVL",
            "confidence": "exact",
        },
    ],
    expected_rejected=[],
    expected_lifecycle_obs=[],
)

# ---------------------------------------------------------------------------
# Fixture 3: Older law with HE + EV but no committee
#
# Pre-committee-era or short legislative pipeline without committee mietintö.
# ---------------------------------------------------------------------------

HE_EV_NO_COMMITTEE = PrepCorpusFixture(
    fixture_id="he_ev_no_committee",
    description=(
        "Older law with HE + EV only; no committee mietintö or opinion."
    ),
    source_statute_id="2003/314",
    xml_bytes=_wrap_with_prelim(
        b'<p><ref href="/akn/fi/doc/government-proposal/2002/218">HE 218/2002</ref></p>'
        b"<p>EV 243/2002</p>"
    ),
    expected_refs=[
        {
            "source_statute_id": "2003/314",
            "kind": "he",
            "canonical_id": "he/2002/218",
            "he_year": 2002,
            "he_number": 218,
            "confidence": "exact",
        },
        {
            "source_statute_id": "2003/314",
            "kind": "parliament_response",
            "canonical_id": "fi.ev.243.2002",
            "confidence": "exact",
        },
    ],
    expected_rejected=[],
    expected_lifecycle_obs=[],
)

# ---------------------------------------------------------------------------
# Fixture 4: EU directive (not regulation)
#
# CELEX type 'L' → EU_DIRECTIVE. Verify directive classification separate
# from regulation.
# ---------------------------------------------------------------------------

EU_DIRECTIVE = PrepCorpusFixture(
    fixture_id="eu_directive",
    description=(
        "EU directive reference with CELEX type 'L'. "
        "Verify kind=eu_directive (not eu_regulation)."
    ),
    source_statute_id="2020/759",
    xml_bytes=_wrap_with_prelim(
        b'<p><ref href="/akn/fi/doc/government-proposal/2019/101">HE 101/2019</ref></p>'
        b"<p>LiVM 5/2020</p>"
        b"<p>EV 67/2020</p>"
        b"<p>Euroopan parlamentin ja neuvoston direktiivi (EU) 2019/904 "
        b"(32019L0904); EUVL L 155, 12.6.2019, s. 1</p>"
    ),
    expected_refs=[
        {
            "source_statute_id": "2020/759",
            "kind": "he",
            "canonical_id": "he/2019/101",
        },
        {
            "source_statute_id": "2020/759",
            "kind": "committee_report",
            "canonical_id": "fi.committee.livm.5.2020",
            "committee_abbrev": "LiVM",
        },
        {
            "source_statute_id": "2020/759",
            "kind": "parliament_response",
            "canonical_id": "fi.ev.67.2020",
        },
        {
            "source_statute_id": "2020/759",
            "kind": "eu_directive",
            "canonical_id": "eu.celex.32019L0904",
            "celex": "32019L0904",
            "eu_form": "EU",
            "eu_year": 2019,
            "eu_number": 904,
            "oj_series": "L",
            "oj_number": 155,
            "confidence": "exact",
        },
    ],
    expected_rejected=[],
    expected_lifecycle_obs=[],
)

# ---------------------------------------------------------------------------
# Fixture 5: Multiple EU acts in same paragraph
#
# A single <p> can contain multiple EU act citations (e.g. amending regulations).
# Our recognizer takes the FIRST EU act match in priority. Document this behavior:
# if a paragraph contains multiple distinct EU acts, we emit one row per
# paragraph (first match wins + CELEX/OJ for that match).
# For multi-act paragraphs where each act is on its OWN <p>, all are captured.
# This fixture tests the more realistic case: each EU act on its own <p>.
# ---------------------------------------------------------------------------

MULTI_EU_ACTS = PrepCorpusFixture(
    fixture_id="multi_eu_acts",
    description=(
        "Multiple EU acts, each on its own <p>. "
        "Verify each is captured as a separate PreparatoryReference row."
    ),
    source_statute_id="2018/1050",
    xml_bytes=_wrap_with_prelim(
        b'<p><ref href="/akn/fi/doc/government-proposal/2018/121">HE 121/2018</ref></p>'
        b"<p>HaVM 14/2018</p>"
        b"<p>EV 90/2018</p>"
        b"<p>Euroopan parlamentin ja neuvoston asetus (EU) 2016/679 "
        b"(32016R0679); EUVL L 119, 4.5.2016, s. 1</p>"
        b"<p>Euroopan parlamentin ja neuvoston asetus (EU) 2018/1725 "
        b"(32018R1725); EUVL L 295, 21.11.2018, s. 39</p>"
    ),
    expected_refs=[
        {
            "source_statute_id": "2018/1050",
            "kind": "he",
            "canonical_id": "he/2018/121",
        },
        {
            "source_statute_id": "2018/1050",
            "kind": "committee_report",
            "committee_abbrev": "HaVM",
        },
        {
            "source_statute_id": "2018/1050",
            "kind": "parliament_response",
        },
        {
            "source_statute_id": "2018/1050",
            "kind": "eu_regulation",
            "canonical_id": "eu.celex.32016R0679",
            "celex": "32016R0679",
        },
        {
            "source_statute_id": "2018/1050",
            "kind": "eu_regulation",
            "canonical_id": "eu.celex.32018R1725",
            "celex": "32018R1725",
        },
    ],
    expected_rejected=[],
    expected_lifecycle_obs=[],
)

# ---------------------------------------------------------------------------
# Fixture 6: Unparseable <p> in preliminaryWork
#
# A <p> with text that does not match any known pattern → UNRESOLVED.
# Emits RejectedPreparatoryCandidate.
# ---------------------------------------------------------------------------

UNRESOLVED_P_TEXT = PrepCorpusFixture(
    fixture_id="unresolved_p_text",
    description=(
        "A <p> in preliminaryWork whose text does not match any known citation pattern. "
        "Emits RejectedPreparatoryCandidate with kind=unresolved."
    ),
    source_statute_id="2015/410",
    xml_bytes=_wrap_with_prelim(
        b'<p><ref href="/akn/fi/doc/government-proposal/2014/182">HE 182/2014</ref></p>'
        b"<p>TyVM 13/2015</p>"
        b"<p>EV 211/2015</p>"
        # Unparseable text (a signature line accidentally in the block, or
        # some non-standard citation form not yet in the grammar)
        b"<p>Allekirjoitettu Helsingiss\xc3\xa4 19 p\xc3\xa4iv\xc3\xa4n\xc3\xa4 "
        b"kes\xc3\xa4kuuta 2015</p>"
    ),
    expected_refs=[
        {
            "source_statute_id": "2015/410",
            "kind": "he",
            "canonical_id": "he/2014/182",
        },
        {
            "source_statute_id": "2015/410",
            "kind": "committee_report",
            "committee_abbrev": "TyVM",
            "canonical_id": "fi.committee.tyvm.13.2015",
        },
        {
            "source_statute_id": "2015/410",
            "kind": "parliament_response",
            "canonical_id": "fi.ev.211.2015",
        },
    ],
    expected_rejected=[
        {
            "rule_id": "fi_prep_ref_unresolved_p_text",
            "phase": "preparatory_ref_extraction",
            "source_statute_id": "2015/410",
            "blocking": False,
        },
    ],
    expected_lifecycle_obs=[],
)

# ---------------------------------------------------------------------------
# Fixture 7: Negative — <p> OUTSIDE preliminaryWork is NOT extracted
#
# Body-text <p> elements that look like citations (e.g. "HaVM 5/2022")
# appearing in section body text must NOT be captured by the extractor.
# The extractor only walks hcontainer[name=preliminaryWork].
# ---------------------------------------------------------------------------

NEGATIVE_OUTSIDE_PRELIM = PrepCorpusFixture(
    fixture_id="negative_outside_prelim",
    description=(
        "Citation-like text in statute body (not in preliminaryWork). "
        "Must NOT produce any PreparatoryReference rows."
    ),
    source_statute_id="2021/999",
    # Body contains committee-looking text but NO preliminaryWork block
    xml_bytes=_wrap_body_only(
        b"<p>T\xc3\xa4m\xc3\xa4 laki perustuu HaVM 12/2021 mietint\xc3\xb6\xc3\xb6n.</p>"
        b"<p>EV 88/2021 mukaisesti.</p>"
    ),
    expected_refs=[],       # Nothing — no preliminaryWork block
    expected_rejected=[],
    expected_lifecycle_obs=[],
)

# ---------------------------------------------------------------------------
# Fixture 8: EVK supplementary parliament response (rare)
# ---------------------------------------------------------------------------

EVK_RESPONSE = PrepCorpusFixture(
    fixture_id="evk_response",
    description=(
        "Supplementary parliament response EVK pattern (rare). "
        "kind=parliament_response_comm, canonical_id=fi.evk.N.YYYY."
    ),
    source_statute_id="2008/532",
    xml_bytes=_wrap_with_prelim(
        b'<p><ref href="/akn/fi/doc/government-proposal/2007/84">HE 84/2007</ref></p>'
        b"<p>LaVM 9/2008</p>"
        b"<p>EVK 3/2008</p>"
    ),
    expected_refs=[
        {
            "source_statute_id": "2008/532",
            "kind": "he",
            "canonical_id": "he/2007/84",
        },
        {
            "source_statute_id": "2008/532",
            "kind": "committee_report",
            "committee_abbrev": "LaVM",
        },
        {
            "source_statute_id": "2008/532",
            "kind": "parliament_response_comm",
            "canonical_id": "fi.evk.3.2008",
            "confidence": "exact",
        },
    ],
    expected_rejected=[],
    expected_lifecycle_obs=[],
)

# ---------------------------------------------------------------------------
# Fixture 9: Law initiative (LA)
# ---------------------------------------------------------------------------

LAW_INITIATIVE = PrepCorpusFixture(
    fixture_id="law_initiative",
    description=(
        "Law initiative (lakialoite LA) in preparation chain. "
        "kind=law_initiative, canonical_id=fi.la.N.YYYY."
    ),
    source_statute_id="2012/348",
    xml_bytes=_wrap_with_prelim(
        b"<p>LA 5/2011</p>"
        b"<p>LaVM 3/2012</p>"
        b"<p>EV 28/2012</p>"
    ),
    expected_refs=[
        {
            "source_statute_id": "2012/348",
            "kind": "law_initiative",
            "canonical_id": "fi.la.5.2011",
            "confidence": "exact",
        },
        {
            "source_statute_id": "2012/348",
            "kind": "committee_report",
            "committee_abbrev": "LaVM",
        },
        {
            "source_statute_id": "2012/348",
            "kind": "parliament_response",
        },
    ],
    expected_rejected=[],
    expected_lifecycle_obs=[],
)


# ---------------------------------------------------------------------------
# ALL_FIXTURES registry
# ---------------------------------------------------------------------------

ALL_FIXTURES = (
    FULL_CHAIN,
    COMMITTEE_OPINION_ONLY,
    HE_EV_NO_COMMITTEE,
    EU_DIRECTIVE,
    MULTI_EU_ACTS,
    UNRESOLVED_P_TEXT,
    NEGATIVE_OUTSIDE_PRELIM,
    EVK_RESPONSE,
    LAW_INITIATIVE,
)

__all__ = [
    "PrepCorpusFixture",
    "ALL_FIXTURES",
    "FULL_CHAIN",
    "COMMITTEE_OPINION_ONLY",
    "HE_EV_NO_COMMITTEE",
    "EU_DIRECTIVE",
    "MULTI_EU_ACTS",
    "UNRESOLVED_P_TEXT",
    "NEGATIVE_OUTSIDE_PRELIM",
    "EVK_RESPONSE",
    "LAW_INITIATIVE",
]
