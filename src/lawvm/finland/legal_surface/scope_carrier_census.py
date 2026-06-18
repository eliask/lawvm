"""Differential census for johtolause scope-carrier phrases.

Wires the family-agnostic :mod:`family_census` engine for scope-block cues
(chapter/part carriers in amendment johtolause text). Pure measure-only — off
the replay/apply path.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from lawvm.finland.legal_surface.family_census import (
    CensusUnit,
    FamilyCensusResult,
    format_family_census_report,
    run_family_census,
)

SCOPE_CARRIER_FAMILY = "scope_carrier"

_SCOPE_CUE_RE = re.compile(
    r"(?i)(?:"
    r"luvun\s+\d+|"
    r"\d+\s*luvun|"
    r"osan\s+\d+|"
    r"\d+\s*osan|"
    r"mainitun\s+luvun|"
    r"tämän\s+lain\s+\d+\s*luvun"
    r")"
)

_GRAMMAR_SCOPE_KEY_RE = re.compile(
    r"(?i)(?:chapter|part):(\S+)"
)


def _scope_carrier_segment_selector(statute_id: str, body_text: str) -> Iterator[CensusUnit]:
    del statute_id
    for line in body_text.splitlines():
        text = line.strip()
        match = _SCOPE_CUE_RE.search(text)
        if not text or match is None:
            continue
        yield CensusUnit(
            text=text,
            parser_lane="scope_carrier_grammar",
            declared_marker=match.group(0),
            parser_facade_lane="grammar_shadow",
        )


def _scope_keys_from_text(text: str, *, lane: str) -> set[str]:
    if lane == "grammar_shadow":
        from lawvm.finland.parser_facade import ParserFacade, ParserLane

        try:
            clause = ParserFacade(lane=ParserLane.GRAMMAR_SHADOW).parse_text(text)
        except Exception:
            return set()
    else:
        from lawvm.finland.johtolause import surface_parse as legacy_surface_parse
        from lawvm.finland.johtolause.lexer import tokenize

        try:
            tokens = tokenize(text)
            clause = legacy_surface_parse.parse(tokens)
        except Exception:
            return set()
    keys: set[str] = set()
    for key_match in _GRAMMAR_SCOPE_KEY_RE.finditer(repr(clause)):
        keys.add(f"scope:{key_match.group(1).lower()}")
    return keys


def _scope_carrier_projection_keys(unit: CensusUnit, statute_id: str) -> set[str]:
    del statute_id
    return _scope_keys_from_text(unit.text, lane="grammar_shadow")


def _scope_carrier_oracle_keys(unit: CensusUnit, oracle_ctx: object) -> set[str]:
    del oracle_ctx
    return _scope_keys_from_text(unit.text, lane="surface_parse")


def _scope_carrier_miss_shape(missing_keys: set[str], declared_marker: str) -> str:
    del declared_marker
    if not missing_keys:
        return "none"
    if all(key.startswith("scope:part:") for key in missing_keys):
        return "missing_part_scope"
    if all(key.startswith("scope:chapter:") for key in missing_keys):
        return "missing_chapter_scope"
    return "mixed_scope_miss"


def run_scope_carrier_census(
    *,
    limit: int = 0,
    min_year: int = 0,
    check_totality: bool | None = None,
    max_examples: int = 8,
) -> FamilyCensusResult:
    """Run scope-carrier differential census over the corpus."""
    return run_family_census(
        family=SCOPE_CARRIER_FAMILY,
        segment_selector=_scope_carrier_segment_selector,
        projection_fn=_scope_carrier_projection_keys,
        oracle_fn=_scope_carrier_oracle_keys,
        miss_shape_fn=_scope_carrier_miss_shape,
        limit=limit,
        min_year=min_year,
        check_totality=check_totality,
        max_examples=max_examples,
    )


def format_scope_carrier_census_report(result: FamilyCensusResult) -> str:
    return format_family_census_report(result, title="Finland scope-carrier grammar census")


__all__ = [
    "SCOPE_CARRIER_FAMILY",
    "format_scope_carrier_census_report",
    "run_scope_carrier_census",
]
