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

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from lawvm.tools.tier2_state import (
    DEFAULT_SCHEMA_VERSION,
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
    tier_1_deps: tuple[str, ...]      # e.g. ("finlex.farchive",)
    tier_2_deps: tuple[str, ...]      # e.g. ("fi_refs",)
    description: str


# Registry of all known Finland Tier 2 projections.
# Each entry here causes rebuild-indexes to regenerate that projection.
# Out-of-scope projections are left untouched.
_FI_PROJECTIONS: tuple[ProjectionSpec, ...] = (
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
        name="fi_he_branch_ops",
        tier_1_deps=("fi_government_proposal.farchive",),
        tier_2_deps=(),
        description="HE proposed amendment ops (branch ops; backs pit-timeline branch view)",
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
    ProjectionSpec(
        name="fi_sections_text",
        tier_1_deps=("finlex.farchive",),
        tier_2_deps=(),
        description="Oracle section-text projection (current consolidated text per section)",
    ),
)

_PROJECTIONS_BY_JURISDICTION: Dict[str, tuple[ProjectionSpec, ...]] = {
    "fi": _FI_PROJECTIONS,
}


def _projections_for(jurisdiction: str) -> tuple[ProjectionSpec, ...]:
    return _PROJECTIONS_BY_JURISDICTION.get(jurisdiction, ())


# ---------------------------------------------------------------------------
# Farchive hash helpers
# ---------------------------------------------------------------------------


# Canonical hashing lives in tier2_state so producers and the READ-side
# freshness guard agree byte-for-byte. These thin wrappers preserve the
# existing call sites/signatures.


def _farchive_hash(data_dir: str, farchive_name: str) -> str:
    """Lightweight size+mtime fingerprint of a farchive (see tier2_state)."""
    from lawvm.tools.tier2_state import farchive_hash

    return farchive_hash(data_dir, farchive_name)


def _primary_farchive_hash(
    data_dir: str,
    spec: ProjectionSpec,
) -> str:
    """Combined hash of all Tier 1 deps for a projection (see tier2_state)."""
    from lawvm.tools.tier2_state import primary_farchive_hash

    return primary_farchive_hash(data_dir, spec.tier_1_deps)


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
    compile_metadata: Optional[Any] = None,
    workers: int = 0,
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
        "rebuild_status": "ok",
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
            compile_metadata=compile_metadata,
            workers=workers,
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
        result["rebuild_status"] = "error"
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
    compile_metadata: Optional[Any] = None,
    workers: int = 0,
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
            compile_metadata=compile_metadata,
        )

    if name in ("fi_refs", "fi_actors", "fi_pools", "fi_preparatory_refs"):
        return _rebuild_fi_crosslink_projection(
            name=name,
            data_dir=data_dir,
            out_dir=out_dir,
            compile_metadata=compile_metadata,
            workers=workers,
        )

    if name == "fi_he_branch_ops":
        return _rebuild_he_branch_ops_projection(
            data_dir=data_dir,
            out_dir=out_dir,
            verbose=verbose,
            compile_metadata=compile_metadata,
        )

    if name == "fi_inline_citations":
        return _rebuild_fi_inline_citations_projection(
            data_dir=data_dir,
            out_dir=out_dir,
            compile_metadata=compile_metadata,
            workers=workers,
        )

    if name == "fi_sections_text":
        return _rebuild_fi_sections_text_projection(
            data_dir=data_dir,
            out_dir=out_dir,
            compile_metadata=compile_metadata,
            workers=workers,
        )

    if name in ("statutes", "sections", "findings", "ops"):
        return _rebuild_core_projections(
            name=name,
            data_dir=data_dir,
            out_dir=out_dir,
            jurisdiction=jurisdiction,
            compile_metadata=compile_metadata,
            workers=workers,
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
    compile_metadata: Optional[Any] = None,
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
        compile_metadata=compile_metadata,
    )
    # Return the count for the specific projection being rebuilt
    return counts.get(spec.name, 0)


