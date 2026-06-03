"""Export fi_he_branch_ops.parquet from fi_government_proposal.farchive.

Reads FULL_AKN HE entries from fi_government_proposal.farchive, parses
amendment-proposal sections into typed BranchProposedOp records, and writes
the projection to data/fi/v1/fi_he_branch_ops.parquet.

Columns in fi_he_branch_ops.parquet:
  branch_id           — 'fi/he/{year}/{number}'
  he_id               — 'HE 184/2024 vp'
  he_year             — int
  he_number           — int
  proposed_voimaantulo — ISO date string or None
  op_index            — int (0-based, per HE)
  operation_kind      — replace | insert | repeal | relabel | commencement | expiry
  target_provision_ref — e.g. '711/2022/7/3'
  target_statute_id   — e.g. '711/2022'
  payload_summary     — str (200 char cap)
  source_span_text    — str (500 char cap, the raw clause text)
  source_span_preamble — str (stripped 'Ehdotetaan, että' text)
  parse_confidence    — float 0.0–1.0
  target_resolution   — resolved | unresolved | proposal_relative | ambiguous
  is_proposal_relative — bool
  parse_status        — full | partial | failed | not_applicable

CLI: lawvm export-fi-he-branch-ops [--data-dir DIR] [--farchive PATH]
     [--limit N] [--year-range Y1:Y2] [--strict] [--verbose] [--dry-run]

AGENTS.md compliance
--------------------
§1.8  No source lane disappearance: parse failures emit HEBranchParseRun
       with per-HE failure records.
§1.9  Typed primitives throughout.
§1.10 No bare try/except; exceptions at bounded XML parse boundaries only.

Phase: Emit evidence (§6 phase 11).
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lawvm.finland.he_branch_parser import (
    HEParsedBranch,
    HEParseStatus,
    branch_to_parquet_rows,
    parse_he_branch,
)

SCHEMA_VERSION = "v1"
_DEFAULT_DATA_DIR = "data/fi/v1"
_DEFAULT_FARCHIVE = "data/fi_government_proposal.farchive"
_AKN_PATH_PREFIX = "akn/fi/doc/government-proposal/"

# ---------------------------------------------------------------------------
# Run provenance
# ---------------------------------------------------------------------------


@dataclass
class HEBranchProjectionRun:
    """Provenance record for one fi_he_branch_ops.parquet build run."""

    farchive_path: str
    data_dir: str
    started_at: str  # ISO datetime
    he_count: int = 0
    ops_count: int = 0
    full_count: int = 0
    partial_count: int = 0
    failed_count: int = 0
    not_applicable_count: int = 0
    pdf_wrapper_skipped: int = 0
    elapsed_sec: float = 0.0
    failures: List[Dict[str, Any]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.failures is None:
            self.failures = []


# ---------------------------------------------------------------------------
# Main projection function
# ---------------------------------------------------------------------------


def project_he_branch_ops(
    *,
    farchive_path: str = _DEFAULT_FARCHIVE,
    data_dir: str = _DEFAULT_DATA_DIR,
    limit: Optional[int] = None,
    year_range: Optional[Tuple[int, int]] = None,
    strict: bool = False,
    verbose: bool = False,
    dry_run: bool = False,
) -> HEBranchProjectionRun:
    """Build fi_he_branch_ops.parquet from fi_government_proposal.farchive.

    Parameters
    ----------
    farchive_path:
        Path to fi_government_proposal.farchive.
    data_dir:
        Output directory for the Parquet file.
    limit:
        Process only the first N HEs (debug).
    year_range:
        (y1, y2) inclusive year range filter.
    strict:
        Treat parse failures as hard errors.
    verbose:
        Print per-HE progress.
    dry_run:
        Parse but do not write Parquet output.

    Returns
    -------
    HEBranchProjectionRun with provenance + counts.
    """
    try:
        import pyarrow as pa  # type: ignore[import]
        import pyarrow.parquet as pq  # type: ignore[import]
    except ImportError:
        print(
            "ERROR: pyarrow not installed; run: uv pip install 'lawvm[analytics]'",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        from farchive import Farchive  # type: ignore[import]
    except ImportError:
        print("ERROR: farchive package not available", file=sys.stderr)
        sys.exit(1)

    started_at = datetime.now(timezone.utc)
    run = HEBranchProjectionRun(
        farchive_path=farchive_path,
        data_dir=data_dir,
        started_at=started_at.isoformat(),
    )

    fa_path = Path(farchive_path)
    if not fa_path.exists():
        print(f"ERROR: farchive not found: {farchive_path}", file=sys.stderr)
        sys.exit(1)

    farchive = Farchive(str(fa_path))

    # Collect all fin@ main.xml locators
    locators: list[str] = []
    for loc in farchive.list():
        if not isinstance(loc, str):
            continue
        if not loc.startswith(_AKN_PATH_PREFIX):
            continue
        if "/fin@/main.xml" not in loc:
            continue
        locators.append(loc)

    locators.sort()

    # Apply year_range filter
    if year_range is not None:
        y1, y2 = year_range
        filtered: list[str] = []
        for loc in locators:
            rest = loc[len(_AKN_PATH_PREFIX):]
            parts = rest.split("/", 2)
            if len(parts) < 2:
                continue
            try:
                y = int(parts[0])
            except ValueError:
                continue
            if y1 <= y <= y2:
                filtered.append(loc)
        locators = filtered

    if limit is not None:
        locators = locators[:limit]

    total = len(locators)
    print(
        f"  {total:,} FULL_AKN HE locators to process"
        + (" (dry-run)" if dry_run else ""),
        file=sys.stderr,
    )

    all_rows: list[dict[str, Any]] = []
    done = 0
    t0 = time.monotonic()

    for loc in locators:
        # Extract year/number from locator path
        rest = loc[len(_AKN_PATH_PREFIX):]
        parts = rest.split("/", 3)
        if len(parts) < 3:
            done += 1
            continue
        try:
            he_year = int(parts[0])
            he_number = int(parts[1])
        except ValueError:
            done += 1
            continue

        # Read blob from farchive
        blob: Optional[bytes] = None
        metadata: dict = {}
        try:
            span = farchive.resolve(loc)
            if span is not None:
                blob = farchive.read(span)
                metadata = farchive.meta(span) or {}
        except Exception as exc:
            run.failures.append({
                "loc": loc,
                "reason": "farchive_read_error",
                "detail": str(exc)[:200],
            })
            done += 1
            continue

        if blob is None:
            done += 1
            continue

        # Skip PDF_WRAPPER HEs (metadata-only)
        structural_tier = metadata.get("structural_tier", "")
        if structural_tier == "pdf_wrapper":
            run.pdf_wrapper_skipped += 1
            done += 1
            continue

        he_id = metadata.get("he_id", f"HE {he_number}/{he_year}")

        # Parse the HE branch ops
        branch = parse_he_branch(
            blob,
            he_year=he_year,
            he_number=he_number,
            he_id=he_id,
            strict=strict,
        )

        run.he_count += 1

        if branch.parse_status == HEParseStatus.FULL:
            run.full_count += 1
        elif branch.parse_status == HEParseStatus.PARTIAL:
            run.partial_count += 1
        elif branch.parse_status == HEParseStatus.FAILED:
            run.failed_count += 1
            run.failures.append({
                "loc": loc,
                "he_id": he_id,
                "reason": "parse_failed",
                "findings": [
                    {"rule_id": getattr(f, "rule_id", "?"), "detail": getattr(f, "detail", "")}
                    for f in branch.parse_findings[:5]
                ],
            })
        elif branch.parse_status == HEParseStatus.NOT_APPLICABLE:
            run.not_applicable_count += 1

        rows = branch_to_parquet_rows(branch)
        all_rows.extend(rows)
        run.ops_count += len(rows)

        done += 1
        if verbose or (done % 200 == 0):
            print(
                f"  [{done}/{total}] HE {he_year}/{he_number} "
                f"status={branch.parse_status.value} "
                f"ops={len(rows)}",
                file=sys.stderr,
            )

        if strict and branch.parse_status == HEParseStatus.FAILED:
            print(
                f"STRICT MODE: aborting on parse failure for HE {he_year}/{he_number}",
                file=sys.stderr,
            )
            break

    farchive.close()
    run.elapsed_sec = time.monotonic() - t0

    if not dry_run and all_rows:
        _write_parquet(all_rows, data_dir=data_dir)

    return run


def _write_parquet(rows: list[dict[str, Any]], *, data_dir: str) -> None:
    """Write rows to fi_he_branch_ops.parquet under data_dir."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    out_dir = Path(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "fi_he_branch_ops.parquet"

    # Build schema with explicit types for stability (AGENTS.md §1.9)
    schema = pa.schema([
        ("branch_id", pa.string()),
        ("he_id", pa.string()),
        ("he_year", pa.int32()),
        ("he_number", pa.int32()),
        ("proposed_voimaantulo", pa.string()),  # nullable ISO date string
        ("op_index", pa.int32()),
        ("operation_kind", pa.string()),
        ("target_provision_ref", pa.string()),
        ("target_statute_id", pa.string()),
        ("payload_summary", pa.string()),
        ("source_span_text", pa.string()),
        ("source_span_preamble", pa.string()),
        ("parse_confidence", pa.float32()),
        ("target_resolution", pa.string()),
        ("is_proposal_relative", pa.bool_()),
        ("parse_status", pa.string()),
    ])

    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, str(out_path), compression="zstd")
    print(f"  Written: {out_path} ({len(rows):,} rows)", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(args: object) -> None:
    """CLI entry point for lawvm export-fi-he-branch-ops."""
    farchive_path = str(getattr(args, "farchive", None) or _DEFAULT_FARCHIVE)
    data_dir = str(getattr(args, "data_dir", None) or _DEFAULT_DATA_DIR)
    limit_raw = getattr(args, "limit", None)
    limit: Optional[int] = int(limit_raw) if limit_raw is not None else None
    year_range_raw: Optional[str] = getattr(args, "year_range", None)
    year_range: Optional[Tuple[int, int]] = None
    if year_range_raw:
        parts = year_range_raw.split(":")
        if len(parts) == 2:
            year_range = (int(parts[0]), int(parts[1]))
        else:
            print(f"ERROR: --year-range must be Y1:Y2, got {year_range_raw!r}", file=sys.stderr)
            sys.exit(1)

    strict = bool(getattr(args, "strict", False))
    verbose = bool(getattr(args, "verbose", False))
    dry_run = bool(getattr(args, "dry_run", False))

    run = project_he_branch_ops(
        farchive_path=farchive_path,
        data_dir=data_dir,
        limit=limit,
        year_range=year_range,
        strict=strict,
        verbose=verbose,
        dry_run=dry_run,
    )

    print("\nBranch-ops projection complete:", file=sys.stderr)
    print(f"  HEs processed:     {run.he_count:,}", file=sys.stderr)
    print(f"  Ops emitted:       {run.ops_count:,}", file=sys.stderr)
    print(f"  FULL:              {run.full_count:,}", file=sys.stderr)
    print(f"  PARTIAL:           {run.partial_count:,}", file=sys.stderr)
    print(f"  FAILED:            {run.failed_count:,}", file=sys.stderr)
    print(f"  NOT_APPLICABLE:    {run.not_applicable_count:,}", file=sys.stderr)
    print(f"  PDF_WRAPPER skip:  {run.pdf_wrapper_skipped:,}", file=sys.stderr)
    print(f"  Elapsed:           {run.elapsed_sec:.1f}s", file=sys.stderr)
    if run.failures:
        print(f"  Failures (first 5):", file=sys.stderr)
        for f in run.failures[:5]:
            print(f"    {f}", file=sys.stderr)
