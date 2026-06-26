#!/usr/bin/env python3
"""W6 read-site inventory — classify ``*.target_*`` attribute reads (core+finland).

Enumerates every ``<receiver>.target_<col>`` attribute READ across
``src/lawvm/core`` + ``src/lawvm/finland`` and buckets each by (file, column,
syntactic shape, receiver-name), to size and sequence the W6–W9 read-migration.

IMPORTANT scoping caveat (surfaced, not hidden): the migration target is the
Finland ``AmendmentOp`` 8 ``target_*`` columns. But the SAME attribute names are
also read off OTHER receiver types that are NOT under migration:
``ResolvedTargetScopeView`` (``scope.target_part``), ``ResolvedOp``
(``rop.target_unit_kind``), parsed-clause objects (``clause.target_section``),
etc. A pure-AST scan cannot resolve receiver TYPE without a type checker, so this
inventory records the RECEIVER NAME and a coarse ``likely_amendment_op`` heuristic
(receiver is ``op``/``amendment_op``/``a_op`` or similar) so the true
``AmendmentOp``-column read population can be separated from same-named reads on
sibling types. The batching plan keys off the receiver-name buckets.

Buckets (per read site):
  - simple_read           : value used directly (accessor-swap candidate)
  - used_in_comparison    : inside a Compare (``== / != / is / in``) node
  - used_as_replace_kwarg : passed as a keyword arg whose name is a target_* col
                            to a replace()/dc_replace()/AmendmentOp() call (write-back)
  - serialization         : inside a dict/asdict/json/tuple build (persistence)
  - other                 : anything else

Field-declaration sites (the dataclass body + ``__init__`` signature + ``self.``
assignments in ops.py) are NOT reads and are excluded.

Writes ``.tmp/w6_read_site_inventory.json``. AST-based, deterministic.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]

_COLUMNS: frozenset[str] = frozenset(
    {
        "target_unit_kind",
        "target_section",
        "target_chapter",
        "target_part",
        "target_paragraph",
        "target_item",
        "target_subitem",
        "target_special",
    }
)

_SCAN_DIRS: tuple[str, ...] = ("src/lawvm/core", "src/lawvm/finland")

_REPLACE_CALLEES: frozenset[str] = frozenset({"replace", "dc_replace", "AmendmentOp"})

# Receiver names that strongly indicate the receiver IS an AmendmentOp (vs a
# ResolvedOp/ResolvedTargetScopeView/clause sibling carrying the same attr names).
_AMENDMENT_OP_RECEIVERS: frozenset[str] = frozenset(
    {"op", "amendment_op", "a_op", "amop", "self"}
)
# Receiver names that strongly indicate a NON-AmendmentOp sibling (excluded from
# the migration but reported, so the count is honest).
_NON_AMENDMENT_OP_RECEIVERS: frozenset[str] = frozenset(
    {"scope", "rop", "resolved", "clause", "view", "addr", "address", "record", "rec"}
)


def _receiver_name(node: ast.Attribute) -> str:
    val = node.value
    if isinstance(val, ast.Name):
        return val.id
    if isinstance(val, ast.Attribute):
        return val.attr
    return "<expr>"


class _ReadSiteVisitor(ast.NodeVisitor):
    def __init__(self, relpath: str) -> None:
        self.relpath = relpath
        self.sites: list[dict[str, Any]] = []
        # Parent map for shape classification.
        self._parents: dict[int, ast.AST] = {}
        # Names declared as kwargs to replace/ctor (handled separately below).

    def _shape(self, node: ast.Attribute) -> str:
        parent = self._parents.get(id(node))
        if isinstance(parent, ast.Compare):
            return "used_in_comparison"
        # keyword-arg write-back: node is the value of a keyword whose arg name is
        # a target_* column on a replace/ctor call.
        if isinstance(parent, ast.keyword) and parent.arg in _COLUMNS:
            gp = self._parents.get(id(parent))
            if isinstance(gp, ast.Call):
                callee = _callee_name(gp.func)
                if callee in _REPLACE_CALLEES:
                    return "used_as_replace_kwarg"
        if isinstance(parent, (ast.Dict, ast.Tuple, ast.List)):
            return "serialization"
        return "simple_read"

    def visit(self, node: ast.AST) -> Any:
        for child in ast.iter_child_nodes(node):
            self._parents[id(child)] = node
        return super().visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        if node.attr in _COLUMNS and isinstance(node.ctx, ast.Load):
            receiver = _receiver_name(node)
            if receiver in _AMENDMENT_OP_RECEIVERS:
                likely = "amendment_op"
            elif receiver in _NON_AMENDMENT_OP_RECEIVERS:
                likely = "non_amendment_op"
            else:
                likely = "ambiguous"
            self.sites.append(
                {
                    "file": self.relpath,
                    "line": node.lineno,
                    "column": node.attr,
                    "receiver": receiver,
                    "shape": self._shape(node),
                    "likely_amendment_op": likely,
                }
            )
        self.generic_visit(node)


def _callee_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _scan_file(path: Path, root: Path) -> list[dict[str, Any]]:
    relpath = str(path.relative_to(root))
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    visitor = _ReadSiteVisitor(relpath)
    visitor.visit(tree)
    return visitor.sites


def run_inventory(root: Path = _REPO_ROOT) -> dict[str, Any]:
    sites: list[dict[str, Any]] = []
    for scan_dir in _SCAN_DIRS:
        base = root / scan_dir
        for path in sorted(base.rglob("*.py")):
            sites.extend(_scan_file(path, root))

    by_shape: Counter[str] = Counter(s["shape"] for s in sites)
    by_column: Counter[str] = Counter(s["column"] for s in sites)
    by_likely: Counter[str] = Counter(s["likely_amendment_op"] for s in sites)
    by_file: Counter[str] = Counter(s["file"] for s in sites)
    # Per-file × shape for batching.
    by_file_shape: dict[str, dict[str, int]] = {}
    for s in sites:
        by_file_shape.setdefault(s["file"], {})
        by_file_shape[s["file"]][s["shape"]] = by_file_shape[s["file"]].get(s["shape"], 0) + 1

    return {
        "inventory": "w6_read_site_inventory",
        "scan_dirs": list(_SCAN_DIRS),
        "columns": sorted(_COLUMNS),
        "total_sites": len(sites),
        "by_shape": dict(by_shape),
        "by_column": dict(by_column),
        "by_likely_amendment_op": dict(by_likely),
        "files_touched": len(by_file),
        "by_file_count": dict(by_file.most_common()),
        "by_file_shape": by_file_shape,
        "sites": sites,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=_REPO_ROOT / ".tmp" / "w6_read_site_inventory.json",
    )
    args = parser.parse_args(argv)

    report = run_inventory()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"total_sites            : {report['total_sites']}")
    print(f"files_touched          : {report['files_touched']}")
    print(f"by_shape               : {report['by_shape']}")
    print(f"by_likely_amendment_op : {report['by_likely_amendment_op']}")
    print(f"by_column              : {report['by_column']}")
    print(f"wrote                  : {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
