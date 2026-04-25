"""lawvm parse-johto — parse a Finnish johtolause text and show parsed ops.

Usage:
    lawvm parse-johto "muutetaan 5 §:n 2 momentti seuraavasti:"
    lawvm parse-johto "muutetaan 5 §:n 2 momentti seuraavasti:" --json
    lawvm parse-johto --statute 2006/1299 "muutetaan 5 §:n 2 momentti seuraavasti:"
"""

from __future__ import annotations

import argparse
import json
from typing import Any


def main(args: argparse.Namespace) -> None:
    from lawvm.finland.johtolause.api import parse_clause

    text: str = args.text
    statute_id: str = args.statute

    result = parse_clause(text, statute_id=statute_id)

    if args.json:
        ops_data: list[dict[str, Any]] = []
        for op in result.parsed_ops:
            ops_data.append(
                {
                    "verb": op.verb,
                    "kind": op.kind,
                    "chapter": op.chapter,
                    "number": op.number,
                    "momentti": op.momentti,
                    "item": op.item,
                    "facet": str(op.facet) if op.facet is not None else None,
                    "part": op.part,
                    "raw": op.raw,
                    "renumber_dest": op.renumber_dest,
                    "notes": list(op.notes),
                }
            )
        output: dict[str, Any] = {
            "text": text,
            "statute_id": statute_id,
            "parsed_ops": ops_data,
            "diagnostics": result.diagnostics,
            "parse_error": result.parse_error,
        }
        print(json.dumps(output, indent=2, default=str))
        return

    if result.parse_error:
        print(f"Parse error: {result.parse_error}")

    if result.diagnostics:
        for d in result.diagnostics:
            print(f"  diag: {d}")

    if not result.parsed_ops:
        print("No ops parsed.")
        return

    print(f"Parsed {len(result.parsed_ops)} op(s) from: {text!r}")
    print()
    for i, op in enumerate(result.parsed_ops, 1):
        facet_str = f"  facet={op.facet}" if op.facet is not None else ""
        action = {"M": "replace", "K": "repeal", "L": "insert", "S": "renumber"}.get(op.verb, op.verb)
        print(
            f"  [{i}] verb={op.verb} ({action})"
            f"  kind={op.kind}"
            f"  number={op.number!r}"
            f"  momentti={op.momentti}"
            f"  item={op.item!r}"
            f"  chapter={op.chapter!r}"
            f"  part={op.part!r}"
            f"{facet_str}"
        )
        if op.renumber_dest:
            print(f"       renumber_dest={op.renumber_dest!r}")
        if op.notes:
            print(f"       notes={op.notes}")

    if args.verbose and result.clause_ast is not None:
        print()
        print("AST verb groups:")
        for vg in result.clause_ast.verb_groups:
            print(f"  verb={vg.verb}  nodes={len(vg.nodes)}")
            for node in vg.nodes:
                print(f"    {type(node).__name__}: {node!r}")
