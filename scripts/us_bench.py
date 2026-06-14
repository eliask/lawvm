#!/usr/bin/env python3
"""Thin runnable wrapper for the U.S. federal dry-run bench harness.

Equivalent to ``python -m lawvm.us_federal.bench``; provided so the bench is
discoverable under ``scripts/`` alongside the other corpus tools::

    python scripts/us_bench.py
    python scripts/us_bench.py --corpus us/bench/us_bench_corpus.csv --json
"""

from __future__ import annotations

from lawvm.us_federal.bench import main

if __name__ == "__main__":
    raise SystemExit(main())
