"""Conformance corpus fixtures for ActorMention extraction.

Covers the conformance cells from ACTOR_MENTION_EXTRACTION.md:
  - EXACT (TLCOrganization-backed) x {DUTY, DISCRETION, PERMISSION, MENTION}
  - REGISTRY_RESOLVED x prose mention of canonical-registered agency
  - LIFECYCLE_RESOLVED x pre-merger phrase ('Evira') in pre-2019 provision
  - UNRESOLVED x generic phrase ('ministerio' without qualifier)

Each fixture is a ActorCorpusFixture with:
  - source_statute_id: str
  - xml_bytes: bytes  (minimal valid AKN XML)
  - expected_mention_assertions: list[dict]  (partial column-level assertions)
  - expected_ambiguous: bool  (whether AmbiguousActorMention should be emitted)
  - expected_lifecycle: bool  (whether LifecycleActorObservation should be emitted)
  - expected_rejected: bool   (whether RejectedActorCandidate should be emitted)
  - description: str

Assertions are partial: only the keys listed must match.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Fixture type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActorCorpusFixture:
    """One conformance corpus fixture for ActorMention extraction."""

    fixture_id: str
    description: str
    source_statute_id: str
    xml_bytes: bytes
    expected_mention_assertions: List[Dict[str, Any]] = field(default_factory=list)
    expected_ambiguous: bool = False
    expected_lifecycle: bool = False
    expected_rejected: bool = False


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


def _tlc_meta(org_eid: bytes, org_href: bytes, show_as: bytes) -> bytes:
    """Build a minimal AKN references/TLCOrganization meta block."""
    return (
        b"<references>"
        b'<TLCOrganization eId="' + org_eid + b'"'
        b' href="' + org_href + b'"'
        b' showAs="' + show_as + b'"/>'
        b"</references>"
    )


# ---------------------------------------------------------------------------
# Fixture 1: EXACT x MENTION
#
# TLCOrganization element for Ruokavirasto in meta.
# No prose modal context -- modal_kind=MENTION.
# ---------------------------------------------------------------------------

EXACT_TLC_MENTION = ActorCorpusFixture(
    fixture_id="exact_tlc_mention",
    description=(
        "TLCOrganization element for Ruokavirasto in AKN meta. "
        "resolution_confidence=EXACT, modal_kind=MENTION (no modal context)."
    ),
    source_statute_id="2019/561",
    xml_bytes=_wrap_body(
        _section(b"1 \xc2\xa7", b"Ruokavirasto valvoo t\xc3\xa4m\xc3\xa4n lain noudattamista."),
        meta_inner=_tlc_meta(
            b"organization_fi.agency.ruokavirasto",
            b"/akn/ontology/organization/fi.agency.ruokavirasto",
            b"Ruokavirasto",
        ),
    ),
    expected_mention_assertions=[
        {
            "actor_phrase": "Ruokavirasto",
            "resolution_confidence": "exact",
            "modal_kind": "mention",
        }
    ],
    expected_ambiguous=False,
    expected_lifecycle=False,
    expected_rejected=False,
)

# ---------------------------------------------------------------------------
# Fixture 2: EXACT x DUTY
#
# TLCOrganization for Traficom + prose duty context 'viraston on'.
# Note: TLC pass yields MENTION; prose pass yields DUTY for the same actor.
# We test that at least one DUTY mention is produced from the prose pass.
# ---------------------------------------------------------------------------

EXACT_TLC_DUTY = ActorCorpusFixture(
    fixture_id="exact_tlc_duty",
    description=(
        "TLCOrganization for Traficom in meta + duty construction in prose: "
        "'Liikenne- ja viestintaviraston on myonnettava lupa'. "
        "Prose pass yields modal_kind=DUTY, confidence=REGISTRY_RESOLVED."
    ),
    source_statute_id="2019/100",
    xml_bytes=_wrap_body(
        _section(
            b"3 \xc2\xa7",
            b"Liikenne- ja viestintaviraston on my\xc3\xb6nnett\xc3\xa4v\xc3\xa4 lupa.",
        ),
        meta_inner=_tlc_meta(
            b"organization_fi.agency.traficom",
            b"/akn/ontology/organization/fi.agency.traficom",
            b"Liikenne- ja viestintavirasto",
        ),
    ),
    expected_mention_assertions=[
        {
            "actor_canonical_id": "fi.agency.traficom",
            "modal_kind": "duty",
            "resolution_confidence": "registry_resolved",
        }
    ],
    expected_ambiguous=False,
    expected_lifecycle=False,
    expected_rejected=False,
)

# ---------------------------------------------------------------------------
# Fixture 3: EXACT x DISCRETION
#
# TLCOrganization for Valvira + prose discretion context 'Valvira voi'.
# ---------------------------------------------------------------------------

EXACT_TLC_DISCRETION = ActorCorpusFixture(
    fixture_id="exact_tlc_discretion",
    description=(
        "TLCOrganization for Valvira in meta + discretion construction: "
        "'Valvira voi periua luvan.' "
        "Prose pass yields modal_kind=DISCRETION, confidence=REGISTRY_RESOLVED."
    ),
    source_statute_id="2019/200",
    xml_bytes=_wrap_body(
        _section(
            b"5 \xc2\xa7",
            b"Valvira voi peri\xc3\xa4 luvan, jos luvanhaltija.",
        ),
        meta_inner=_tlc_meta(
            b"organization_fi.agency.valvira",
            b"/akn/ontology/organization/fi.agency.valvira",
            b"Valvira",
        ),
    ),
    expected_mention_assertions=[
        {
            "actor_phrase": "Valvira",
            "modal_kind": "discretion",
            "resolution_confidence": "registry_resolved",
        }
    ],
    expected_ambiguous=False,
    expected_lifecycle=False,
    expected_rejected=False,
)

# ---------------------------------------------------------------------------
# Fixture 4: EXACT x PERMISSION
#
# TLCOrganization for STUK + prose permission context 'STUK saa'.
# ---------------------------------------------------------------------------

EXACT_TLC_PERMISSION = ActorCorpusFixture(
    fixture_id="exact_tlc_permission",
    description=(
        "TLCOrganization for STUK in meta + permission construction: "
        "'STUK saa antaa maarayksia.' "
        "Prose pass yields modal_kind=PERMISSION, confidence=REGISTRY_RESOLVED."
    ),
    source_statute_id="2018/900",
    xml_bytes=_wrap_body(
        _section(
            b"7 \xc2\xa7",
            b"STUK saa antaa m\xc3\xa4\xc3\xa4r\xc3\xa4yksi\xc3\xa4 ydinturvallisuudesta.",
        ),
        meta_inner=_tlc_meta(
            b"organization_fi.agency.stuk",
            b"/akn/ontology/organization/fi.agency.stuk",
            b"STUK",
        ),
    ),
    expected_mention_assertions=[
        {
            "actor_phrase": "STUK",
            "modal_kind": "permission",
            "resolution_confidence": "registry_resolved",
        }
    ],
    expected_ambiguous=False,
    expected_lifecycle=False,
    expected_rejected=False,
)

# ---------------------------------------------------------------------------
# Fixture 5: REGISTRY_RESOLVED x prose mention
#
# No TLCOrganization element; Traficom appears in prose only.
# Prose pass yields REGISTRY_RESOLVED + MENTION (no modal context).
# ---------------------------------------------------------------------------

REGISTRY_RESOLVED_PROSE = ActorCorpusFixture(
    fixture_id="registry_resolved_prose",
    description=(
        "No TLCOrganization; Traficom appears in prose only. "
        "resolution_confidence=REGISTRY_RESOLVED, modal_kind=MENTION."
    ),
    source_statute_id="2020/50",
    xml_bytes=_wrap_body(
        _section(
            b"2 \xc2\xa7",
            b"Traficom julkaisee ohjeensa verkkosivuillaan.",
        )
    ),
    expected_mention_assertions=[
        {
            "actor_phrase": "Traficom",
            "actor_canonical_id": "fi.agency.traficom",
            "modal_kind": "mention",
            "resolution_confidence": "registry_resolved",
        }
    ],
    expected_ambiguous=False,
    expected_lifecycle=False,
    expected_rejected=False,
)

# ---------------------------------------------------------------------------
# Fixture 6: LIFECYCLE_RESOLVED x pre-2019 phrase 'Evira'
#
# The phrase 'Evira' is a predecessor of Ruokavirasto.
# Prose pass yields LIFECYCLE_RESOLVED + LifecycleActorObservation.
# ---------------------------------------------------------------------------

LIFECYCLE_RESOLVED_EVIRA = ActorCorpusFixture(
    fixture_id="lifecycle_resolved_evira",
    description=(
        "Phrase 'Evira' appears in prose; predecessor of Ruokavirasto (2019). "
        "resolution_confidence=LIFECYCLE_RESOLVED; LifecycleActorObservation emitted. "
        "Canonical ID resolves to fi.agency.ruokavirasto."
    ),
    source_statute_id="2017/100",
    xml_bytes=_wrap_body(
        _section(
            b"4 \xc2\xa7",
            b"Evira valvoo elintarviketurvallisuutta.",
        )
    ),
    expected_mention_assertions=[
        {
            "actor_phrase": "Evira",
            "actor_canonical_id": "fi.agency.ruokavirasto",
            "resolution_confidence": "lifecycle_resolved",
        }
    ],
    expected_ambiguous=False,
    expected_lifecycle=True,  # LifecycleActorObservation emitted
    expected_rejected=False,
)

# ---------------------------------------------------------------------------
# Fixture 7: LIFECYCLE_RESOLVED x pre-2019 phrase 'Trafi'
#
# 'Trafi' is a predecessor of Traficom (Liikenne- ja viestintavirasto).
# ---------------------------------------------------------------------------

LIFECYCLE_RESOLVED_TRAFI = ActorCorpusFixture(
    fixture_id="lifecycle_resolved_trafi",
    description=(
        "Phrase 'Trafi' appears in prose; predecessor of Traficom (2019). "
        "resolution_confidence=LIFECYCLE_RESOLVED; LifecycleActorObservation emitted. "
        "Canonical ID resolves to fi.agency.traficom."
    ),
    source_statute_id="2015/200",
    xml_bytes=_wrap_body(
        _section(
            b"2 \xc2\xa7",
            b"Trafi my\xc3\xb6nt\xc3\xa4\xc3\xa4 ajokortteja.",
        )
    ),
    expected_mention_assertions=[
        {
            "actor_phrase": "Trafi",
            "actor_canonical_id": "fi.agency.traficom",
            "resolution_confidence": "lifecycle_resolved",
        }
    ],
    expected_ambiguous=False,
    expected_lifecycle=True,
    expected_rejected=False,
)

# ---------------------------------------------------------------------------
# Fixture 8: UNRESOLVED x generic phrase 'ministerio' without qualifier
#
# A bare 'ministerio' matches multiple ministry registry entries -> AMBIGUOUS.
# (ministerio -> [fi.ministry.stm, fi.ministry.sm, ...])
# AmbiguousActorMention emitted; no canonical ID assigned.
# ---------------------------------------------------------------------------

UNRESOLVED_GENERIC_MINISTERIO = ActorCorpusFixture(
    fixture_id="unresolved_generic_ministerio",
    description=(
        "Bare 'ministerio' without qualifier appears in prose. "
        "Multiple registry entries match -> AmbiguousActorMention emitted. "
        "No canonical ID assigned."
    ),
    source_statute_id="2010/400",
    xml_bytes=_wrap_body(
        _section(
            b"1 \xc2\xa7",
            b"Ministerio antaa tarkemmat ohjeet.",
        )
    ),
    expected_mention_assertions=[],  # No ActorMention (ambiguous)
    expected_ambiguous=True,
    expected_lifecycle=False,
    expected_rejected=False,
)

# ---------------------------------------------------------------------------
# Fixture 9: No-leak -- synthetic statute markers
# ---------------------------------------------------------------------------

NO_LEAK_SYNTHETIC_MARKER = ActorCorpusFixture(
    fixture_id="no_leak_synthetic_marker",
    description=(
        "Synthetic statute ID -- must NOT appear in fi_actors.parquet "
        "on non-test runs. Ruokavirasto mention in prose."
    ),
    source_statute_id="__test__/9999/actors_source",
    xml_bytes=_wrap_body(
        _section(
            b"1 \xc2\xa7",
            b"Ruokavirasto valvoo.",
        )
    ),
    expected_mention_assertions=[
        {
            "actor_phrase": "Ruokavirasto",
            "actor_canonical_id": "fi.agency.ruokavirasto",
            "resolution_confidence": "registry_resolved",
        }
    ],
    expected_ambiguous=False,
    expected_lifecycle=False,
    expected_rejected=False,
)

# ---------------------------------------------------------------------------
# Fixture 10: XML parse failure (source pathology)
# ---------------------------------------------------------------------------

XML_PARSE_FAILURE = ActorCorpusFixture(
    fixture_id="xml_parse_failure",
    description=(
        "Corrupt XML bytes. Extractor emits a blocking RejectedActorCandidate "
        "and no mentions."
    ),
    source_statute_id="2000/1",
    xml_bytes=b"<not-valid-xml>",
    expected_mention_assertions=[],
    expected_ambiguous=False,
    expected_lifecycle=False,
    expected_rejected=True,
)

# ---------------------------------------------------------------------------
# All fixtures, indexed by fixture_id
# ---------------------------------------------------------------------------

ALL_FIXTURES: dict[str, ActorCorpusFixture] = {
    f.fixture_id: f
    for f in [
        EXACT_TLC_MENTION,
        EXACT_TLC_DUTY,
        EXACT_TLC_DISCRETION,
        EXACT_TLC_PERMISSION,
        REGISTRY_RESOLVED_PROSE,
        LIFECYCLE_RESOLVED_EVIRA,
        LIFECYCLE_RESOLVED_TRAFI,
        UNRESOLVED_GENERIC_MINISTERIO,
        NO_LEAK_SYNTHETIC_MARKER,
        XML_PARSE_FAILURE,
    ]
}
