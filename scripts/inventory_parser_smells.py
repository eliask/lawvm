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
    Path("src/lawvm/finland/normalize.py"),
    Path("src/lawvm/finland/payload_normalize.py"),
    Path("src/lawvm/finland/johtolause/clause_patterns.py"),
    Path("src/lawvm/uk_legislation/nlp_parser.py"),
    Path("src/lawvm/uk_legislation/source_definition_fragments.py"),
    Path("src/lawvm/uk_legislation/text_selectors.py"),
    Path("src/lawvm/new_zealand/instruction_workqueue.py"),
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
    "bounded_wildcard_gap": (
        "Bounded wildcard gap needing semantic span ownership",
        r"\.\{[01],\d+\}\??",
    ),
    "regex_coverage_surface": (
        "Regex recognition coverage / skipped-span ownership surface",
        r"\b(RegexRecognitionCoverage|regex_recognition_coverage|coverage_status|ignored_spans)\b",
    ),
    "text_selector_sentinel": (
        "Stringly TEXT_* selector sentinel",
        r"\bTEXT_[A-Z0-9_]+",
    ),
}


_BOUND_COVERAGE_NEARBY_LINES = 80
_RE_PATTERN_OWNER_ASSIGNMENT = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:re\.(?:compile|finditer|search|match)|\()?"
)
_RE_FUNCTION_DEF = re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_RE_COVERAGE_FUNCTION = re.compile(r"^\s*def\s+\w*coverage\w*\(")
_RE_REGEX_COVERAGE_SENSOR_FIELD = re.compile(
    r"\b(regex_recognition_coverage|coverage_status|ignored_spans)\b"
)
_RE_NAMED_CAPTURE = re.compile(r"\?P<([A-Za-z_][A-Za-z0-9_]*)>")
_SEMANTIC_CAPTURE_NAMES = frozenset(
    {
        "anchor",
        "inserted",
        "items",
        "original",
        "payload",
        "replacement",
        "terms",
        "text",
    }
)
_COVERED_BOUNDED_WILDCARD_STATUSES = frozenset(
    {
        "coverage_function_reference",
        "nearby_coverage_surface",
    }
)


def _is_comment_only_line(line: str) -> bool:
    return line.lstrip().startswith("#")


def _bounded_wildcard_semantic_role(line: str) -> str:
    capture_names = frozenset(_RE_NAMED_CAPTURE.findall(line))
    if capture_names & _SEMANTIC_CAPTURE_NAMES:
        return "semantic_payload_capture"
    if "\\b" in line or re.search(
        r"\b(?:omit|insert|substitute|replace|repeal|kumot|muut|lisät)",
        line,
        re.I,
    ):
        return "drafting_classifier"
    return "unknown_pattern_bound"


def _bounded_wildcard_soundness_risk(
    *,
    coverage_status: str,
    semantic_role: str,
) -> str:
    if coverage_status in _COVERED_BOUNDED_WILDCARD_STATUSES:
        return "covered_by_regex_coverage_surface"
    if semantic_role == "semantic_payload_capture":
        return "needs_typed_coverage_or_grammar"
    if semantic_role == "drafting_classifier":
        return "needs_classifier_safety_review"
    return "needs_triage"


def _owner_symbol_for_line(lines: list[str], line_no: int) -> str:
    """Return the closest variable/table name owning a regex pattern line."""
    start = max(0, line_no - 40)
    for idx in range(line_no - 1, start - 1, -1):
        match = _RE_PATTERN_OWNER_ASSIGNMENT.search(lines[idx])
        if match:
            return match.group(1)
    return ""


def _owner_function_for_line(lines: list[str], line_no: int) -> str:
    """Return the nearest enclosing function name for a regex pattern line."""
    for idx in range(line_no - 1, -1, -1):
        match = _RE_FUNCTION_DEF.search(lines[idx])
        if match:
            return match.group(1)
    return ""


def _is_referenced_from_coverage_function(lines: list[str], owner_symbol: str) -> bool:
    if not owner_symbol:
        return False
    for idx, line in enumerate(lines):
        if owner_symbol not in line:
            continue
        window_start = max(0, idx - 40)
        if any(_RE_COVERAGE_FUNCTION.search(prev_line) for prev_line in lines[window_start:idx + 1]):
            return True
    return False


