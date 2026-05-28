"""Shared comparison-only text normalization helpers.

These rules are for oracle/display comparison projections. They must not be
used to repair source text, replay payloads, or legal tree state silently.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Literal, Mapping, Optional, cast

from lawvm.core.ir import IRNode

ComparisonRuleKind = Literal["translation", "literal", "regex", "placeholder"]
TranslationTable = Mapping[int, str | int | None]


@dataclass(frozen=True)
class ComparisonNormalizationRule:
    name: str
    rule_class: str
    kind: ComparisonRuleKind
    description: str
    translation: Optional[TranslationTable] = None
    pattern: Optional[re.Pattern[str]] = None
    replacement: str | Callable[[re.Match[str]], str] = ""
    old_text: str = ""
    new_text: str = ""


@dataclass(frozen=True)
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
