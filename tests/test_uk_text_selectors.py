"""Tests for the typed UK text-selector algebra (B0).

These pin the legacy ``original`` serialization byte-for-byte against the
sentinel strings ``nlp_parser.py`` constructs today, so a later production
migration (B1) that builds typed fragments and serializes them stays
behaviorally identical.  No parser code is exercised here yet.
"""

from __future__ import annotations

import pytest

from lawvm.uk_legislation.text_selectors import (
    AfterAnchorToEndSelector,
    AfterChildSelector,
    BeforeChildSelector,
    BeginningSelector,
    DefinitionAnchorSelector,
    EndSelector,
    LiteralSelector,
    OpeningWordsSelector,
    RangeFromToSelector,
    RangeToEndSelector,
    UKTextRewriteFragment,
    fragment_to_legacy_dict,
    selector_to_legacy_original,
)


class TestSelectorToLegacyOriginal:
    def test_literal(self) -> None:
        assert selector_to_legacy_original(LiteralSelector("the words")) == "the words"

    def test_range_to_end(self) -> None:
        assert selector_to_legacy_original(RangeToEndSelector("foo")) == "TEXT_FROM_foo_TO_END"

    def test_range_from_to(self) -> None:
        assert selector_to_legacy_original(RangeFromToSelector("a", "b")) == "TEXT_FROM_a_TO_b"

    def test_range_from_beginning_to(self) -> None:
        # `from the beginning to End` has an empty start: TEXT_FROM__TO_<end>.
        assert selector_to_legacy_original(RangeFromToSelector("", "b")) == "TEXT_FROM__TO_b"

    def test_after_anchor_to_end(self) -> None:
        assert selector_to_legacy_original(AfterAnchorToEndSelector("bar")) == "TEXT_AFTER_bar_TO_END"

    def test_opening_words(self) -> None:
        assert selector_to_legacy_original(OpeningWordsSelector()) == "TEXT_OPENING_WORDS"

    def test_beginning(self) -> None:
        assert selector_to_legacy_original(BeginningSelector()) == "TEXT_BEGINNING"

    def test_end(self) -> None:
        assert selector_to_legacy_original(EndSelector()) == "TEXT_END"

    def test_before_child(self) -> None:
        sel = BeforeChildSelector("paragraph", "(a)")
        assert selector_to_legacy_original(sel) == "TEXT_BEFORE_CHILD_paragraph_(a)"

    def test_after_child(self) -> None:
        sel = AfterChildSelector("subsection", "(3)")
        assert selector_to_legacy_original(sel) == "TEXT_AFTER_CHILD_subsection_(3)"

    def test_definition_anchor_before(self) -> None:
        sel = DefinitionAnchorSelector("the relevant period", "before")
        assert selector_to_legacy_original(sel) == "TEXT_BEFORE_DEFINITION_the relevant period"

    def test_definition_anchor_after(self) -> None:
        sel = DefinitionAnchorSelector("X", "after")
        assert selector_to_legacy_original(sel) == "TEXT_AFTER_DEFINITION_X"


class TestFragmentToLegacyDict:
    def test_minimal_fragment(self) -> None:
        frag = UKTextRewriteFragment(
            selector=LiteralSelector("X"),
            replacement="Y",
            rule_id="uk_effect_example",
        )
        assert fragment_to_legacy_dict(frag) == {
            "original": "X",
            "replacement": "Y",
            "rule_id": "uk_effect_example",
        }

    def test_omits_empty_optional_fields(self) -> None:
        frag = UKTextRewriteFragment(
            selector=RangeToEndSelector("foo"),
            replacement="bar",
            rule_id="uk_effect_range",
        )
        # No occurrence/source_child/target_suffix keys when they are empty.
        assert set(fragment_to_legacy_dict(frag)) == {"original", "replacement", "rule_id"}

    def test_includes_set_optional_fields(self) -> None:
        frag = UKTextRewriteFragment(
            selector=LiteralSelector("X"),
            replacement="Y",
            rule_id="uk_effect_ordinal",
            occurrence="2",
            source_child_kind="paragraph",
            source_child_label="(a)",
        )
        out = fragment_to_legacy_dict(frag)
        assert out["occurrence"] == "2"
        assert out["source_child_kind"] == "paragraph"
        assert out["source_child_label"] == "(a)"
        assert "end_occurrence" not in out

    def test_all_occurrences_sentinel(self) -> None:
        frag = UKTextRewriteFragment(
            selector=LiteralSelector("X"),
            replacement="Y",
            rule_id="uk_effect_all",
            occurrence="-1",
        )
        assert fragment_to_legacy_dict(frag)["occurrence"] == "-1"


