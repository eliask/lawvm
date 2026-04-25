"""Inventory replay-facing adjudication kinds from replay pipeline source files."""
from __future__ import annotations

import argparse
import ast
import json
import re
from datetime import UTC, datetime
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

REPLAY_SOURCE_FILES = {
    "EU": "src/lawvm/eu/pipeline.py",
    "EE": "src/lawvm/estonia/grafter.py",
    "NO": "src/lawvm/norway/grafter.py",
    "SE": "src/lawvm/sweden/grafter.py",
    "UK": "src/lawvm/uk_legislation/uk_amendment_replay.py",
    "FI": "src/lawvm/finland/grafter.py",
}


def _is_replay_family_kind(kind: str) -> bool:
    return kind.startswith("replay_") or re.match(r"^[a-z]+_replay_", kind) is not None


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected positive integer, got {raw!r}") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError(f"min-count must be a positive integer, got {value}")
    return value


@dataclass(frozen=True)
class AdjudicationSite:
    line: int
    function: str | None


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_relevant_call_node(node: ast.AST, *, include_wrappers: bool) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name) and node.func.id == "CompileAdjudication":
        return True
    if isinstance(node.func, ast.Attribute) and node.func.attr == "CompileAdjudication":
        return True
    if not include_wrappers:
        return False
    if isinstance(node.func, ast.Name) and "adjudication" in node.func.id:
        return True
    if isinstance(node.func, ast.Attribute) and "adjudication" in node.func.attr:
        return True
    return False


def _find_kind_keyword(node: ast.Call) -> str | None:
    for keyword in node.keywords:
        if keyword.arg != "kind":
            continue
        return _literal_string(keyword.value)
    return None


class _AdjudicationVisitor(ast.NodeVisitor):
    def __init__(self, *, include_wrappers: bool) -> None:
        self._function_stack: list[str] = []
        self.include_wrappers = include_wrappers
        self.by_kind: defaultdict[str, list[AdjudicationSite]] = defaultdict(list)

    @property
    def function_name(self) -> str | None:
        return self._function_stack[-1] if self._function_stack else None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._function_stack.append(node.name)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if _is_relevant_call_node(node, include_wrappers=self.include_wrappers):
            kind = _find_kind_keyword(node)
            if kind is not None:
                self.by_kind[kind].append(AdjudicationSite(node.lineno, self.function_name))
        self.generic_visit(node)


