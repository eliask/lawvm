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


def _selected_source_audit_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    audited_rows = [row for row in rows if "selected_source_matches_phrase" in row]
    if not audited_rows:
        return {}
    matches = [
        row
        for row in audited_rows
        if row.get("selected_source_matches_phrase") is True
    ]
    no_revive_matches = [
        row
        for row in matches
        if row.get("source_phrase_family") == "repeal_of_repeal_no_revive_phrase"
    ]
    return {
        "n_selected_source_audited_candidates": len(audited_rows),
        "n_selected_source_phrase_matches": len(matches),
        "n_selected_source_unproved_candidates": len(audited_rows) - len(matches),
        "n_no_revive_selected_source_phrase_matches": len(no_revive_matches),
        "selected_source_match_statuses": dict(
            Counter(str(row.get("selected_source_matches_phrase")) for row in audited_rows)
        ),
        "selected_source_statuses": dict(
            Counter(str(row.get("selected_source_status") or "") for row in audited_rows)
        ),
        "selected_source_tags": dict(
            Counter(str(row.get("selected_source_tag") or "") for row in audited_rows)
        ),
        "selected_source_phrase_families": dict(
            Counter(str(row.get("selected_source_phrase_family") or "") for row in audited_rows)
        ),
    }


def _owner_phase_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    from lawvm.uk_legislation.phase_discipline import uk_phase_owner_for_diagnostic

    return dict(
        sorted(
            Counter(uk_phase_owner_for_diagnostic(row) for row in rows).items()
        )
    )


def run_scan(args: argparse.Namespace) -> dict[str, Any]:
    from farchive import Farchive
    from lawvm.uk_legislation.repeal_semantics_witnesses import (
        scan_repeal_semantics_affecting_act_phrase_candidates,
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

    phrase_ids_scanned = 0
    phrase_witness_act_count = 0
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
        elif args.source_phrase_effect_candidates:
            diagnostics = []
            phrase_ids: list[str] = []
            if args.phrase_ids:
                phrase_ids.extend(args.phrase_ids)
            if args.phrase_ids_file:
                phrase_ids.extend(ids_from_file(args.phrase_ids_file))
            if args.phrase_all:
                phrase_ids.extend(statute_ids_from_archive(args.db, classes=args.classes))
            phrase_ids = list(dict.fromkeys(phrase_ids or ids))
            phrase_ids_scanned = len(phrase_ids)
            phrase_witnesses_by_act: dict[str, tuple[Any, ...]] = {}
            for statute_id in phrase_ids:
                locator = f"{LEG_BASE}/{statute_id}/data.xml"
                phrase_witnesses = scan_repeal_semantics_source_phrase_xml(
                    statute_id,
                    archive.get(locator) or b"",
                    source_locator=locator,
                )
                if phrase_witnesses:
                    phrase_witnesses_by_act[statute_id] = phrase_witnesses
            phrase_witness_act_count = len(phrase_witnesses_by_act)
            witnesses = list(
                scan_repeal_semantics_affecting_act_phrase_candidates(
                    ids,
                    archive,
                    phrase_witnesses_by_act=phrase_witnesses_by_act,
                    audit_selected_source=args.audit_selected_source,
                    diagnostics_out=diagnostics,
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

    all_rows = [witness.to_dict() for witness in witnesses]
    audit_summary = _selected_source_audit_summary(all_rows)
    rows = all_rows
    if args.limit is not None:
        rows = rows[: args.limit]
    summary = {
        "n_statutes_scanned": len(ids),
        "n_witnesses": len(witnesses),
        "families": dict(Counter(w.family for w in witnesses)),
        "owner_phase_counts": _owner_phase_counts(all_rows),
        "n_source_diagnostics": len(diagnostics),
        "witnesses": rows,
    }
    if args.source_phrase_effect_candidates:
        summary["n_phrase_statutes_scanned"] = phrase_ids_scanned
        summary["n_phrase_witness_acts"] = phrase_witness_act_count
        summary.update(audit_summary)
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
    parser.add_argument(
        "--source-phrase-effect-candidates",
        action="store_true",
        help=(
            "scan source XML for phrase-bearing Acts, then link those Acts to "
            "repeal-family effect rows without selected-source extraction"
        ),
    )
    parser.add_argument(
        "--phrase-all",
        action="store_true",
        help=(
            "with --source-phrase-effect-candidates, scan all current XML for "
            "phrase-bearing Acts while using --ids/--ids-file/--sample/--all as "
            "the affected-effect scope"
        ),
    )
    parser.add_argument(
        "--phrase-ids",
        nargs="+",
        help="with --source-phrase-effect-candidates, explicit phrase-source statute IDs",
    )
    parser.add_argument(
        "--phrase-ids-file",
        type=Path,
        help="with --source-phrase-effect-candidates, phrase-source statute IDs file",
    )
    parser.add_argument(
        "--audit-selected-source",
        action="store_true",
        help=(
            "with --source-phrase-effect-candidates, also resolve each candidate's "
            "selected source provision and report whether that selected source "
            "contains a repeal/revival phrase"
        ),
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
