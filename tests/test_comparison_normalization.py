from __future__ import annotations

import re

import pytest

from lawvm.core.comparison_normalization import (
    ComparisonNormalizationRule,
    normalize_comparison_text,
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

    assert validate_comparison_normalization_rule(missing_pattern) == (
        "comparison normalization rule 'bad_regex' requires a regex pattern",
    )
    assert validate_comparison_normalization_rule(empty_literal) == (
        "comparison normalization rule 'bad_literal' requires non-empty old_text",
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


def test_current_comparison_rule_sets_validate() -> None:
    from lawvm.estonia.compare import _EE_CORE_NORMALIZATION_RULES
    from lawvm.norway.verify import _NO_COMPARISON_NORMALIZATION_RULES
    from lawvm.open_law.audit import _TYPOGRAPHY_COMPARISON_RULES
    from lawvm.sweden.fetch import _SE_COMPARE_NORMALIZATION_RULES
    from lawvm.tools.editorial_hygiene import _FINLEX_ORACLE_COMPARISON_RULES

    for rules in (
        _EE_CORE_NORMALIZATION_RULES,
        _NO_COMPARISON_NORMALIZATION_RULES,
        _TYPOGRAPHY_COMPARISON_RULES,
        _SE_COMPARE_NORMALIZATION_RULES,
        _FINLEX_ORACLE_COMPARISON_RULES,
    ):
        assert validate_comparison_normalization_rules(rules) == ()