def _rebuild_fi_crosslink_projection(
    *,
    name: str,
    data_dir: str,
    out_dir: Path,
    compile_metadata: Optional[Any] = None,
    workers: int = 0,
) -> int:
    """Rebuild fi_refs / fi_actors / fi_pools from finlex.farchive corpus.

    Per-statute projection is sharded across ``workers`` processes; rows are
    reassembled in corpus order so the output is byte-identical to the serial
    (workers=1) build.
    """
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
        return export_fi_refs(corpus, data_dir=str(out_dir), use_parquet=True, compile_metadata=compile_metadata, workers=workers)

    if name == "fi_actors":
        from lawvm.tools.export_fi_actors import export_fi_actors
        return export_fi_actors(corpus, data_dir=str(out_dir), use_parquet=True, compile_metadata=compile_metadata, workers=workers)

    if name == "fi_pools":
        from lawvm.tools.export_fi_pools import export_fi_pools
        return export_fi_pools(corpus, data_dir=str(out_dir), use_parquet=True, compile_metadata=compile_metadata, workers=workers)

    if name == "fi_preparatory_refs":
        from lawvm.tools.export_fi_preparatory_refs import export_fi_preparatory_refs
        return export_fi_preparatory_refs(corpus, data_dir=str(out_dir), use_parquet=True, compile_metadata=compile_metadata, workers=workers)

    return 0


def _rebuild_he_branch_ops_projection(
    *,
    data_dir: str,
    out_dir: Path,
    verbose: bool,
    compile_metadata: Optional[Any] = None,
) -> int:
    """Rebuild fi_he_branch_ops from fi_government_proposal.farchive."""
    from lawvm.tools.export_fi_he_branch_ops import project_he_branch_ops

    farchive_path = str(Path(data_dir) / "fi_government_proposal.farchive")
    if not Path(farchive_path).exists():
        print(
            f"  SKIP fi_he_branch_ops: farchive not found at {farchive_path}",
            file=sys.stderr,
        )
        return 0

    run = project_he_branch_ops(
        farchive_path=farchive_path,
        data_dir=str(out_dir),
        verbose=verbose,
        compile_metadata=compile_metadata,
    )
    return run.ops_count


def _rebuild_fi_inline_citations_projection(
    *,
    data_dir: str,
    out_dir: Path,
    compile_metadata: Optional[Any] = None,
    workers: int = 0,
) -> int:
    """Rebuild fi_inline_citations from finlex.farchive + fi_government_proposal.farchive.

    The statute phase is sharded across ``workers`` processes (corpus-ordered
    reassembly); the HE phase stays serial. Output is byte-identical to the
    serial build.
    """
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
        compile_metadata=compile_metadata,
        workers=workers,
    )


def _rebuild_fi_sections_text_projection(
    *,
    data_dir: str,
    out_dir: Path,
    compile_metadata: Optional[Any] = None,
    workers: int = 0,
) -> int:
    """Rebuild fi_sections_text from finlex.farchive oracle corpus.

    Per-statute projection is sharded across ``workers`` processes; rows are
    reassembled in corpus order so output is byte-identical to the serial build.
    """
    corpus = _load_default_fi_corpus(data_dir)
    if not corpus:
        print(
            f"  SKIP fi_sections_text: no corpus found in {data_dir}",
            file=sys.stderr,
        )
        return 0

    from lawvm.tools.export_fi_sections_text import export_fi_sections_text
    return export_fi_sections_text(
        corpus,
        data_dir=str(out_dir),
        use_parquet=True,
        compile_metadata=compile_metadata,
        workers=workers,
    )


def _rebuild_core_projections(
    *,
    name: str,
    data_dir: str,
    out_dir: Path,
    jurisdiction: str,
    compile_metadata: Optional[Any] = None,
    workers: int = 0,
) -> int:
    """Rebuild statutes / sections / findings / ops from export_parquet.

    export_projections already parallelizes per-statute replay across a process
    pool; thread the caller's --workers choice through so it is honored end-to-
    end (previously rebuild-indexes computed workers and dropped them, leaving
    export_projections on its own default). 0 = export_projections' auto.
    """
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
        compile_metadata=compile_metadata,
        workers=workers,
    )
    return counts.get(name, 0)


