"""Tests for the canonical UK definition-predicate vocabulary."""

from __future__ import annotations

from lawvm.uk_legislation.definition_grammar import predicate_alternation


class TestPredicateAlternation:
    def test_with_shall_matches_historical_replay_constant(self) -> None:
        assert predicate_alternation(with_shall=True) == (
            "\nmeans"
            "\n|have\\s+the\\s+same\\s+meaning\\s+as"
            "\n|has\\s+the\\s+same\\s+meaning\\s+as"
            "\n|have\\s+the\\s+meaning"
            "\n|has\\s+the\\s+meaning"
            "\n|are\\s+to\\s+be\\s+construed"
            "\n|is\\s+to\\s+be\\s+construed"
            "\n|shall\\s+be\\s+construed"
            "\n|includes\n"
        )

    def test_without_shall_drops_only_shall_be_construed(self) -> None:
        out = predicate_alternation(with_shall=False)
        assert "shall\\s+be\\s+construed" not in out
        assert "includes" in out  # `includes` is kept in the without-shall variant
        assert out == (
            "\nmeans"
            "\n|have\\s+the\\s+same\\s+meaning\\s+as"
            "\n|has\\s+the\\s+same\\s+meaning\\s+as"
            "\n|have\\s+the\\s+meaning"
            "\n|has\\s+the\\s+meaning"
            "\n|are\\s+to\\s+be\\s+construed"
            "\n|is\\s+to\\s+be\\s+construed"
            "\n|includes\n"
        )

    def test_replay_constants_are_built_from_this_module(self) -> None:
        from lawvm.uk_legislation import replay_text_apply as r

        assert r._UK_DEFINITION_PREDICATE_PATTERN == predicate_alternation(with_shall=True)
        assert r._UK_DEFINITION_PREDICATE_PATTERN_WITHOUT_SHALL == predicate_alternation(
            with_shall=False
        )
