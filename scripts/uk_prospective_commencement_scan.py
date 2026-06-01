#!/usr/bin/env python3
"""Scan UK prospective-only effects against affecting-provision commencement.

This is an evidence surface for the §6.8 PIT resolver. It does not filter or
mutate replay; it classifies prospective-only structural effects as resolved
in-force, resolved future, or unresolved using the affecting act's
``RestrictStartDate`` metadata.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import random
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "uk_legislation.farchive"
LEG_BASE = "https://www.legislation.gov.uk"


def statute_ids_from_archive(db_path: Path, *, classes: list[str] | None = None) -> list[str]:
    from farchive import Farchive

    with Farchive(db_path) as archive:
        current: set[str] = set()
        suffix = "/data.xml"
        for loc in archive.locators(f"{LEG_BASE}/%/data.xml"):
            if loc.endswith("/enacted/data.xml"):
                continue
            sid = loc[len(LEG_BASE) + 1 : -len(suffix)]
            if sid.count("/") == 2 and "/changes/" not in loc and "/affecting/" not in loc:
                current.add(sid)
    ids = sorted(current)
    if classes:
        class_set = set(classes)
        ids = [sid for sid in ids if sid.split("/", 1)[0] in class_set]
    return ids


def ids_from_file(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _owner_phase_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    from lawvm.uk_legislation.phase_discipline import (
        uk_phase_owner_counts_for_diagnostics,
    )

    return uk_phase_owner_counts_for_diagnostics(rows)


def _limited_rows_and_owner_phase_counts(
    all_rows: list[dict[str, Any]],
    *,
    limit: int | None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows = all_rows[:limit] if limit is not None else all_rows
    return rows, _owner_phase_counts(all_rows)


def run_scan(args: argparse.Namespace) -> dict[str, Any]:
    from farchive import Farchive
    from lawvm.uk_legislation.prospective_commencement_witnesses import (
        prospective_commencement_status_counts,
        scan_prospective_commencement_witnesses,
    )

    ids: list[str] = []
    if args.ids:
        ids.extend(args.ids)
    if args.ids_file:
        ids.extend(ids_from_file(args.ids_file))
    if args.all:
        ids.extend(statute_ids_from_archive(args.db, classes=args.classes))
    if args.sample:
        pool = statute_ids_from_archive(args.db, classes=args.classes)
        rng = random.Random(args.seed)
        rng.shuffle(pool)
        ids.extend(pool[: args.sample])
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise SystemExit("pass --ids, --ids-file, --sample, or --all")

    diagnostics: list[dict[str, Any]] = []
    with Farchive(args.db) as archive:
        witnesses = scan_prospective_commencement_witnesses(
            ids,
            archive,
            as_of=args.as_of,
            diagnostics_out=diagnostics,
        )

    all_rows = [witness.to_dict() for witness in witnesses]
    rows, owner_phase_counts = _limited_rows_and_owner_phase_counts(
        all_rows,
        limit=args.limit,
    )
    return {
        "as_of": args.as_of,
        "n_statutes_scanned": len(ids),
        "n_witnesses": len(witnesses),
        "statuses": prospective_commencement_status_counts(witnesses),
        "rules": dict(Counter(w.rule_id for w in witnesses)),
        "owner_phase_counts": owner_phase_counts,
        "n_source_diagnostics": len(diagnostics),
        "witnesses": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--ids", nargs="+", help="explicit affected statute IDs")
    parser.add_argument("--ids-file", type=Path, help="newline-separated affected statute IDs")
    parser.add_argument("--sample", type=int, help="sample N current statutes from archive")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--classes", nargs="+", help="restrict --sample/--all to document classes")
    parser.add_argument("--all", action="store_true", help="scan all current statute IDs in archive")
    parser.add_argument("--as-of", default="2026-05-31", help="PIT date for commencement lookup")
    parser.add_argument("--limit", type=int, help="limit emitted witness rows")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    payload = run_scan(args)
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(REPO_ROOT / "src"))
    raise SystemExit(main())
