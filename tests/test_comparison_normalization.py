from __future__ import annotations

import re

import pytest

from lawvm.core.comparison_normalization import (
    INLINE_TEXT_COMPARISON_RULES,
    ComparisonNormalizationRule,
    normalize_comparison_text,
    normalize_inline_comparison_text,
    normalized_inline_contains,
    normalized_inline_occurrence_count,
    project_ir_comparison_text,
    validate_comparison_normalization_rule,
    validate_comparison_normalization_rules,
)
from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind


TYPOGRAPHY_RULE = ComparisonNormalizationRule(
    name="quote_typography",
    rule_class="presentation_cleanup",
    kind="translation",
    description="Normalize curly and straight quotation marks for comparison.",
    translation=str.maketrans({"\u201c": '"', "\u201d": '"'}),
)


def test_normalize_comparison_text_reports_fired_rules() -> None:
    result = normalize_comparison_text("\u201cquoted\u201d", (TYPOGRAPHY_RULE,))

    assert result.text == '"quoted"'
    assert result.fired_rules == ("quote_typography",)


def test_project_ir_comparison_text_preserves_unchanged_identity() -> None:
    node = IRNode(kind=IRNodeKind.SECTION, label="1", text="plain")

    assert project_ir_comparison_text(node, (TYPOGRAPHY_RULE,)) is node


def test_project_ir_comparison_text_rebuilds_changed_text() -> None:
    node = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="\u201cquoted\u201d"),),
    )

    projected = project_ir_comparison_text(node, (TYPOGRAPHY_RULE,))

    assert projected is not node
    assert projected.children[0].text == '"quoted"'


def test_normalize_comparison_text_supports_placeholder_equivalence() -> None:
    rule = ComparisonNormalizationRule(
        name="bare_dash_placeholder",
        rule_class="placeholder_equivalence",
        kind="placeholder",
        description="Treat a bare dash placeholder as empty for comparison.",
        pattern=re.compile(r"^-$"),
        replacement="",
    )

    result = normalize_comparison_text(" - ", (rule,))

    assert result.text == ""
    assert result.fired_rules == ("bare_dash_placeholder",)


def test_normalize_comparison_text_supports_named_callable_rule() -> None:
    rule = ComparisonNormalizationRule(
        name="callable_trim",
        rule_class="presentation_cleanup",
        kind="callable",
        description="Use an explicit named callable for comparison projection.",
        transform=lambda text: text.strip(),
    )

    result = normalize_comparison_text(" text ", (rule,))

    assert result.text == "text"
    assert result.fired_rules == ("callable_trim",)


def test_normalize_comparison_text_honors_required_substring_prefilter() -> None:
    called = False

    def transform(text: str) -> str:
        nonlocal called
        called = True
        return text.upper()

    rule = ComparisonNormalizationRule(
        name="guarded_callable",
        rule_class="presentation_cleanup",
        kind="callable",
        description="Skip expensive comparison projection when a required literal is absent.",
        transform=transform,
        required_substring="needle",
    )

    result = normalize_comparison_text("plain text", (rule,))

    assert result.text == "plain text"
    assert result.fired_rules == ()
    assert not called


def test_normalize_comparison_text_honors_required_any_substrings_prefilter() -> None:
    called = False

    def transform(text: str) -> str:
        nonlocal called
        called = True
        return text.upper()

    rule = ComparisonNormalizationRule(
        name="guarded_any_callable",
        rule_class="presentation_cleanup",
        kind="callable",
        description="Skip expensive comparison projection unless one trigger literal is present.",
        transform=transform,
        required_any_substrings=("alpha", "beta"),
    )

    skipped = normalize_comparison_text("plain text", (rule,))
    applied = normalize_comparison_text("plain alpha text", (rule,))

    assert skipped.text == "plain text"
    assert skipped.fired_rules == ()
    assert applied.text == "PLAIN ALPHA TEXT"
    assert applied.fired_rules == ("guarded_any_callable",)
    assert called


