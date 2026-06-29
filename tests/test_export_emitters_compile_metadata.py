"""Tests for parquet emitters — compile metadata embedding.

Covers:
  4. test_parquet_emit_includes_compile_metadata_when_provided
  5. test_parquet_emit_warns_when_compile_metadata_absent
  9. test_no_uk_emitter_files_touched
  11. real-corpus regression: build CompileMetadata from synthetic graph + profile,
      emit fi_refs parquet, verify metadata round-trips
"""
from __future__ import annotations

import subprocess
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
        build_id="test-build-001",
    )


def _make_real_compile_metadata():
    """Build CompileMetadata from a real synthetic graph + profile + policy."""
    from lawvm.core.compile_metadata import build_compile_metadata
    from lawvm.core.compile_result import StrictProfile
    from lawvm.core.evidence_policy import EvidencePolicyRegistry
    from lawvm.core.provenance_graph import GraphBuilder, attestation_kind_registry_hash

    graph = GraphBuilder(attestation_kind_registry_hash()).finalize()
    profile = StrictProfile(name="test_fi_profile")
    registry = EvidencePolicyRegistry.build(
        registry_id="test.policy.v0",
        registry_version="v0.0.1",
        predicates=(),
    )
    return build_compile_metadata(
        graph=graph,
        profile=profile,
        evidence_policy=registry,
        source_bundle_hash="source_hash_" + "x" * 52,
        build_id="ci-001",
    )


# ---------------------------------------------------------------------------
# Tests 4 + 5: parquet emit includes / warns on compile_metadata
# ---------------------------------------------------------------------------


def test_parquet_emit_includes_compile_metadata_when_provided(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    from lawvm.tools.export_fi_refs import _try_write_parquet
    from lawvm.core.manual_claims.primitive import ProfileTag

    meta = _make_minimal_compile_metadata()
    out_path = tmp_path / "fi_refs__deterministic_only.parquet"

    ok = _try_write_parquet(out_path, [], ProfileTag.DETERMINISTIC_ONLY, meta)
    assert ok

    schema_meta = pq.read_metadata(str(out_path)).metadata
    assert b"lawvm.provenance_graph_hash" in schema_meta
    assert b"lawvm.strict_profile_fingerprint" in schema_meta
    assert b"lawvm.evidence_policy_fingerprint" in schema_meta
    assert b"lawvm.source_bundle_hash" in schema_meta
    assert b"lawvm.attestation_kind_registry_hash" in schema_meta
    assert schema_meta[b"lawvm.provenance_graph_hash"] == b"a" * 64


def test_parquet_emit_raises_when_compile_metadata_absent(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")

    from lawvm.tools.export_fi_refs import _try_write_parquet
    from lawvm.core.manual_claims.primitive import ProfileTag

    out_path = tmp_path / "fi_refs__deterministic_only.parquet"

    with pytest.raises(ValueError, match="CompileMetadata is required"):
        _try_write_parquet(out_path, [], ProfileTag.DETERMINISTIC_ONLY, None)


def test_fi_actors_parquet_includes_compile_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    from lawvm.tools.export_fi_actors import _try_write_parquet

    meta = _make_minimal_compile_metadata()
    out_path = tmp_path / "fi_actors.parquet"
    ok = _try_write_parquet(out_path, [], meta)
    assert ok

    schema_meta = pq.read_metadata(str(out_path)).metadata
    assert b"lawvm.provenance_graph_hash" in schema_meta


def test_fi_actors_parquet_raises_without_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")

    from lawvm.tools.export_fi_actors import _try_write_parquet

    out_path = tmp_path / "fi_actors.parquet"
    with pytest.raises(ValueError, match="CompileMetadata is required"):
        _try_write_parquet(out_path, [], None)


def test_fi_pools_parquet_includes_compile_metadata(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    from lawvm.tools.export_fi_pools import _try_write_parquet

    meta = _make_minimal_compile_metadata()
    out_path = tmp_path / "fi_pools.parquet"
    ok = _try_write_parquet(out_path, [], meta)
    assert ok

    schema_meta = pq.read_metadata(str(out_path)).metadata
    assert b"lawvm.provenance_graph_hash" in schema_meta


# ---------------------------------------------------------------------------
# Test 9: no UK emitter files touched
# ---------------------------------------------------------------------------


def test_no_uk_emitter_files_touched() -> None:
    """Verify no UK emitter files were modified by Step 5."""
    worktree = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        capture_output=True,
        text=True,
        cwd=str(worktree),
    )
    changed = result.stdout.strip().split("\n") if result.stdout.strip() else []
    uk_changed = [
        f
        for f in changed
        if Path(f).name.startswith("export_uk") or "uk_emitter" in Path(f).name
    ]
    assert uk_changed == [], (
        f"UK files were modified by Step 5: {uk_changed!r}. "
        "Step 5 must NOT touch UK emitters."
    )


# ---------------------------------------------------------------------------
# Test 11: real-corpus regression — synthetic graph + profile → parquet round-trip
# ---------------------------------------------------------------------------


def test_real_corpus_regression_fi_refs_parquet_compile_metadata_roundtrip(
    tmp_path: Path,
) -> None:
    """Build CompileMetadata from real synthetic graph+profile, emit fi_refs parquet,
    verify CompileMetadata round-trips through parquet metadata read.
    """
    pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    from lawvm.tools.export_fi_refs import _try_write_parquet
    from lawvm.core.manual_claims.primitive import ProfileTag
    from lawvm.core.compile_metadata import CompileMetadata

    meta = _make_real_compile_metadata()

    # Emit a small parquet with real rows (enough to exercise the schema)
    sample_rows = [
        {
            "source_statute_id": "2002/738",
            "source_provision_ref_str": "2002/738/1/1",
            "target_statute_id": "1999/999",
            "target_provision_ref_str": "",
            "cite_kind": "direct",
            "cite_confidence": "high",
            "edge_subtype": "",
            "phrase_lemma": "",
            "source_span_file": "akn/fi/act/2002/738/main.xml",
            "source_span_byte_offset": 0,
            "source_span_len": 100,
            "valid_at_start": "2002-01-01",
            "valid_at_end": "",
            "target_stat_hash": "",
            "source_witness_type": "finlex_akn",
            "claim_id": None,
            "validator_status": "span_verified",
            "review_status": "verified_manual",
            "replay_authorized": True,
            "deterministic_extraction": True,
            "emit_profile": "deterministic_only",
        }
    ]

    out_path = tmp_path / "fi_refs__deterministic_only.parquet"
    ok = _try_write_parquet(out_path, sample_rows, ProfileTag.DETERMINISTIC_ONLY, meta)
    assert ok

    # Read back and verify CompileMetadata round-trip
    schema_meta = pq.read_metadata(str(out_path)).metadata
    decoded = {k.decode(): v.decode() for k, v in schema_meta.items() if k.startswith(b"lawvm.")}

    restored = CompileMetadata.from_metadata_dict(decoded)
    assert restored.provenance_graph_hash == meta.provenance_graph_hash
    assert restored.strict_profile_fingerprint == meta.strict_profile_fingerprint
    assert restored.evidence_policy_fingerprint == meta.evidence_policy_fingerprint
    assert restored.source_bundle_hash == meta.source_bundle_hash
    assert restored.attestation_kind_registry_hash == meta.attestation_kind_registry_hash
    assert restored.build_id == meta.build_id
