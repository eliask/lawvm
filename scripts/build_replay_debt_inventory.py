"""Generate a bounded replay-debt inventory from oracle check results."""
from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import UTC, datetime
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

DIAGNOSES = {
    "REPLAY_MISSING",
    "REPLAY_EXTRA",
    "UNKNOWN",
    "MISSING",
    "EXTRA",
}


def family_label(section: str) -> str:
    """Return a compact family label for a section identifier."""
    section = (section or "").strip()
    if re.fullmatch(r"\d+", section):
        return "numeric"
    if re.fullmatch(r"\d+[a-zA-Z]+", section):
        return "alpha-suffix"
    if re.fullmatch(r"\d+[a-zA-Z]?[-–]\d+[a-zA-Z]?", section):
        return "section-range"
    return "other"


def _to_int_or_zero(value: str | int | None) -> int:
    if value is None:
        return 0
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def load_inventory_rows(path: Path, diagnoses: Iterable[str] = DIAGNOSES) -> list[dict[str, Any]]:
    """Load and filter failing rows from an oracle results CSV."""
    selected: list[dict[str, Any]] = []
    diagnosis_set = set(diagnoses)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            diag = (row.get("diagnosis") or "").strip()
            if diag not in diagnosis_set:
                continue
            row = dict(row)
            row["family_label"] = family_label(row.get("section", ""))
            selected.append(row)
    return selected


def count_source_rows(path: Path) -> int:
    """Count total rows in the oracle check CSV (including non-failing rows)."""
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return sum(1 for _ in reader)


def build_summary(
    rows: list[dict[str, Any]],
    *,
    source_row_count: int,
    top_statutes: int = 25,
    head_per_statute: int = 5,
) -> dict[str, Any]:
    """Build a compact JSON-serializable replay debt summary."""
    by_statute = defaultdict(list)
    for row in rows:
        by_statute[row.get("statute_id", "")].append(row)

    known_blame_source = sum(1 for row in rows if (row.get("blame_source") or "").strip())
    top = sorted(
        ((sid, len(values)) for sid, values in by_statute.items()),
        key=lambda item: (-item[1], item[0]),
    )[:top_statutes]

    bounded_rows = []
    for statute_id, _count in top:
        statute_rows = by_statute[statute_id][:head_per_statute]
        bounded_rows.extend(statute_rows)

    diagnosis_counts = Counter(row["diagnosis"] for row in rows)
    failure_family_counts = Counter(row["family_label"] for row in rows)

    return {
        "generated_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_rows": source_row_count,
        "replay_tail_rows": len(rows),
        "known_blame_source": known_blame_source,
        "known_blame_source_rate": known_blame_source / len(rows) if rows else 0.0,
        "diagnosis_counts": dict(diagnosis_counts),
        "family_counts": dict(failure_family_counts),
        "top_statutes": [{"statute_id": sid, "rows": count} for sid, count in top],
        "bounded_inventory": [
            {
                "statute_id": row.get("statute_id"),
                "section": row.get("section"),
                "diagnosis": row.get("diagnosis"),
                "first_bad_amendment": row.get("blame_source") or "",
                "family_label": row["family_label"],
            }
            for row in bounded_rows
        ],
    }


def _markdown_text(summary: dict[str, Any], top_statutes: int) -> str:
    top = summary["top_statutes"]
    bounded = summary["bounded_inventory"]

    failure_class_summary = ", ".join(
        f"{k}: {v}" for k, v in sorted(summary["diagnosis_counts"].items(), key=lambda kv: (-kv[1], kv[0]))
    )

    lines = [
        "# Replay Debt Reduction Inventory (Generated)",
        "",
        f"> generated_at: {summary['generated_at']}",
        "",
        "| metric | value |",
        "| --- | ---: |",
        f"| rows in source file | {summary['source_rows']} |",
        f"| replay-tail rows | {summary['replay_tail_rows']} |",
        f"| known `blame_source` | {summary['known_blame_source']} |",
        f"| known `blame_source` rate | {_to_int_or_zero(summary['known_blame_source']) / summary['replay_tail_rows'] if summary['replay_tail_rows'] else 0.0:.1%} |",
        f"| dominant failure classes | {failure_class_summary} |",
        "",
        f"## Top {top_statutes} statutes by failing rows",
        "",
        "| statute_id | failing_rows |",
        "| --- | ---: |",
    ]
    lines.extend(f"| {row['statute_id']} | {row['rows']} |" for row in top)

    lines.extend(
        [
            "",
            "## Top statutes × bounded rows",
            "",
            "| statute_id | section | diagnosis | first_bad_amendment | family_label |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    lines.extend(
        f"| {row['statute_id']} | {row['section']} | {row['diagnosis']} | {row['first_bad_amendment']} | {row['family_label']} |"
        for row in bounded
    )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a bounded replay-debt inventory from oracle_check_results.csv"
    )
    parser.add_argument(
        "--input",
        default="oracle_check_results.csv",
        help="Path to oracle_check_results.csv",
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="Output format",
    )
    parser.add_argument(
        "--top-statutes",
        type=int,
        default=25,
        help="How many statute ids to include in top-statute ranking",
    )
    parser.add_argument(
        "--per-statute",
        type=int,
        default=5,
        help="How many rows to keep per statute in bounded inventory",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path; if omitted, writes to stdout",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    rows = load_inventory_rows(Path(args.input))
    source_row_count = count_source_rows(Path(args.input))
    summary = build_summary(
        rows,
        source_row_count=source_row_count,
        top_statutes=args.top_statutes,
        head_per_statute=args.per_statute,
    )

    if args.format == "json":
        payload = {"generated_with": "scripts/build_replay_debt_inventory.py", **summary}
        text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    else:
        text = _markdown_text(summary, top_statutes=args.top_statutes)

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