def _load_default_fi_corpus(data_dir: str) -> list[tuple[int, str]]:
    """Load the full Finnish farchive statute ID list for projection.

    Projection emitters need full corpus coverage (the curated
    bench_core.csv subset is for replay-benchmark scoring, not graph
    projections), so this enumerates ``store.list_statute_ids()`` over the
    finlex corpus.

    The corpus is resolved *relative to ``data_dir``* (consistent with how
    Tier-1 dependency hashes are resolved): if no finlex corpus is present
    under ``data_dir`` we return an empty list so the caller skips fast,
    instead of silently reaching past ``data_dir`` to a global default corpus
    and running a full multi-worker export. This keeps ``--data-dir`` honest
    (e.g. an empty test data dir does no real work) and avoids the
    cwd-relative-resolution footgun.
    """
    from lawvm.corpus_store import _archive_is_populated

    corpus_path = Path(data_dir) / "finlex.farchive"
    if not _archive_is_populated(corpus_path):
        return []

    from farchive import Farchive
    from lawvm.finland.transparent_store import TransparentCorpusStore

    store = TransparentCorpusStore(
        archive=Farchive(corpus_path, readonly=True),
        cache_only=True,
    )
    try:
        ids = list(store.list_statute_ids())
    finally:
        store.close()
    return [(0, sid) for sid in ids]


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
    compile_metadata: Optional[Any] = None,
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

    if compile_metadata is None:
        from lawvm.core.compile_metadata_default import build_default_compile_metadata
        source_bundle_hash = _primary_farchive_hash(
            data_dir, _projections_for(jurisdiction)[0]
        ) if _projections_for(jurisdiction) else "sha256:no-farchive"
        if not source_bundle_hash:
            source_bundle_hash = "sha256:no-farchive"
        compile_metadata = build_default_compile_metadata(
            jurisdiction=jurisdiction,
            source_bundle_hash=f"sha256:{source_bundle_hash}",
            build_id=f"cli.rebuild-indexes.{jurisdiction}",
            graph_store_root=Path(data_dir) / jurisdiction,
        )

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
            compile_metadata=compile_metadata,
            workers=workers,
        )

        if res["rebuild_status"] == "ok":
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


def _run_freshness_check(
    *,
    jurisdiction: str,
    data_dir: str,
    schema_version: str,
) -> None:
    """Report per-projection freshness vs the source farchive; exit non-zero if stale.

    Does NOT rebuild — read-only audit surface for CI / pre-flight checks.
    """
    from lawvm.tools.projection_freshness import sweep_freshness

    # The query commands read projections from data/{j}/{sv}; map the farchive
    # root data_dir to that projection dir for the sweep.
    projection_dir = str(tier2_dir(
        data_dir=data_dir,
        jurisdiction=jurisdiction,
        schema_version=schema_version,
    ))
    verdicts = sweep_freshness(
        projection_dir,
        jurisdiction=jurisdiction,
        schema_version=schema_version,
    )
    stale = [n for n, v in verdicts.items() if v.freshness_status in ("stale", "no_state")]
    print(f"freshness check: {jurisdiction}/{schema_version} ({projection_dir})")
    for name in sorted(verdicts):
        v = verdicts[name]
        print(f"  {name:<22} {v.freshness_status}")
    if stale:
        print(
            f"\n{len(stale)} projection(s) stale/missing-state: {', '.join(sorted(stale))}",
            file=sys.stderr,
        )
        print(
            f"Rebuild with:  lawvm rebuild-indexes -j {jurisdiction} --incremental",
            file=sys.stderr,
        )
        sys.exit(1)
    print("\nAll projections fresh.")


def main(args: Any) -> None:
    """CLI entry point for lawvm rebuild-indexes."""
    jurisdiction = getattr(args, "jurisdiction", "fi") or "fi"
    full = getattr(args, "full", False)
    # Default: incremental (unless --full is specified)
    incremental = not full
    workers_raw = getattr(args, "workers", 0)
    workers = int(workers_raw) if workers_raw else 0
    data_dir = getattr(args, "data_dir", None) or "data"
    schema_version = getattr(args, "schema_version", None) or DEFAULT_SCHEMA_VERSION
    verbose = getattr(args, "verbose", False)

    if getattr(args, "check", False):
        _run_freshness_check(
            jurisdiction=jurisdiction,
            data_dir=data_dir,
            schema_version=schema_version,
        )
        return

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
