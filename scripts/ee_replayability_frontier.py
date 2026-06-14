#!/usr/bin/env python3
"""Estonia replayability-frontier report — read-only diagnostic entrypoint.

For each ``(base_id, oracle_id)`` pair in an EE replayable-corpus CSV, this
classifies WHY the pair is or is not end-to-end replayable, using the existing
``replay_ee_to_pit`` signal (see
``lawvm.estonia.replayability_frontier``). It turns the bare replay-error count
into a typed, actionable frontier.

It is DIAGNOSTIC and read-only: it runs the existing replay over cached source
bytes and classifies the result. It changes no replay, source, residual, or
archive state.

Output is a DETERMINISTIC sorted JSON report (sorted keys/rows, no timestamps),
so two runs over the same archive + corpus diff empty.

Usage (from LawVM/ dir):
    export LAWVM_CANONICAL_DATA_ROOT=...   # archive must be linked
    uv run python scripts/ee_replayability_frontier.py            # full corpus
    uv run python scripts/ee_replayability_frontier.py --limit 25 # first 25 pairs
    uv run python scripts/ee_replayability_frontier.py --csv data/estonia/replayable_corpus.csv
    uv run python scripts/ee_replayability_frontier.py --output report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from lawvm.estonia.replayability_frontier import (  # noqa: E402
    ee_replayability_frontier_for_corpus,
    ee_replayability_states_to_report,
    read_ee_corpus_pairs,
)

_DEFAULT_CSV = _REPO_ROOT / "data" / "estonia" / "current_replayable_corpus.csv"
_DEFAULT_AS_OF_FALLBACK = "2026-03-24"


def _build_replay_pair_callable(archive: Any) -> Any:
    """Return a ``(base_id, oracle_id, oracle_effective) -> EEPitResult`` callable.

    The as-of date is the oracle terviktekst's own effective date (the same
    derivation ``ee_bench`` uses), falling back to the corpus CSV's
    ``oracle_effective`` and then to a fixed sentinel so the run stays
    deterministic when the oracle XML is unavailable.
    """
    from lawvm.estonia.fetch import extract_effective_date, fetch_rt_xml
    from lawvm.estonia.replay import replay_ee_to_pit

    def _replay_pair(base_id: str, oracle_id: str, oracle_effective: str) -> Any:
        as_of = ""
        try:
            oracle_xml = fetch_rt_xml(oracle_id, archive=archive)
            as_of = extract_effective_date(oracle_xml) or ""
        except Exception:
            as_of = ""
        if not as_of:
            as_of = oracle_effective or _DEFAULT_AS_OF_FALLBACK
        return replay_ee_to_pit(
            base_id,
            as_of=as_of,
            archive=archive,
            verbose=False,
            oracle_id=oracle_id,
        )

    return _replay_pair


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Estonia replayability-frontier diagnostic (read-only).",
    )
    parser.add_argument(
        "--csv",
        type=str,
        default=str(_DEFAULT_CSV),
        help="Path to a replayable-corpus CSV (default: current_replayable_corpus.csv).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Classify only the first N pairs (deterministic by base_id, oracle_id).",
    )
    parser.add_argument(
        "--db",
        type=str,
        default="",
        help="Path to the EE Riigi Teataja farchive (default: data/ee_riigiteataja.farchive).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Write the JSON report to this path instead of stdout.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"ERROR: corpus CSV not found: {csv_path}", file=sys.stderr)
        return 1

    pairs = read_ee_corpus_pairs(csv_path)

    from lawvm.estonia.fetch import open_rt_archive

    db_path = Path(args.db) if args.db else None
    archive = open_rt_archive(db_path, readonly=True)
    try:
        replay_pair = _build_replay_pair_callable(archive)
        states = ee_replayability_frontier_for_corpus(
            pairs,
            replay_pair=replay_pair,
            limit=args.limit,
        )
    finally:
        archive.close()

    report = ee_replayability_states_to_report(states)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text + "\n", encoding="utf-8")
        print(f"Written: {out_path}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
