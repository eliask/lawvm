"""Deterministic rendering helpers for the frozen Finland rulebook."""

from __future__ import annotations

from lawvm.finland.rulebook.rulebook import FinlandRulebook


def render_rulebook_markdown(rulebook: FinlandRulebook) -> str:
    lines: list[str] = ["# Finland Rulebook", ""]
    families = (
        ("Clause", rulebook.clause_rules),
        ("Payload", rulebook.payload_rules),
        ("Temporal", rulebook.temporal_rules),
        ("Source", rulebook.source_rules),
        ("Compare", rulebook.compare_rules),
    )
    for family_title, family in families:
        lines.append(f"## {family_title} Rules")
        lines.append("")
        lines.append(f"Family id: `{family.family_id}`")
        if family.description:
            lines.append("")
            lines.append(family.description)
        lines.append("")
        for rule in family.rules:
            lines.append(f"### {rule.header.rule_id}")
            lines.append("")
            lines.append(f"- Phase: `{rule.header.phase}`")
            lines.append(f"- Priority: `{rule.header.priority}`")
            lines.append(f"- Authority: `{rule.header.authority}`")
            lines.append(f"- Strength: `{rule.header.strength}`")
            lines.append(f"- Purpose: {rule.header.purpose}")
            if rule.header.examples:
                lines.append("- Examples:")
                for example in rule.header.examples:
                    lines.append(f"  - `{example.label}`")
                    if example.input_text:
                        lines.append(f"    - text: `{example.input_text}`")
                    if example.input_xml:
                        lines.append(f"    - xml: `{example.input_xml}`")
                    if example.expects:
                        lines.append(f"    - expects: {', '.join(f'`{item}`' for item in example.expects)}")
                    if example.rejects:
                        lines.append(f"    - rejects: {', '.join(f'`{item}`' for item in example.rejects)}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
