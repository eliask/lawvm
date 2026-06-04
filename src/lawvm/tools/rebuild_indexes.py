"""lawvm rebuild-indexes — regenerate Tier 2 Parquet projections from Tier 1 farchive.

Formalizes the three-tier storage model for LawVM:

  Tier 1 — Farchive (immutable substrate): finlex.farchive,
            fi_government_proposal.farchive, etc.
  Tier 2 — Parquet+zstd indexes (regenerable): data/{jurisdiction}/{sv}/*.parquet
  Tier 3 — CLI on-demand computation: never bulk-exported.

Each projection declares its Tier 1 dependencies. Incremental mode skips
projections whose state files show they are up-to-date with the current
farchive state (matching source hash). Full mode unconditionally regenerates
all projections.

Usage:
    lawvm rebuild-indexes
    lawvm rebuild-indexes -j fi --full
    lawvm rebuild-indexes --incremental --workers 4
    lawvm rebuild-indexes --data-dir /mnt/lawvm-data --schema-version v2
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from lawvm.tools.tier2_state import (
    DEFAULT_SCHEMA_VERSION,
    ProjectionState,
    make_state,
    parquet_path_for,
    read_state,
    tier2_dir,
    write_state,
)


# ---------------------------------------------------------------------------
# Projection registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProjectionSpec:
    """Declares one Tier 2 projection and its dependencies.

    name:              Projection name (stem of the .parquet filename).
    tier_1_deps:       farchive filenames this projection depends on.
    tier_2_deps:       Other projection names this projection depends on.
    description:       Human-readable summary.
    """

    name: str
    tier_1_deps: tuple      # e.g. ("finlex.farchive",)
    tier_2_deps: tuple      # e.g. ("fi_refs",)
    description: str


# Registry of all known Finland Tier 2 projections.
# Each entry here causes rebuild-indexes to regenerate that projection.
# Out-of-scope projections are left untouched.
_FI_PROJECTIONS: tuple = (
    ProjectionSpec(
        name="fi_refs",
        tier_1_deps=("finlex.farchive",),
        tier_2_deps=(),
        description="ReferenceMention cross-statute citations",
    ),
    ProjectionSpec(
        name="fi_actors",
        tier_1_deps=("finlex.farchive",),
        tier_2_deps=(),
        description="ActorMention institutional actor mentions",
    ),
    ProjectionSpec(
        name="fi_pools",
        tier_1_deps=("finlex.farchive",),
        tier_2_deps=(),
        description="PoolMention budget-line/quantity mentions",
    ),
    ProjectionSpec(
        name="fi_he_corpus",
        tier_1_deps=("fi_government_proposal.farchive",),
        tier_2_deps=(),
        description="HE corpus metadata (one row per language variant)",
    ),
    ProjectionSpec(
        name="fi_he_atoms",
        tier_1_deps=("fi_government_proposal.farchive",),
        tier_2_deps=(),
        description="HE body structure atoms",
    ),
    ProjectionSpec(
        name="fi_he_law_refs",
        tier_1_deps=("fi_government_proposal.farchive",),
        tier_2_deps=(),
        description="HE typed citations to enacted statutes",
    ),
    ProjectionSpec(
        name="fi_he_signatures",
        tier_1_deps=("fi_government_proposal.farchive",),
        tier_2_deps=(),
        description="HE typed signature elements",
    ),
    ProjectionSpec(
        name="statutes",
        tier_1_deps=("finlex.farchive",),
        tier_2_deps=(),
        description="Statute metadata + aggregate scores",
    ),
    ProjectionSpec(
        name="sections",
        tier_1_deps=("finlex.farchive",),
        tier_2_deps=(),
        description="Section-level diff rows",
    ),
    ProjectionSpec(
        name="findings",
        tier_1_deps=("finlex.farchive",),
        tier_2_deps=(),
        description="Evidence findings",
    ),
    ProjectionSpec(
        name="ops",
        tier_1_deps=("finlex.farchive",),
        tier_2_deps=(),
        description="Compiled operation rows",
    ),
    ProjectionSpec(
        name="fi_preparatory_refs",
        tier_1_deps=("finlex.farchive",),
        tier_2_deps=(),
        description="PreparatoryReference legislative preparation chain refs",
    ),
    ProjectionSpec(
        name="fi_inline_citations",
        tier_1_deps=("finlex.farchive", "fi_government_proposal.farchive"),
        tier_2_deps=(),
        description="InlineCitation body-prose citations (court, EOA, OKa, statute, HE, VTV, EK)",
    ),
)

_PROJECTIONS_BY_JURISDICTION: Dict[str, tuple] = {
    "fi": _FI_PROJECTIONS,
}


def _projections_for(jurisdiction: str) -> tuple:
    return _PROJECTIONS_BY_JURISDICTION.get(jurisdiction, ())


# ---------------------------------------------------------------------------
# Farchive hash helpers
# ---------------------------------------------------------------------------


def _farchive_hash(data_dir: str, farchive_name: str) -> str:
    """Compute a lightweight hash of a farchive for staleness detection.

    Uses file size + mtime as a proxy (fast; deterministic for unchanged files).
    A full SHA-256 over multi-GB farchives would be prohibitively slow here.
    Returns a hex string suitable for state-file comparison.
    """
    path = Path(data_dir) / farchive_name
    if not path.exists():
        return ""
    stat = path.stat()
    # Stable: size + mtime_ns encoded as hex
    raw = f"{stat.st_size}:{stat.st_mtime_ns}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _primary_farchive_hash(
    data_dir: str,
    spec: ProjectionSpec,
) -> str:
    """Return the combined hash of all Tier 1 deps for a projection.

    Returns "" if ALL dependency farchives are absent (no data to hash).
    This ensures that state files pre-populated with "" hash correctly match
    subsequent incremental checks when no farchive exists.
    """
    parts = []
    for dep in spec.tier_1_deps:
        parts.append(_farchive_hash(data_dir, dep))

    # If all deps are absent (empty hash), propagate "" so incremental check works.
    if all(p == "" for p in parts):
        return ""

    combined = ":".join(parts).encode()
    return hashlib.sha256(combined).hexdigest()[:24]


# ---------------------------------------------------------------------------
# Staleness check
# ---------------------------------------------------------------------------


def _is_stale(
    parquet_path: Path,
    current_hash: str,
    schema_version: str,
) -> bool:
    """Return True if the projection needs regeneration.

    Stale conditions:
    - Parquet file does not exist.
    - State file does not exist.
    - source_farchive_hash in state file differs from current_hash.
    - schema_version in state file differs.
    """
    if not parquet_path.exists():
        return True
    state = read_state(parquet_path)
    if state is None:
        return True
    if state.source_farchive_hash != current_hash:
        return True
    if state.schema_version != schema_version:
        return True
    return False


# ---------------------------------------------------------------------------
# Per-projection rebuild dispatcher
# ---------------------------------------------------------------------------


def _rebuild_projection(
    spec: ProjectionSpec,
    *,
    jurisdiction: str,
    schema_version: str,
    data_dir: str,
    current_hash: str,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Rebuild one projection and write its state file.

    Returns a result dict with keys: name, row_count, elapsed, status, error.
    """
    t0 = time.time()
    out_dir = tier2_dir(
        data_dir=data_dir,
        jurisdiction=jurisdiction,
        schema_version=schema_version,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    parquet_out = out_dir / f"{spec.name}.parquet"

    result: Dict[str, Any] = {
        "name": spec.name,
        "row_count": 0,
        "elapsed": 0.0,
        "status": "ok",
        "error": None,
    }

    try:
        row_count = _dispatch_projection(
            spec=spec,
            jurisdiction=jurisdiction,
            data_dir=data_dir,
            out_dir=out_dir,
            parquet_out=parquet_out,
            verbose=verbose,
        )
        result["row_count"] = row_count

        # Write state file
        state = make_state(
            projection_name=spec.name,
            schema_version=schema_version,
            row_count=row_count,
            source_farchive_hash=current_hash,
            tier_1_dependencies=list(spec.tier_1_deps),
            tier_2_dependencies=list(spec.tier_2_deps),
        )
        write_state(parquet_out, state)

    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        print(f"  ERROR {spec.name}: {exc}", file=sys.stderr)

    result["elapsed"] = time.time() - t0
    return result


def _dispatch_projection(
    *,
    spec: ProjectionSpec,
    jurisdiction: str,
    data_dir: str,
    out_dir: Path,
    parquet_out: Path,
    verbose: bool,
) -> int:
    """Route each projection name to its existing emitter.

    Returns row count written.
    """
    name = spec.name

    # --- Finland statutory projections ---
    if name in ("fi_he_corpus", "fi_he_atoms", "fi_he_law_refs", "fi_he_signatures"):
        return _rebuild_he_corpus_projections(
            spec=spec,
            data_dir=data_dir,
            out_dir=out_dir,
            verbose=verbose,
        )

    if name in ("fi_refs", "fi_actors", "fi_pools", "fi_preparatory_refs"):
        return _rebuild_fi_crosslink_projection(
            name=name,
            data_dir=data_dir,
            out_dir=out_dir,
        )

    if name == "fi_inline_citations":
        return _rebuild_fi_inline_citations_projection(
            data_dir=data_dir,
            out_dir=out_dir,
        )

    if name in ("statutes", "sections", "findings", "ops"):
        return _rebuild_core_projections(
            name=name,
            data_dir=data_dir,
            out_dir=out_dir,
            jurisdiction=jurisdiction,
        )

    # Unknown projection — log and skip (not an error; allow forward-compat)
    print(f"  SKIP {name}: no emitter registered for this projection", file=sys.stderr)
    return 0


def _rebuild_he_corpus_projections(
    *,
    spec: ProjectionSpec,
    data_dir: str,
    out_dir: Path,
    verbose: bool,
) -> int:
    """Rebuild fi_he_* projections from fi_government_proposal.farchive."""
    from lawvm.tools.export_fi_he_corpus import project_he_corpus

    farchive_path = str(Path(data_dir) / "fi_government_proposal.farchive")
    if not Path(farchive_path).exists():
        print(
            f"  SKIP {spec.name}: farchive not found at {farchive_path}",
            file=sys.stderr,
        )
        return 0

    counts = project_he_corpus(
        farchive_path=farchive_path,
        data_dir=str(out_dir),
        use_parquet=True,
        verbose=verbose,
    )
    # Return the count for the specific projection being rebuilt
    return counts.get(spec.name, 0)


def _rebuild_fi_crosslink_projection(
    *,
    name: str,
    data_dir: str,
    out_dir: Path,
) -> int:
    """Rebuild fi_refs / fi_actors / fi_pools from finlex.farchive corpus."""
    # These emitters take a corpus list. Load from bench_core.csv if available.
    corpus = _load_default_fi_corpus(data_dir)
    if not corpus:
        print(
            f"  SKIP {name}: no corpus CSV found in {data_dir}",
            file=sys.stderr,
        )
        return 0

    if name == "fi_refs":
        from lawvm.tools.export_fi_refs import export_fi_refs
        return export_fi_refs(corpus, data_dir=str(out_dir), use_parquet=True)

    if name == "fi_actors":
        from lawvm.tools.export_fi_actors import export_fi_actors
        return export_fi_actors(corpus, data_dir=str(out_dir), use_parquet=True)

    if name == "fi_pools":
        from lawvm.tools.export_fi_pools import export_fi_pools
        return export_fi_pools(corpus, data_dir=str(out_dir), use_parquet=True)

    if name == "fi_preparatory_refs":
        from lawvm.tools.export_fi_preparatory_refs import export_fi_preparatory_refs
        return export_fi_preparatory_refs(corpus, data_dir=str(out_dir), use_parquet=True)

    return 0


def _rebuild_fi_inline_citations_projection(
    *,
    data_dir: str,
    out_dir: Path,
) -> int:
    """Rebuild fi_inline_citations from finlex.farchive + fi_government_proposal.farchive."""
    corpus = _load_default_fi_corpus(data_dir)
    if not corpus:
        print(
            "  SKIP fi_inline_citations: no corpus CSV found in {data_dir}",
            file=sys.stderr,
        )
        return 0

    he_farchive_path = str(Path(data_dir) / "fi_government_proposal.farchive")
    from lawvm.tools.export_fi_inline_citations import export_fi_inline_citations
    return export_fi_inline_citations(
        corpus,
        data_dir=str(out_dir),
        use_parquet=True,
        he_farchive_path=he_farchive_path,
    )


def _rebuild_core_projections(
    *,
    name: str,
    data_dir: str,
    out_dir: Path,
    jurisdiction: str,
) -> int:
    """Rebuild statutes / sections / findings / ops from export_parquet."""
    from lawvm.tools.export_parquet import export_projections

    corpus = _load_default_fi_corpus(data_dir)
    if not corpus:
        print(
            f"  SKIP {name}: no corpus CSV found in {data_dir}",
            file=sys.stderr,
        )
        return 0

    counts = export_projections(
        data_dir=str(out_dir),
        use_parquet=True,
        include_refs=False,
        include_actors=False,
        include_pools=False,
        include_he_corpus=False,
    )
    return counts.get(name, 0)


def _load_default_fi_corpus(data_dir: str) -> list:
    """Load the full Finnish farchive statute ID list for projection.

    Projection emitters need full corpus coverage (the curated
    bench_core.csv subset is for replay-benchmark scoring, not graph
    projections). Defaults to ``corpus="all"`` which enumerates
    ``store.list_statute_ids()``. The unused ``data_dir`` arg is
    retained for back-compat with callers.
    """
    from lawvm.tools.export_parquet import _load_corpus
    return _load_corpus("all")


# ---------------------------------------------------------------------------
# Main rebuild logic
# ---------------------------------------------------------------------------


def rebuild_indexes(
    *,
    jurisdiction: str = "fi",
    incremental: bool = True,
    workers: int = 0,
    data_dir: str = "data",
    schema_version: str = DEFAULT_SCHEMA_VERSION,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Regenerate Tier 2 Parquets from current Tier 1 farchive state.

    Args:
        jurisdiction:   Jurisdiction code (e.g. "fi").
        incremental:    If True, skip up-to-date projections. If False, rebuild all.
        workers:        Parallel workers (0 = auto = cpu_count - 2, min 1).
        data_dir:       Root data directory containing farchives + jurisdiction dirs.
        schema_version: Parquet namespace version (e.g. "v1").
        verbose:        Print per-projection progress.

    Returns:
        Summary dict: {jurisdiction, schema_version, rebuilt, skipped, errors, elapsed}.
    """
    if workers <= 0:
        workers = max(1, (os.cpu_count() or 2) - 2)

    specs = _projections_for(jurisdiction)
    if not specs:
        print(
            f"No projections registered for jurisdiction {jurisdiction!r}",
            file=sys.stderr,
        )
        return {
            "jurisdiction": jurisdiction,
            "schema_version": schema_version,
            "rebuilt": [],
            "skipped": [],
            "errors": [],
            "elapsed": 0.0,
        }

    t_start = time.time()
    rebuilt: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []

    print(
        f"rebuild-indexes: {jurisdiction}/{schema_version} "
        f"({'incremental' if incremental else 'full'}), "
        f"{len(specs)} projections",
    )

    for spec in specs:
        current_hash = _primary_farchive_hash(data_dir, spec)
        parquet_out = parquet_path_for(
            data_dir=data_dir,
            jurisdiction=jurisdiction,
            schema_version=schema_version,
            projection_name=spec.name,
        )

        if incremental and not _is_stale(parquet_out, current_hash, schema_version):
            if verbose:
                print(f"  skip  {spec.name} (up-to-date)")
            skipped.append(spec.name)
            continue

        print(f"  rebuild {spec.name}: {spec.description}")
        res = _rebuild_projection(
            spec,
            jurisdiction=jurisdiction,
            schema_version=schema_version,
            data_dir=data_dir,
            current_hash=current_hash,
            verbose=verbose,
        )

        if res["status"] == "ok":
            rebuilt.append(spec.name)
            print(
                f"    -> {res['row_count']:,} rows in {res['elapsed']:.1f}s"
            )
        else:
            errors.append(spec.name)

    elapsed = time.time() - t_start
    print(
        f"\nDone: {len(rebuilt)} rebuilt, {len(skipped)} skipped, "
        f"{len(errors)} errors in {elapsed:.1f}s"
    )

    return {
        "jurisdiction": jurisdiction,
        "schema_version": schema_version,
        "rebuilt": rebuilt,
        "skipped": skipped,
        "errors": errors,
        "elapsed": elapsed,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(args: Any) -> None:
    """CLI entry point for lawvm rebuild-indexes."""
    jurisdiction = getattr(args, "jurisdiction", "fi") or "fi"
    full = getattr(args, "full", False)
    incremental_flag = getattr(args, "incremental", False)
    # Default: incremental (unless --full is specified)
    incremental = not full
    workers_raw = getattr(args, "workers", 0)
    workers = int(workers_raw) if workers_raw else 0
    data_dir = getattr(args, "data_dir", None) or "data"
    schema_version = getattr(args, "schema_version", None) or DEFAULT_SCHEMA_VERSION
    verbose = getattr(args, "verbose", False)

    result = rebuild_indexes(
        jurisdiction=jurisdiction,
        incremental=incremental,
        workers=workers,
        data_dir=data_dir,
        schema_version=schema_version,
        verbose=verbose,
    )

    if result["errors"]:
        sys.exit(1)
