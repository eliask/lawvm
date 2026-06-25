#!/usr/bin/env python3
"""Inventory call sites to PEP 702 ``@deprecated`` FI legacy-fallback symbols.

A monotone legacy-elimination ratchet companion to ``test_regex_ratchet.py``.

The FI legacy fallback cohort (regex/field-based lanes that are demoted behind
typed/owning parsers, kept only as strangled safety nets) is decorated with
``warnings.deprecated`` (PEP 702).  On Python 3.14 this makes ``ty`` emit
``warning[deprecated]`` at every call site and the runtime emit a
``DeprecationWarning`` — both a checker signal and fire-rate telemetry.

This scanner counts, per tracked symbol, the call sites that reach it
(``symbol(`` use-sites) across ``src/`` and ``tests/``, excluding the symbol's
own ``def`` line.  The committed baseline (``tests/data/deprecated_callsite_baseline.json``)
records those counts.  The companion test FAILS if any count INCREASES (a new
caller bound a strangled legacy lane without acknowledgment) — the ratchet may
only ever fall as the legacy cohort is retired.

It is deliberately textual (not import-based): tracked symbols include private
and module-internal helpers, and we want to count
call sites without importing the world.  A "call site" is a line containing
``<symbol>(`` that is not the ``def <symbol>(`` definition and not a pure
comment line.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[1]

BASELINE_PATH = Path("tests/data/deprecated_callsite_baseline.json")

# The PEP 702 @deprecated FI legacy-fallback cohort.  Each entry maps the
# tracked symbol to the module that defines it (for documentation / auditing).
DEPRECATED_SYMBOLS: dict[str, str] = {
    # NB: the three rank-3 normalize fallback op-heuristics
    # (parse_ops_fallback_heuristic, parse_ops_fallback_heuristic_with_coverage,
    # parse_ops_title_fallback) were DE-DEPRECATED after a whole-corpus census
    # (lawvm.finland.normalize_fallback_heuristic_census, pinned by
    # tests/test_fi_normalize_fallback_heuristic_census.py) proved each is a
    # load-bearing required residual the typed grammar cannot own — not a
    # strangled lane. They left this cohort honestly (matching the
    # strip_legacy_*_heading_prefix retain-with-guard precedent).
    "extract_plain_text_statute_mentions": (
        "src/lawvm/finland/references/ref_mention_extractor.py"
    ),
    "strip_legacy_roman_division_heading_prefix": (
        "src/lawvm/finland/oracle_comparison.py"
    ),
    "strip_legacy_numbered_section_heading_prefix": (
        "src/lawvm/finland/oracle_comparison.py"
    ),
}

# Roots scanned for call sites.
_SCAN_DIRS = ("src", "tests", "scripts")

# The ratchet's own test file carries synthetic guard-liveness fixtures that
# embed ``<symbol>(`` as string literals; counting those would self-trip the
# gate.  This scanner file mentions the symbols only in DEPRECATED_SYMBOLS (no
# call-site shape), but exclude it too for clarity.
_EXCLUDED_RELPATHS = frozenset(
    {
        "tests/test_deprecated_callsite_ratchet.py",
        "scripts/inventory_deprecated_callsites.py",
    }
)


def _iter_python_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for rel in _SCAN_DIRS:
        base = repo_root / rel
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if path.relative_to(repo_root).as_posix() in _EXCLUDED_RELPATHS:
                continue
            files.append(path)
    return files


def _is_call_site(line: str, symbol: str) -> bool:
    """Return True if ``line`` is a call site of ``symbol`` (not a def/comment)."""
    token = symbol + "("
    if token not in line:
        return False
    stripped = line.lstrip()
    if stripped.startswith("#"):
        return False
    if stripped.startswith("def ") or stripped.startswith("async def "):
        return False
    return True


def scan_deprecated_callsites(repo_root: Path | None = None) -> dict[str, Any]:
    """Scan the repo and return per-symbol call-site records + counts.

    Returns a dict with:
      - ``counts``: {symbol: int} — call-site count per tracked symbol.
      - ``sites``: {symbol: [ "rel/path.py:lineno", ... ]} — the call sites.
      - ``total``: int — sum of counts.
    """
    root = (repo_root or _DEFAULT_REPO_ROOT).resolve()
    counts: dict[str, int] = {sym: 0 for sym in DEPRECATED_SYMBOLS}
    sites: dict[str, list[str]] = {sym: [] for sym in DEPRECATED_SYMBOLS}

    for path in _iter_python_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover - defensive
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for symbol in DEPRECATED_SYMBOLS:
                if _is_call_site(line, symbol):
                    counts[symbol] += 1
                    sites[symbol].append(f"{rel}:{lineno}")

    return {
        "counts": counts,
        "sites": sites,
        "total": sum(counts.values()),
    }


def _baseline_payload(repo_root: Path | None = None) -> dict[str, Any]:
    state = scan_deprecated_callsites(repo_root)
    return {
        "_doc": (
            "Monotone call-site baseline for the PEP 702 @deprecated FI "
            "legacy-fallback cohort. Counts may only fall. Regenerate with "
            "`uv run python scripts/inventory_deprecated_callsites.py "
            "--update-baseline` after legitimately retiring a legacy call site "
            "(or, with explicit acknowledgment, after adding one)."
        ),
        "symbols": DEPRECATED_SYMBOLS,
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
        help="Rewrite the committed call-site baseline JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.update_baseline:
        out_path = update_baseline()
        print(f"Wrote deprecated call-site baseline: {out_path}")
        return 0
    state = scan_deprecated_callsites()
    for symbol in DEPRECATED_SYMBOLS:
        print(f"{state['counts'][symbol]:4d}  {symbol}")
    print(f"{state['total']:4d}  TOTAL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
