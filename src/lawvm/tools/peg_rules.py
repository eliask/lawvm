"""peg_rules — CLI tool: list Finland parse rules from the Phase 8 rule registry.

Usage:
    lawvm peg-rules
    lawvm peg-rules --category meta
    lawvm peg-rules --node-kind SurfaceTargetRef
    lawvm peg-rules --examples
    lawvm peg-rules --json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lawvm.finland.johtolause.rule_registry import ParseRule


def main(args: argparse.Namespace) -> None:
    from lawvm.finland.johtolause.rule_registry import FINLAND_RULE_REGISTRY

    # Apply filters
    rules = FINLAND_RULE_REGISTRY.all_rules()
    if getattr(args, "category", None):
        rules = [r for r in rules if r.category == args.category]
    if getattr(args, "node_kind", None):
        rules = [r for r in rules if r.node_kind == args.node_kind]

    show_examples = getattr(args, "examples", False)
    emit_json = getattr(args, "json", False)

    if emit_json:
        _emit_json(rules, show_examples=show_examples)
    else:
        _emit_text(rules, show_examples=show_examples)


def _emit_text(rules: list[ParseRule], *, show_examples: bool) -> None:
    if not rules:
        print("No rules matched the filter.", file=sys.stderr)
        return

    # Group by category for readability
    categories: dict[str, list[ParseRule]] = {}
    for rule in rules:
        categories.setdefault(rule.category or "uncategorized", []).append(rule)

    total_examples = sum(len(r.examples) for r in rules)
    print(f"Finland parse rule registry — {len(rules)} rule(s), {total_examples} example(s)\n")

    for cat, cat_rules in categories.items():
        print(f"  [{cat.upper()}]")
        for rule in cat_rules:
            example_count = len(rule.examples)
            shape = f"  shape={rule.shape!r}" if rule.shape else ""
            print(f"  {rule.rule_id}")
            print(f"    node_kind: {rule.node_kind}")
            print(f"    {rule.description}{shape}")
            print(f"    {example_count} example(s)")
            if show_examples:
                for i, ex in enumerate(rule.examples):
                    desc = f"  [{ex.description}]" if ex.description else ""
                    print(f"      [{i}] {ex.input_text!r:.90}{desc}")
                    if ex.expected_fields:
                        print(f"           expected_fields: {ex.expected_fields}")
            print()
        print()


def _emit_json(rules: list[ParseRule], *, show_examples: bool) -> None:
    output = []
    for rule in rules:
        entry: dict = {
            "rule_id": rule.rule_id,
            "description": rule.description,
            "node_kind": rule.node_kind,
            "category": rule.category,
            "shape": rule.shape,
            "example_count": len(rule.examples),
        }
        if show_examples:
            entry["examples"] = [
                {
                    "input_text": ex.input_text,
                    "expected_node_kind": ex.expected_node_kind,
                    "expected_fields": ex.expected_fields,
                    "description": ex.description,
                }
                for ex in rule.examples
            ]
        output.append(entry)
    print(json.dumps(output, ensure_ascii=False, indent=2))
