"""lawvm sync-fi-proposals — acquire + project Finnish government proposals.

Two-step composition:
  1. Calls lawvm acquire-fi-proposals (he_acquisition.py) to update the farchive.
  2. Calls export_fi_he_corpus.project_he_corpus() to rebuild all four Parquet
     projections under data/fi/v1/.

Both steps are separately invokable; this command provides one ergonomic
entrypoint per the feature brief.

Usage:
    lawvm sync-fi-proposals
    lawvm sync-fi-proposals --source ~/Downloads/government-proposal.zip
    lawvm sync-fi-proposals --full
    lawvm sync-fi-proposals --lang swe
    lawvm sync-fi-proposals --projection-only  # skip acquisition, just rebuild projections

Per JURISDICTION_CLI_TOOLING_CONTRACT.md §4: common flags.
"""
from __future__ import annotations

import sys
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Core composition function
# ---------------------------------------------------------------------------


def sync_fi_proposals(
    *,
    source: Optional[str] = None,
    farchive: Optional[str] = None,
    data_dir: Optional[str] = None,
    lang: str = "fin",
    full: bool = False,
    workers: int = 4,
    limit: Optional[int] = None,
    year_range: Optional[str] = None,
    strict: bool = False,
    verbose: bool = False,
    dry_run: bool = False,
    projection_only: bool = False,
    no_parquet: bool = False,
) -> Dict[str, Any]:
    """Acquire HEs into farchive and project to Parquet.

    Args:
        source:          Local path or https:// URL to government-proposal.zip.
        farchive:        Farchive DB path (default: data/fi_government_proposal.farchive).
        data_dir:        Output directory for projections (default: data/fi/v1).
        lang:            Language to project (default: fin).
        full:            If True, re-ingest all HEs (not incremental).
        workers:         Parallel zip-extract workers.
        limit:           Debug: process only first N HE groups.
        year_range:      Debug: Y1:Y2 year range filter.
        strict:          Abort on first acquisition failure.
        verbose:         Print per-HE progress.
        dry_run:         Parse and classify but do not write to farchive or disk.
        projection_only: Skip acquisition step; only rebuild projections.
        no_parquet:      Write JSONL only (no Parquet).

    Returns:
        Dict with keys 'ingest_run' (HEIngestRun or None) and
        'projection_counts' (dict of table -> row count).
    """
    _farchive_path = farchive or "data/fi_government_proposal.farchive"
    _data_dir = data_dir or "data/fi/v1"

    result: Dict[str, Any] = {
        "ingest_run": None,
        "projection_counts": {},
    }

    # --- Step 1: Acquire ---
    if not projection_only:
        from lawvm.finland.he_acquisition import acquire_fi_proposals

        year_range_parsed: Optional[tuple[int, int]] = None
        if year_range:
            parts = year_range.split(":")
            if len(parts) == 2:
                year_range_parsed = (int(parts[0]), int(parts[1]))
            else:
                print(
                    f"error: --year-range must be Y1:Y2, got {year_range!r}",
                    file=sys.stderr,
                )
                sys.exit(1)

        print("Step 1: Acquiring HE corpus ...", file=sys.stderr)
        run = acquire_fi_proposals(
            source=source,
            dest=_farchive_path,
            incremental=not full,
            workers=workers,
            limit=limit,
            year_range=year_range_parsed,
            strict=strict,
            verbose=verbose,
            dry_run=dry_run,
        )
        result["ingest_run"] = run

        print(
            f"  Acquisition complete: added={run.added:,} skipped={run.skipped:,} "
            f"failed={run.failed:,}",
            file=sys.stderr,
        )

        if strict and run.failures and run.failed > 0:
            print("STRICT MODE: aborting after acquisition failure.", file=sys.stderr)
            sys.exit(1)

    # --- Step 2: Project ---
    if not dry_run:
        from lawvm.tools.export_fi_he_corpus import project_he_corpus
        from lawvm.core.compile_metadata_default import build_default_compile_metadata
        from pathlib import Path as _Path
        import hashlib as _hashlib

        _farchive_p = _Path(_farchive_path)
        if _farchive_p.exists():
            _stat = _farchive_p.stat()
            _src_hash = _hashlib.sha256(
                f"{_stat.st_size}:{_stat.st_mtime_ns}".encode()
            ).hexdigest()
            _source_bundle_hash = f"sha256:{_src_hash}"
        else:
            _source_bundle_hash = "sha256:no-farchive"

        _compile_metadata = build_default_compile_metadata(
            jurisdiction="fi",
            source_bundle_hash=_source_bundle_hash,
            build_id="cli.sync-fi-proposals.fi",
        )

        print("Step 2: Projecting HE corpus to Parquet ...", file=sys.stderr)
        counts = project_he_corpus(
            farchive_path=_farchive_path,
            data_dir=_data_dir,
            lang=lang,
            limit=limit,
            use_parquet=not no_parquet,
            strict=strict,
            verbose=verbose,
            compile_metadata=_compile_metadata,
        )
        result["projection_counts"] = counts

        # Keep the Tier 2 freshness sidecars in lockstep with the farchive we
        # just projected from, so the READ-side freshness guard does not flag a
        # just-built projection as stale (these dedicated exporters historically
        # wrote only the parquet body, not the .state.json).
        from lawvm.tools.tier2_state import write_projection_state_after_export

        for _proj in (
            "fi_he_corpus",
            "fi_he_atoms",
            "fi_he_law_refs",
            "fi_he_signatures",
        ):
            if _proj in counts:
                try:
                    write_projection_state_after_export(
                        projection_dir=_data_dir,
                        projection_name=_proj,
                        row_count=int(counts[_proj]),
                        tier_1_dependencies=("fi_government_proposal.farchive",),
                    )
                except Exception as _exc:  # never fail the sync over a sidecar
                    print(
                        f"  warning: could not write {_proj}.state.json: {_exc}",
                        file=sys.stderr,
                    )

        print("\nProjection counts:", file=sys.stderr)
        for name, n in counts.items():
            print(f"  {name}: {n:,}", file=sys.stderr)
    else:
        print("  (Dry-run: skipping projection step)", file=sys.stderr)

    return result


# ---------------------------------------------------------------------------
# CLI entry point (called from cli.py)
# ---------------------------------------------------------------------------


def main(args: Any) -> None:
    source: Optional[str] = getattr(args, "source", None) or None
    farchive: Optional[str] = getattr(args, "farchive", None) or None
    data_dir: Optional[str] = getattr(args, "data_dir", None) or None
    lang: str = getattr(args, "lang", "fin") or "fin"
    full: bool = getattr(args, "full", False)
    workers: int = int(getattr(args, "workers", 4))
    limit_raw = getattr(args, "limit", None)
    limit: Optional[int] = int(limit_raw) if limit_raw is not None else None
    year_range: Optional[str] = getattr(args, "year_range", None)
    strict: bool = getattr(args, "strict", False)
    verbose: bool = getattr(args, "verbose", False)
    dry_run: bool = getattr(args, "dry_run", False)
    projection_only: bool = getattr(args, "projection_only", False)
    no_parquet: bool = getattr(args, "no_parquet", False)

    sync_fi_proposals(
        source=source,
        farchive=farchive,
        data_dir=data_dir,
        lang=lang,
        full=full,
        workers=workers,
        limit=limit,
        year_range=year_range,
        strict=strict,
        verbose=verbose,
        dry_run=dry_run,
        projection_only=projection_only,
        no_parquet=no_parquet,
    )
