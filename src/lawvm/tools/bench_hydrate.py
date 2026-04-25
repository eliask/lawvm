"""lawvm bench-hydrate — serially hydrate source/oracle cache for a corpus.

Purpose:
  - eliminate operational benchmark noise (HTTP 429, uncached PIT/source misses)
  - persist successful fetches and missing-source markers in SQLite before benches

This is intentionally serial and conservative.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import List

from lawvm.tools.bench import _warm_oracle, _warm_sources


def _lawvm_dir() -> Path:
    here = Path(__file__).resolve()
    return here.parent.parent.parent.parent


def _default_corpus_path() -> Path:
    return _lawvm_dir() / "data" / "finland" / "bench_pending.csv"


def _load_sids(path: Path) -> List[str]:
    sids: List[str] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue
            try:
                int(row[0])
            except ValueError:
                continue
            sids.append(row[1].strip())
    return sids


def main(args) -> None:
    corpus_path = Path(getattr(args, "corpus", "") or _default_corpus_path())
    passes = int(getattr(args, "passes", 3) or 3)

    sids = _load_sids(corpus_path)
    if not sids:
        raise SystemExit(f"ERROR: empty or unreadable corpus: {corpus_path}")

    print(f"Hydrating corpus: {corpus_path}  ({len(sids)} statutes)")
    total_source = 0
    total_oracle = 0

    for p in range(1, passes + 1):
        print(f"\nPass {p}/{passes}")
        n_source = _warm_sources(sids)
        n_oracle = _warm_oracle(sids, force=False)
        total_source += n_source
        total_oracle += n_oracle
        print(f"  pass source fetched: {n_source}")
        print(f"  pass oracle fetched: {n_oracle}")
        if n_source == 0 and n_oracle == 0:
            print("  no further cache progress — stopping early")
            break

    print("\nHydration summary:")
    print(f"  source fetched total: {total_source}")
    print(f"  oracle fetched total: {total_oracle}")

