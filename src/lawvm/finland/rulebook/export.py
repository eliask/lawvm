"""Export helpers for the frozen Finland rulebook scaffold."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

from lawvm.finland.rulebook.rulebook import FinlandRulebook
from lawvm.finland.rulebook.render import render_rulebook_markdown


def _enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def rulebook_index_data(rulebook: FinlandRulebook) -> dict[str, Any]:
    families = []
    for family in (
        rulebook.clause_rules,
        rulebook.payload_rules,
        rulebook.temporal_rules,
        rulebook.source_rules,
        rulebook.compare_rules,
    ):
        families.append(
            {
                "family_id": _enum_value(family.family_id),
                "description": family.description,
                "rules": [
                    {
                        "rule_id": rule.header.rule_id,
                        "phase": _enum_value(rule.header.phase),
                        "priority": rule.header.priority,
                        "authority": _enum_value(rule.header.authority),
                        "strength": _enum_value(rule.header.strength),
                        "purpose": rule.header.purpose,
                        "when": tuple(_enum_value(atom) for atom in rule.when),
                        "guards": [
                            {
                                "guard_id": _enum_value(guard.guard_id),
                                "args": guard.args,
                            }
                            for guard in rule.guards
                        ],
                        "emits": [
                            {
                                "emit_id": _enum_value(emit.emit_id),
                                "args": emit.args,
                            }
                            for emit in rule.emits
                        ],
                        "examples": [
                            {
                                "label": example.label,
                                "input_text": example.input_text,
                                "input_xml": example.input_xml,
                                "expects": example.expects,
                                "rejects": example.rejects,
                            }
                            for example in rule.header.examples
                        ],
                    }
                    for rule in family.rules
                ],
            }
        )
    return {"rulebook": {"families": families}}


def render_rulebook_index_json(rulebook: FinlandRulebook) -> str:
    return json.dumps(rulebook_index_data(rulebook), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def write_generated_rulebook_assets(rulebook: FinlandRulebook, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = out_dir / "RULEBOOK.md"
    index_path = out_dir / "RULE_INDEX.json"
    markdown_path.write_text(render_rulebook_markdown(rulebook), encoding="utf-8")
    index_path.write_text(render_rulebook_index_json(rulebook), encoding="utf-8")
    return markdown_path, index_path
