#!/usr/bin/env python3
"""Estonia witness-attribution surface — standalone entrypoint.

The EE analog of the UK effect-witness audit. Runs ``replay_ee_to_pit`` over an
EE ``(base_id, oracle_id)`` pair (or a small corpus slice) and emits a
DETERMINISTIC JSON report mapping each compiled op's ``witness_rule_id`` back to
its source witness, the target address, and the operation family. Ops with no
``witness_rule_id`` are loudly tagged ``unattributed_witness_blind_spot``.

Read-only / diagnostic: reuses replay output, never edits the replay/compile
path. No timestamps, sorted keys — the same inputs yield byte-identical output.

Usage (from LawVM/ dir, archive linked via scripts/setup_worktree_links.sh):

    # Single pair
    uv run python scripts/ee_witness_attribution.py \
        --base 121042020059 --oracle 102122020006 --as-of 2024-01-01

    # Small corpus slice from data/estonia/bench_corpus.csv
    uv run python scripts/ee_witness_attribution.py \
        --corpus data/estonia/bench_corpus.csv --limit 5 --as-of 2024-01-01

    # Write the JSON to a file instead of stdout
    uv run python scripts/ee_witness_attribution.py \
        --base 121042020059 --oracle 102122020006 --out report.json
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lawvm.estonia.fetch import open_rt_archive
from lawvm.estonia.witness_attribution import build_ee_op_witness_attribution


def _pair_report(
    *,
    base_id: str,
    oracle_id: str,
    as_of: str,
    archive: Any,
) -> dict[str, Any]:
    surface = build_ee_op_witness_attribution(
        base_id,
        as_of,
        oracle_id=oracle_id or None,
        archive=archive,
    )
    return surface.to_jsonable()


def _load_corpus_pairs(corpus_path: Path, limit: int) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    with open(corpus_path, newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            base_id = (row.get("base_id") or "").strip()
            oracle_id = (row.get("oracle_id") or "").strip()
            if not base_id:
                continue
            pairs.append((base_id, oracle_id))
    # Deterministic order independent of CSV row order.
    pairs = sorted(set(pairs))
    if limit > 0:
        pairs = pairs[:limit]
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Estonia witness-attribution surface (deterministic JSON).",
    )
    parser.add_argument("--base", default="", help="Base act aktViide or XML path.")
    parser.add_argument("--oracle", default="", help="Oracle aktViide (optional).")
    parser.add_argument("--as-of", default="2024-01-01", help="PIT date YYYY-MM-DD.")
    parser.add_argument(
        "--corpus",
        default="",
        help="CSV with base_id,oracle_id columns for a corpus-slice run.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Max pairs to run from --corpus (default: 5; 0 = all).",
    )
    parser.add_argument("--out", default="", help="Write JSON here instead of stdout.")
    args = parser.parse_args()

    if not args.base and not args.corpus:
        parser.error("provide --base (single pair) or --corpus (slice)")

    archive = open_rt_archive()

    if args.corpus:
        pairs = _load_corpus_pairs(Path(args.corpus), args.limit)
        pair_reports = [
            _pair_report(
                base_id=base_id,
                oracle_id=oracle_id,
                as_of=args.as_of,
                archive=archive,
            )
            for base_id, oracle_id in pairs
        ]
        # Sort surfaces by (base_id, oracle_id) for byte-stable output.
        pair_reports.sort(key=lambda rep: (rep["base_id"], rep["oracle_id"]))
        report: dict[str, Any] = {
            "kind": "ee_witness_attribution_corpus",
            "as_of": args.as_of,
            "n_pairs": len(pair_reports),
            "n_ops_total": sum(rep["summary"]["n_ops"] for rep in pair_reports),
            "n_blind_spots_total": sum(
                rep["summary"]["n_blind_spots"] for rep in pair_reports
            ),
            "pairs": pair_reports,
        }
    else:
        report = {
            "kind": "ee_witness_attribution_pair",
            **_pair_report(
                base_id=args.base,
                oracle_id=args.oracle,
                as_of=args.as_of,
                archive=archive,
            ),
        }

    text = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
