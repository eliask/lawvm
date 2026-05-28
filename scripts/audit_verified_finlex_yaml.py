#!/usr/bin/env python3
"""Audit verified_finlex_divergences YAML coverage against the publication DB.

Checks:
1. Statutes from the queue snapshot (`error_count > 0`) but no YAML file.
2. YAML files whose statute is not present in the queue snapshot.

This is intentionally separate from the publication DB builder because the
current backlog is large; we want an explicit audit/reporting command rather
than silently enforcing repo-wide failure in unrelated workflows.

Important: the publication DB is only a queue snapshot. It is NOT the
authoritative current source for a statute's live divergence section set.
Live section-set reassessment must be done from current lawvm CLI outputs.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _stem_to_statute_id(path: Path) -> str:
    stem = path.stem
    year, rest = stem.split("_", 1)
    return f"{year}/{rest}"


@dataclass(frozen=True)
class YamlReview:
    statute_id: str
    path: Path
    section_paths: frozenset[str]
    raw: dict[str, Any]


def _load_yaml_reviews(yaml_dir: Path) -> dict[str, YamlReview]:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - environment issue
        raise SystemExit(f"pyyaml required for audit: {exc}") from exc

    reviews: dict[str, YamlReview] = {}
    for fpath in sorted(yaml_dir.glob("*.yaml")):
        if fpath.name.startswith("_"):
            continue
        data = yaml.safe_load(fpath.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        sid = str(data.get("statute_id") or "").strip() or _stem_to_statute_id(fpath)
        sections = data.get("sections") or []
        section_paths = frozenset(
            str(sec.get("path") or "").strip()
            for sec in sections
            if isinstance(sec, dict) and str(sec.get("path") or "").strip()
        )
        reviews[sid] = YamlReview(
            statute_id=sid,
            path=fpath,
            section_paths=section_paths,
            raw=data,
        )
    return reviews


def _db_error_statutes(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT statute_id, title, error_count, ready_artifact_count,
               primary_proof_tier, error_families, error_family_counts,
               consolidated_url, is_repealed
        FROM statutes
        WHERE COALESCE(error_count, 0) > 0
        ORDER BY error_count DESC, statute_sort_key ASC
        """
    ).fetchall()
    return {str(r["statute_id"]): r for r in rows}


def _candidate_db_paths(configured: str) -> tuple[Path, ...]:
    primary = Path(configured)
    candidates: list[Path] = [primary]
    if primary.name == "finlex_errors_publication.db":
        local_default = Path(".tmp/finlex_errors_publication.db")
        if local_default not in candidates:
            candidates.append(local_default)
    return tuple(candidates)


def _resolve_db_path(configured: str) -> Path:
    for candidate in _candidate_db_paths(configured):
        if candidate.exists():
            return candidate
    attempted = ", ".join(str(path) for path in _candidate_db_paths(configured))
    raise SystemExit(f"publication db not found; tried: {attempted}")


def _write_missing_csv(
    path: Path,
    missing_ids: list[str],
    error_rows: dict[str, sqlite3.Row],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "statute_id",
                "title",
                "error_count",
                "ready_artifact_count",
                "primary_proof_tier",
                "error_families",
                "error_family_counts",
                "consolidated_url",
                "is_repealed",
            ]
        )
        for sid in missing_ids:
            row = error_rows[sid]
            writer.writerow(
                [
                    row["statute_id"],
                    row["title"],
                    row["error_count"],
                    row["ready_artifact_count"],
                    row["primary_proof_tier"],
                    row["error_families"],
                    row["error_family_counts"],
                    row["consolidated_url"],
                    row["is_repealed"],
                ]
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        default=".tmp/finlex_errors_publication.db",
        help="Path to finlex_errors_publication.db",
    )
    parser.add_argument(
        "--yaml-dir",
        default="notes/verified_finlex_divergences",
        help="Directory containing per-statute YAML files",
    )
    parser.add_argument(
        "--write-missing-csv",
        default="",
        help="Optional path to write the current missing-YAML snapshot CSV",
    )
    parser.add_argument(
        "--show-limit",
        type=int,
        default=50,
        help="How many ids per category to print",
    )
    args = parser.parse_args()

    yaml_dir = Path(args.yaml_dir)
    db_path = _resolve_db_path(args.db)

    reviews = _load_yaml_reviews(yaml_dir)
    conn = sqlite3.connect(db_path)
    try:
        error_rows = _db_error_statutes(conn)
    finally:
        conn.close()

    db_ids = set(error_rows)
    yaml_ids = set(reviews)

    missing_yaml = sorted(db_ids - yaml_ids)
    extra_yaml = sorted(yaml_ids - db_ids)

    print("=== verified_finlex_divergences audit ===")
    print(f"db_error_statutes : {len(db_ids)}")
    print(f"yaml_files        : {len(yaml_ids)}")
    print(f"missing_yaml      : {len(missing_yaml)}")
    print(f"extra_yaml        : {len(extra_yaml)}")

    limit = max(0, args.show_limit)
    if missing_yaml:
        print("\n[missing yaml]")
        for sid in missing_yaml[:limit]:
            row = error_rows[sid]
            print(f"{sid} | errors={row['error_count']} | {row['title']}")
    if extra_yaml:
        print("\n[extra yaml]")
        for sid in extra_yaml[:limit]:
            print(f"{sid} | {reviews[sid].path}")

    if args.write_missing_csv:
        out_path = Path(args.write_missing_csv)
        _write_missing_csv(out_path, missing_yaml, error_rows)
        print(f"\nmissing_csv       : {out_path}")

    if missing_yaml or extra_yaml:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
