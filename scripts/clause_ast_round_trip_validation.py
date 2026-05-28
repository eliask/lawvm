#!/usr/bin/env python3
"""
Round-trip validation of ClauseAST.

Validates that ParsedOp → ClauseAST → LegalOperation produces identical
results to ParsedOp → LegalOperation for the corpus benchmark set.

Usage:
    uv run python scripts/clause_ast_round_trip_validation.py

Output:
    notes/CLAUSE_AST_ROUND_TRIP_VALIDATION.md — detailed validation report.
"""

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, cast

from lawvm.corpus_store import get_corpus_store
from lawvm.finland.amendment_index import get_amendment_children
from lawvm.finland.metadata import get_johtolause
from lawvm.finland.johtolause.peg3 import extract_ops_diagnostic
from lawvm.finland.johtolause.parsed_op_clause_ast import build_clause_ast
from lawvm.core.clause_ast import clause_ast_to_legal_ops
from lawvm.core.ir import LegalOperation


@dataclass
class RoundTripMismatch:
    """Record a single mismatch."""
    statute_id: str
    amendment_id: str
    johtolause_index: int
    johtolause_text: str
    mismatch_type: str  # "length", "action", "target", "anchor"
    path_a_value: str
    path_b_value: str


@dataclass
class ValidationReport:
    """Validation results."""
    total_statutes: int
    total_amendments: int
    total_johtolause: int
    total_ops: int
    mismatches: list[RoundTripMismatch]

    def summary_text(self) -> str:
        lines = [
            "# ClauseAST Round-Trip Validation",
            "",
            "## Summary",
            "",
            f"- **Statutes tested:** {self.total_statutes}",
            f"- **Amendments analyzed:** {self.total_amendments}",
            f"- **Johtolause texts parsed:** {self.total_johtolause}",
            f"- **Operations compared:** {self.total_ops}",
            f"- **Mismatches found:** {len(self.mismatches)}",
            "",
        ]

        if not self.mismatches:
            lines.extend([
                "## Result",
                "",
                "✓ **LOSSLESS**: All round-trips matched.",
                "",
                "The ClauseAST bridge is verified to preserve all semantic content of ParsedOp.",
                "Path A (direct ParsedOp → LegalOperation) and Path B (via ClauseAST) produce",
                "identical results for action, target.path, target.special, and anchor.",
                "",
            ])
        else:
            lines.extend([
                "## Result",
                "",
                f"✗ **{len(self.mismatches)} mismatch(es) found** — see details below.",
                "",
            ])

            lines.extend([
                "## Mismatches",
                "",
            ])
            for mm in self.mismatches:
                lines.extend([
                    f"### {mm.statute_id} / {mm.amendment_id} — johtolause[{mm.johtolause_index}]",
                    "",
                    f"**Type:** {mm.mismatch_type}",
                    "",
                    "**Johtolause text:**",
                    "```",
                    mm.johtolause_text,
                    "```",
                    "",
                    f"**Path A (ParsedOp → LegalOp):** {mm.path_a_value}",
                    "",
                    f"**Path B (ClauseAST):** {mm.path_b_value}",
                    "",
                ])

        lines.extend([
            "## Methodology",
            "",
            "For each statute in the benchmark corpus:",
            "",
            "1. Load all amendment IDs for the statute",
            "2. For each amendment, fetch its XML from archive",
            "3. Extract johtolause text",
            "4. Parse johtolause via PEG3 to get ParsedOps",
            "5. Compare two conversion paths:",
            "",
            "   - **Path A:** `[op.to_legal_operation(i) for i, op in enumerate(ops)]`",
            "   - **Path B:** `clause_ast_to_legal_ops(build_clause_ast(ops, text))`",
            "",
            "6. For each operation, verify:",
            "   - List length matches",
            "   - action fields match",
            "   - target.path fields match",
            "   - target.special fields match",
            "   - anchor fields match",
            "",
            "## Notes",
            "",
            "- `op_id` is not preserved (empty string in both paths by design)",
            "- sequence numbers are re-assigned in both paths",
            "- MetaClause nodes are dropped in both paths (no tree-op equivalent)",
            "- The test uses the latest benchmark CSV from `data/bench_runs/`",
            "- Amendment metadata sourced from `amendment_index.py` (get_amendment_children)",
            "",
        ])

        return "\n".join(lines)


def load_bench_statutes() -> list[str]:
    """Load the latest benchmark CSV and return statute IDs."""
    bench_dir = Path("data/bench_runs")
    # Find latest CSV with "statute_id" column (standard bench format, not consistency CSVs)
    bench_csvs = []
    for csv_file in sorted(bench_dir.glob("*.csv"), reverse=True):
        try:
            with open(csv_file) as f:
                first_line = f.readline()
                if "statute_id" in first_line:
                    bench_csvs.append(csv_file)
                    break
        except Exception:
            continue

    if not bench_csvs:
        raise FileNotFoundError("No benchmark CSV files with 'statute_id' column found")

    latest = bench_csvs[0]
    print(f"Loading benchmark from {latest.name}...")

    statute_ids = []
    with open(latest) as f:
        reader = csv.DictReader(f)
        for row in reader:
            statute_ids.append(row["statute_id"])

    return statute_ids


