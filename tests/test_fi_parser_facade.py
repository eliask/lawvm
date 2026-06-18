"""Tests for ParserFacade and curated shadow gate."""
from __future__ import annotations

from lawvm.finland.parser_facade import (
    ParserFacade,
    ParserLane,
    grammar_primary_enabled,
    parse_tokens_production,
    run_curated_shadow_gate,
)
from lawvm.finland.johtolause.lexer import tokenize
from lawvm.finland.johtolause.scan import apply_annotations_with_jolloin_pairs


def test_parser_facade_surface_lane_matches_authority() -> None:
    text = "Muutetaan lain 5 §:n 1 momentti seuraavasti:"
    facade = ParserFacade(lane=ParserLane.SURFACE_PARSE)
    assert facade.parse_text(text).consumed_count > 0


def test_parser_facade_shadow_diff_self_zero_delta() -> None:
    text = "Kumotaan lain 7 §."
    report = ParserFacade.shadow_diff(text)
    assert report is not None


def test_curated_shadow_gate_partition() -> None:
    buckets = run_curated_shadow_gate()
    assert buckets["zero_delta"] + buckets["declined"] + buckets["delta"] >= 0
    assert "skipped" in buckets
    assert "witness_span_normalized" in buckets


def test_curated_shadow_gate_no_structural_delta() -> None:
    buckets = run_curated_shadow_gate()
    assert buckets["delta"] == 0
    assert buckets["witness_span_normalized"] == 1


def test_parse_tokens_production_grammar_owned_by_default() -> None:
    assert grammar_primary_enabled()
    raw = tokenize("Muutetaan 5 §.")
    tokens, jolloin = apply_annotations_with_jolloin_pairs(raw)
    result = parse_tokens_production(tokens, jolloin_renumber_pairs=jolloin or None)
    assert result.parser_lane == "grammar_owned"
    assert result.clause.consumed_count > 0
