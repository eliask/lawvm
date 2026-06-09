"""Tier 2 storage state-file schema and I/O utilities.

Three-tier storage model (per TIER_2_STORAGE_ARCHITECTURE.md):

  Tier 1 — Farchive (immutable substrate)
      data/finlex.farchive, data/fi_government_proposal.farchive, etc.
      Full XML / text blobs / amendment sources. Internals only.

  Tier 2 — Parquet+zstd indexes (regenerable from Tier 1)
      data/{jurisdiction}/{schema_version}/{projection_name}.parquet
      data/{jurisdiction}/{schema_version}/{projection_name}.state.json
      Per-projection state files track rebuild provenance and enable
      incremental regeneration by comparing partition_hashes against
      current farchive state.

  Tier 3 — CLI on-demand computation (never bulk-exported)
      Materialized PIT views, per-HE bundles, topic-search results.
      Computed per CLI call; never bulk-exported. Hits farchive + Tier 2.

State file format (typed, per AGENTS.md §1.9):
    {
      "projection_name": "fi_refs",
      "schema_version": "v1",
      "row_count": 124567,
      "last_rebuild_at": "2026-06-04T12:34:56Z",
      "source_farchive_hash": "abc123...",
      "tier_1_dependencies": ["finlex.farchive"],
      "tier_2_dependencies": [],
      "incremental_state": {
        "partition_hashes": {},
        "last_amendment_seen": ""
      }
    }
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence


# ---------------------------------------------------------------------------
# Typed incremental state (AGENTS.md §1.9)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IncrementalState:
    """Tracks partition-level hashes for incremental rebuild decisions.

    partition_hashes: maps partition key (e.g. statute_id) to its last-seen
        content hash. Stale partitions are regenerated; others are skipped.
    last_amendment_seen: last amendment ID observed during the previous build,
        used as a watermark for farchive ingest-order incremental scans.
    """

    partition_hashes: Dict[str, str]
    last_amendment_seen: str


@dataclass(frozen=True, slots=True)
class ProjectionState:
    """Complete state record for one Tier 2 projection.

    Written alongside each .parquet as {name}.state.json.
    Read before each rebuild to decide incremental vs full regen.

    AGENTS.md §1.9: typed, not stringly-typed.
    """

    projection_name: str
    schema_version: str
    row_count: int
    last_rebuild_at: str          # ISO 8601 UTC string
    source_farchive_hash: str     # SHA-256 hex of the primary Tier 1 farchive
    tier_1_dependencies: tuple    # farchive filenames consumed
    tier_2_dependencies: tuple    # other projections consumed
    incremental_state: IncrementalState


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _state_to_dict(state: ProjectionState) -> dict:
    """Convert ProjectionState to a JSON-serializable dict."""
    return {
        "projection_name": state.projection_name,
        "schema_version": state.schema_version,
        "row_count": state.row_count,
        "last_rebuild_at": state.last_rebuild_at,
        "source_farchive_hash": state.source_farchive_hash,
        "tier_1_dependencies": list(state.tier_1_dependencies),
        "tier_2_dependencies": list(state.tier_2_dependencies),
        "incremental_state": {
            "partition_hashes": dict(state.incremental_state.partition_hashes),
            "last_amendment_seen": state.incremental_state.last_amendment_seen,
        },
    }


def _state_from_dict(d: dict) -> ProjectionState:
    """Parse a JSON dict into a typed ProjectionState."""
    inc_raw = d.get("incremental_state", {})
    incremental = IncrementalState(
        partition_hashes=dict(inc_raw.get("partition_hashes", {})),
        last_amendment_seen=str(inc_raw.get("last_amendment_seen", "")),
    )
    return ProjectionState(
        projection_name=str(d["projection_name"]),
        schema_version=str(d["schema_version"]),
        row_count=int(d["row_count"]),
        last_rebuild_at=str(d["last_rebuild_at"]),
        source_farchive_hash=str(d.get("source_farchive_hash", "")),
        tier_1_dependencies=tuple(d.get("tier_1_dependencies", [])),
        tier_2_dependencies=tuple(d.get("tier_2_dependencies", [])),
        incremental_state=incremental,
    )


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def state_path_for(parquet_path: Path) -> Path:
    """Return the .state.json path alongside a .parquet file."""
    return parquet_path.with_suffix(".state.json")


def write_state(parquet_path: Path, state: ProjectionState) -> None:
    """Write a ProjectionState as JSON alongside the given parquet."""
    sp = state_path_for(parquet_path)
    sp.write_text(
        json.dumps(_state_to_dict(state), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def read_state(parquet_path: Path) -> Optional[ProjectionState]:
    """Read and parse the .state.json for a parquet. Returns None if missing."""
    sp = state_path_for(parquet_path)
    if not sp.exists():
        return None
    raw = json.loads(sp.read_text(encoding="utf-8"))
    return _state_from_dict(raw)


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def make_state(
    *,
    projection_name: str,
    schema_version: str,
    row_count: int,
    source_farchive_hash: str,
    tier_1_dependencies: List[str],
    tier_2_dependencies: Optional[List[str]] = None,
    partition_hashes: Optional[Dict[str, str]] = None,
    last_amendment_seen: str = "",
) -> ProjectionState:
    """Build a fresh ProjectionState with UTC timestamp."""
    now_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return ProjectionState(
        projection_name=projection_name,
        schema_version=schema_version,
        row_count=row_count,
        last_rebuild_at=now_iso,
        source_farchive_hash=source_farchive_hash,
        tier_1_dependencies=tuple(tier_1_dependencies),
        tier_2_dependencies=tuple(tier_2_dependencies or []),
        incremental_state=IncrementalState(
            partition_hashes=dict(partition_hashes or {}),
            last_amendment_seen=last_amendment_seen,
        ),
    )


# ---------------------------------------------------------------------------
# Tier 2 directory convention helpers
# ---------------------------------------------------------------------------


DEFAULT_SCHEMA_VERSION = "v1"


def tier2_dir(
    *,
    data_dir: str,
    jurisdiction: str,
    schema_version: str = DEFAULT_SCHEMA_VERSION,
) -> Path:
    """Return the canonical Tier 2 directory path.

    Convention: data/{jurisdiction}/{schema_version}/
    """
    return Path(data_dir) / jurisdiction / schema_version


def parquet_path_for(
    *,
    data_dir: str,
    jurisdiction: str,
    schema_version: str,
    projection_name: str,
) -> Path:
    """Return the canonical .parquet path for a projection."""
    return tier2_dir(
        data_dir=data_dir,
        jurisdiction=jurisdiction,
        schema_version=schema_version,
    ) / f"{projection_name}.parquet"


# ---------------------------------------------------------------------------
# Canonical farchive fingerprint (single source of truth)
# ---------------------------------------------------------------------------
#
# These are the ONE definition of "what hash goes in a projection's
# source_farchive_hash". Both the builder (rebuild-indexes / the dedicated
# export-* commands) and the READ-side freshness guard
# (lawvm.tools.projection_freshness) must compute the hash identically, or a
# freshly-built projection would look stale immediately. Keep this here so every
# producer and consumer imports the same function.


def farchive_hash(data_root: str, farchive_name: str) -> str:
    """Lightweight size+mtime fingerprint of a single farchive (hex, 16 chars).

    A full SHA-256 over multi-GB farchives would be prohibitively slow; size +
    mtime_ns is a fast, deterministic proxy that flips whenever the farchive is
    re-ingested. Returns "" when the farchive is absent.
    """
    path = Path(data_root) / farchive_name
    if not path.exists():
        return ""
    stat = path.stat()
    raw = f"{stat.st_size}:{stat.st_mtime_ns}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def primary_farchive_hash(data_root: str, tier_1_deps: Sequence[str]) -> str:
    """Combined fingerprint of all Tier 1 deps for a projection (hex, 24 chars).

    Returns "" if ALL dependency farchives are absent (so pre-populated ""
    hashes still match in farchive-less environments such as CI).
    """
    parts = [farchive_hash(data_root, dep) for dep in tier_1_deps]
    if all(p == "" for p in parts):
        return ""
    combined = ":".join(parts).encode()
    return hashlib.sha256(combined).hexdigest()[:24]


def write_projection_state_after_export(
    *,
    projection_dir: str,
    projection_name: str,
    row_count: int,
    tier_1_dependencies: Sequence[str],
    schema_version: str = DEFAULT_SCHEMA_VERSION,
    data_root: Optional[str] = None,
) -> None:
    """Write/refresh a projection's .state.json after a dedicated export.

    The dedicated export commands (sync-fi-proposals, export-fi-he-branch-ops,
    ...) write parquet bodies but historically did NOT update the tier-2 state
    sidecar, so the READ-side freshness guard would report a just-built
    projection as stale. Call this after writing the parquet to keep the sidecar
    in lockstep with the farchive the export read from.

    ``projection_dir`` is the directory the parquet was written to
    (e.g. ``data/fi/v1``). ``data_root`` is where the farchives live; when None
    it is inferred as the directory two levels up (``data/fi/v1`` -> ``data``),
    falling back to ``"data"``.
    """
    pdir = Path(projection_dir)
    if data_root is None:
        candidate = pdir.parent.parent
        data_root = str(candidate) if candidate != pdir else "data"

    current_hash = primary_farchive_hash(data_root, list(tier_1_dependencies))
    parquet_path = pdir / f"{projection_name}.parquet"
    state = make_state(
        projection_name=projection_name,
        schema_version=schema_version,
        row_count=row_count,
        source_farchive_hash=current_hash,
        tier_1_dependencies=list(tier_1_dependencies),
    )
    write_state(parquet_path, state)
