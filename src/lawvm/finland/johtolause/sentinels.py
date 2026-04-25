"""sentinels — Single source of truth for sentinel token taxonomy.

All sentinel categories, their annotation kinds, and token representations
are defined here.  Other modules (scan.py, views.py, annotations.py,
surface_parse.py) should import from this module rather than maintaining
their own copies.

Taxonomy:
    sentinel_cat  — the cat field on Token objects (e.g. "CITATION_SPAN")
    annotation_kind — the semantic kind string (e.g. "citation_span")
    token — the synthetic Token produced for this sentinel
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SentinelSpec:
    cat: str
    kind: str
    text: str
    lemma: str


_SENTINEL_SPECS: tuple[SentinelSpec, ...] = (
    SentinelSpec(cat="CITATION_SPAN", kind="citation_span", text="[CITE]", lemma="citation"),
    SentinelSpec(cat="STATUTE_NAME_SPAN", kind="statute_name_span", text="[STATUTE_NAME]", lemma="statute_name"),
    SentinelSpec(cat="PROVENANCE_SPAN", kind="provenance_span", text="[PROV]", lemma="provenance"),
    SentinelSpec(cat="REINST_SPAN", kind="reinstatement", text="[REINST]", lemma="reinstatement"),
    SentinelSpec(cat="END_SENTINEL_SPAN", kind="end_sentinel", text="[END]", lemma="seuraavasti"),
    SentinelSpec(cat="JOLLOIN_MOVE", kind="jolloin", text="jolloin-move", lemma="jolloin"),
    SentinelSpec(cat="VALIOTSIKKO", kind="heading_placement", text="väliotsikko", lemma="otsikko"),
)

_BY_CAT: dict[str, SentinelSpec] = {s.cat: s for s in _SENTINEL_SPECS}
_BY_KIND: dict[str, SentinelSpec] = {s.kind: s for s in _SENTINEL_SPECS}

ALL_SENTINEL_CATS: frozenset[str] = frozenset(s.cat for s in _SENTINEL_SPECS)
ALL_SENTINEL_KINDS: frozenset[str] = frozenset(s.kind for s in _SENTINEL_SPECS)

SKIP_CATS: frozenset[str] = frozenset(
    cat for cat, spec in _BY_CAT.items() if cat not in ("JOLLOIN_MOVE", "VALIOTSIKKO")
)


def spec_by_cat(cat: str) -> SentinelSpec | None:
    return _BY_CAT.get(cat)


def spec_by_kind(kind: str) -> SentinelSpec | None:
    return _BY_KIND.get(kind)


def cat_to_kind(cat: str) -> str:
    spec = _BY_CAT.get(cat)
    return spec.kind if spec else cat