def collect_adjudication_kinds(path: Path, *, include_wrappers: bool = False) -> dict[str, list[AdjudicationSite]]:
    """
    Parse a jurisdiction source file and collect `kind=` values on adjudication calls.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    visitor = _AdjudicationVisitor(include_wrappers=include_wrappers)
    visitor.visit(tree)
    return {kind: sorted(sites, key=lambda site: site.line) for kind, sites in visitor.by_kind.items()}


def build_inventory(
    files: dict[str, str],
    *,
    root: Path,
    jurisdictions: set[str] | None = None,
    include_wrappers: bool = True,
    replay_only: bool = False,
    kind_filter: str | None = None,
    min_count: int | None = None,
) -> dict[str, dict[str, list[AdjudicationSite]]]:
    """
    Build an inventory map from jurisdiction name to `{kind: [sites...]}`.
    """
    inventory: dict[str, dict[str, list[AdjudicationSite]]] = {}
    if min_count is not None and min_count <= 0:
        raise ValueError("min_count must be a positive integer")
    selected_files = files
    if jurisdictions is not None:
        selected_files = {jurisdiction: path for jurisdiction, path in files.items() if jurisdiction in jurisdictions}
    for jurisdiction, relative_path in selected_files.items():
        path = (root / relative_path).resolve()
        kind_sites = collect_adjudication_kinds(path, include_wrappers=include_wrappers)
        if replay_only:
            kind_sites = {
                kind: sites
                for kind, sites in kind_sites.items()
                if _is_replay_family_kind(kind)
            }
        if kind_filter:
            kind_sites = {
                kind: sites
                for kind, sites in kind_sites.items()
                if kind_filter in kind
            }
        if min_count is not None:
            kind_sites = {
                kind: sites
                for kind, sites in kind_sites.items()
                if len(sites) >= min_count
            }
        inventory[jurisdiction] = kind_sites
    return inventory


def _summarize_inventory(inventory: dict[str, dict[str, list[AdjudicationSite]]]) -> dict[str, int]:
    total_kinds = sum(len(kinds) for kinds in inventory.values())
    total_adjudications = sum(len(sites) for kinds in inventory.values() for sites in kinds.values())
    return {
        "jurisdiction_count": len(inventory),
        "kind_count": total_kinds,
        "adjudication_count": total_adjudications,
    }


def build_surface_comparison(
    files: dict[str, str],
    *,
    root: Path,
    jurisdictions: set[str] | None = None,
    replay_only: bool = False,
    kind_filter: str | None = None,
    min_count: int | None = None,
) -> dict[str, dict[str, object]]:
    wrapper_inventory = build_inventory(
        files,
        root=root,
        jurisdictions=jurisdictions,
        include_wrappers=True,
        replay_only=replay_only,
        kind_filter=kind_filter,
        min_count=min_count,
    )
    direct_inventory = build_inventory(
        files,
        root=root,
        jurisdictions=jurisdictions,
        include_wrappers=False,
        replay_only=replay_only,
        kind_filter=kind_filter,
        min_count=min_count,
    )
    comparison: dict[str, dict[str, object]] = {}
    for jurisdiction in sorted(wrapper_inventory):
        wrapper_kinds = wrapper_inventory[jurisdiction]
        direct_kinds = direct_inventory.get(jurisdiction, {})
        wrapper_only = {
            kind: sites
            for kind, sites in wrapper_kinds.items()
            if kind not in direct_kinds
        }
        comparison[jurisdiction] = {
            "wrapper_kind_count": len(wrapper_kinds),
            "wrapper_adjudication_count": sum(len(sites) for sites in wrapper_kinds.values()),
            "direct_kind_count": len(direct_kinds),
            "direct_adjudication_count": sum(len(sites) for sites in direct_kinds.values()),
            "wrapper_only_kind_count": len(wrapper_only),
            "wrapper_only_adjudication_count": sum(len(sites) for sites in wrapper_only.values()),
            "wrapper_only_kinds": sorted(wrapper_only),
        }
    return comparison


def _format_markdown(inventory: dict[str, dict[str, list[AdjudicationSite]]]) -> str:
    summary = _summarize_inventory(inventory)
    generated_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [
        "# Cross-Jurisdiction Adjudication Kind Inventory",
        "",
        f"> generated_at: {generated_at}",
        "",
        "| jurisdiction | kind_count | adjudication_count |",
        "| --- | ---: | ---: |",
    ]
    for jurisdiction, kind_sites in sorted(inventory.items()):
        rows.append(
            f"| {jurisdiction} | {len(kind_sites)} | {sum(len(sites) for sites in kind_sites.values())} |"
        )
    rows.extend(
        [
            "",
            "| metric | value |",
            "| --- | ---: |",
            f"| jurisdictions | {summary['jurisdiction_count']} |",
            f"| kinds | {summary['kind_count']} |",
            f"| adjudications | {summary['adjudication_count']} |",
            "",
        ]
    )
    for jurisdiction, kind_sites in sorted(inventory.items()):
        rows.append(f"## {jurisdiction}")
        rows.append("")
        rows.append("| kind | count | sample_lines |")
        rows.append("| --- | ---: | --- |")
        for kind in sorted(kind_sites):
            sites = kind_sites[kind]
            sample_lines = ", ".join(str(site.line) for site in sites[:3])
            rows.append(f"| {kind} | {len(sites)} | {sample_lines} |")
        rows.append("")
    return "\n".join(rows) + "\n"


def _to_plain_dict(
    inventory: dict[str, dict[str, list[AdjudicationSite]]]
) -> dict[str, dict[str, list[dict[str, int | str | None]]]]:
    return {
        jurisdiction: {
            kind: [
                {"line": site.line, "function": site.function}
                for site in sites
            ]
            for kind, sites in kinds.items()
        }
        for jurisdiction, kinds in inventory.items()
    }


def _summarize_comparison(comparison: dict[str, dict[str, object]]) -> dict[str, int]:
    def _as_int(row: dict[str, object], key: str) -> int:
        value = row[key]
        if not isinstance(value, int):
            raise TypeError(f"{key} must be int, got {type(value).__name__}")
        return value

    return {
        "jurisdiction_count": len(comparison),
        "wrapper_kind_count": sum(_as_int(row, "wrapper_kind_count") for row in comparison.values()),
        "wrapper_adjudication_count": sum(_as_int(row, "wrapper_adjudication_count") for row in comparison.values()),
        "direct_kind_count": sum(_as_int(row, "direct_kind_count") for row in comparison.values()),
        "direct_adjudication_count": sum(_as_int(row, "direct_adjudication_count") for row in comparison.values()),
        "wrapper_only_kind_count": sum(_as_int(row, "wrapper_only_kind_count") for row in comparison.values()),
        "wrapper_only_adjudication_count": sum(_as_int(row, "wrapper_only_adjudication_count") for row in comparison.values()),
    }


def _format_comparison_markdown(comparison: dict[str, dict[str, object]]) -> str:
    summary = _summarize_comparison(comparison)
    generated_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [
        "# Cross-Jurisdiction Adjudication Surface Comparison",
        "",
        f"> generated_at: {generated_at}",
        "",
        "| jurisdiction | wrapper_kinds | wrapper_adjudications | direct_kinds | direct_adjudications | wrapper_only_kinds | wrapper_only_adjudications |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for jurisdiction, row in sorted(comparison.items()):
        rows.append(
            f"| {jurisdiction} | {row['wrapper_kind_count']} | {row['wrapper_adjudication_count']} | "
            f"{row['direct_kind_count']} | {row['direct_adjudication_count']} | "
            f"{row['wrapper_only_kind_count']} | {row['wrapper_only_adjudication_count']} |"
        )
    rows.extend(
        [
            "",
            "| metric | value |",
            "| --- | ---: |",
            f"| jurisdictions | {summary['jurisdiction_count']} |",
            f"| wrapper_kinds | {summary['wrapper_kind_count']} |",
            f"| wrapper_adjudications | {summary['wrapper_adjudication_count']} |",
            f"| direct_kinds | {summary['direct_kind_count']} |",
            f"| direct_adjudications | {summary['direct_adjudication_count']} |",
            f"| wrapper_only_kinds | {summary['wrapper_only_kind_count']} |",
            f"| wrapper_only_adjudications | {summary['wrapper_only_adjudication_count']} |",
            "",
        ]
    )
    for jurisdiction, row in sorted(comparison.items()):
        rows.append(f"## {jurisdiction}")
        rows.append("")
        rows.append("| wrapper_only_kind |")
        rows.append("| --- |")
        kinds = row["wrapper_only_kinds"]
        if not isinstance(kinds, list):
            raise TypeError(f"wrapper_only_kinds must be list, got {type(kinds).__name__}")
        if not kinds:
            rows.append("| none |")
        else:
            for kind in kinds:
                rows.append(f"| {kind} |")
        rows.append("")
    return "\n".join(rows) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inventory replay adjudication kinds from key jurisdiction files."
    )
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="Output format.",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Project root containing src/lawvm and notes folders.",
    )
    parser.add_argument(
        "--kind-filter",
        default=None,
        help="Optional substring filter on kind names (e.g. '_replay_').",
    )
    parser.add_argument(
        "--replay-only",
        action="store_true",
        help="Restrict inventory to replay-family kinds (`replay_*` and `*_replay_*`).",
    )
    parser.add_argument(
        "--min-count",
        type=_positive_int,
        default=None,
        help="Optional minimum adjudication count per kind; filter out kinds below this threshold.",
    )
    parser.add_argument(
        "--jurisdiction",
        action="append",
        default=None,
        help="Optional jurisdiction whitelist (EU, EE, NO, SE, UK, FI). Repeat for many.",
    )
    parser.add_argument(
        "--all-kinds",
        action="store_true",
        help="Kept for compatibility; helper wrappers are included by default.",
    )
    parser.add_argument(
        "--direct-only",
        action="store_true",
        help="Restrict inventory to direct CompileAdjudication(...) calls only.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path; if omitted, prints to stdout.",
    )
    parser.add_argument(
        "--compare-direct",
        action="store_true",
        help="Emit a wrapper-inclusive vs direct-only surface comparison instead of a single inventory.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    include_wrappers = args.all_kinds or (not args.direct_only)
    jurisdictions = None
    if args.jurisdiction:
        jurisdictions = {jurisdiction.strip().upper() for jurisdiction in args.jurisdiction}
        unsupported = jurisdictions - set(REPLAY_SOURCE_FILES)
        if unsupported:
            supported = ", ".join(sorted(REPLAY_SOURCE_FILES))
            raise SystemExit(
                "Unknown jurisdictions: "
                f"{', '.join(sorted(unsupported))}. Supported jurisdictions: {supported}"
            )
    if args.compare_direct and (args.direct_only or args.all_kinds):
        raise SystemExit("--compare-direct cannot be combined with --direct-only or --all-kinds")

    if args.compare_direct:
        comparison = build_surface_comparison(
            REPLAY_SOURCE_FILES,
            root=root,
            jurisdictions=jurisdictions,
            replay_only=args.replay_only,
            kind_filter=args.kind_filter,
            min_count=args.min_count,
        )
        if args.format == "markdown":
            text = _format_comparison_markdown(comparison)
        else:
            payload = {
                "generated_with": "scripts/inventory_replay_adjudications.py",
                "generated_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "summary": _summarize_comparison(comparison),
                "comparison": comparison,
            }
            text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    else:
        inventory = build_inventory(
            REPLAY_SOURCE_FILES,
            root=root,
            jurisdictions=jurisdictions,
            include_wrappers=include_wrappers,
            replay_only=args.replay_only,
            kind_filter=args.kind_filter,
            min_count=args.min_count,
        )
        if args.format == "markdown":
            text = _format_markdown(inventory)
        else:
            payload = {
                "generated_with": "scripts/inventory_replay_adjudications.py",
                "generated_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "summary": _summarize_inventory(inventory),
                "inventories": _to_plain_dict(inventory),
            }
            text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
