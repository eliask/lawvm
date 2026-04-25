"""lawvm cite — show cross-reference edges for a Finnish statute."""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def main(args: "argparse.Namespace") -> None:
    from lawvm.finland.cross_refs import extract_cross_refs, extract_eu_refs
    from lawvm.finland.grafter import get_corpus

    sid = args.statute_id
    cs = get_corpus()

    xml_bytes = cs.read_oracle(sid)
    if xml_bytes is None:
        print(f"Statute {sid!r} not found in consolidated ZIP.", file=sys.stderr)
        sys.exit(1)
    edges = extract_cross_refs(xml_bytes, sid)
    if not getattr(args, "no_eu", False):
        edges = edges + extract_eu_refs(xml_bytes, sid)

    if not edges:
        print(f"{sid}: no cross-reference edges found.")
        return

    # Filter by edge type
    if args.type:
        wanted = {t.upper() for t in args.type.split(",")}
        edges = [e for e in edges if e.edge_type in wanted]

    # Group by edge type
    by_type: dict[str, list] = {}
    for e in edges:
        by_type.setdefault(e.edge_type, []).append(e)

    total = len(edges)
    print(f"{sid}: {total} edge(s)  "
          + "  ".join(f"{k}:{len(v)}" for k, v in sorted(by_type.items())))
    print()

    for etype in sorted(by_type):
        group = sorted(by_type[etype], key=lambda e: (-e.count, e.source_section, e.target_statute_id))
        print(f"── {etype} ──")
        for e in group:
            cnt = f" x{e.count}" if e.count > 1 else ""
            src = f"§{e.source_section} " if e.source_section else ""
            tgt_sec = f"#{e.target_section}" if e.target_section else ""
            print(f"  {src}→ {e.target_statute_id}{tgt_sec}{cnt}")
        print()
