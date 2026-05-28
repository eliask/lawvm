#!/usr/bin/env python3
"""Corpus-wide replay/product invariant audit for LawVM Finland statutes.

Replays every statute in the corpus and collects structural invariant findings,
plus any direct invariant lists surfaced through replay metadata.

Two invariant signals are collected per statute:
  1. tree_invariant_violation adjudications emitted by the grafter (already
     surfaces in the compile path).
  2. Direct check_invariants() call on the materialized IR for thoroughness.

Usage:
    uv run python scripts/audit_invariants.py
    uv run python scripts/audit_invariants.py --sample-size 50
    uv run python scripts/audit_invariants.py --workers 4
    uv run python scripts/audit_invariants.py --corpus path/to/ids.txt
    uv run python scripts/audit_invariants.py --workers 8 --output .tmp/my_audit.csv

    # Filter summary to specific families/scopes (still processes all, filters output):
    uv run python scripts/audit_invariants.py --filter-phase-scope materialized_only
    uv run python scripts/audit_invariants.py --filter-detector-family duplicate_label
    uv run python scripts/audit_invariants.py --filter-detector-family flattened_sublist_family
    uv run python scripts/audit_invariants.py --min-chain-length 1 --summary-only
    uv run python scripts/audit_invariants.py --filter-phase-scope both --min-chain-length 1
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from types import SimpleNamespace

LAWVM_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAWVM_DIR / "src"))

DEFAULT_CORPUS = LAWVM_DIR / ".tmp" / "diff_triage_corpus.txt"
DEFAULT_OUTPUT = LAWVM_DIR / ".tmp" / "invariant_audit.csv"

# Patterns for classifying violation strings produced by check_invariants()
_DUPLICATE_RE = re.compile(r"duplicate\s+(\w+):(\S+)", re.IGNORECASE)
_NORM_DUPLICATE_RE = re.compile(r"normalized-duplicate\s+(\w+):(\S+)", re.IGNORECASE)
_OUT_OF_ORDER_RE = re.compile(r"(\w+)\s+out of order:\s+(\S+)\s+>\s+(\S+)", re.IGNORECASE)
_UNEXPECTED_NESTING_RE = re.compile(r"unexpected\s+(\w+)\s+inside\s+(\w+)", re.IGNORECASE)
_ILLEGAL_EDGE_PAIRS = frozenset(
    {
        ("paragraph", "section"),
        ("subparagraph", "section"),
        ("subsection", "chapter"),
        ("paragraph", "chapter"),
        ("subparagraph", "chapter"),
    }
)


def _classify_violation(violation: str) -> tuple[str, str, str]:
    """Return (violation_type, path, detail) for a violation string.

    The violation string produced by the tree/product invariant checks has the form:
      "body/chapter:3/section:5: duplicate section:5a (2 times)"
      "body/section:1: section out of order: 5 > 2"
      "body/chapter:3: unexpected foo inside chapter"

    Path segments use "kind:label" with no space after the colon.
    The path/message separator is ": " (colon-space) occurring after the last
    "/" path separator.  This is reliably the first ": " *after* the last "/".
    For violation strings with no "/" (flat paths), fall back to the first ": ".
    """
    last_slash = violation.rfind("/")
    search_from = last_slash + 1 if last_slash != -1 else 0
    sep = violation.find(": ", search_from)
    if sep != -1:
        path = violation[:sep].strip()
        message = violation[sep + 2:].strip()
    else:
        path = ""
        message = violation.strip()

    m = _DUPLICATE_RE.search(message)
    if m:
        return "duplicate_label", path, f"{m.group(1)}:{m.group(2)}"

    m = _NORM_DUPLICATE_RE.search(message)
    if m:
        return "normalized_duplicate", path, f"{m.group(1)}:{m.group(2)}"

    m = _OUT_OF_ORDER_RE.search(message)
    if m:
        return "sort_order", path, f"{m.group(1)}: {m.group(2)} > {m.group(3)}"

    m = _UNEXPECTED_NESTING_RE.search(message)
    if m:
        child_kind = m.group(1)
        parent_kind = m.group(2)
        detail = f"{child_kind} inside {parent_kind}"
        if (child_kind.lower(), parent_kind.lower()) in _ILLEGAL_EDGE_PAIRS:
            return "illegal_edge", path, detail
        return "nesting_violation", path, detail

    return "other", path, message[:200]


def _classify_typed_tree_violation(record: dict[str, object]) -> tuple[str, str, str]:
    """Return audit classification from typed TreeInvariantViolation metadata."""
    kind = str(record.get("kind") or "")
    path = str(record.get("path") or "")
    child_kind = str(record.get("child_kind") or "")
    parent_kind = str(record.get("parent_kind") or "")
    label = str(record.get("label") or "")
    normalized_label = str(record.get("normalized_label") or "")
    previous_label = str(record.get("previous_label") or "")
    next_label = str(record.get("next_label") or "")

    if kind == "duplicate_label":
        return "duplicate_label", path, f"{child_kind}:{label}"
    if kind == "normalized_duplicate_label":
        return "normalized_duplicate", path, f"{child_kind}:{normalized_label}"
    if kind == "sort_order":
        return "sort_order", path, f"{child_kind}: {previous_label} > {next_label}"
    if kind == "unexpected_child_kind":
        detail = f"{child_kind} inside {parent_kind}"
        if (child_kind.lower(), parent_kind.lower()) in _ILLEGAL_EDGE_PAIRS:
            return "illegal_edge", path, detail
        return "nesting_violation", path, detail

    message = str(record.get("message") or "")
    return "other", path, message[:200]


def _coerce_typed_tree_violation_records(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    records: list[dict[str, object]] = []
    for record in raw:
        if isinstance(record, dict):
            records.append({str(key): value for key, value in record.items()})
    return records


def _append_violation_row(
    rows: list[dict[str, str]],
    seen: set[tuple[str, str, str]],
    *,
    norm_id: str,
    violation_type: str,
    path: str,
    detail: str,
    source: str,
    adj_kind: str,
    phase: str,
    chain_length: str,
    oracle_suspect: str,
) -> None:
    key = (violation_type, path, detail)
    if key in seen:
        return
    rows.append({
        "statute_id": norm_id,
        "status": "violation",
        "violation_type": violation_type,
        "path": path,
        "detail": detail,
        "source": source,
        "adj_kind": adj_kind,
        "phase": phase,
        "chain_length": chain_length,
        "oracle_suspect": oracle_suspect,
    })
    seen.add(key)


def _infer_phase(row: dict[str, str]) -> str:
    """Return a stable phase bucket for one audit row."""
    explicit_phase = str(row.get("phase") or "").strip()
    if explicit_phase:
        return explicit_phase
    adj_kind = str(row.get("adj_kind") or "")
    source = str(row.get("source") or "")
    if adj_kind == "APPLY.TREE_INVARIANT_VIOLATION":
        return "replay_fold"
    if adj_kind == "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION":
        return "materialized"
    if source == "replay_meta_tree":
        return "replay_fold"
    if source == "replay_meta_product":
        return "materialized"
    return "unknown"


def _phase_scope_for(phases: set[str]) -> str:
    """Collapse concrete phases into a summary scope label."""
    if not phases:
        return "unknown"
    norm = {phase for phase in phases if phase}
    if norm == {"replay_fold"}:
        return "replay_fold_only"
    if norm == {"materialized"}:
        return "materialized_only"
    if "replay_fold" in norm and "materialized" in norm:
        return "both"
    if len(norm) == 1:
        return next(iter(norm))
    return "mixed"


def _detector_family_for(row: dict[str, str]) -> str:
    """Classify a row into a more root-cause-like detector family."""
    violation_type = str(row.get("violation_type") or "")
    path = str(row.get("path") or "")
    detail = str(row.get("detail") or "")
    source = str(row.get("source") or "")
    phase_scope = str(row.get("phase_scope") or "")
    chain_length = str(row.get("chain_length") or "").strip()

    if chain_length == "0" and violation_type in {
        "duplicate_label",
        "normalized_duplicate",
        "illegal_edge",
        "nesting_violation",
    }:
        if (
            ("paragraph:" in detail or "subparagraph:" in detail)
            and "/subsection:" in path
        ):
            return "base_text_flattened_sublist_family"
        return "base_text_shape"

    if violation_type == "illegal_edge":
        if "paragraph inside section" in detail or "subparagraph inside section" in detail:
            return "illegal_edge_section_child"
        return "illegal_edge"

    if violation_type in {"duplicate_label", "normalized_duplicate"}:
        if (
            ("paragraph:" in detail or "subparagraph:" in detail)
            and "/subsection:" in path
        ):
            return "flattened_sublist_family"
        if source == "finding_ledger" and phase_scope == "replay_fold_only":
            return "pre_dedup_duplicate_label"

    if violation_type == "nesting_violation":
        return "generic_nesting_violation"
    if violation_type == "sort_order":
        return "sort_order"
    return violation_type or "other"


def _annotate_phase_scope(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Annotate each row with inferred phase and grouped phase scope."""
    grouped_phases: dict[tuple[str, str, str, str], set[str]] = {}
    for row in rows:
        inferred_phase = _infer_phase(row)
        row["inferred_phase"] = inferred_phase
        key = (
            row["statute_id"],
            row["violation_type"],
            row["path"],
            row["detail"],
        )
        grouped_phases.setdefault(key, set()).add(inferred_phase)

    for row in rows:
        key = (
            row["statute_id"],
            row["violation_type"],
            row["path"],
            row["detail"],
        )
        row["phase_scope"] = _phase_scope_for(grouped_phases.get(key, set()))
        row["detector_family"] = _detector_family_for(row)
    return rows


