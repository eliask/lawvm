from __future__ import annotations

from lawvm.core.comparison_normalization import (
    ComparisonNormalizationRule,
    normalize_comparison_text,
    project_ir_comparison_text,
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