class TestParserProductionParity:
    """Byte-exact parity gate for the families migrated to typed fragments (B1).

    Each case asserts the full ``parse_fragment_substitution`` output for an
    input that triggers a migrated production.  The expected dicts were captured
    from the parser *before* the migration; a typed-fragment rewrite that
    serializes through ``fragment_to_legacy_dict`` must reproduce them exactly.
    """

    @pytest.mark.parametrize(
        "text,expected",
        [
            (
                'from "the date specified" to the end, substitute "the appointed day"',
                [{
                    "original": "TEXT_FROM_the date specified_TO_END",
                    "replacement": "the appointed day",
                    "rule_id": "uk_effect_anchor_to_end_substitution_text_patch",
                }],
            ),
            (
                'for "old text" to the end, substitute— the new block text here',
                [{
                    "original": "TEXT_FROM_old text_TO_END",
                    "replacement": "the new block text here",
                    "rule_id": "uk_effect_quoted_anchor_to_end_block_substitution_text_patch",
                }],
            ),
            (
                'for the words "old words" to the end, substitute "new words"',
                [{
                    "original": "TEXT_FROM_old words_TO_END",
                    "replacement": "new words",
                    "rule_id": "uk_effect_quoted_words_anchor_to_end_substitution_text_patch",
                }],
            ),
            (
                'for words "phrase" to the end, substitute the replacement block',
                [{
                    "original": "TEXT_FROM_phrase_TO_END",
                    "replacement": "the replacement block",
                    "rule_id": "uk_effect_anchor_to_end_block_substitution_text_patch",
                }],
            ),
            (
                'for the words from "start phrase" to the end, substitute " — new opening block',
                [{
                    "original": "TEXT_FROM_start phrase_TO_END",
                    "replacement": "new opening block",
                    "rule_id": "uk_effect_range_to_end_open_quote_block_substitution_text_patch",
                }],
            ),
            (
                'for words from "term" where it second occurs to the end, substitute the block text',
                [{
                    "original": "TEXT_FROM_term_TO_END",
                    "replacement": "the block text",
                    "occurrence": "2",
                    "rule_id": "uk_effect_range_to_end_ordinal_block_substitution_text_patch",
                }],
            ),
            (
                'for the words after "anchor word" substitute "inserted text"',
                [{
                    "original": "TEXT_AFTER_anchor word_TO_END",
                    "replacement": "inserted text",
                    "rule_id": "uk_effect_after_anchor_to_end_substitution_text_patch",
                }],
            ),
            (
                'for the opening words substitute "New opening words"',
                [{
                    "original": "TEXT_OPENING_WORDS",
                    "replacement": "New opening words",
                    "rule_id": "uk_effect_opening_words_substitution_text_patch",
                }],
            ),
            (
                'for words before paragraph (a), substitute "preamble text"',
                [{
                    "original": "TEXT_BEFORE_CHILD_paragraph_a",
                    "replacement": "preamble text",
                    "rule_id": "uk_effect_before_child_text_substitution_patch",
                }],
            ),
            # from-beginning family (RangeFromToSelector with empty start)
            (
                'for the words from the beginning to "the cutoff" is substituted "new start"',
                [{
                    "original": "TEXT_FROM__TO_the cutoff",
                    "replacement": "new start",
                    "rule_id": "uk_effect_from_beginning_passive_substitution_text_patch",
                }],
            ),
            (
                'for words from the beginning to "the cutoff" there shall be substituted "new start"',
                [{
                    "original": "TEXT_FROM__TO_the cutoff",
                    "replacement": "new start",
                    "rule_id": "uk_effect_from_beginning_passive_substitution_text_patch",
                }],
            ),
            (
                'omit the words from the beginning to "the cutoff"',
                [{
                    "original": "TEXT_FROM__TO_the cutoff",
                    "replacement": "",
                    "rule_id": "uk_effect_from_beginning_omission_text_patch",
                }],
            ),
            (
                'for words from the beginning to "the cutoff" substitute— the new block text',
                [{
                    "original": "TEXT_FROM__TO_the cutoff",
                    "replacement": "the new block text",
                    "rule_id": "uk_effect_from_beginning_block_substitution_text_patch",
                }],
            ),
            # after-child insertion family (AfterChildSelector)
            (
                'after paragraph (a), insert "the new text"',
                [{
                    "original": "TEXT_AFTER_CHILD_paragraph_a",
                    "replacement": "the new text",
                    "rule_id": "uk_effect_after_child_text_insertion_patch",
                }],
            ),
            (
                'after paragraph (b), insert— and the remaining words;',
                [{
                    "original": "TEXT_AFTER_CHILD_paragraph_b",
                    "replacement": "and the remaining words",
                    "rule_id": "uk_effect_after_child_text_insertion_patch",
                }],
            ),
            (
                'insert "a new clause" after subsection (3)',
                [{
                    "original": "TEXT_AFTER_CHILD_subsection_3",
                    "replacement": "a new clause",
                    "rule_id": "uk_effect_after_child_text_insertion_patch",
                }],
            ),
            # definition-anchor insertion family (DefinitionAnchorSelector)
            (
                'after the definition of "widget", insert "and gadget"',
                [{
                    "original": "TEXT_AFTER_DEFINITION_widget",
                    "replacement": '"and gadget"',
                    "rule_id": "uk_effect_after_definition_text_insertion_patch",
                }],
            ),
            (
                'after the definitions of "a" and "b" there is inserted "new clause"',
                [{
                    "original": "TEXT_AFTER_DEFINITION_b",
                    "replacement": '"new clause"',
                    "rule_id": "uk_effect_after_definitions_text_insertion_patch",
                }],
            ),
            (
                'before the definition of "zebra", insert "and yak"',
                [{
                    "original": "TEXT_BEFORE_DEFINITION_zebra",
                    "replacement": '"and yak"',
                    "rule_id": "uk_effect_before_definition_text_insertion_patch",
                }],
            ),
            (
                'before the entry for "alpha", insert "beta" means a thing;',
                [{
                    "original": "TEXT_BEFORE_DEFINITION_alpha",
                    "replacement": '"beta" means a thing;',
                    "rule_id": "uk_effect_before_definition_entry_text_insertion_patch",
                }],
            ),
        ],
    )
    def test_migrated_family_parity(self, text: str, expected: list) -> None:
        from lawvm.uk_legislation.nlp_parser import parse_fragment_substitution

        assert parse_fragment_substitution(text) == expected