def _audit_one(norm_id: str) -> list[dict[str, str]]:
    """Replay one statute and collect all tree/product invariant violations.

    Returns a list of row dicts. An empty list means no violations (or a clean
    replay). A single row with violation_type="ERROR" means replay failed.
    """
    try:
        from lawvm.finland.grafter import replay_xml
        from lawvm.tools.replay_plan import build_replay_plan_inspection

        plan_bundle = build_replay_plan_inspection(
            SimpleNamespace(
                statute_id=norm_id,
                mode="finlex_oracle",
                strict=False,
                oracle_selector_mode="bench_comparable",
            )
        )
        chain_length = str(len(plan_bundle.get("amendment_chain") or []))
        oracle_suspect = str(plan_bundle.get("oracle_suspect") or "")

        replay_meta: dict[str, object] = {}
        replay_result = replay_xml(
            norm_id,
            mode="legal_pit",
            quiet=True,
            replay_meta_out=replay_meta,
        )

        rows: list[dict[str, str]] = []

        # Signal 1: replay-owned runtime invariant findings
        seen: set[tuple[str, str, str]] = set()
        for finding in replay_result.findings:
            if str(getattr(finding, "kind", "") or "") != "RUNTIME.VIOLATION":
                continue
            raw_detail = dict(getattr(finding, "detail", {}) or {})
            barrier_code = str(raw_detail.get("barrier_code") or "")
            if barrier_code not in (
                "APPLY.TREE_INVARIANT_VIOLATION",
                "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",
            ):
                continue
            violation_str = str(raw_detail.get("violation") or raw_detail.get("message") or "")
            phase = str(raw_detail.get("phase") or "")
            vtype, path, detail = _classify_violation(violation_str)
            _append_violation_row(
                rows,
                seen,
                norm_id=norm_id,
                violation_type=vtype,
                path=path,
                detail=detail,
                source="finding_ledger",
                adj_kind=barrier_code,
                phase=phase,
                chain_length=chain_length,
                oracle_suspect=oracle_suspect,
            )

        # Signal 2: typed invariant metadata preferred over legacy strings
        typed_replay_violations = _coerce_typed_tree_violation_records(
            replay_meta.get("typed_invariant_violations")
        )
        for record in typed_replay_violations:
            vtype, path, detail = _classify_typed_tree_violation(record)
            _append_violation_row(
                rows,
                seen,
                norm_id=norm_id,
                violation_type=vtype,
                path=path,
                detail=detail,
                source="replay_meta_tree",
                adj_kind="APPLY.TREE_INVARIANT_VIOLATION",
                phase="",
                chain_length=chain_length,
                oracle_suspect=oracle_suspect,
            )

        typed_product_raw = replay_meta.get("typed_product_tree_invariant_violations")
        typed_product_violations: list[dict[str, object]] = []
        if isinstance(typed_product_raw, dict):
            for product_phase, records in typed_product_raw.items():
                for record in _coerce_typed_tree_violation_records(records):
                    record = dict(record)
                    record["product_phase"] = str(product_phase)
                    typed_product_violations.append(record)
        for record in typed_product_violations:
            vtype, path, detail = _classify_typed_tree_violation(record)
            product_phase = str(record.get("product_phase") or "")
            _append_violation_row(
                rows,
                seen,
                norm_id=norm_id,
                violation_type=vtype,
                path=path,
                detail=detail,
                source="replay_meta_product",
                adj_kind="APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",
                phase="materialized" if product_phase == "materialized_tree" else "replay_fold",
                chain_length=chain_length,
                oracle_suspect=oracle_suspect,
            )

        # Signal 3: direct legacy invariant lists preserved in replay metadata
        for source_name, barrier_code, violations_raw in (
            (
                "replay_meta_tree",
                "APPLY.TREE_INVARIANT_VIOLATION",
                None if typed_replay_violations else replay_meta.get("invariant_violations"),
            ),
            (
                "replay_meta_product",
                "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",
                replay_meta.get("product_invariant_violations"),
            ),
        ):
            if not isinstance(violations_raw, list):
                continue
            for raw_violation in violations_raw:
                violation_str = str(raw_violation)
                if (
                    source_name == "replay_meta_product"
                    and typed_product_violations
                    and (
                        violation_str.startswith("replay_fold_tree:")
                        or violation_str.startswith("materialized_tree:")
                    )
                ):
                    continue
                vtype, path, detail = _classify_violation(violation_str)
                _append_violation_row(
                    rows,
                    seen,
                    norm_id=norm_id,
                    violation_type=vtype,
                    path=path,
                    detail=detail,
                    source=source_name,
                    adj_kind=barrier_code,
                    phase="",
                    chain_length=chain_length,
                    oracle_suspect=oracle_suspect,
                )

        return rows

    except Exception:
        tb = traceback.format_exc().strip().splitlines()
        # Keep last 2 lines of traceback as concise error detail
        short_err = " | ".join(line.strip() for line in tb[-2:] if line.strip())
        return [{
            "statute_id": norm_id,
            "status": "error",
            "violation_type": "ERROR",
            "path": "",
            "detail": short_err[:400],
            "source": "compile_error",
            "adj_kind": "",
            "phase": "",
            "chain_length": "",
            "oracle_suspect": "",
        }]


