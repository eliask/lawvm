"""Inventory U.S. federal Public Law units present in a farchive.

This is an honest source-shape report: it counts what amendment-source units are
archived (per Congress) and lists their identities. It makes NO replay,
verification, coverage, or legal-effect claim — the USC oracle that would anchor
coverage is out of scope and blocked (see :mod:`lawvm.us_federal.sources`).

Runnable without the global CLI::

    python -m lawvm.us_federal.inventory
    python -m lawvm.us_federal.inventory --congress 118 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lawvm.us_federal.sources import (
    PlawMemberIdentity,
    UsArchiveReader,
    list_plaw_identities,
    open_us_federal_farchive,
    resolve_us_federal_farchive_path,
)


@dataclass(frozen=True, slots=True)
class PlawInventory:
    """Honest source-shape inventory of PLAW amendment-source units."""

    total_units: int
    congresses: tuple[int, ...]
    counts_per_congress: dict[int, int]
    units: tuple[tuple[int, int], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "us_federal_plaw_inventory",
            "truth_claim": (
                "passive amendment-source unit inventory; no replay, coverage, "
                "or legal-effect claim"
            ),
            "total_units": self.total_units,
            "congresses": list(self.congresses),
            "counts_per_congress": {
                str(c): n for c, n in sorted(self.counts_per_congress.items())
            },
            "units": [
                {"congress": congress, "law_number": number}
                for congress, number in self.units
            ],
            "oracle_status": {
                "usc_oracle": "out_of_scope_blocked",
                "reason": (
                    "OLRC uscode.house.gov is geo-blocked; govinfo USCODE needs an "
                    "api.data.gov key (not configured)"
                ),
            },
        }


def build_inventory(
    archive: UsArchiveReader, *, congress: int | None = None
) -> PlawInventory:
    """Build a PLAW inventory from an open archive."""
    identities: list[PlawMemberIdentity] = list_plaw_identities(archive, congress)
    counts: dict[int, int] = {}
    for identity in identities:
        counts[identity.congress] = counts.get(identity.congress, 0) + 1
    units = tuple((i.congress, i.number) for i in identities)
    return PlawInventory(
        total_units=len(identities),
        congresses=tuple(sorted(counts)),
        counts_per_congress=counts,
        units=units,
    )


def inventory_us_federal(
    *, db_path: Path | None = None, congress: int | None = None
) -> PlawInventory:
    """Open the canonical (or given) U.S. farchive and inventory PLAW units."""
    archive = open_us_federal_farchive(db_path, readonly=True)
    try:
        return build_inventory(archive, congress=congress)
    finally:
        archive.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inventory U.S. federal Public Law units in a farchive.",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="Explicit farchive path (default: canonical data/us_federal.farchive).",
    )
    parser.add_argument(
        "--congress",
        type=int,
        default=None,
        help="Restrict the inventory to a single Congress.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the inventory as JSON instead of a human summary.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.dest is None:
        path, rule = resolve_us_federal_farchive_path()
        print(f"Inventory farchive: {path}  ({rule})", file=sys.stderr)

    inventory = inventory_us_federal(db_path=args.dest, congress=args.congress)

    if args.json:
        print(json.dumps(inventory.to_dict(), indent=2, ensure_ascii=False))
        return 0

    print("U.S. federal PLAW inventory (amendment-source units only):")
    print(f"  Total Public Laws: {inventory.total_units:,}")
    for congress in inventory.congresses:
        print(f"    Congress {congress}: {inventory.counts_per_congress[congress]:,}")
    print(
        "  Oracle status: USC oracle out of scope/blocked "
        "(OLRC geo-blocked; govinfo USCODE needs api.data.gov key)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
