#!/usr/bin/env python3
"""Select the next currently-live Finland review queue from a snapshot CSV.

This script:
1. reads a snapshot CSV such as `.tmp/fi_golden_dataset_to_classify.csv`
2. skips statutes that already have a YAML in `notes/verified_finlex_divergences/`
3. runs `lawvm structural-review --dump <sid>` in parallel
4. emits only the rows whose dump is non-empty
5. stops once the requested number of live rows has been found

The output is the same CSV schema as the input, filtered to the next live rows.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path


def _load_existing_yaml_statutes(yaml_dir: Path) -> set[str]:
    statutes: set[str] = set()
    for fpath in sorted(yaml_dir.glob("*.yaml")):
        if fpath.name.startswith("_"):
            continue
        stem = fpath.stem
        if "_" not in stem:
            continue
        year, rest = stem.split("_", 1)
        statutes.add(f"{year}/{rest}")
    return statutes


def _read_candidate_rows(csv_path: Path, existing_yaml: set[str]) -> tuple[list[str], list[dict[str, str]]]:
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise SystemExit(f"CSV has no header: {csv_path}")
        rows = [
            row
            for row in reader
            if (row.get("statute_id") or "").strip()
            and (row.get("statute_id") or "").strip() not in existing_yaml
        ]
        return list(reader.fieldnames), rows


def _has_live_dump(statute_id: str, timeout_s: int) -> bool:
    result = subprocess.run(
        ["uv", "run", "lawvm", "structural-review", "--dump", statute_id],
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(f"{statute_id}: structural-review failed ({result.returncode}) {stderr}")
    return bool(result.stdout.strip())


def _write_rows(fieldnames: list[str], rows: list[dict[str, str]], out_path: str) -> None:
    if out_path == "-":
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        return

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input-csv",
        default=".tmp/fi_golden_dataset_to_classify.csv",
        help="Snapshot CSV to read",
    )
    parser.add_argument(
        "--yaml-dir",
        default="notes/verified_finlex_divergences",
        help="Directory containing per-statute YAML reviews",
    )
    parser.add_argument(
        "--output-csv",
        default="-",
        help="Filtered CSV output path, or '-' for stdout",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="Maximum parallel structural-review processes",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Stop after this many live statutes have been emitted",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=240,
        help="Per-statute timeout in seconds",
    )
    args = parser.parse_args()

    if args.limit <= 0:
        raise SystemExit("--limit must be positive")
    if args.workers <= 0:
        raise SystemExit("--workers must be positive")

    input_csv = Path(args.input_csv)
    yaml_dir = Path(args.yaml_dir)

    existing_yaml = _load_existing_yaml_statutes(yaml_dir)
    fieldnames, candidate_rows = _read_candidate_rows(input_csv, existing_yaml)

    selected: list[dict[str, str]] = []
    selected_ids: set[str] = set()
    next_index = 0
    max_in_flight = min(args.workers, len(candidate_rows))

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        in_flight: dict[Future[bool], tuple[int, dict[str, str]]] = {}

        while next_index < len(candidate_rows) and len(in_flight) < max_in_flight:
            row = candidate_rows[next_index]
            future = pool.submit(_has_live_dump, row["statute_id"], args.timeout)
            in_flight[future] = (next_index, row)
            next_index += 1

        while in_flight and len(selected) < args.limit:
            done, _ = wait(in_flight.keys(), return_when=FIRST_COMPLETED)
            for future in done:
                index, row = in_flight.pop(future)
                sid = row["statute_id"]
                try:
                    is_live = future.result()
                except Exception as exc:
                    print(f"[warn] {sid}: {exc}", file=sys.stderr)
                    is_live = False

                if is_live and sid not in selected_ids:
                    selected.append(row)
                    selected_ids.add(sid)
                    print(f"[live {len(selected)}/{args.limit}] {sid}", file=sys.stderr)
                    if len(selected) >= args.limit:
                        break

                if next_index < len(candidate_rows) and len(selected) < args.limit:
                    next_row = candidate_rows[next_index]
                    next_future = pool.submit(_has_live_dump, next_row["statute_id"], args.timeout)
                    in_flight[next_future] = (next_index, next_row)
                    next_index += 1

    _write_rows(fieldnames, selected, args.output_csv)
    print(
        f"[done] selected={len(selected)} scanned={next_index} yaml_skipped={len(existing_yaml)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
