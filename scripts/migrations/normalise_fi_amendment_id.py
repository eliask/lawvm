"""One-shot migration: normalise ``amendment_id`` on FI corrigendum
data files to the YEAR/NUM form (e.g. ``2002/1248``).

Background
----------
LawVM-FI internally uses YEAR/NUM as the address/amendment_mid form
(see ``_to_grafter_mid``). The Fi corrigendum data files imported from
external acquisition are mostly NUM/YEAR (``1248/2002``), forcing
callers to know two forms and dance around candidate-match. There's no
semantic reason to keep both — the canonical LawVM form should be
YEAR/NUM everywhere.

Files normalised:
  - corrigendum_official_fi.jsonl  (``amendment_id`` field)
  - corrigendum_sources_fi.jsonl   (``amendment_id`` field)
  - corrigendum_adjudications_fi.jsonl  (no amendment_id; stable_id
    encoded by sk.pdf name, untouched here)
  - corrigendum_misapplied_fi.jsonl (already YEAR/NUM from runtime)
  - corrigendum_retry_overlays_fi.jsonl (already YEAR/NUM from migration
    split step 1)

Idempotent: re-running on already-normalised files is a no-op.

Run:
    uv run python scripts/migrations/normalise_fi_amendment_id.py --in-place
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DATA_DIR = _REPO_ROOT / "data" / "finland"

# Patterns: NUM/YEAR (e.g. "1248/2002"). NUM is small (0-9999), YEAR is 1900-2100.
_AMEND_NUM_YEAR_RE = re.compile(r"^(\d{1,4})/(\d{4})$")


def _to_year_num(amendment_id: str) -> str:
    """Convert NUM/YEAR to YEAR/NUM. Idempotent on YEAR/NUM input."""
    aid = amendment_id.strip()
    m = _AMEND_NUM_YEAR_RE.match(aid)
    if not m:
        return aid
    num, year = m.group(1), m.group(2)
    nu = int(num)
    yr = int(year)
    # NUM/YEAR: num is small (1-9999 typical for amendments), year is 4-digit ~1900-2100
    # If first num > 1900 and second num < 2000, this is already YEAR/NUM → leave.
    if nu > 1900 and yr < 2000:
        return aid
    return f"{year}/{num}"


def _normalise_jsonl(path: Path) -> int:
    """Rewrite ``amendment_id`` field on JSONL rows to YEAR/NUM form."""
    if not path.exists():
        return 0
    rewritten = 0
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            aid = str(row.get("amendment_id") or "").strip()
            new_aid = _to_year_num(aid)
            if new_aid != aid:
                row["amendment_id"] = new_aid
                rewritten += 1
            rows.append(row)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=False))
            f.write("\n")
    return rewritten


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in-place", action="store_true", help="write to canonical paths")
    args = parser.parse_args(argv)

    if not args.in_place:
        print("dry run — pass --in-place to normalise", file=sys.stderr)

    targets = [
        "corrigendum_official_fi.jsonl",
        "corrigendum_sources_fi.jsonl",
        "corrigendum_misapplied_fi.jsonl",
        "corrigendum_retry_overlays_fi.jsonl",
    ]
    total = 0
    for name in targets:
        path = _DATA_DIR / name
        if not path.exists():
            print(f"  skip (missing): {path}", file=sys.stderr)
            continue
        if args.in_place:
            n = _normalise_jsonl(path)
        else:
            # Dry-run sample: count first 5 rows to evaluate impact
            rows = []
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
            n = 0
            for row in rows:
                old = str(row.get("amendment_id") or "").strip()
                if old != _to_year_num(old):
                    n += 1
        print(f"  {name}: {n} rows rewritten", file=sys.stderr)
        total += n
    print(f"total: {total} rows rewritten", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())