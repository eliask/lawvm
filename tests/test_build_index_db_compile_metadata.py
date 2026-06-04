"""Tests for build_index_db with compile_metadata — Step 5.

Covers:
  6. test_duckdb_emit_includes_compile_metadata
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_compile_metadata():
    from lawvm.core.compile_metadata import CompileMetadata
    return CompileMetadata(
        provenance_graph_hash="a" * 64,
        strict_profile_fingerprint="b" * 64,
        evidence_policy_fingerprint="c" * 64,
        source_bundle_hash="d" * 64,
        attestation_kind_registry_hash="e" * 64,
        build_id="test-duckdb-001",
    )


# ---------------------------------------------------------------------------
# Test 6: duckdb emit includes compile_metadata
# ---------------------------------------------------------------------------


def test_duckdb_emit_includes_compile_metadata(tmp_path: Path) -> None:
    """build_index_db writes CompileMetadata fields to the lawvm_meta table."""
    duckdb = pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")

    from lawvm.tools.build_index_db import build_index_db
    from lawvm.core.manual_claims.primitive import ProfileTag

    meta = _make_minimal_compile_metadata()

    # Create a minimal tier2_dir with a dummy parquet so build_index_db doesn't bail early
    tier2_dir = tmp_path / "fi" / "v1"
    tier2_dir.mkdir(parents=True)

    import pyarrow as pa
    import pyarrow.parquet as pq
    dummy_table = pa.table({"col1": pa.array(["val1"])})
    pq.write_table(dummy_table, str(tier2_dir / "dummy.parquet"))

    out_db = str(tmp_path / "test_lawvm.db")
    result = build_index_db(
        jurisdiction="fi",
        data_dir=str(tmp_path),
        out_db=out_db,
        schema_version="v1",
        profile=ProfileTag.DETERMINISTIC_ONLY,
        compile_metadata=meta,
    )

    assert Path(out_db).exists()

    con = duckdb.connect(out_db)
    rows = con.execute("SELECT * FROM lawvm_meta").fetchall()
    con.close()

    assert len(rows) == 1
    row = rows[0]
    # Row columns: profile_tag, build_timestamp, provenance_graph_hash, ...
    # Build a dict from column names
    con2 = duckdb.connect(out_db)
    col_names = [d[0] for d in con2.execute("DESCRIBE lawvm_meta").fetchall()]
    row_dict = dict(zip(col_names, row))
    con2.close()

    assert row_dict["provenance_graph_hash"] == "a" * 64
    assert row_dict["strict_profile_fingerprint"] == "b" * 64
    assert row_dict["evidence_policy_fingerprint"] == "c" * 64
    assert row_dict["source_bundle_hash"] == "d" * 64
    assert row_dict["attestation_kind_registry_hash"] == "e" * 64
    assert row_dict["build_id"] == "test-duckdb-001"
    assert row_dict["profile_tag"] == "deterministic_only"


def test_duckdb_emit_with_default_compile_metadata(tmp_path: Path) -> None:
    """build_index_db with a minimal compile_metadata produces a valid db."""
    duckdb = pytest.importorskip("duckdb")
    pytest.importorskip("pyarrow")

    from lawvm.tools.build_index_db import build_index_db
    from lawvm.core.manual_claims.primitive import ProfileTag

    tier2_dir = tmp_path / "fi" / "v1"
    tier2_dir.mkdir(parents=True)

    import pyarrow as pa
    import pyarrow.parquet as pq
    dummy_table = pa.table({"col1": pa.array(["val"])})
    pq.write_table(dummy_table, str(tier2_dir / "dummy.parquet"))

    meta = _make_minimal_compile_metadata()
    out_db = str(tmp_path / "lawvm_meta.db")
    result = build_index_db(
        jurisdiction="fi",
        data_dir=str(tmp_path),
        out_db=out_db,
        schema_version="v1",
        profile=ProfileTag.DETERMINISTIC_ONLY,
        compile_metadata=meta,
    )

    assert Path(out_db).exists()

    con = duckdb.connect(out_db)
    rows = con.execute("SELECT * FROM lawvm_meta").fetchall()
    con.close()

    assert len(rows) == 1
    col_names_con = duckdb.connect(out_db)
    col_names = [d[0] for d in col_names_con.execute("DESCRIBE lawvm_meta").fetchall()]
    row_dict = dict(zip(col_names, rows[0]))
    col_names_con.close()

    assert row_dict["provenance_graph_hash"] == "a" * 64
    assert row_dict["strict_profile_fingerprint"] == "b" * 64
