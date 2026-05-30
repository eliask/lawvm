"""`words added` is a synonym for `words inserted` (OPC Part 6.3.8: "say insert
rather than add"). The effect-feed verb must lower to a word-level insert, not be
dropped as an unsupported action."""
from __future__ import annotations

from lawvm.uk_legislation.effects import STRUCTURAL_EFFECT_TYPES
from lawvm.uk_legislation.lowering_actions import (
    _is_uk_word_level_effect_type,
    _uk_effect_type_action,
)


class TestWordsAddedLowering:
    def test_words_added_lowers_to_insert(self) -> None:
        assert _uk_effect_type_action("words added") == "insert"
        assert _uk_effect_type_action("word added") == "insert"

    def test_words_added_is_word_level(self) -> None:
        assert _is_uk_word_level_effect_type("words added") is True
        assert _is_uk_word_level_effect_type("word added") is True

    def test_words_added_is_structural(self) -> None:
        assert "words added" in STRUCTURAL_EFFECT_TYPES
        assert "word added" in STRUCTURAL_EFFECT_TYPES

    def test_matches_existing_words_inserted_behaviour(self) -> None:
        # the new synonym must behave exactly like the canonical "words inserted"
        assert _uk_effect_type_action("words added") == _uk_effect_type_action("words inserted")
        assert _is_uk_word_level_effect_type("words added") == _is_uk_word_level_effect_type("words inserted")

    def test_unrelated_verb_unaffected(self) -> None:
        assert _uk_effect_type_action("excluded") is None
        assert _is_uk_word_level_effect_type("added") is False  # whole-provision add, not word-level