def _normalize_id(raw_id: str) -> str:
    """Strip -NNN amendment suffix so '1896/37-000' becomes '1896/37'."""
    if "-" in raw_id:
        parts = raw_id.rsplit("-", 1)
        if parts[1].isdigit():
            return parts[0]
    return raw_id


def load_corpus(corpus_path: Path) -> list[str]:
    """Load statute IDs from a text file, normalize, and deduplicate them."""
    ids: list[str] = []
    seen: set[str] = set()
    with corpus_path.open() as f:
        for line in f:
            sid = line.strip()
            if sid and not sid.startswith("#"):
                normalized = _normalize_id(sid)
                if normalized not in seen:
                    seen.add(normalized)
                    ids.append(normalized)
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Corpus-wide tree invariant audit for LawVM Finland statutes."
    )
    parser.add_argument(
        "--corpus",
        default=str(DEFAULT_CORPUS),
        help=f"Path to statute ID list (default: {DEFAULT_CORPUS})",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Output CSV path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel worker processes (default: 1)",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=0,
        help="Randomly sample this many statutes from the corpus (0 = all)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for sampling (default: 42)",
    )
    parser.add_argument(
        "--filter-phase-scope",
        metavar="SCOPE",
        default="",
        help=(
            "Only include rows matching this phase_scope in the summary. "
            "Values: replay_fold_only, materialized_only, both, unknown"
        ),
    )
    parser.add_argument(
        "--filter-detector-family",
        metavar="FAMILY",
        default="",
        help=(
            "Only include rows matching this detector_family in the summary. "
            "E.g. duplicate_label, flattened_sublist_family, illegal_edge_section_child"
        ),
    )
    parser.add_argument(
        "--filter-violation-type",
        metavar="TYPE",
        default="",
        help=(
            "Only include rows matching this violation_type in the summary. "
            "E.g. duplicate_label, illegal_edge, sort_order"
        ),
    )
    parser.add_argument(
        "--min-chain-length",
        type=int,
        default=0,
        help="Only include rows where chain_length >= this value in the summary (default: 0 = all)",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print summary only; skip writing the CSV output file",
    )
    args = parser.parse_args(argv)

    corpus_path = Path(args.corpus)
    if not corpus_path.exists():
        print(f"ERROR: corpus file not found: {corpus_path}", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ids = load_corpus(corpus_path)
    if not ids:
        print("ERROR: corpus is empty", file=sys.stderr)
        return 1

    if args.sample_size and args.sample_size < len(ids):
        rng = random.Random(args.seed)
        ids = rng.sample(ids, args.sample_size)
        print(f"Sampling {len(ids)} statutes (seed={args.seed})")
    else:
        print(f"Processing all {len(ids)} statutes from corpus")

    print(f"Workers: {args.workers}")
    print(f"Output: {output_path}")
    print()

    all_rows: list[dict[str, str]] = []
    error_count = 0
    violation_count = 0
    processed = 0
    start = time.monotonic()

    fieldnames = [
        "statute_id",
        "status",
        "violation_type",
        "path",
        "detail",
        "source",
        "adj_kind",
        "phase",
        "chain_length",
        "oracle_suspect",
        "inferred_phase",
        "phase_scope",
        "detector_family",
    ]

    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(_audit_one, sid): sid for sid in ids}
            for fut in as_completed(futures):
                sid = futures[fut]
                try:
                    rows = fut.result()
                except Exception as exc:
                    rows = [{
                        "statute_id": sid,
                        "status": "error",
                        "violation_type": "ERROR",
                        "path": "",
                        "detail": str(exc)[:400],
                        "source": "worker_error",
                        "adj_kind": "",
                        "phase": "",
                        "chain_length": "",
                        "oracle_suspect": "",
                    }]
                processed += 1
                for row in rows:
                    if row["violation_type"] == "ERROR":
                        error_count += 1
                    else:
                        violation_count += 1
                all_rows.extend(rows)
                if processed % 100 == 0:
                    elapsed = time.monotonic() - start
                    rate = processed / elapsed if elapsed > 0 else 0
                    print(
                        f"  {processed}/{len(ids)} processed  "
                        f"violations={violation_count}  errors={error_count}  "
                        f"{rate:.1f} stat/s"
                    )
    else:
        for i, sid in enumerate(ids):
            rows = _audit_one(sid)
            processed += 1
            for row in rows:
                if row["violation_type"] == "ERROR":
                    error_count += 1
                else:
                    violation_count += 1
            all_rows.extend(rows)
            if processed % 50 == 0:
                elapsed = time.monotonic() - start
                rate = processed / elapsed if elapsed > 0 else 0
                print(
                    f"  {processed}/{len(ids)} processed  "
                    f"violations={violation_count}  errors={error_count}  "
                    f"{rate:.1f} stat/s"
                )

    all_rows = _annotate_phase_scope(all_rows)

    elapsed = time.monotonic() - start
    print(f"\nDone in {elapsed:.1f}s — {processed} statutes")

    # Write CSV (unless --summary-only)
    if not args.summary_only:
        with output_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_rows)
        print(f"Wrote {len(all_rows)} rows to {output_path}")

    # Apply summary filters
    violation_rows = [r for r in all_rows if r["violation_type"] != "ERROR"]
    summary_rows = violation_rows

    filter_active = False
    filter_desc_parts: list[str] = []

    if args.filter_phase_scope:
        summary_rows = [r for r in summary_rows if r.get("phase_scope") == args.filter_phase_scope]
        filter_desc_parts.append(f"phase_scope={args.filter_phase_scope!r}")
        filter_active = True

    if args.filter_detector_family:
        summary_rows = [r for r in summary_rows if r.get("detector_family") == args.filter_detector_family]
        filter_desc_parts.append(f"detector_family={args.filter_detector_family!r}")
        filter_active = True

    if args.filter_violation_type:
        summary_rows = [r for r in summary_rows if r.get("violation_type") == args.filter_violation_type]
        filter_desc_parts.append(f"violation_type={args.filter_violation_type!r}")
        filter_active = True

    if args.min_chain_length:
        summary_rows = [
            r for r in summary_rows
            if r.get("chain_length", "").strip().lstrip("-").isdigit()
            and int(r["chain_length"]) >= args.min_chain_length
        ]
        filter_desc_parts.append(f"chain_length>={args.min_chain_length}")
        filter_active = True

    # Summary
    print("\n=== Summary ===")
    if filter_active:
        print(f"Active filters: {', '.join(filter_desc_parts)}")
        print(f"Rows after filter: {len(summary_rows)} of {len(violation_rows)} violation rows")
    print(f"Total statutes processed : {processed}")
    print(f"Statutes with violations : {len({r['statute_id'] for r in violation_rows})}")
    print(f"Total violations         : {len(violation_rows)}")
    print(f"Compile errors           : {error_count}")

    if summary_rows:
        print("\nTop violation types:")
        type_counts: Counter[str] = Counter(r["violation_type"] for r in summary_rows)
        for vtype, count in type_counts.most_common(10):
            print(f"  {vtype:35s}  {count:6d}")

        print("\nPhase scopes:")
        scope_counts: Counter[str] = Counter(r["phase_scope"] for r in summary_rows)
        for scope, count in scope_counts.most_common(10):
            print(f"  {scope:35s}  {count:6d}")

        print("\nDetector families:")
        detector_counts: Counter[str] = Counter(r["detector_family"] for r in summary_rows)
        for family, count in detector_counts.most_common(10):
            print(f"  {family:35s}  {count:6d}")

        print("\nTop violation details (by pattern):")
        detail_counts: Counter[str] = Counter(r["detail"] for r in summary_rows)
        for detail, count in detail_counts.most_common(15):
            print(f"  {count:6d}  {detail[:80]}")

        if filter_active:
            print(f"\nMatching statutes ({len({r['statute_id'] for r in summary_rows})}):")
            statute_counts: Counter[str] = Counter(r["statute_id"] for r in summary_rows)
            for sid, count in statute_counts.most_common(20):
                print(f"  {sid:20s}  {count:4d} violation(s)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
