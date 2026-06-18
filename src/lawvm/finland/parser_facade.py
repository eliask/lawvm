"""Parser lane selector for Finland johtolause surface parsing."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any

from lawvm.finland.johtolause.grammar.diff import (
    ParserDeltaReport,
    compare_surface_models_structural,
    compare_surface_parsers,
    parse_text_with,
)
from lawvm.finland.johtolause.surface_model import SurfaceClause

ParserLaneId = str  # grammar_owned | legacy_reference_fallback | old_parser_forced


@dataclass(frozen=True, slots=True)
class ProductionParseResult:
    """Token-level parse outcome for the production johtolause path."""

    clause: SurfaceClause
    parser_lane: ParserLaneId
    grammar_decline_reason: str | None = None


def grammar_primary_enabled() -> bool:
    """True when the grammar parser is production primary (default ON)."""
    return os.environ.get("LAWVM_FI_NEW_PARSER", "1") not in ("0", "false", "off", "")


def parse_tokens_production(
    tokens: Any,
    *,
    jolloin_renumber_pairs: Any = None,
) -> ProductionParseResult:
    """Grammar-primary parse with legacy surface_parse fallback on OutOfScope."""
    from lawvm.finland.johtolause.grammar.parser import (
        OutOfScope as GrammarOutOfScope,
        parse as grammar_parse,
    )

    jolloin_arg = jolloin_renumber_pairs if jolloin_renumber_pairs else None
    if grammar_primary_enabled():
        try:
            clause = grammar_parse(tokens, jolloin_renumber_pairs=jolloin_arg)
            return ProductionParseResult(clause=clause, parser_lane="grammar_owned")
        except GrammarOutOfScope as decline:
            clause = _surface_parse(tokens, jolloin_renumber_pairs=jolloin_arg)
            return ProductionParseResult(
                clause=clause,
                parser_lane="legacy_reference_fallback",
                grammar_decline_reason=str(decline) or "OutOfScope",
            )
    clause = _surface_parse(tokens, jolloin_renumber_pairs=jolloin_arg)
    return ProductionParseResult(clause=clause, parser_lane="old_parser_forced")


class ParserLane(str, Enum):
    """Owned parser lane for johtolause surface recognition."""

    SURFACE_PARSE = "surface_parse"
    GRAMMAR_SHADOW = "grammar_shadow"


def _surface_parse(tokens, jolloin_renumber_pairs=None):
    from lawvm.finland.johtolause import surface_parse

    return surface_parse.parse(tokens, jolloin_renumber_pairs=jolloin_renumber_pairs)


def _grammar_parse(tokens, jolloin_renumber_pairs=None):
    from lawvm.finland.johtolause.grammar import parser as grammar_parser

    return grammar_parser.parse(tokens, jolloin_renumber_pairs=jolloin_renumber_pairs)


@dataclass(frozen=True, slots=True)
class ParserFacade:
    """Select surface_parse authority vs grammar shadow recognizer."""

    lane: ParserLane = ParserLane.SURFACE_PARSE

    def parse_text(self, text: str) -> SurfaceClause:
        """Parse one johtolause clause on the selected lane."""
        parser = _grammar_parse if self.lane is ParserLane.GRAMMAR_SHADOW else _surface_parse
        return parse_text_with(text, parser)

    @staticmethod
    def shadow_diff(text: str) -> ParserDeltaReport:
        """Diff grammar shadow against surface_parse authority."""
        return compare_surface_parsers(text, _surface_parse, _grammar_parse)


def run_curated_shadow_gate() -> dict[str, int]:
    """Classify curated cases: 0-delta, OutOfScope decline, or genuine delta.

    Witness-span-only disagreements (replay-neutral per
    ``compare_surface_models_structural``) land in ``witness_span_normalized``,
    not ``delta`` — matching the corpus census class G_witness_span_only.
    """
    from lawvm.finland.johtolause.curated_cases import CURATED_CASES
    from lawvm.finland.johtolause.grammar import parser as grammar_parser

    buckets = {
        "zero_delta": 0,
        "declined": 0,
        "witness_span_normalized": 0,
        "delta": 0,
        "skipped": 0,
    }
    for case in CURATED_CASES:
        if case.get("xfail"):
            buckets["skipped"] += 1
            continue
        text = str(case.get("text") or "")
        if not text:
            buckets["skipped"] += 1
            continue
        try:
            parse_text_with(text, _grammar_parse)
        except grammar_parser.OutOfScope:
            buckets["declined"] += 1
            continue
        except Exception:
            buckets["delta"] += 1
            continue
        report = ParserFacade.shadow_diff(text)
        if report.equal:
            buckets["zero_delta"] += 1
            continue
        old_model = parse_text_with(text, _surface_parse)
        new_model = parse_text_with(text, _grammar_parse)
        if compare_surface_models_structural(old_model, new_model).equal:
            buckets["witness_span_normalized"] += 1
        else:
            buckets["delta"] += 1
    return buckets


shadow_diff = ParserFacade.shadow_diff

__all__ = [
    "ParserFacade",
    "ParserLane",
    "ProductionParseResult",
    "grammar_primary_enabled",
    "parse_tokens_production",
    "run_curated_shadow_gate",
    "shadow_diff",
]
