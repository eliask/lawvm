"""Tests for the canonical UK definition-predicate vocabulary."""

from __future__ import annotations

from lawvm.uk_legislation.definition_grammar import (
    predicate_alternation,
    predicate_substring_regex,
)
from lawvm.uk_legislation.source_definition_fragments import (
    _looks_like_appropriate_place_definition_entry_insert_text,
)


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


class TestPredicateSubstringRegex:
    def test_flat_alternation_relaxes_spaces_and_includes_plurals(self) -> None:
        assert predicate_substring_regex(with_includes=True) == (
            r"means|have\s+the\s+same\s+meaning|has\s+the\s+same\s+meaning"
            r"|have\s+the\s+meaning|has\s+the\s+meaning|are\s+to\s+be\s+construed"
            r"|is\s+to\s+be\s+construed|shall\s+be\s+construed|includes"
        )

    def test_default_omits_includes(self) -> None:
        assert "includes" not in predicate_substring_regex().split("|")

    def test_is_strict_superset_of_the_legacy_inline_alternation(self) -> None:
        # The hand-written alternation this builder replaced; the new vocabulary
        # must keep every one of its members (no narrowing) and add the plurals.
        legacy = {
            "means",
            r"has\s+the\s+same\s+meaning",
            r"has\s+the\s+meaning",
            r"is\s+to\s+be\s+construed",
            r"shall\s+be\s+construed",
            "includes",
        }
        members = set(predicate_substring_regex(with_includes=True).split("|"))
        assert legacy <= members
        assert {r"have\s+the\s+same\s+meaning", r"have\s+the\s+meaning", r"are\s+to\s+be\s+construed"} <= members


class TestAppropriatePlaceDefinitionInsertRecognition:
    SINGULAR = 'insert at the appropriate place "widget" means a small device;'
    PLURAL = 'insert at the appropriate place "widget" and "gadget" have the meaning given in section 5;'

    def test_singular_predicate_still_recognized(self) -> None:
        assert _looks_like_appropriate_place_definition_entry_insert_text(self.SINGULAR)

    def test_plural_predicate_now_recognized(self) -> None:
        # Previously missed: the legacy inline alternation lacked "have the meaning".
        assert _looks_like_appropriate_place_definition_entry_insert_text(self.PLURAL)

    def test_requires_appropriate_place_and_insert_context(self) -> None:
        # Predicate alone, without the appropriate-place + insert framing, is not a match.
        assert not _looks_like_appropriate_place_definition_entry_insert_text(
            '"widget" means a small device;'
        )
