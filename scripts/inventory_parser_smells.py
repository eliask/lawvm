"""Generate a bounded parser-smell inventory from source files.

The script is intentionally mechanical: it only reports explicit pattern hits that
are known to indicate fallback- or heuristic-heavy parser behavior.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from collections.abc import Iterable


DEFAULT_FILES = (
    Path("src/lawvm/finland/grafter.py"),
    Path("src/lawvm/finland/payload_normalize.py"),
    Path("src/lawvm/finland/johtolause/clause_patterns.py"),
)

SMELL_MARKERS = {
    "fallback_heuristics": (
        "Fallback-path handling",
        r"(?i)fallback",
    ),
    "clause_modifier_filter": (
        "Clause modifier / marker filtering",
        r"(?i)\b(clause_modifier_blacklist|blacklist)\b",
    ),
    "row_target_normalization": (
        "Row/target normalization fallback",
        r"(?i)\b(continuation_row_subsections|parse_ops_fallback_heuristic|allows_omission_expansion|_sec1_fallback_peg_skip_required|_collapse_intro_list_subsections_inside_section_ir)\b",
    ),
    "regex_structural_heuristic": (
        "Regex-driven structural heuristics",
        r"(?i)\bre\.(match|search|findall|finditer|sub|subn|split|compile)\(",
    ),
}


def _collect_hits(path: Path, markers: dict[str, tuple[str, str]]) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").splitlines()
    hits: list[dict[str, Any]] = []
    compiled = {
        key: (label, re.compile(pattern))
        for key, (label, pattern) in markers.items()
    }

    for line_no, line in enumerate(text, start=1):
        for key, (label, regex) in compiled.items():
            if regex.search(line):
                hits.append(
                    {
                        "category": key,
                        "label": label,
                        "line": line_no,
                        "snippet": line.strip(),
                    }
                )

    hits.sort(key=lambda hit: (hit["category"], hit["line"]))
    return hits


def build_inventory(
    file_paths: Iterable[Path],
    markers: dict[str, tuple[str, str]] | None = None,
    *,
    categories: set[str] | None = None,
    marker_filter: str | None = None,
) -> dict[str, Any]:
    marker_map = dict(SMELL_MARKERS if markers is None else markers)
    if categories is not None:
        marker_map = {
            category: (label, pattern)
            for category, (label, pattern) in marker_map.items()
            if category in categories
        }
    marker_regex = (
        re.compile(marker_filter, re.IGNORECASE)
        if marker_filter is not None
        else None
    )
    by_file: dict[str, list[dict[str, Any]]] = {}
    category_totals: Counter[str] = Counter()
    file_totals: Counter[str] = Counter()

    for path in sorted(file_paths, key=lambda p: str(p)):
        if not path.exists():
            continue
        hits = _collect_hits(path, marker_map)
        if marker_regex is not None:
            hits = [
                hit
                for hit in hits
                if marker_regex.search(hit["snippet"]) or marker_regex.search(hit["label"])
            ]
        by_file[str(path)] = hits
        file_totals[str(path)] = len(hits)
        category_totals.update(hit["category"] for hit in hits)

    for category in marker_map:
        category_totals.setdefault(category, 0)

    generated_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    category_count = len(marker_map)
    return {
        "generated_with": "scripts/inventory_parser_smells.py",
        "generated_at": generated_at,
        "hit_count": sum(file_totals.values()),
        "summary": {
            "file_count": len(file_totals),
            "category_count": category_count,
            "filtered_category_count": len(marker_map),
            "hit_count": sum(file_totals.values()),
        },
        "file_counts": dict(sorted(file_totals.items())),
        "category_counts": dict(sorted(category_totals.items())),
        "by_file": by_file,
    }


def _to_markdown(inventory: dict[str, Any]) -> str:
    summary = inventory["summary"]
    lines = [
        "# Parser Smell Inventory (Generated)",
        "",
        f"> generated_at: {inventory['generated_at']}",
        "",
        "| metric | value |",
        "| --- | ---: |",
        f"| files | {summary['file_count']} |",
        f"| categories | {summary['category_count']} |",
        f"| filtered_categories | {summary['filtered_category_count']} |",
        f"| hits | {summary['hit_count']} |",
        "",
        f"Total hit rows: {inventory['hit_count']}",
        "",
        "| File | Hits |",
        "| --- | ---: |",
    ]
    for file_path, hit_count in inventory["file_counts"].items():
        lines.append(f"| {file_path} | {hit_count} |")

    lines.extend(
        [
            "",
            "| Category | Count |",
            "| --- | ---: |",
        ]
    )
    for category, count in inventory["category_counts"].items():
        lines.append(f"| {category} | {count} |")

    for path, hits in inventory["by_file"].items():
        lines.extend(
            [
                "",
                f"## {path}",
                "",
                "| Line | Category | Label | Snippet |",
                "| --- | --- | --- | --- |",
            ]
        )
        if not hits:
            lines.append("| n/a | no smells | n/a | no matching lines |")
            continue
        for hit in hits:
            snippet = hit["snippet"].replace("|", "\\|")
            lines.append(
                f"| {hit['line']} | {hit['category']} | {hit['label']} | {snippet} |"
            )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate parser smell inventory from known heuristic patterns."
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="Output format",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path; if omitted, prints to stdout",
    )
    parser.add_argument(
        "--category",
        action="append",
        default=None,
        help="Optional category filter (repeatable). "
        "Known values: fallback_heuristics, clause_modifier_filter, "
        "row_target_normalization, regex_structural_heuristic.",
    )
    parser.add_argument(
        "--marker",
        default=None,
        help="Optional substring/regex filter over marker snippet/label.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        default=DEFAULT_FILES,
        help="Files to scan; defaults to key Finland parser files",
    )
    return parser


# Backward-compatible alias retained for external callers and tests.
_build_parser = build_parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    categories = None if args.category is None else {category.strip() for category in args.category}
    if categories is not None:
        unknown = categories - set(SMELL_MARKERS)
        if unknown:
            raise SystemExit(f"Unknown categories: {', '.join(sorted(unknown))}")

    try:
        inventory = build_inventory(
            args.files,
            categories=categories,
            marker_filter=args.marker,
        )
    except re.error as exc:
        raise SystemExit(f"Invalid marker regex: {exc}") from exc

    if args.format == "json":
        text = json.dumps(inventory, indent=2, sort_keys=True, ensure_ascii=False)
    else:
        text = _to_markdown(inventory)

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
