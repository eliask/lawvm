"""Shared comparison-only text normalization helpers.

These rules are for oracle/display comparison projections. They must not be
used to repair source text, replay payloads, or legal tree state silently.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Literal, Mapping, Optional

from lawvm.core.ir import IRNode

ComparisonRuleKind = Literal["translation", "literal", "regex"]
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


def normalize_comparison_text(
    text: str,
    rules: tuple[ComparisonNormalizationRule, ...],
) -> ComparisonNormalizationResult:
    """Apply comparison-only normalization rules and report which rules fired."""
    normalized = text
    fired: list[str] = []
    for rule in rules:
        before = normalized
        if rule.kind == "translation":
            if rule.translation is None:
                continue
            normalized = normalized.translate(rule.translation)
        elif rule.kind == "literal":
            normalized = normalized.replace(rule.old_text, rule.new_text)
        elif rule.kind == "regex":
            if rule.pattern is None:
                continue
            normalized = rule.pattern.sub(rule.replacement, normalized)
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
