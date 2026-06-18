"""lawvm delegate — show delegation clauses in a Finnish statute."""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def _parse_type_filter(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {part.strip().upper() for part in raw.split(",") if part.strip()}


def main(args: "argparse.Namespace") -> None:
    from lawvm.finland.delegation import extract_delegations, extract_asetus_authority
    from lawvm.finland.corpus import get_corpus

    sid = args.statute_id
    cs = get_corpus()

    xml_bytes = cs.read_oracle(sid)
    if xml_bytes is None:
        print(f"Statute {sid!r} not found in consolidated ZIP.", file=sys.stderr)
        sys.exit(1)

    if args.reverse:
        edges = extract_asetus_authority(xml_bytes, sid)
        if not edges:
            print(f"{sid}: no nojalla authority references found.")
            return
        print(f"{sid}: {len(edges)} authority reference(s) to parent laws\n")
        for e in edges:
            print(f"  → {e.parent_statute_id} §{e.parent_section}"
                  + (f" mom.{e.parent_moment}" if e.parent_moment else ""))
            if args.verbose:
                print(f"    {e.quote.strip()!r}")
    else:
        edges = extract_delegations(xml_bytes, sid)
        if not edges:
            print(f"{sid}: no delegation clauses found.")
            return

        wanted = _parse_type_filter(args.type)
        if wanted:
            edges = [e for e in edges if e.delegation_type in wanted]
            if not edges:
                print(
                    f"{sid}: no delegation clauses found for type filter "
                    f"{','.join(sorted(wanted))}."
                )
                return

        print(f"{sid}: {len(edges)} delegation clause(s)\n")
        for e in edges:
            print(f"  §{e.section} [{e.delegation_type}]: {e.match_text}")
            if args.verbose:
                print(f"    {e.quote[:200].strip()!r}")
