"""lawvm build-index-db — compose Tier 2 Parquets into a single DuckDB .db file.

Wraps each Parquet in the Tier 2 directory as a DuckDB view inside a single
portable .db file. Optional --fts flag builds DuckDB FTS indexes on text
columns for fast topic search via `lawvm topic --mode fts`.

Usage:
    lawvm build-index-db
    lawvm build-index-db -j fi --out data/fi/v1/lawvm.db
    lawvm build-index-db --fts
    lawvm build-index-db --data-dir /mnt/lawvm-data --schema-version v2
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from lawvm.tools.tier2_state import (
    DEFAULT_SCHEMA_VERSION,
    tier2_dir,
)
from lawvm.core.manual_claims.primitive import ProfileTag


# ---------------------------------------------------------------------------
# DuckDB helpers
# ---------------------------------------------------------------------------


def _require_duckdb() -> Any:
    """Import duckdb or print a helpful error and exit."""
    try:
        import duckdb  # ty: ignore[unresolved-import]
        return duckdb
    except ImportError:
        print(
            "error: duckdb is not installed.\n\n"
            "Install it with:\n"
            "  uv pip install duckdb\n",
            file=sys.stderr,
        )
        sys.exit(1)


def _discover_parquets(tier2_path: Path) -> List[Path]:
    """Return sorted list of .parquet files in the Tier 2 directory.

    Excludes the output .db itself and any temp files.
    """
    return sorted(
        p for p in tier2_path.glob("*.parquet")
        if p.suffix == ".parquet"
    )


# ---------------------------------------------------------------------------
# FTS helpers
# ---------------------------------------------------------------------------


# Columns in each projection that hold text content suitable for FTS.
# Only indexed if the table and column exist in the produced .db.
_FTS_TARGETS: Dict[str, str] = {
    "sections": "replay_text",
    "fi_he_atoms": "text_content",
}


def _build_fts_index(con: Any, table_name: str, text_column: str) -> bool:
    """Attempt to build a DuckDB FTS index on one table/column.

    Returns True on success, False if FTS extension unavailable or column absent.
    """
    # Check if the table/view exists and has the column
    try:
        cols = [
            row[0]
            for row in con.execute(f"DESCRIBE {table_name}").fetchall()
        ]
    except Exception:
        return False

    if text_column not in cols:
        return False

    # DuckDB FTS requires the fts extension (bundled since 1.0)
    # CREATE OR REPLACE is not supported for FTS; use IF NOT EXISTS pattern
    try:
        con.execute(f"""
            PRAGMA create_fts_index(
                '{table_name}', 'rowid', '{text_column}',
                overwrite=1
            )
        """)
        return True
    except Exception:
        pass

    # Fallback: try the newer API
    try:
        con.execute(
            f"CREATE INDEX IF NOT EXISTS fts_{table_name}_{text_column} "
            f"ON {table_name} USING FTS ({text_column})"
        )
        return True
    except Exception:
        return False



# ---------------------------------------------------------------------------
# Cross-profile join detection
# ---------------------------------------------------------------------------


def _detect_cross_profile_joins(parquets: List[Path]) -> Optional[str]:
    """Return a warning message if parquets with different profiles are attached.

    Scans parquet metadata for lawvm.claim_profile. Returns None if all profiles
    match or no profiles are set. Returns a warning string if profiles differ.
    """
    try:
        import pyarrow.parquet as pq  # ty: ignore[unresolved-import]
    except ImportError:
        return None

    profiles: Dict[str, str] = {}
    for p in parquets:
        try:
            meta = pq.read_metadata(str(p))
            schema_meta = meta.metadata or {}
            profile_bytes = schema_meta.get(b"lawvm.claim_profile")
            if profile_bytes is not None:
                profiles[p.name] = profile_bytes.decode()
        except Exception:
            continue

    unique_profiles = set(profiles.values())
    if len(unique_profiles) <= 1:
        return None

    detail = ", ".join(f"{name}={prof}" for name, prof in sorted(profiles.items()))
    return (
        f"WARNING: cross-profile JOIN detected — files have different claim_profiles. "
        f"Results may mix deterministic and claim-derived rows. Profiles: {detail}"
    )


# ---------------------------------------------------------------------------
# Main build logic
# ---------------------------------------------------------------------------


def build_index_db(
    *,
    jurisdiction: str = "fi",
    data_dir: str = "data",
    out_db: Optional[str] = None,
    build_fts: bool = False,
    schema_version: str = DEFAULT_SCHEMA_VERSION,
    verbose: bool = False,
    profile: Optional[ProfileTag] = None,
    compile_metadata: Optional[Any] = None,
) -> Dict[str, Any]:
    """Compose Tier 2 Parquets into a single DuckDB .db file.

    For each .parquet in the Tier 2 directory, creates a DuckDB view
    ``CREATE VIEW {name} AS SELECT * FROM read_parquet(...)``.
    Optionally builds FTS indexes on text columns.

    Args:
        jurisdiction:   Jurisdiction code (e.g. "fi").
        data_dir:       Root data directory.
        out_db:         Output .db file path. Default: {tier2_dir}/lawvm.db.
        build_fts:      If True, build DuckDB FTS indexes on text columns.
        schema_version: Parquet namespace version.
        verbose:        Print per-table progress.
        profile:        REQUIRED. ProfileTag for the build. Refuses to emit
                        without a profile tag (§14 adversary defense).

    Returns:
        Summary dict: {out_db, views_created, fts_indexed, elapsed}.
    """
    duckdb = _require_duckdb()

    # §14 adversary defense: REFUSE to build without profile metadata
    if profile is None:
        print(
            "error: --profile is required for build-index-db.\n"
            "Specify one of: deterministic_only, strict_with_attested_claims, "
            "non_strict_with_claims\n"
            "Example: lawvm build-index-db --profile deterministic_only",
            file=sys.stderr,
        )
        sys.exit(1)

    t2_dir = tier2_dir(
        data_dir=data_dir,
        jurisdiction=jurisdiction,
        schema_version=schema_version,
    )

    if not t2_dir.exists():
        print(
            f"error: Tier 2 directory {t2_dir} does not exist.\n"
            "Run 'lawvm rebuild-indexes' first to generate Parquet projections.",
            file=sys.stderr,
        )
        sys.exit(1)

    parquets = _discover_parquets(t2_dir)
    if not parquets:
        print(
            f"No .parquet files found in {t2_dir}.\n"
            "Run 'lawvm rebuild-indexes' first.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Default output path
    if out_db is None:
        out_db = str(t2_dir / "lawvm.db")

    out_path = Path(out_db)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Remove stale .db if it exists (fresh build)
    if out_path.exists():
        out_path.unlink()

    t_start = time.time()
    views_created: List[str] = []
    fts_indexed: List[str] = []

    print(
        f"build-index-db: {jurisdiction}/{schema_version} "
        f"-> {out_path} ({len(parquets)} projections)"
    )

    con = duckdb.connect(str(out_path))
    for parquet_path in parquets:
        view_name = parquet_path.stem
        # Skip the db file itself (shouldn't happen since .parquet filter)
        if view_name.endswith(".db"):
            continue

        abs_path = str(parquet_path.resolve())
        create_sql = (
            f"CREATE VIEW IF NOT EXISTS {view_name} "
            f"AS SELECT * FROM read_parquet('{abs_path}')"
        )
        con.execute(create_sql)
        views_created.append(view_name)

        if verbose:
            try:
                row_count = con.execute(
                    f"SELECT count(*) FROM {view_name}"
                ).fetchone()
                n = row_count[0] if row_count else 0
                print(f"  view  {view_name}: {n:,} rows")
            except Exception:
                print(f"  view  {view_name}")

    if build_fts:
        print("  Building FTS indexes...")
        for table_name, text_col in _FTS_TARGETS.items():
            if table_name in views_created:
                ok = _build_fts_index(con, table_name, text_col)
                if ok:
                    fts_indexed.append(f"{table_name}.{text_col}")
                    print(f"    FTS: {table_name}.{text_col}")
                elif verbose:
                    print(
                        f"    FTS: {table_name}.{text_col} — skipped "
                        "(column absent or extension unavailable)"
                    )

    # Write lawvm_meta single-row table (profile sticky, §14)
    from datetime import datetime, timezone
    build_ts = datetime.now(tz=timezone.utc).isoformat()
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS lawvm_meta ("
            "profile_tag VARCHAR, "
            "build_timestamp VARCHAR, "
            "provenance_graph_hash VARCHAR, "
            "strict_profile_fingerprint VARCHAR, "
            "evidence_policy_fingerprint VARCHAR, "
            "source_bundle_hash VARCHAR, "
            "attestation_kind_registry_hash VARCHAR, "
            "interpretation_policy_fingerprint VARCHAR, "
            "build_id VARCHAR"
            ")"
        )
        if compile_metadata is not None:
            meta_d = compile_metadata.to_metadata_dict()
            con.execute(
                "INSERT INTO lawvm_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    profile.value,
                    build_ts,
                    meta_d.get("lawvm.provenance_graph_hash", ""),
                    meta_d.get("lawvm.strict_profile_fingerprint", ""),
                    meta_d.get("lawvm.evidence_policy_fingerprint", ""),
                    meta_d.get("lawvm.source_bundle_hash", ""),
                    meta_d.get("lawvm.attestation_kind_registry_hash", ""),
                    meta_d.get("lawvm.interpretation_policy_fingerprint", "") or "",
                    meta_d.get("lawvm.build_id", "") or "",
                ],
            )
        else:
            con.execute(
                "INSERT INTO lawvm_meta VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [profile.value, build_ts, "", "", "", "", "", "", ""],
            )
    except Exception as e:
        print(f"  warning: could not write lawvm_meta table: {e}", file=sys.stderr)

    # Cross-profile join detection (§14)
    cross_warn = _detect_cross_profile_joins(parquets)
    if cross_warn:
        print(cross_warn, file=sys.stderr)

    con.close()
    elapsed = time.time() - t_start

    print(
        f"\nDone: {len(views_created)} views, {len(fts_indexed)} FTS indexes "
        f"in {elapsed:.1f}s -> {out_path} (profile={profile.value})"
    )

    return {
        "out_db": str(out_path),
        "views_created": views_created,
        "fts_indexed": fts_indexed,
        "elapsed": elapsed,
        "profile": profile.value,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(args: Any) -> None:
    """CLI entry point for lawvm build-index-db."""
    jurisdiction = getattr(args, "jurisdiction", "fi") or "fi"
    data_dir = getattr(args, "data_dir", None) or "data"
    out_db = getattr(args, "out", None)
    build_fts = getattr(args, "fts", False)
    schema_version = getattr(args, "schema_version", None) or DEFAULT_SCHEMA_VERSION
    verbose = getattr(args, "verbose", False)
    profile_str = getattr(args, "profile", None)

    profile: Optional[ProfileTag]
    if profile_str:
        try:
            profile = ProfileTag(profile_str)
        except ValueError:
            valid = [t.value for t in ProfileTag]
            print(
                f"error: invalid --profile value {profile_str!r}. "
                f"Valid values: {valid}",
                file=sys.stderr,
            )
            import sys as _sys
            _sys.exit(1)
    else:
        profile = None  # will be caught inside build_index_db

    build_index_db(
        jurisdiction=jurisdiction,
        data_dir=data_dir,
        out_db=out_db,
        build_fts=build_fts,
        schema_version=schema_version,
        verbose=verbose,
        profile=profile,
    )
