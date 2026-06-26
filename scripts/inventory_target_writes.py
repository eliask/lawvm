#!/usr/bin/env python3
"""Inventory raw ``target_*`` writes to ``AmendmentOp`` under ``src/lawvm/finland``.

A monotone "no new direct ``target_*`` writes" ratchet companion to
``test_deprecated_callsite_ratchet.py`` / ``test_regex_ratchet.py``, for the
``TargetSelector`` migration (wave W3a).

The FI ``AmendmentOp`` is being migrated off its 8 scattered, loosely-typed
``target_*`` columns onto a typed ``TargetSelector`` constructed through the
sanctioned facades (``target_selector_facades.py``) and lowered by the codec
(``target_selector_codec.py``). This scanner AST-counts, per file under
``src/lawvm/finland/``, the sites that construct or ``dataclasses.replace`` an
``AmendmentOp`` while passing a raw ``target_*`` keyword argument:

  - ``AmendmentOp(..., target_section=..., ...)`` constructor calls, and
  - ``dataclasses.replace(op, target_*=...)`` / ``dc_replace(op, target_*=...)``
    / ``replace(op, target_*=...)`` calls.

A "raw target_* write" is one keyword argument whose name is in
``_TARGET_KWARGS`` on such a call. The per-call count is the number of distinct
target_* kwargs it passes (so a single ``AmendmentOp(target_section=...,
target_chapter=...)`` counts as 2 raw writes — tightening any of them helps).

The committed baseline (``tests/data/target_write_baseline.json``) freezes the
current per-file counts. The companion test FAILS only when a file's count
EXCEEDS its baseline — new untyped construction is forbidden; existing writes are
grandfathered and may only shrink (route them through the facades).

EXCLUDED (the sanctioned lowering point — these legitimately emit target_*):
  - ``src/lawvm/finland/target_selector_codec.py`` (the codec itself)
  - ``src/lawvm/finland/target_selector_facades.py`` (the facades)

AST-based (not textual): only genuine call-site keyword arguments are counted, so
the ``AmendmentOp`` dataclass field declarations, the ``__init__`` signature, and
attribute reads (``op.target_section``) are NOT miscounted.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]

BASELINE_PATH = Path("tests/data/target_write_baseline.json")

# The directory whose AmendmentOp construction sites are governed by the ratchet.
_SCAN_DIR = "src/lawvm/finland"

# The 8 loosely-typed legacy target columns whose raw keyword-write we forbid.
_TARGET_KWARGS = frozenset(
    {
        "target_section",
        "target_chapter",
        "target_part",
        "target_paragraph",
        "target_item",
        "target_subitem",
        "target_special",
        "target_unit_kind",
    }
)

# The construct/replace callees that produce an AmendmentOp from target_* kwargs.
_AMENDMENT_OP_CTOR = "AmendmentOp"
_REPLACE_CALLEES = frozenset({"replace", "dc_replace", "dataclasses.replace"})

# The sanctioned lowering point — excluded from the scan (it MUST emit target_*).
_EXCLUDED_RELPATHS = frozenset(
    {
        "src/lawvm/finland/target_selector_codec.py",
        "src/lawvm/finland/target_selector_facades.py",
    }
)


def _callee_name(func: ast.expr) -> str:
    """Render a call's callee as a dotted name (best-effort, for matching)."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        base = _callee_name(func.value)
        return f"{base}.{func.attr}" if base else func.attr
    return ""


def _is_amendment_op_construction(call: ast.Call) -> bool:
    """True if ``call`` constructs or replace()s an ``AmendmentOp``.

    Construction: ``AmendmentOp(...)`` (Name or trailing ``.AmendmentOp``).
    Replace: ``replace(...)`` / ``dc_replace(...)`` / ``dataclasses.replace(...)``
    (we cannot statically prove the target is an AmendmentOp, but any replace
    that passes a raw ``target_*`` kwarg is, by construction, an AmendmentOp
    target write — replace only forwards fields of the dataclass it copies).
    """
    name = _callee_name(call.func)
    if name == _AMENDMENT_OP_CTOR or name.endswith("." + _AMENDMENT_OP_CTOR):
        return True
    if name in _REPLACE_CALLEES or name.endswith(".replace"):
        return True
    return False


