"""Conformance corpus fixtures for ReferenceMention extraction.

Covers each cell of cite_kind × confidence per
REFERENCE_MENTION_EXTRACTION.md §Conformance corpus.

Each fixture is a CorpusFixture with:
  - source_statute_id: str
  - xml_bytes: bytes  (minimal valid AKN XML)
  - expected_mentions: list[dict]  (column-level assertions)
  - expected_rejected: list[dict]  (RejectedRefCandidate assertions)
  - description: str

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
class CorpusFixture:
    """One conformance corpus fixture for ReferenceMention extraction."""

    fixture_id: str
    description: str
    source_statute_id: str
    xml_bytes: bytes
    expected_mentions: List[Dict[str, Any]] = field(default_factory=list)
    expected_rejected: List[Dict[str, Any]] = field(default_factory=list)
    expected_diagnostics_rule_ids: List[str] = field(default_factory=list)


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
# Fixture: EXACT × CROSS_STATUTE
#
# Inline <ref> element pointing to statute 711/2022 §7.
# confidence=EXACT because the markup names the target unambiguously.
# ---------------------------------------------------------------------------

EXACT_CROSS_STATUTE = CorpusFixture(
    fixture_id="exact_cross_statute",
    description=(
        "Inline <ref href> element to a resolvable Finnish statute provision. "
        "cite_kind=CROSS_STATUTE, confidence=EXACT."
    ),
    source_statute_id="2003/314",
    xml_bytes=_wrap_body(
        _section(
            b"5 \xc2\xa7",
            b"Noudatetaan, mit\xc3\xa4 "
            b'<ref href="/akn/fi/act/statute-consolidated/2022/711#sec_7">'
            b"lannoitelaissa</ref>"
            b" s\xc3\xa4\xc3\xa4det\xc3\xa4\xc3\xa4n.",
        )
    ),
    expected_mentions=[
        {
            "source_statute_id": "2003/314",
            "target_statute_id": "2022/711",
            "cite_kind": "cross_statute",
            "cite_confidence": "exact",
            "edge_subtype": "CITES",
            "phrase_lemma": "ref_element",
        }
    ],
    expected_rejected=[],
    expected_diagnostics_rule_ids=[],
)

# ---------------------------------------------------------------------------
# Fixture: EXACT × INTERNAL
#
# Self-reference should emit a CrossRefDiagnostic (skipped) not a mention.
# The INTERNAL kind is emitted when source and target statute_ids match.
# We test by using a target that IS the source statute — expects skip diagnostic.
# ---------------------------------------------------------------------------

EXACT_INTERNAL_SELF_REF_SKIPPED = CorpusFixture(
    fixture_id="exact_internal_self_ref_skipped",
    description=(
        "Inline <ref> element pointing back to the same statute. "
        "Self-references are skipped by cross_refs.py with a diagnostic. "
        "No ReferenceMention emitted; one CrossRefDiagnostic emitted."
    ),
    source_statute_id="2003/314",
    xml_bytes=_wrap_body(
        _section(
            b"5 \xc2\xa7",
            b"T\xc3\xa4m\xc3\xa4 laki koskee my\xc3\xb6s "
            b'<ref href="/akn/fi/act/statute/2003/314#sec_3">'
            b"3 \xc2\xa7:ss\xc3\xa4</ref>"
            b" tarkoitettuja.",
        )
    ),
    expected_mentions=[],  # self-ref = skip, no mention
    expected_rejected=[],
    expected_diagnostics_rule_ids=["fi_cross_ref_self_reference_skipped"],
)

# ---------------------------------------------------------------------------
# Fixture: EXACT × EU
#
# EU regulation reference extracted via text pattern.
# Uses "(EY) N:o 999/2001" format which the existing EU extractor handles.
# The EU extractor pattern P1 handles "N:o NUMBER/YEAR" (small-number-first).
# cite_kind=EU, confidence=EXACT.
# ---------------------------------------------------------------------------

EXACT_EU = CorpusFixture(
    fixture_id="exact_eu",
    description=(
        "Finnish statute referencing EU regulation 999/2001 via "
        "text pattern '(EY) N:o 999/2001'. cite_kind=EU, confidence=EXACT. "
        "Uses the NUMBER/YEAR format that the existing EU extractor P1 handles."
    ),
    source_statute_id="2018/1050",
    xml_bytes=_wrap_body(
        _section(
            b"3 \xc2\xa7",
            b"Neuvoston asetus (EY) N:o 999/2001.",
        )
    ),
    expected_mentions=[
        {
            "source_statute_id": "2018/1050",
            "target_statute_id": "eu/reg/2001/999",
            "cite_kind": "eu",
            "cite_confidence": "exact",
            "phrase_lemma": "eu_text_pattern",
        }
    ],
    expected_rejected=[],
    expected_diagnostics_rule_ids=[],
)

# ---------------------------------------------------------------------------
# Fixture: EXACT × NON_STATUTORY_INSTRUMENT (ISSUED_UNDER)
#
# A statute issued under authority of another statute.
# Metadata finlex:issuedUnderActs → cite_kind=NON_STATUTORY_INSTRUMENT,
# edge_subtype=ISSUED_UNDER, confidence=EXACT.
# ---------------------------------------------------------------------------

EXACT_ISSUED_UNDER = CorpusFixture(
    fixture_id="exact_issued_under",
    description=(
        "Statute 2023/964 issued under authority of statute 2006/1013 "
        "via finlex:issuedUnderActs metadata. "
        "cite_kind=NON_STATUTORY_INSTRUMENT, confidence=EXACT."
    ),
    source_statute_id="2023/964",
    xml_bytes=_wrap_body(
        b"<section><num>1 \xc2\xa7</num></section>",
        meta_inner=(
            b"<finlex:issuedUnderActs>"
            b'<finlex:ref href="/akn/fi/act/statute-consolidated/2006/1013"/>'
            b"</finlex:issuedUnderActs>"
        ),
    ),
    expected_mentions=[
        {
            "source_statute_id": "2023/964",
            "target_statute_id": "2006/1013",
            "cite_kind": "non_statutory_instrument",
            "cite_confidence": "exact",
            "edge_subtype": "ISSUED_UNDER",
            "phrase_lemma": "ISSUED_UNDER",
        }
    ],
    expected_rejected=[],
    expected_diagnostics_rule_ids=[],
)

# ---------------------------------------------------------------------------
# Fixture: EXACT × REPEALS
#
# A statute repeals another statute via finlex:repeals metadata.
# cite_kind=CROSS_STATUTE, edge_subtype=REPEALS, confidence=EXACT.
# ---------------------------------------------------------------------------

EXACT_REPEALS = CorpusFixture(
    fixture_id="exact_repeals",
    description=(
        "Statute 2022/711 repeals statute 2011/539 via finlex:repeals metadata. "
        "cite_kind=CROSS_STATUTE, confidence=EXACT."
    ),
    source_statute_id="2022/711",
    xml_bytes=_wrap_body(
        b"<section><num>1 \xc2\xa7</num></section>",
        meta_inner=(
            b"<finlex:repeals>"
            b'<finlex:ref href="/akn/fi/act/statute-consolidated/2011/539"/>'
            b"</finlex:repeals>"
        ),
    ),
    expected_mentions=[
        {
            "source_statute_id": "2022/711",
            "target_statute_id": "2011/539",
            "cite_kind": "cross_statute",
            "cite_confidence": "exact",
            "edge_subtype": "REPEALS",
        }
    ],
    expected_rejected=[],
    expected_diagnostics_rule_ids=[],
)

# ---------------------------------------------------------------------------
# Fixture: XML parse failure (source pathology)
#
# Invalid XML → CrossRefDiagnostic blocking, no mentions.
# ---------------------------------------------------------------------------

XML_PARSE_FAILURE = CorpusFixture(
    fixture_id="xml_parse_failure",
    description=(
        "Corrupt XML bytes. Extractor emits a blocking CrossRefDiagnostic "
        "and no mentions."
    ),
    source_statute_id="2000/1",
    xml_bytes=b"<not-valid-xml>",
    expected_mentions=[],
    expected_rejected=[],
    expected_diagnostics_rule_ids=["fi_cross_ref_xml_parse_failed"],
)

# ---------------------------------------------------------------------------
# Fixture: No-leak — synthetic statute_id markers must not appear in output
# ---------------------------------------------------------------------------

NO_LEAK_SYNTHETIC_MARKER = CorpusFixture(
    fixture_id="no_leak_synthetic_marker",
    description=(
        "XML with a citation to a test-synthetic statute ID "
        "(__test__/9999/synthetic). Must NOT appear in fi_refs.parquet "
        "on non-test runs."
    ),
    source_statute_id="__test__/9999/synthetic_source",
    xml_bytes=_wrap_body(
        _section(
            b"1 \xc2\xa7",
            b'<ref href="/akn/fi/act/statute/9999/1">'
            b"testilaki</ref>",
        )
    ),
    expected_mentions=[
        {
            "source_statute_id": "__test__/9999/synthetic_source",
            "target_statute_id": "9999/1",
            "cite_kind": "cross_statute",
            "cite_confidence": "exact",
        }
    ],
    expected_rejected=[],
    expected_diagnostics_rule_ids=[],
)

# ---------------------------------------------------------------------------
# All fixtures, indexed by fixture_id
# ---------------------------------------------------------------------------

ALL_FIXTURES: dict[str, CorpusFixture] = {
    f.fixture_id: f
    for f in [
        EXACT_CROSS_STATUTE,
        EXACT_INTERNAL_SELF_REF_SKIPPED,
        EXACT_EU,
        EXACT_ISSUED_UNDER,
        EXACT_REPEALS,
        XML_PARSE_FAILURE,
        NO_LEAK_SYNTHETIC_MARKER,
    ]
}
