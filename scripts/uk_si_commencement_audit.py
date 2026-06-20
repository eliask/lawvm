#!/usr/bin/env python3
"""Audit UK statutory-instrument commencement-metadata state per affecting SI.

This is a DIAGNOSTIC, READ-ONLY surface. For each affected statute it classifies
every affecting statutory instrument's commencement-metadata state into a typed
taxonomy (resolved_in_force / multiple_commencement_dates / textual_only_
commencement / no_made_date / made_date_default_candidate_but_unproved /
prospective_unresolved / source_unavailable / source_parse_error) with reason
tags. It reuses the replay-path commencement-metadata extractor verbatim and
does NOT change commencement resolution, in-force filtering, or PIT selection.

Output JSON is deterministically ordered (statutes and states sorted; no
timestamps in the body).
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

    with Farchive(db_path, readonly=True) as archive:
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
    from lawvm.uk_legislation.si_commencement_audit import (
        audit_si_commencement_for_statute,
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
    ids = sorted(dict.fromkeys(ids))
    if not ids:
        raise SystemExit("pass --ids, --ids-file, --sample, or --all")

    diagnostics: list[dict[str, Any]] = []
    statute_payloads: list[dict[str, Any]] = []
    overall_state_counts: Counter[str] = Counter()
    n_affecting_si = 0
    with Farchive(args.db, readonly=True) as archive:
        for statute_id in ids:
            audit = audit_si_commencement_for_statute(
                statute_id,
                archive,
                as_of=args.as_of,
                diagnostics_out=diagnostics,
            )
            for state in audit.states:
                overall_state_counts[state.state] += 1
            n_affecting_si += len(audit.states)
            payload = audit.to_dict()
            if args.limit is not None:
                payload["states"] = payload["states"][: args.limit]
            statute_payloads.append(payload)

    return {
        "as_of": args.as_of,
        "n_statutes_scanned": len(ids),
        "n_affecting_si": n_affecting_si,
        "state_counts": dict(sorted(overall_state_counts.items())),
        "n_source_diagnostics": len(diagnostics),
        "statutes": statute_payloads,
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
    parser.add_argument("--as-of", default="2026-05-31", help="PIT date for prospective lookup")
    parser.add_argument("--limit", type=int, help="limit emitted state rows per statute")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    payload = run_scan(args)
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(REPO_ROOT / "src"))
    raise SystemExit(main())
