"""Tests for the typed UK text-selector algebra (B0).

These pin the legacy ``original`` serialization byte-for-byte against the
sentinel strings ``nlp_parser.py`` constructs today, so a later production
migration (B1) that builds typed fragments and serializes them stays
behaviorally identical.  No parser code is exercised here yet.
"""

from __future__ import annotations

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
