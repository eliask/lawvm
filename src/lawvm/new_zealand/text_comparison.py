"""New Zealand comparison-only text normalization helpers.

These helpers are for witness/oracle comparison only. They must not be used to
repair source XML, replay payloads, or legal tree state.
"""

from __future__ import annotations

import re

from lawvm.core.comparison_normalization import (
    ComparisonNormalizationRule,
    normalize_comparison_text,
    validate_comparison_normalization_rules,
)


_NZ_INLINE_TEXT_COMPARISON_RULES = (
    ComparisonNormalizationRule(
        name="nz_inline_text_trim",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Trim edge whitespace before inline text occurrence counting.",
        pattern=re.compile(r"^\s+|\s+$"),
        replacement="",
    ),
    ComparisonNormalizationRule(
        name="nz_inline_text_whitespace_collapse",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Collapse XML and rendered whitespace for inline text occurrence counting.",
        pattern=re.compile(r"\s+"),
        replacement=" ",
    ),
    ComparisonNormalizationRule(
        name="nz_inline_text_punctuation_spacing",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Remove source-display spaces before punctuation for inline text occurrence counting.",
        pattern=re.compile(r"\s+([,.;:])"),
        replacement=r"\1",
    ),
    ComparisonNormalizationRule(
        name="nz_inline_text_open_paren_spacing",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Remove source-display spaces after opening parentheses for inline text occurrence counting.",
        pattern=re.compile(r"([(])\s+"),
        replacement=r"\1",
    ),
)

_NZ_INLINE_TEXT_COMPARISON_RULE_ISSUES = validate_comparison_normalization_rules(
    _NZ_INLINE_TEXT_COMPARISON_RULES
)
if _NZ_INLINE_TEXT_COMPARISON_RULE_ISSUES:
    raise ValueError("; ".join(_NZ_INLINE_TEXT_COMPARISON_RULE_ISSUES))


def normalize_nz_inline_comparison_text(text: str) -> str:
    return normalize_comparison_text(text, _NZ_INLINE_TEXT_COMPARISON_RULES).text


def normalized_nz_inline_occurrence_count(haystack: str, needle: str) -> int:
    normalized_needle = normalize_nz_inline_comparison_text(needle)
    if not normalized_needle:
        return 0
    return normalize_nz_inline_comparison_text(haystack).count(normalized_needle)


def normalized_nz_inline_contains(haystack: str, needle: str) -> bool:
    normalized_needle = normalize_nz_inline_comparison_text(needle)
    return bool(normalized_needle) and normalized_needle in normalize_nz_inline_comparison_text(haystack)
