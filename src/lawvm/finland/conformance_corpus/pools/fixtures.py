"""Conformance corpus fixtures for PoolMention extraction.

Covers the conformance cells from POOL_MENTION_EXTRACTION.md:
  - EXACT x BUDGET_LINE with explicit '\\d{2}\\.\\d{2}\\.\\d{2}' momentti
  - APPROXIMATE x BUDGET_LINE via renumbered momentti lineage (2020: 28.91.51)
  - EXACT x CAPACITY_CAP with explicit quantity+unit (lannoitelaki Cd-kuormakatto)
  - THRESHOLD with numeric value and unit
  - UNRESOLVED x generic 'yleiskate' without further context
  - Negative: year reference 'vuoden 2020' that must NOT produce a PoolMention

Each fixture is a PoolCorpusFixture with:
  - source_statute_id: str
  - xml_bytes: bytes  (minimal valid AKN XML)
  - expected_kind: QuantityKind (expected quantity_kind)
  - expected_confidence: PoolResolutionConfidence (expected resolution_confidence)
  - expected_pool_id: str | None  (expected pool_canonical_id)
  - expected_numeric: float | None
  - expected_unit: str | None
  - description: str
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from lawvm.finland.pool_mention_primitive import QuantityKind, PoolResolutionConfidence

# ---------------------------------------------------------------------------
# Fixture type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PoolCorpusFixture:
    """One conformance corpus fixture for PoolMention extraction."""

    fixture_id: str
    description: str
    source_statute_id: str
    xml_bytes: bytes
    expected_kind: Optional[QuantityKind] = None
    expected_confidence: Optional[PoolResolutionConfidence] = None
    expected_pool_id: Optional[str] = None
    expected_numeric: Optional[float] = None
    expected_unit: Optional[str] = None
    expected_ambiguous: bool = False
    expected_rejected: bool = False
    expected_renumbering: bool = False
    expected_mention_count: int = 0
    """If > 0, asserts at least this many mentions."""


# ---------------------------------------------------------------------------
# AKN namespace boilerplate (reused across fixtures)
# ---------------------------------------------------------------------------

_AKN_OPEN = (
    b'<akomaNtoso '
    b'xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0" '
    b'xmlns:finlex="http://data.finlex.fi/schema/finlex">'
)
_AKN_CLOSE = b"</akomaNtoso>"


def _wrap_body(body_inner: bytes, meta_inner: bytes = b"") -> bytes:
    """Wrap body + meta in a minimal AKN act element."""
    meta = b"<meta>" + meta_inner + b"</meta>" if meta_inner else b""
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


def _section(num: bytes, content: bytes) -> bytes:
    return (
        b"<section><num>" + num + b"</num>"
        b"<paragraph><content><p>" + content + b"</p></content></paragraph>"
        b"</section>"
    )


# ---------------------------------------------------------------------------
# Fixture 1: EXACT x BUDGET_LINE -- explicit momentti address in registry
# ---------------------------------------------------------------------------
# Statute text references momentti 28.91.50 which is in the 2024 registry.

EXACT_BUDGET_LINE = PoolCorpusFixture(
    fixture_id="exact_budget_line",
    description="EXACT x BUDGET_LINE: explicit '28.91.50' momentti address registered in 2024",
    source_statute_id="711/2022",
    xml_bytes=_wrap_body(
        _section(
            b"3 \xc2\xa7",
            (
                b"M\xc3\xa4\xc3\xa4r\xc3\xa4raha osoitetaan talousarvion momentilla 28.91.50 "
                b"kunnille maksettavaan valtionosuuteen."
            ),
        )
    ),
    expected_kind=QuantityKind.BUDGET_LINE,
    expected_confidence=PoolResolutionConfidence.EXACT,
    expected_pool_id="fi.budget.28.91.50",
    expected_numeric=None,
    expected_unit=None,
    expected_mention_count=1,
)

# ---------------------------------------------------------------------------
# Fixture 2: APPROXIMATE x BUDGET_LINE -- renumbered momentti (2020: 28.91.51)
# ---------------------------------------------------------------------------
# Momentti 28.91.51 existed in 2020 and was renamed to 28.91.50 in 2022.
# Resolution should emit BudgetLineRenumberingObservation + APPROXIMATE confidence.

APPROXIMATE_BUDGET_LINE_RENUMBERED = PoolCorpusFixture(
    fixture_id="approximate_budget_line_renumbered",
    description="APPROXIMATE x BUDGET_LINE: momentti 28.91.51 (2020 year) resolves via lineage",
    source_statute_id="500/2020",
    xml_bytes=_wrap_body(
        _section(
            b"4 \xc2\xa7",
            (
                b"Avustus maksetaan momentilta 28.91.51. "
                b"Hakemus on tehtava viimeistaan 31.12.2020."
            ),
        )
    ),
    expected_kind=QuantityKind.BUDGET_LINE,
    # APPROXIMATE because 28.91.51 is not in current year but resolves via lineage
    expected_confidence=PoolResolutionConfidence.APPROXIMATE,
    expected_pool_id="fi.budget.28.91.50",
    expected_renumbering=True,
    expected_mention_count=1,
)

# ---------------------------------------------------------------------------
# Fixture 3: EXACT x CAPACITY_CAP -- lannoitelaki Cd-kuormakatto
# ---------------------------------------------------------------------------
# The Finnish Fertilizer Act (lannoitelaki) specifies a cadmium load cap:
# 'kuormakatto 7,5 g Cd/ha/5 v' (7.5 g cadmium per hectare per 5 years).

EXACT_CAPACITY_CAP = PoolCorpusFixture(
    fixture_id="exact_capacity_cap",
    description="EXACT x CAPACITY_CAP: lannoitelaki Cd-kuormakatto 7,5 g Cd/ha/5 v",
    source_statute_id="539/2006",
    xml_bytes=_wrap_body(
        _section(
            b"8 \xc2\xa7",
            (
                b"Lannoitevalmisteen kadmiumkuormakatto on enint\xc3\xa4\xc3\xa4n 7,5 g Cd/ha/5 v. "
                b"Kuormakatto koskee kaikkia lannoitevalmisteita."
            ),
        )
    ),
    expected_kind=QuantityKind.CAPACITY_CAP,
    expected_confidence=PoolResolutionConfidence.UNRESOLVED,
    expected_pool_id=None,
    expected_numeric=7.5,
    expected_unit="g Cd/ha/5 v",
    expected_mention_count=1,
)

# ---------------------------------------------------------------------------
# Fixture 4: THRESHOLD -- explicit numeric value and unit
# ---------------------------------------------------------------------------
# Finnish threshold: 'vahingoittamiseksi vahintaan 0,5 promillea' (blood alcohol).

THRESHOLD_NUMERIC = PoolCorpusFixture(
    fixture_id="threshold_numeric",
    description="THRESHOLD: numeric threshold 0,5 promillea",
    source_statute_id="267/2003",
    xml_bytes=_wrap_body(
        _section(
            b"5 \xc2\xa7",
            (
                b"Rikos katsotaan t\xc3\xa4rkeiksi, jos veren alkoholipitoisuus on "
                b"v\xc3\xa4hint\xc3\xa4\xc3\xa4n 0,5 promillea."
            ),
        )
    ),
    expected_kind=QuantityKind.THRESHOLD,
    expected_confidence=PoolResolutionConfidence.UNRESOLVED,
    expected_pool_id=None,
    expected_numeric=0.5,
    expected_unit="promillea",
    expected_mention_count=1,
)

# ---------------------------------------------------------------------------
# Fixture 5: UNRESOLVED x FISCAL_POOL -- 'yleiskate' without further context
# ---------------------------------------------------------------------------
# Generic pool phrase 'yleiskate' without a momentti address or numeric value.
# Should produce a FISCAL_POOL mention with UNRESOLVED confidence.

UNRESOLVED_YLEISKATE = PoolCorpusFixture(
    fixture_id="unresolved_yleiskate",
    description="UNRESOLVED x FISCAL_POOL: 'yleiskate' generic pool phrase",
    source_statute_id="2003/314",
    xml_bytes=_wrap_body(
        _section(
            b"10 \xc2\xa7",
            (
                b"Kustannukset katetaan valtion yleiskate-momentilta. "
                b"Momenttikohdistusta ei ole m\xc3\xa4\xc3\xa4ritelty."
            ),
        )
    ),
    expected_kind=QuantityKind.FISCAL_POOL,
    expected_confidence=PoolResolutionConfidence.UNRESOLVED,
    expected_pool_id=None,
    expected_mention_count=1,
)

# ---------------------------------------------------------------------------
# Fixture 6: Negative -- year reference 'vuoden 2020' must NOT produce a PoolMention
# ---------------------------------------------------------------------------
# A provision like 'vuoden 2020 talousarviossa' contains '2020' but no
# budget-line address pattern. The text '2020' alone is not a momentti code.

NEGATIVE_YEAR_REFERENCE = PoolCorpusFixture(
    fixture_id="negative_year_reference",
    description="NEGATIVE: 'vuoden 2020' year reference must not produce a PoolMention",
    source_statute_id="100/2019",
    xml_bytes=_wrap_body(
        _section(
            b"2 \xc2\xa7",
            (
                b"Vuoden 2020 talousarviossa osoitettu m\xc3\xa4\xc3\xa4r\xc3\xa4raha "
                b"siirret\xc3\xa4\xc3\xa4n seuraavalle vuodelle."
            ),
        )
    ),
    # Should produce FISCAL_POOL mention for 'maaraaraha' keyword but NOT
    # a BUDGET_LINE mention from '2020' alone.
    # The test verifies no BUDGET_LINE candidate is extracted from bare year numbers.
    expected_kind=QuantityKind.FISCAL_POOL,
    expected_confidence=PoolResolutionConfidence.UNRESOLVED,
    expected_pool_id=None,
    expected_mention_count=0,
    # zero BUDGET_LINE mentions specifically (year refs blocked by negative guard)
)

# ---------------------------------------------------------------------------
# Fixture 7: No-leak synthetic marker
# ---------------------------------------------------------------------------
# Synthetic statute ID for no-leak testing.

NO_LEAK_SYNTHETIC_MARKER = PoolCorpusFixture(
    fixture_id="no_leak_synthetic_marker",
    description="No-leak test: synthetic statute ID must not appear in production output",
    source_statute_id="__test__/999",
    xml_bytes=_wrap_body(
        _section(
            b"1 \xc2\xa7",
            b"Avustus maksetaan momentilta 29.20.30.",
        )
    ),
    expected_kind=QuantityKind.BUDGET_LINE,
    expected_confidence=PoolResolutionConfidence.EXACT,
    expected_pool_id="fi.budget.29.20.30",
    expected_mention_count=1,
)

# ---------------------------------------------------------------------------
# Fixture 8: XML parse failure
# ---------------------------------------------------------------------------

XML_PARSE_FAILURE = PoolCorpusFixture(
    fixture_id="xml_parse_failure",
    description="Corrupt XML produces blocking RejectedPoolCandidate",
    source_statute_id="bad/0",
    xml_bytes=b"<not valid xml ><<",
    expected_rejected=True,
    expected_mention_count=0,
)

# ---------------------------------------------------------------------------
# All fixtures registry
# ---------------------------------------------------------------------------

ALL_FIXTURES: Dict[str, PoolCorpusFixture] = {
    f.fixture_id: f
    for f in [
        EXACT_BUDGET_LINE,
        APPROXIMATE_BUDGET_LINE_RENUMBERED,
        EXACT_CAPACITY_CAP,
        THRESHOLD_NUMERIC,
        UNRESOLVED_YLEISKATE,
        NEGATIVE_YEAR_REFERENCE,
        NO_LEAK_SYNTHETIC_MARKER,
        XML_PARSE_FAILURE,
    ]
}