def test_validate_comparison_normalization_rule_rejects_silent_noops() -> None:
    missing_pattern = ComparisonNormalizationRule(
        name="bad_regex",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Invalid regex rule with no pattern.",
    )
    empty_literal = ComparisonNormalizationRule(
        name="bad_literal",
        rule_class="presentation_cleanup",
        kind="literal",
        description="Invalid literal rule with no old_text.",
    )
    missing_transform = ComparisonNormalizationRule(
        name="bad_callable",
        rule_class="presentation_cleanup",
        kind="callable",
        description="Invalid callable rule with no transform.",
    )

    assert validate_comparison_normalization_rule(missing_pattern) == (
        "comparison normalization rule 'bad_regex' requires a regex pattern",
    )
    assert validate_comparison_normalization_rule(empty_literal) == (
        "comparison normalization rule 'bad_literal' requires non-empty old_text",
    )
    assert validate_comparison_normalization_rule(missing_transform) == (
        "comparison normalization rule 'bad_callable' requires a transform",
    )

    with pytest.raises(ValueError, match="requires a regex pattern"):
        normalize_comparison_text("text", (missing_pattern,))


def test_validate_comparison_normalization_rules_rejects_duplicate_names() -> None:
    duplicate = ComparisonNormalizationRule(
        name="quote_typography",
        rule_class="presentation_cleanup",
        kind="translation",
        description="Duplicate rule name.",
        translation=str.maketrans({"\u201c": '"'}),
    )

    assert validate_comparison_normalization_rules((TYPOGRAPHY_RULE, duplicate)) == (
        "comparison normalization rule 'quote_typography' is duplicated",
    )
    with pytest.raises(ValueError, match="quote_typography"):
        normalize_comparison_text("\u201cquoted\u201d", (TYPOGRAPHY_RULE, duplicate))


def test_inline_comparison_rules_validate() -> None:
    assert validate_comparison_normalization_rules(INLINE_TEXT_COMPARISON_RULES) == ()


def test_normalize_inline_comparison_text_normalizes_whitespace_and_punctuation() -> None:
    # Display spaces are removed both after an opening paren and before a closing
    # paren/bracket, so "(  a )" collapses to "(a)".
    assert normalize_inline_comparison_text("  old \n text  , (  a )  ") == "old text, (a)"


def test_normalize_inline_comparison_text_matches_rule_pipeline() -> None:
    samples = (
        "",
        "plain",
        "  old \n text  , (  a )  ",
        "section 197 ) and item 4 ]",
        "\talpha  ; beta: ( gamma )",
    )

    for sample in samples:
        assert normalize_inline_comparison_text(sample) == normalize_comparison_text(
            sample,
            INLINE_TEXT_COMPARISON_RULES,
        ).text


def test_normalize_inline_comparison_text_removes_space_before_closing_paren() -> None:
    assert normalize_inline_comparison_text("section 197 )") == "section 197)"
    assert normalize_inline_comparison_text("item 4 ]") == "item 4]"


def test_normalized_inline_occurrence_count_uses_shared_normalization() -> None:
    assert normalized_inline_occurrence_count("old \n text, and old text ,", " old text, ") == 2
    assert normalized_inline_occurrence_count("anything", " \n\t ") == 0


def test_normalized_inline_contains_uses_shared_normalization() -> None:
    assert normalized_inline_contains("new text and old text", " old  text ")
    assert not normalized_inline_contains("new text", " \n\t ")


def test_current_comparison_rule_sets_validate() -> None:
    from lawvm.estonia.compare import _EE_CORE_NORMALIZATION_RULES
    from lawvm.norway.verify import _NO_COMPARISON_NORMALIZATION_RULES
    from lawvm.open_law.audit import _TYPOGRAPHY_COMPARISON_RULES
    from lawvm.sweden.fetch import _SE_COMPARE_NORMALIZATION_RULES
    from lawvm.finland.oracle_comparison import _FINLEX_ORACLE_COMPARISON_RULES

    for rules in (
        _EE_CORE_NORMALIZATION_RULES,
        _NO_COMPARISON_NORMALIZATION_RULES,
        _TYPOGRAPHY_COMPARISON_RULES,
        _SE_COMPARE_NORMALIZATION_RULES,
        _FINLEX_ORACLE_COMPARISON_RULES,
    ):
        assert validate_comparison_normalization_rules(rules) == ()
