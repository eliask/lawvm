#!/usr/bin/env python3
"""Scan UK effect/source corpus for repeal-semantics witnesses.

This is a diagnostic inventory for roadmap item 6. It reports candidate rows for
no-revive / no-double-entry follow-up and does not mutate replay behavior.
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


def run_scan(args: argparse.Namespace) -> dict[str, Any]:
    from farchive import Farchive
    from lawvm.uk_legislation.repeal_semantics_witnesses import (
        scan_repeal_semantics_source_phrase_xml,
        scan_repeal_semantics_witnesses,
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

    with Farchive(args.db) as archive:
        if args.source_phrase_only:
            diagnostics: list[dict[str, Any]] = []
            witnesses = []
            for statute_id in ids:
                locator = f"{LEG_BASE}/{statute_id}/data.xml"
                witnesses.extend(
                    scan_repeal_semantics_source_phrase_xml(
                        statute_id,
                        archive.get(locator) or b"",
                        source_locator=locator,
                    )
                )
        else:
            diagnostics = []
            witnesses = list(
                scan_repeal_semantics_witnesses(
                    ids,
                    archive,
                    diagnostics_out=diagnostics,
                )
            )

    rows = [witness.to_dict() for witness in witnesses]
    if args.limit is not None:
        rows = rows[: args.limit]
    summary = {
        "n_statutes_scanned": len(ids),
        "n_witnesses": len(witnesses),
        "families": dict(Counter(w.family for w in witnesses)),
        "n_source_diagnostics": len(diagnostics),
        "witnesses": rows,
    }
    if args.include_diagnostics:
        summary["source_diagnostics"] = diagnostics
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--ids", nargs="+", help="explicit affected statute IDs")
    parser.add_argument("--ids-file", type=Path, help="newline-separated affected statute IDs")
    parser.add_argument("--sample", type=int, help="sample N current statutes from archive")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--classes", nargs="+", help="restrict --sample/--all to document classes")
    parser.add_argument("--all", action="store_true", help="scan all current statute IDs in archive")
    parser.add_argument("--limit", type=int, help="limit emitted witness rows")
    parser.add_argument("--include-diagnostics", action="store_true")
    parser.add_argument(
        "--source-phrase-only",
        action="store_true",
        help="scan source XML text directly for repeal/revival phrases without resolving effect rows",
    )
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    payload = run_scan(args)
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(REPO_ROOT / "src"))
    raise SystemExit(main())
