"""Shared comparison-only text normalization helpers.

These rules are for oracle/display comparison projections. They must not be
used to repair source text, replay payloads, or legal tree state silently.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Literal, Mapping, Optional, cast

from lawvm.core.ir import IRNode

ComparisonRuleKind = Literal["translation", "literal", "regex", "placeholder", "callable"]
TranslationTable = Mapping[int, str | int | None]


@dataclass(frozen=True, slots=True)
class ComparisonNormalizationRule:
    name: str
    rule_class: str
    kind: ComparisonRuleKind
    description: str
    translation: Optional[TranslationTable] = None
    pattern: Optional[re.Pattern[str]] = None
    replacement: str | Callable[[re.Match[str]], str] = ""
    transform: Callable[[str], str] | None = None
    required_substring: str = ""
    required_any_substrings: tuple[str, ...] = ()
    old_text: str = ""
    new_text: str = ""


@dataclass(frozen=True, slots=True)
class ComparisonNormalizationResult:
    text: str
    fired_rules: tuple[str, ...]


def validate_comparison_normalization_rule(rule: ComparisonNormalizationRule) -> tuple[str, ...]:
    """Return rule-shape issues for comparison-only normalization rules."""

    issues: list[str] = []
    if not rule.name:
        issues.append("comparison normalization rule requires a non-empty name")
    if not rule.rule_class:
        issues.append(f"comparison normalization rule {rule.name!r} requires a non-empty rule_class")
    if rule.kind == "translation" and rule.translation is None:
        issues.append(f"comparison normalization rule {rule.name!r} requires a translation table")
    elif rule.kind == "literal" and not rule.old_text:
        issues.append(f"comparison normalization rule {rule.name!r} requires non-empty old_text")
    elif rule.kind in {"regex", "placeholder"} and rule.pattern is None:
        issues.append(f"comparison normalization rule {rule.name!r} requires a regex pattern")
    elif rule.kind == "callable" and rule.transform is None:
        issues.append(f"comparison normalization rule {rule.name!r} requires a transform")
    return tuple(issues)


def validate_comparison_normalization_rules(
    rules: tuple[ComparisonNormalizationRule, ...],
) -> tuple[str, ...]:
    """Return rule-shape issues for an ordered comparison-normalization pipeline."""

    issues: list[str] = []
    seen_names: set[str] = set()
    for rule in rules:
        issues.extend(validate_comparison_normalization_rule(rule))
        if rule.name in seen_names:
            issues.append(f"comparison normalization rule {rule.name!r} is duplicated")
        elif rule.name:
            seen_names.add(rule.name)
    return tuple(issues)


def normalize_comparison_text(
    text: str,
    rules: tuple[ComparisonNormalizationRule, ...],
) -> ComparisonNormalizationResult:
    """Apply comparison-only normalization rules and report which rules fired."""
    issues = validate_comparison_normalization_rules(rules)
    if issues:
        raise ValueError("; ".join(issues))
    normalized = text
    fired: list[str] = []
    for rule in rules:
        if rule.required_substring and rule.required_substring not in normalized:
            continue
        if rule.required_any_substrings and not any(
            literal in normalized for literal in rule.required_any_substrings
        ):
            continue
        before = normalized
        if rule.kind == "translation":
            translation = rule.translation
            assert translation is not None
            normalized = normalized.translate(cast(Any, translation))
        elif rule.kind == "literal":
            normalized = normalized.replace(rule.old_text, rule.new_text)
        elif rule.kind == "regex":
            pattern = rule.pattern
            assert pattern is not None
            normalized = pattern.sub(rule.replacement, normalized)
        elif rule.kind == "placeholder":
            pattern = rule.pattern
            assert pattern is not None
            if pattern.fullmatch(normalized.strip()):
                normalized = cast(str, rule.replacement)
        elif rule.kind == "callable":
            transform = rule.transform
            assert transform is not None
            normalized = transform(normalized)
        if normalized != before:
            fired.append(rule.name)
    return ComparisonNormalizationResult(text=normalized, fired_rules=tuple(fired))


def project_ir_comparison_text(
    node: IRNode,
    rules: tuple[ComparisonNormalizationRule, ...],
) -> IRNode:
    """Project IR node text through comparison-only rules, preserving identity when unchanged."""
    text = normalize_comparison_text(node.text, rules).text
    children = tuple(project_ir_comparison_text(child, rules) for child in node.children)
    if text == node.text and children == node.children:
        return node
    return IRNode(kind=node.kind, label=node.label, text=text, attrs=dict(node.attrs), children=children)


# Default inline text-comparison rule set for Westminster / common-law frontends.
#
# These rules normalize edge whitespace, internal whitespace runs, and the
# source-display spacing around inline punctuation so that witness/oracle text
# can be compared and occurrences counted independent of presentation. They are
# the shared default for common-law frontends (New Zealand uses them today; the
# UK frontend can adopt them without code change). Like every rule set in this
# module, they are comparison-only: they must not repair source text, replay
# payloads, or legal tree state.
INLINE_TEXT_COMPARISON_RULES: tuple[ComparisonNormalizationRule, ...] = (
    ComparisonNormalizationRule(
        name="inline_text_trim",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Trim edge whitespace before inline text occurrence counting.",
        pattern=re.compile(r"^\s+|\s+$"),
        replacement="",
    ),
    ComparisonNormalizationRule(
        name="inline_text_whitespace_collapse",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Collapse XML and rendered whitespace for inline text occurrence counting.",
        pattern=re.compile(r"\s+"),
        replacement=" ",
    ),
    ComparisonNormalizationRule(
        name="inline_text_punctuation_spacing",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Remove source-display spaces before punctuation for inline text occurrence counting.",
        pattern=re.compile(r"\s+([,.;:])"),
        replacement=r"\1",
    ),
    ComparisonNormalizationRule(
        name="inline_text_open_paren_spacing",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Remove source-display spaces after opening parentheses for inline text occurrence counting.",
        pattern=re.compile(r"([(])\s+"),
        replacement=r"\1",
    ),
    ComparisonNormalizationRule(
        name="inline_text_close_paren_spacing",
        rule_class="presentation_cleanup",
        kind="regex",
        description=(
            "Remove source-display spaces before closing parentheses/brackets for "
            "inline text occurrence counting. Symmetric with the open-paren rule: a "
            "consolidated body renders an inline cross-reference followed by a "
            "closing paren as 'section 197 )' while the amending act's payload "
            "renders the same content as 'section 197)'. Comparison-only spacing."
        ),
        pattern=re.compile(r"\s+([)\]])"),
        replacement=r"\1",
    ),
)

_INLINE_TEXT_COMPARISON_RULE_ISSUES = validate_comparison_normalization_rules(
    INLINE_TEXT_COMPARISON_RULES
)
if _INLINE_TEXT_COMPARISON_RULE_ISSUES:
    raise ValueError("; ".join(_INLINE_TEXT_COMPARISON_RULE_ISSUES))


# Jurisdiction-agnostic comparison-normalization primitives that individual
# frontends previously open-coded. Expressed here as typed rules so every
# frontend applies ONE canonical definition (via ``normalize_comparison_text``)
# rather than reinventing a per-jurisdiction variant. Comparison-only, like the
# rest of this module: they must not repair source text, replay payloads, or
# legal tree state.

# Canonical em dash used by the dash-fold below.
DASH_CANONICAL_EM = "—"

# Hyphen/dash/minus variants folded to the em dash for comparison. Covers the
# Unicode hyphen (U+2010), non-breaking hyphen (U+2011), figure dash (U+2012),
# en dash (U+2013), em dash (U+2014), horizontal bar (U+2015), and minus sign
# (U+2212).
DASH_VARIANT_CHARS: frozenset[str] = frozenset(
    (
        "‐",
        "‑",
        "‒",
        "–",
        "—",
        "―",
        "−",
    )
)

DASH_VARIANT_CANONICALIZATION_RULE = ComparisonNormalizationRule(
    name="dash_variant_canonicalization",
    rule_class="presentation_cleanup",
    kind="translation",
    description=(
        "Canonicalize hyphen/dash/minus variants (U+2010..U+2015, U+2212) to the "
        "em dash U+2014 for comparison. Jurisdiction-agnostic dash-fold. Frontends "
        "that must preserve dashes inside quoted legal text apply it OUTSIDE quotes "
        "only (see the UK parser-view normalizer), so the shared typed rule is the "
        "single source of the variant set and the canonical target."
    ),
    translation={ord(ch): DASH_CANONICAL_EM for ch in DASH_VARIANT_CHARS},
)

EDITORIAL_HYPHEN_WRAP_SPACE_RULE = ComparisonNormalizationRule(
    name="editorial_hyphen_wrap_space",
    rule_class="presentation_cleanup",
    kind="regex",
    description=(
        "Collapse a line-wrap space sitting BETWEEN two word characters joined by "
        "a hyphen (``non- Federal`` -> ``non-Federal``). A consolidated body "
        "re-joins a hyphenated compound the enacted source broke across a line. "
        "Matches ONLY ``\\w-\\s+\\w`` (never a space after a standalone dash / "
        "em-dash introducer). Jurisdiction-agnostic; witnessed by U.S. F1 "
        "(``the non- Federal cost share``) but a general OLRC/consolidation "
        "artifact. Comparison-only."
    ),
    pattern=re.compile(r"(?<=\w-)\s+(?=\w)"),
    replacement="",
)

_SHARED_PRIMITIVE_RULE_ISSUES = validate_comparison_normalization_rules(
    (DASH_VARIANT_CANONICALIZATION_RULE, EDITORIAL_HYPHEN_WRAP_SPACE_RULE)
)
if _SHARED_PRIMITIVE_RULE_ISSUES:
    raise ValueError("; ".join(_SHARED_PRIMITIVE_RULE_ISSUES))

_INLINE_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:)\]])")
_INLINE_SPACE_AFTER_OPEN_PAREN_RE = re.compile(r"([(])\s+")


def normalize_inline_comparison_text(text: str) -> str:
    """Normalize inline text for witness/oracle comparison (common-law default).

    Comparison-only: never use this to repair source text, replay payloads, or
    legal tree state.
    """
    normalized = " ".join(text.split())
    if not normalized:
        return ""
    if (
        " ," not in normalized
        and " ." not in normalized
        and " ;" not in normalized
        and " :" not in normalized
        and " )" not in normalized
        and " ]" not in normalized
        and "( " not in normalized
    ):
        return normalized
    normalized = _INLINE_SPACE_BEFORE_PUNCT_RE.sub(r"\1", normalized)
    return _INLINE_SPACE_AFTER_OPEN_PAREN_RE.sub(r"\1", normalized)


def normalized_inline_occurrence_count(haystack: str, needle: str) -> int:
    """Count comparison-normalized occurrences of ``needle`` within ``haystack``.

    Comparison-only: never use this to repair source text, replay payloads, or
    legal tree state.
    """
    normalized_needle = normalize_inline_comparison_text(needle)
    if not normalized_needle:
        return 0
    return normalize_inline_comparison_text(haystack).count(normalized_needle)


def normalized_inline_contains(haystack: str, needle: str) -> bool:
    """Return whether ``haystack`` contains comparison-normalized ``needle``.

    Comparison-only: never use this to repair source text, replay payloads, or
    legal tree state.
    """
    normalized_needle = normalize_inline_comparison_text(needle)
    return bool(normalized_needle) and normalized_needle in normalize_inline_comparison_text(haystack)