def _raw_target_kwargs(call: ast.Call) -> list[str]:
    """The raw ``target_*`` keyword names this call passes (sorted, deduped)."""
    found: set[str] = set()
    for kw in call.keywords:
        if kw.arg is not None and kw.arg in _TARGET_KWARGS:
            found.add(kw.arg)
    return sorted(found)


def scan_file_target_writes(text: str) -> list[dict[str, Any]]:
    """Return one record per AmendmentOp construct/replace call with raw target_*.

    Each record: ``{"line": int, "callee": str, "kwargs": [name, ...]}``. Driven
    by the AST so only genuine call-site keyword arguments count. If the file
    does not parse, returns ``[]`` (no silent over-claim).
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:  # pragma: no cover - defensive
        return []
    records: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_amendment_op_construction(node):
            continue
        kwargs = _raw_target_kwargs(node)
        if not kwargs:
            continue
        records.append(
            {
                "line": node.lineno,
                "callee": _callee_name(node.func),
                "kwargs": kwargs,
            }
        )
    return records


def _iter_python_files(repo_root: Path) -> list[Path]:
    base = repo_root / _SCAN_DIR
    if not base.exists():
        return []
    files: list[Path] = []
    for path in sorted(base.rglob("*.py")):
        if path.relative_to(repo_root).as_posix() in _EXCLUDED_RELPATHS:
            continue
        files.append(path)
    return files


def scan_target_writes(repo_root: Path | None = None) -> dict[str, Any]:
    """Scan ``src/lawvm/finland`` and return per-file raw-target_*-write counts.

    Returns a dict with:
      - ``counts``: {rel_path: int} — raw target_* writes per file (files with 0
        are omitted).
      - ``sites``: {rel_path: [ {"line", "callee", "kwargs"}, ... ]}.
      - ``total``: int — sum of counts.
    """
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    counts: dict[str, int] = {}
    sites: dict[str, list[dict[str, Any]]] = {}

    for path in _iter_python_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
        records = scan_file_target_writes(text)
        if not records:
            continue
        file_count = sum(len(r["kwargs"]) for r in records)
        counts[rel] = file_count
        sites[rel] = records

    return {
        "counts": counts,
        "sites": sites,
        "total": sum(counts.values()),
    }


def _baseline_payload(repo_root: Path | None = None) -> dict[str, Any]:
    state = scan_target_writes(repo_root)
    return {
        "_doc": (
            "Monotone per-file baseline of raw target_* writes to AmendmentOp "
            "under src/lawvm/finland (constructor kwargs + dataclasses.replace). "
            "Counts may only fall as call sites migrate onto the typed "
            "target_selector_facades. Regenerate with `uv run python "
            "scripts/inventory_target_writes.py --update-baseline` after "
            "legitimately retiring a raw write (the codec + facades are excluded "
            "as the sanctioned lowering point)."
        ),
        "scan_dir": _SCAN_DIR,
        "target_kwargs": sorted(_TARGET_KWARGS),
        "excluded": sorted(_EXCLUDED_RELPATHS),
        "counts": state["counts"],
        "sites": state["sites"],
        "total": state["total"],
    }


def update_baseline(repo_root: Path | None = None) -> Path:
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    out_path = root / BASELINE_PATH
    payload = _baseline_payload(root)
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return out_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the committed raw-target_*-write baseline JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.update_baseline:
        out_path = update_baseline()
        print(f"Wrote target-write baseline: {out_path}")
        return 0
    state = scan_target_writes()
    for rel in sorted(state["counts"], key=lambda r: (-state["counts"][r], r)):
        print(f"{state['counts'][rel]:4d}  {rel}")
    print(f"{state['total']:4d}  TOTAL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
