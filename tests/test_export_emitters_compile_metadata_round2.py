"""Tests for parquet emitters — compile metadata embedding (Round 2).

Covers the 4 emitters deferred from Step 5:
  - export_fi_preparatory_refs
  - export_fi_inline_citations
  - export_fi_sections_text
  - export_parquet (statutes / sections / findings / ops projections)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lawvm.core.compile_metadata import CompileMetadata

ParquetRow = dict[str, Any]
SchemaMetadata = dict[bytes, bytes]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_minimal_compile_metadata() -> CompileMetadata:
    return CompileMetadata(
        provenance_graph_hash="a" * 64,
        strict_profile_fingerprint="b" * 64,
        evidence_policy_fingerprint="c" * 64,
        source_bundle_hash="d" * 64,
        attestation_kind_registry_hash="e" * 64,
        build_id="test-round2-001",
    )


_REQUIRED_KEYS = (
    b"lawvm.provenance_graph_hash",
    b"lawvm.strict_profile_fingerprint",
    b"lawvm.evidence_policy_fingerprint",
    b"lawvm.source_bundle_hash",
    b"lawvm.attestation_kind_registry_hash",
)


def _assert_all_lawvm_keys_present(schema_meta: SchemaMetadata) -> None:
    for key in _REQUIRED_KEYS:
        assert key in schema_meta, f"Missing metadata key: {key!r}"


# ---------------------------------------------------------------------------
# export_fi_preparatory_refs
# ---------------------------------------------------------------------------


def test_export_fi_preparatory_refs_includes_compile_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    from lawvm.tools.export_fi_preparatory_refs import _try_write_parquet

    meta = _make_minimal_compile_metadata()
    out_path = tmp_path / "fi_preparatory_refs.parquet"

    ok = _try_write_parquet(out_path, [], meta)
    assert ok

    schema_meta = pq.read_metadata(str(out_path)).metadata
    _assert_all_lawvm_keys_present(schema_meta)
    assert schema_meta[b"lawvm.provenance_graph_hash"] == b"a" * 64


def test_export_fi_preparatory_refs_raises_when_absent(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")

    from lawvm.tools.export_fi_preparatory_refs import _try_write_parquet

    out_path = tmp_path / "fi_preparatory_refs.parquet"

    with pytest.raises(ValueError, match="CompileMetadata is required"):
        _try_write_parquet(out_path, [], None)


# ---------------------------------------------------------------------------
# export_fi_inline_citations
# ---------------------------------------------------------------------------


def test_export_fi_inline_citations_includes_compile_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    from lawvm.tools.export_fi_inline_citations import _try_write_parquet

    meta = _make_minimal_compile_metadata()
    out_path = tmp_path / "fi_inline_citations.parquet"

    ok = _try_write_parquet(out_path, [], meta)
    assert ok

    schema_meta = pq.read_metadata(str(out_path)).metadata
    _assert_all_lawvm_keys_present(schema_meta)
    assert schema_meta[b"lawvm.provenance_graph_hash"] == b"a" * 64


def test_export_fi_inline_citations_raises_when_absent(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")

    from lawvm.tools.export_fi_inline_citations import _try_write_parquet

    out_path = tmp_path / "fi_inline_citations.parquet"

    with pytest.raises(ValueError, match="CompileMetadata is required"):
        _try_write_parquet(out_path, [], None)


# ---------------------------------------------------------------------------
# export_fi_sections_text
# ---------------------------------------------------------------------------


def test_export_fi_sections_text_includes_compile_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    from lawvm.tools.export_fi_sections_text import _try_write_parquet

    meta = _make_minimal_compile_metadata()
    out_path = tmp_path / "fi_sections_text.parquet"

    ok = _try_write_parquet(out_path, [], meta)
    assert ok

    schema_meta = pq.read_metadata(str(out_path)).metadata
    _assert_all_lawvm_keys_present(schema_meta)
    assert schema_meta[b"lawvm.provenance_graph_hash"] == b"a" * 64


def test_export_fi_sections_text_raises_when_absent(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")

    from lawvm.tools.export_fi_sections_text import _try_write_parquet

    out_path = tmp_path / "fi_sections_text.parquet"

    with pytest.raises(ValueError, match="CompileMetadata is required"):
        _try_write_parquet(out_path, [], None)


# ---------------------------------------------------------------------------
# export_parquet — statutes / sections / findings / ops projections
# ---------------------------------------------------------------------------


def _sample_statute_row() -> ParquetRow:
    return {
        "statute_id": "2002/738",
        "title": "Test statute",
        "amendment_count": 5,
        "oracle_version": "2024-01-01",
        "score": 0.99,
        "run_status": "OK",
        "diff_kind_summary": "identical:1",
    }


def _sample_section_row() -> ParquetRow:
    return {
        "statute_id": "2002/738",
        "section_key": "section:1",
        "diff_kind": "identical",
        "oracle_label_basis": "label",
        "replay_label_basis": "label",
        "oracle_text": "text",
        "replay_text": "text",
        "similarity": 1.0,
        "events": "[]",
        "is_purpose_section": False,
        "purpose_text_snippet": None,
    }


def _sample_finding_row() -> ParquetRow:
    return {
        "statute_id": "2002/738",
        "claim_kind": "section_diff.editorial_only",
        "claim_rule": "EXPORT.SECTION_DIFF",
        "section_key": "section:1",
        "severity": "info",
        "detail": "similarity=0.99",
    }


def _sample_op_row() -> ParquetRow:
    return {
        "statute_id": "2002/738",
        "amendment_id": "2024/100",
        "op_type": "REPLACE",
        "target_kind": "section",
        "target_section": "1",
        "target_chapter": "",
        "target_paragraph": "",
    }


def test_export_parquet_statutes_includes_compile_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    from lawvm.tools.export_parquet import _try_write_parquet

    meta = _make_minimal_compile_metadata()
    out_path = tmp_path / "statutes.parquet"

    ok = _try_write_parquet(out_path, [_sample_statute_row()], meta)
    assert ok

    schema_meta = pq.read_metadata(str(out_path)).metadata
    _assert_all_lawvm_keys_present(schema_meta)
    assert schema_meta[b"lawvm.provenance_graph_hash"] == b"a" * 64


def test_export_parquet_sections_includes_compile_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    from lawvm.tools.export_parquet import _try_write_parquet

    meta = _make_minimal_compile_metadata()
    out_path = tmp_path / "sections.parquet"

    ok = _try_write_parquet(out_path, [_sample_section_row()], meta)
    assert ok

    schema_meta = pq.read_metadata(str(out_path)).metadata
    _assert_all_lawvm_keys_present(schema_meta)


def test_export_parquet_findings_includes_compile_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    from lawvm.tools.export_parquet import _try_write_parquet

    meta = _make_minimal_compile_metadata()
    out_path = tmp_path / "findings.parquet"

    ok = _try_write_parquet(out_path, [_sample_finding_row()], meta)
    assert ok

    schema_meta = pq.read_metadata(str(out_path)).metadata
    _assert_all_lawvm_keys_present(schema_meta)


def test_export_parquet_ops_includes_compile_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    from lawvm.tools.export_parquet import _try_write_parquet

    meta = _make_minimal_compile_metadata()
    out_path = tmp_path / "ops.parquet"

    ok = _try_write_parquet(out_path, [_sample_op_row()], meta)
    assert ok

    schema_meta = pq.read_metadata(str(out_path)).metadata
    _assert_all_lawvm_keys_present(schema_meta)


def test_export_parquet_raises_when_absent(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")

    from lawvm.tools.export_parquet import _try_write_parquet

    out_path = tmp_path / "statutes.parquet"

    with pytest.raises(ValueError, match="CompileMetadata is required"):
        _try_write_parquet(out_path, [_sample_statute_row()], None)
