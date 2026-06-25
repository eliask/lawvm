#!/usr/bin/env python3
"""Cross-jurisdiction legacy/coherence-surface census probe.

Regenerable evidence generator for notes/CROSS_JURISDICTION_LEGACY_CENSUS.md.
Scans each jurisdiction package under src/lawvm/<juris>/ for the metrics that
distinguish FI's high typed/fail-loud discipline from the other jurisdictions:

  1. @deprecated cohort (markers + DeprecationWarning)
  2. legacy/fallback symbols + broad except swallows
  3. structured-dispatch maturity (true match-stmts, assert_never, enum/Status
     enum classes, Literal[] typed unions)
  4. bare-status sites (status="literal" on construction)
  5. typed-waist signals (frozen dataclasses, core LegalOperation refs,
     raw dict string-keyed flows, regex-classifier wrapping)

Read-only. Prints a TSV-ish table to stdout. No source mutation.

Usage:
    uv run python scripts/cross_jurisdiction_legacy_census.py
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "lawvm"

JURISDICTIONS = [
    "uk_legislation",
    "estonia",
    "norway",
    "sweden",
    "new_zealand",
    "us_federal",
    "eu",
    "open_law",
    "finland",
]

# (label, regex, exclude_tests) — counted over python source lines.
PATTERNS: list[tuple[str, str, bool]] = [
    ("deprecated", r"@deprecated|warnings\.deprecated|DeprecationWarning|deprecated\(", False),
    ("legacy_fallback_sym", r"def [a-z_]*(legacy|fallback|_old|deprecated|compat)|class [A-Za-z]*(Legacy|Fallback|Old|Deprecated|Compat)", False),
    ("broad_except", r"except (Exception|BaseException)", False),
    ("bare_except", r"except\s*:", False),
    ("true_match_stmt", r"^\s*match [a-zA-Z_].*:\s*$", False),
    ("assert_never", r"assert_never", False),
    ("enum_class", r"class [A-Za-z]+\((str, )?(Enum|StrEnum|IntEnum)\)", False),
    ("status_enum_class", r"class [A-Za-z]*Status[A-Za-z]*\((str, )?(Enum|StrEnum|IntEnum)\)", False),
    ("literal_annot", r"Literal\[", False),
    ("bare_status", r"status\s*=\s*\"[a-z_]+\"|status\s*=\s*'[a-z_]+'", True),
    ("frozen_dataclass", r"@dataclass\(frozen=True", False),
    ("core_op_ref", r"from lawvm\.core|import LegalOperation|LegalOperation\b", False),
    ("raw_dict_key", r"\"(kind|type|op_type|status|target_type)\"\s*:", True),
    ("regex_compile", r"re\.compile\(", False),
    ("wrapped_classifier", r"compile_classifier_regex|PrefilteredPattern|compile_[a-z_]*_regex", False),
]


@dataclass
class JurisMetrics:
    name: str
    loc: int = 0
    counts: dict[str, int] = field(default_factory=dict)


def _py_files(pkg: Path, exclude_tests: bool) -> list[Path]:
    files = [p for p in pkg.rglob("*.py")]
    if exclude_tests:
        files = [p for p in files if not p.name.startswith("test_")]
    return files


def _count(pkg: Path, pattern: str, exclude_tests: bool, multiline: bool) -> int:
    rx = re.compile(pattern, re.MULTILINE if multiline else 0)
    total = 0
    for p in _py_files(pkg, exclude_tests):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if multiline:
            total += len(rx.findall(text))
        else:
            total += sum(1 for line in text.splitlines() if rx.search(line))
    return total


def _loc(pkg: Path) -> int:
    total = 0
    for p in _py_files(pkg, exclude_tests=True):
        try:
            total += len(p.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            continue
    return total


def main() -> None:
    results: list[JurisMetrics] = []
    for juris in JURISDICTIONS:
        pkg = SRC / juris
        if not pkg.is_dir():
            continue
        jm = JurisMetrics(name=juris, loc=_loc(pkg))
        for label, pattern, excl in PATTERNS:
            multiline = label in {"true_match_stmt"}
            jm.counts[label] = _count(pkg, pattern, excl, multiline)
        results.append(jm)

    labels = [p[0] for p in PATTERNS]
    header = ["juris", "kLOC", *labels]
    print("\t".join(header))
    for jm in results:
        row = [jm.name, f"{jm.loc / 1000:.1f}"]
        row += [str(jm.counts.get(lab, 0)) for lab in labels]
        print("\t".join(row))


if __name__ == "__main__":
    main()