def _bounded_wildcard_grammar_family(
    *,
    owner_symbol: str,
    owner_function: str,
    snippet: str,
    semantic_role: str,
) -> str:
    """Coarse migration family for bounded wildcard recognizers.

    The label is intentionally advisory. It helps prioritize grammar extraction
    without claiming that a line-level regex scan has proven semantics.
    """

    haystack = f"{owner_symbol} {owner_function} {snippet}".lower()
    if semantic_role != "semantic_payload_capture":
        if "omit" in haystack or "repeal" in haystack:
            return "omission_classifier"
        if "insert" in haystack:
            return "insertion_classifier"
        return "lexical_or_classifier"
    if "definition" in haystack or "entr" in haystack:
        return "definition_entry_or_definition_body_instruction"
    if "step" in haystack:
        return "step_insert_instruction"
    if "bracket" in haystack or "parenthes" in haystack:
        return "bracket_or_parenthetical_text_selector_instruction"
    if "ordinal" in haystack or "anchor" in haystack:
        return "anchor_ordered_insert_instruction"
    if "at_end" in haystack or "at the end" in haystack:
        return "at_end_insert_instruction"
    if "omit" in haystack or "repeal" in haystack:
        return "omission_instruction"
    return "unclassified_semantic_payload_instruction"


def _annotate_bounded_wildcard_coverage(
    hits: list[dict[str, Any]],
    lines: list[str],
) -> None:
    coverage_lines = sorted({
        line_no
        for line_no, line in enumerate(lines, start=1)
        if _RE_REGEX_COVERAGE_SENSOR_FIELD.search(line)
    } | {
        int(hit["line"])
        for hit in hits
        if hit["category"] == "regex_coverage_surface"
    })

    for hit in hits:
        if hit["category"] != "bounded_wildcard_gap":
            continue
        line_no = int(hit["line"])
        semantic_role = _bounded_wildcard_semantic_role(str(hit.get("snippet") or ""))
        nearest_line = min(
            coverage_lines,
            key=lambda coverage_line: abs(coverage_line - line_no),
            default=None,
        )
        nearest_distance = (
            None
            if nearest_line is None
            else abs(int(nearest_line) - line_no)
        )
        owner_symbol = _owner_symbol_for_line(lines, line_no)
        owner_function = _owner_function_for_line(lines, line_no)
        referenced_from_coverage = _is_referenced_from_coverage_function(
            lines,
            owner_symbol,
        )

        if referenced_from_coverage:
            coverage_status = "coverage_function_reference"
        elif nearest_distance is not None and nearest_distance <= _BOUND_COVERAGE_NEARBY_LINES:
            coverage_status = "nearby_coverage_surface"
        elif nearest_line is not None:
            coverage_status = "file_level_coverage_surface"
        else:
            coverage_status = "missing_coverage_surface"
        soundness_risk = _bounded_wildcard_soundness_risk(
            coverage_status=coverage_status,
            semantic_role=semantic_role,
        )

        hit["coverage_sensor"] = {
            "status": coverage_status,
            "recognizer_name": owner_symbol,
            "owner_symbol": owner_symbol,
            "owner_function": owner_function,
            "semantic_role": semantic_role,
            "soundness_risk": soundness_risk,
            "grammar_family": _bounded_wildcard_grammar_family(
                owner_symbol=owner_symbol,
                owner_function=owner_function,
                snippet=str(hit.get("snippet") or ""),
                semantic_role=semantic_role,
            ),
            "nearest_coverage_line": nearest_line,
            "nearest_coverage_distance": nearest_distance,
            "nearby_line_window": _BOUND_COVERAGE_NEARBY_LINES,
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
                if key == "bounded_wildcard_gap" and _is_comment_only_line(line):
                    continue
                hits.append(
                    {
                        "category": key,
                        "label": label,
                        "line": line_no,
                        "snippet": line.strip(),
                    }
                )

    hits.sort(key=lambda hit: (hit["category"], hit["line"]))
    _annotate_bounded_wildcard_coverage(hits, text)
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

    bounded_wildcard_coverage_status_counts: Counter[str] = Counter(
        str(hit.get("coverage_sensor", {}).get("status") or "not_applicable")
        for hits in by_file.values()
        for hit in hits
        if hit["category"] == "bounded_wildcard_gap"
    )
    bounded_wildcard_semantic_role_counts: Counter[str] = Counter(
        str(hit.get("coverage_sensor", {}).get("semantic_role") or "unknown_pattern_bound")
        for hits in by_file.values()
        for hit in hits
        if hit["category"] == "bounded_wildcard_gap"
    )
    bounded_wildcard_soundness_risk_counts: Counter[str] = Counter(
        str(hit.get("coverage_sensor", {}).get("soundness_risk") or "needs_triage")
        for hits in by_file.values()
        for hit in hits
        if hit["category"] == "bounded_wildcard_gap"
    )
    for status in (
        "coverage_function_reference",
        "file_level_coverage_surface",
        "missing_coverage_surface",
        "nearby_coverage_surface",
    ):
        bounded_wildcard_coverage_status_counts.setdefault(status, 0)
    for role in (
        "drafting_classifier",
        "semantic_payload_capture",
        "unknown_pattern_bound",
    ):
        bounded_wildcard_semantic_role_counts.setdefault(role, 0)
    for risk in (
        "covered_by_regex_coverage_surface",
        "needs_classifier_safety_review",
        "needs_triage",
        "needs_typed_coverage_or_grammar",
    ):
        bounded_wildcard_soundness_risk_counts.setdefault(risk, 0)

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
            "bounded_wildcard_coverage_status_counts": dict(
                sorted(bounded_wildcard_coverage_status_counts.items())
            ),
            "bounded_wildcard_semantic_role_counts": dict(
                sorted(bounded_wildcard_semantic_role_counts.items())
            ),
            "bounded_wildcard_soundness_risk_counts": dict(
                sorted(bounded_wildcard_soundness_risk_counts.items())
            ),
            "bounded_wildcard_soundness_note": (
                "bounded wildcard regexes are recognizer-local span claims, not semantic "
                "exhaustiveness proofs; coverage sensors must still classify captured "
                "semantic slots, skipped context, and unclassified gaps"
            ),
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

    coverage_status_counts = summary.get("bounded_wildcard_coverage_status_counts") or {}
    if coverage_status_counts:
        lines.extend(
            [
                "",
                "| Bounded Wildcard Coverage Status | Count |",
                "| --- | ---: |",
            ]
        )
        for status, count in sorted(coverage_status_counts.items()):
            lines.append(f"| {status} | {count} |")
        lines.extend(
            [
                "",
                "> Bounded wildcard note: a bounded regex span is not a semantic "
                "exhaustiveness proof. Coverage means the recognizer exposes owned "
                "semantic slots, skipped context, or unclassified gaps for review.",
            ]
        )

    semantic_role_counts = summary.get("bounded_wildcard_semantic_role_counts") or {}
    if semantic_role_counts:
        lines.extend(
            [
                "",
                "| Bounded Wildcard Semantic Role | Count |",
                "| --- | ---: |",
            ]
        )
        for role, count in sorted(semantic_role_counts.items()):
            lines.append(f"| {role} | {count} |")

    soundness_risk_counts = summary.get("bounded_wildcard_soundness_risk_counts") or {}
    if soundness_risk_counts:
        lines.extend(
            [
                "",
                "| Bounded Wildcard Soundness Risk | Count |",
                "| --- | ---: |",
            ]
        )
        for risk, count in sorted(soundness_risk_counts.items()):
            lines.append(f"| {risk} | {count} |")

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
            coverage_sensor = hit.get("coverage_sensor")
            if isinstance(coverage_sensor, dict):
                status = str(coverage_sensor.get("status") or "")
                recognizer = str(coverage_sensor.get("recognizer_name") or "")
                owner_function = str(coverage_sensor.get("owner_function") or "")
                role = str(coverage_sensor.get("semantic_role") or "")
                risk = str(coverage_sensor.get("soundness_risk") or "")
                family = str(coverage_sensor.get("grammar_family") or "")
                if status:
                    suffix = f" [coverage={status}"
                    if recognizer:
                        suffix += f", recognizer={recognizer}"
                    if owner_function:
                        suffix += f", function={owner_function}"
                    if role:
                        suffix += f", role={role}"
                    if risk:
                        suffix += f", risk={risk}"
                    if family:
                        suffix += f", family={family}"
                    suffix += "]"
                    snippet = f"{snippet}{suffix}"
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
        "Known values: "
        + ", ".join(sorted(SMELL_MARKERS))
        + ".",
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