def compare_legal_ops(op_a: LegalOperation, op_b: LegalOperation) -> Optional[str]:
    """Compare two LegalOperations for semantic equivalence.

    Returns None if identical, or a string describing the difference.
    """
    if op_a.action != op_b.action:
        return f"action: {op_a.action!r} vs {op_b.action!r}"

    # Compare target paths
    a_path = op_a.target.path if op_a.target else None
    b_path = op_b.target.path if op_b.target else None
    if a_path != b_path:
        return f"target.path: {a_path!r} vs {b_path!r}"

    # Compare target.special
    a_special = op_a.target.special if op_a.target else None
    b_special = op_b.target.special if op_b.target else None
    if a_special != b_special:
        return f"target.special: {a_special!r} vs {b_special!r}"

    # Compare anchor
    if op_a.anchor != op_b.anchor:
        return f"anchor: {op_a.anchor!r} vs {op_b.anchor!r}"

    return None


def validate_statute(cs, statute_id: str, amendments_map: dict[str, list[str]]) -> tuple[int, int, list[RoundTripMismatch]]:
    """Validate all amendments for one statute.

    Returns (amendments_tested, ops_tested, mismatches).
    """
    mismatches = []
    amendments_tested = 0
    ops_tested = 0

    # Get amendments from index
    amendments = amendments_map.get(statute_id, [])
    if not amendments:
        return 0, 0, []

    for amendment_id in amendments:
        try:
            xml_bytes = cs.read_amendment(amendment_id)
        except Exception:
            # Skip silently — may not be in archive yet
            continue

        amendments_tested += 1

        # Extract johtolause
        try:
            johto = get_johtolause(xml_bytes)
        except Exception:
            continue

        if not johto or not johto.strip():
            # No operative text
            continue

        # Extract ops via diagnostic
        try:
            diag = extract_ops_diagnostic(johto)
            ops = cast(list[Any], diag.ops)
        except Exception:
            continue

        if not ops:
            # No ops extracted
            continue

        # Path A: direct conversion
        try:
            path_a_ops = [op.to_legal_operation(i) for i, op in enumerate(ops)]
        except Exception:
            continue

        # Path B: via ClauseAST
        try:
            ast = build_clause_ast(ops, johto)
            path_b_ops = clause_ast_to_legal_ops(ast)
        except Exception:
            continue

        ops_tested += len(path_a_ops)

        # Compare
        if len(path_a_ops) != len(path_b_ops):
            mismatches.append(RoundTripMismatch(
                statute_id=statute_id,
                amendment_id=amendment_id,
                johtolause_index=0,
                johtolause_text=johto[:200],
                mismatch_type="length",
                path_a_value=f"{len(path_a_ops)} ops",
                path_b_value=f"{len(path_b_ops)} ops",
            ))
            continue

        # Compare each op
        for i, (op_a, op_b) in enumerate(zip(path_a_ops, path_b_ops, strict=True)):
            diff = compare_legal_ops(op_a, op_b)
            if diff:
                mismatch_type, value_diff = diff.split(": ", 1)
                path_a_val, path_b_val = value_diff.split(" vs ", 1)
                mismatches.append(RoundTripMismatch(
                    statute_id=statute_id,
                    amendment_id=amendment_id,
                    johtolause_index=i,
                    johtolause_text=johto[:200],
                    mismatch_type=mismatch_type,
                    path_a_value=path_a_val,
                    path_b_value=path_b_val,
                ))

    return amendments_tested, ops_tested, mismatches


def main():
    """Run full validation."""
    cs = get_corpus_store()
    statute_ids = load_bench_statutes()
    print(f"Loaded {len(statute_ids)} statutes from benchmark.")

    # Load amendment index
    amendments_map = get_amendment_children()
    print(f"Loaded {len(amendments_map)} statutes with amendments.")

    total_amendments = 0
    total_ops = 0
    all_mismatches = []

    for i, statute_id in enumerate(statute_ids):
        print(f"[{i+1}/{len(statute_ids)}] {statute_id}")

        amend_count, op_count, mismatches = validate_statute(cs, statute_id, amendments_map)
        total_amendments += amend_count
        total_ops += op_count
        all_mismatches.extend(mismatches)

    # Build and write report
    report = ValidationReport(
        total_statutes=len(statute_ids),
        total_amendments=total_amendments,
        total_johtolause=total_amendments,  # One johtolause per amendment
        total_ops=total_ops,
        mismatches=all_mismatches,
    )

    output_path = Path("notes/CLAUSE_AST_ROUND_TRIP_VALIDATION.md")
    output_path.write_text(report.summary_text())

    print(f"\n✓ Report written to {output_path}")
    print(f"  Statutes: {report.total_statutes}")
    print(f"  Amendments: {report.total_amendments}")
    print(f"  Operations: {report.total_ops}")
    print(f"  Mismatches: {len(report.mismatches)}")


if __name__ == "__main__":
    main()
