"""Piece 6 — Hard-fail enforcement tests for v3 CompileMetadata requirement.

Verifies that each wired emitter raises ValueError when compile_metadata is None,
and that build_default_compile_metadata produces a valid record.

Per UNIFIED_PROVENANCE_GRAPH_DESIGN_v3.md §13 Step 5 + §14 reproducibility contract.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


def _make_minimal_compile_metadata():
    from lawvm.core.compile_metadata import CompileMetadata
    return CompileMetadata(
        provenance_graph_hash="a" * 64,
        strict_profile_fingerprint="b" * 64,
        evidence_policy_fingerprint="c" * 64,
        source_bundle_hash="d" * 64,
        attestation_kind_registry_hash="e" * 64,
        build_id="test.fixture",
        build_timestamp=None,
    )


# ---------------------------------------------------------------------------
# Piece 6, tests 1–11: each emitter raises ValueError when compile_metadata=None
# ---------------------------------------------------------------------------


def test_export_fi_refs_raises_without_compile_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    from lawvm.tools.export_fi_refs import _try_write_parquet
    from lawvm.core.manual_claims.primitive import ProfileTag

    with pytest.raises(ValueError, match="CompileMetadata is required"):
        _try_write_parquet(tmp_path / "fi_refs.parquet", [], ProfileTag.DETERMINISTIC_ONLY, None)


def test_export_fi_actors_raises_without_compile_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    from lawvm.tools.export_fi_actors import _try_write_parquet

    with pytest.raises(ValueError, match="CompileMetadata is required"):
        _try_write_parquet(tmp_path / "fi_actors.parquet", [], None)


def test_export_fi_pools_raises_without_compile_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    from lawvm.tools.export_fi_pools import _try_write_parquet

    with pytest.raises(ValueError, match="CompileMetadata is required"):
        _try_write_parquet(tmp_path / "fi_pools.parquet", [], None)


def test_export_fi_he_corpus_raises_without_compile_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    from lawvm.tools.export_fi_he_corpus import _attach_compile_metadata
    import pyarrow as pa

    table = pa.table({"col": pa.array(["val"])})
    with pytest.raises(ValueError, match="CompileMetadata is required"):
        _attach_compile_metadata(table, None)


def test_export_fi_he_branch_ops_raises_without_compile_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    from lawvm.tools.export_fi_he_branch_ops import _write_parquet

    rows = [{
        "branch_id": "fi/he/2024/1",
        "he_id": "HE 1/2024 vp",
        "he_year": 2024,
        "he_number": 1,
        "proposed_voimaantulo": None,
        "op_index": 0,
        "operation_kind": "replace",
        "target_provision_ref": "711/2022/1/1",
        "target_statute_id": "711/2022",
        "payload_summary": "test",
        "source_span_text": "test",
        "source_span_preamble": "",
        "parse_confidence": 0.9,
        "target_resolution": "resolved",
        "is_proposal_relative": False,
        "parse_status": "full",
    }]
    with pytest.raises(ValueError, match="CompileMetadata is required"):
        _write_parquet(rows, data_dir=str(tmp_path), compile_metadata=None)


def test_export_fi_preparatory_refs_raises_without_compile_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    from lawvm.tools.export_fi_preparatory_refs import _try_write_parquet

    with pytest.raises(ValueError, match="CompileMetadata is required"):
        _try_write_parquet(tmp_path / "fi_preparatory_refs.parquet", [], None)


def test_export_fi_inline_citations_raises_without_compile_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    from lawvm.tools.export_fi_inline_citations import _try_write_parquet

    with pytest.raises(ValueError, match="CompileMetadata is required"):
        _try_write_parquet(tmp_path / "fi_inline_citations.parquet", [], None)


def test_export_fi_sections_text_raises_without_compile_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    from lawvm.tools.export_fi_sections_text import _try_write_parquet

    with pytest.raises(ValueError, match="CompileMetadata is required"):
        _try_write_parquet(tmp_path / "fi_sections_text.parquet", [], None)


def test_export_parquet_raises_without_compile_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    from lawvm.tools.export_parquet import _try_write_parquet

    row = {
        "statute_id": "2002/738",
        "title": "T",
        "amendment_count": 1,
        "oracle_version": "",
        "score": 1.0,
        "run_status": "OK",
        "diff_kind_summary": "",
    }
    with pytest.raises(ValueError, match="CompileMetadata is required"):
        _try_write_parquet(tmp_path / "statutes.parquet", [row], None)


def test_build_evidence_bundle_raises_without_compile_metadata() -> None:
    from lawvm.tools.evidence import build_evidence_bundle

    with pytest.raises(ValueError, match="CompileMetadata is required"):
        build_evidence_bundle("2002/738", compile_metadata=None)


# ---------------------------------------------------------------------------
# Piece 6, test 12: build_default_compile_metadata returns valid record
# ---------------------------------------------------------------------------


def test_build_default_compile_metadata_returns_valid_record() -> None:
    from lawvm.core.compile_metadata_default import build_default_compile_metadata
    from lawvm.core.compile_metadata import CompileMetadata

    meta = build_default_compile_metadata(
        jurisdiction="fi",
        source_bundle_hash="sha256:" + "a" * 64,
        build_id="test.build.fi",
    )

    assert isinstance(meta, CompileMetadata)
    assert meta.provenance_graph_hash
    assert meta.strict_profile_fingerprint
    assert meta.evidence_policy_fingerprint
    assert meta.source_bundle_hash == "sha256:" + "a" * 64
    assert meta.attestation_kind_registry_hash
    assert meta.build_id == "test.build.fi"
    assert meta.build_timestamp is None


# ---------------------------------------------------------------------------
# Piece 6, test 13: handles empty graph store (no snapshots on disk)
# ---------------------------------------------------------------------------


def test_build_default_compile_metadata_handles_empty_graph_store(tmp_path: Path) -> None:
    from lawvm.core.compile_metadata_default import (
        build_default_compile_metadata,
        _canonical_empty_graph_hash,
    )
    from lawvm.core.compile_metadata import CompileMetadata

    meta = build_default_compile_metadata(
        jurisdiction="fi",
        source_bundle_hash="sha256:" + "b" * 64,
        build_id="test.empty.graph",
        graph_store_root=tmp_path,  # empty dir — no snapshots
    )

    assert isinstance(meta, CompileMetadata)
    assert meta.provenance_graph_hash == _canonical_empty_graph_hash()
