"""Tests for compile_metadata.py — Step 5 acceptance criteria.

Covers:
  1. to_metadata_dict / from_metadata_dict round-trip (identity)
  2. strict_profile_fingerprint is deterministic across processes
  3. from_metadata_dict({}) raises ValueError (required fields validated)
  4. build_compile_metadata produces a CompileMetadata from graph+profile+policy
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lawvm.core.compile_metadata import (
    CompileMetadata,
    build_compile_metadata,
    compute_strict_profile_fingerprint,
)
from lawvm.core.compile_result import StrictProfile
from lawvm.core.evidence_policy import EvidencePolicyRegistry
from lawvm.core.provenance_graph import (
    GraphBuilder,
    attestation_kind_registry_hash,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_profile(name: str = "test_profile") -> StrictProfile:
    return StrictProfile(name=name)


def _make_registry() -> EvidencePolicyRegistry:
    return EvidencePolicyRegistry.build(
        registry_id="test.policy.v0",
        registry_version="v0.0.1",
        predicates=(),
    )


def _make_empty_graph() -> object:
    """Build a minimal empty ProvenanceGraph."""
    builder = GraphBuilder(attestation_kind_registry_hash())
    return builder.finalize()


def _make_metadata(
    graph_hash: str = "abc" * 21 + "a",  # 64 hex chars
    profile_fp: str = "def" * 21 + "d",
    policy_fp: str = "ghi" * 21 + "g",
    bundle_hash: str = "jkl" * 21 + "j",
    reg_hash: str = "mno" * 21 + "m",
) -> CompileMetadata:
    return CompileMetadata(
        provenance_graph_hash=graph_hash,
        strict_profile_fingerprint=profile_fp,
        evidence_policy_fingerprint=policy_fp,
        source_bundle_hash=bundle_hash,
        attestation_kind_registry_hash=reg_hash,
    )


# ---------------------------------------------------------------------------
# Test 1: round-trip identity
# ---------------------------------------------------------------------------


def test_compile_metadata_round_trip_dict() -> None:
    meta = CompileMetadata(
        provenance_graph_hash="a" * 64,
        strict_profile_fingerprint="b" * 64,
        evidence_policy_fingerprint="c" * 64,
        source_bundle_hash="d" * 64,
        attestation_kind_registry_hash="e" * 64,
        interpretation_policy_fingerprint="f" * 64,
        build_id="build-001",
        build_timestamp=datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc),
    )
    d = meta.to_metadata_dict()
    restored = CompileMetadata.from_metadata_dict(d)
    assert restored == meta


def test_compile_metadata_round_trip_dict_minimal_fields() -> None:
    meta = CompileMetadata(
        provenance_graph_hash="a" * 64,
        strict_profile_fingerprint="b" * 64,
        evidence_policy_fingerprint="c" * 64,
        source_bundle_hash="d" * 64,
        attestation_kind_registry_hash="e" * 64,
    )
    d = meta.to_metadata_dict()
    # Optional fields must not appear in the dict when not set
    assert "lawvm.interpretation_policy_fingerprint" not in d
    assert "lawvm.build_id" not in d
    assert "lawvm.build_timestamp" not in d
    restored = CompileMetadata.from_metadata_dict(d)
    assert restored == meta


def test_compile_metadata_round_trip_dict_preserves_optional_none() -> None:
    meta = CompileMetadata(
        provenance_graph_hash="a" * 64,
        strict_profile_fingerprint="b" * 64,
        evidence_policy_fingerprint="c" * 64,
        source_bundle_hash="d" * 64,
        attestation_kind_registry_hash="e" * 64,
        interpretation_policy_fingerprint=None,
        build_id="",
        build_timestamp=None,
    )
    d = meta.to_metadata_dict()
    restored = CompileMetadata.from_metadata_dict(d)
    assert restored.interpretation_policy_fingerprint is None
    assert restored.build_id == ""
    assert restored.build_timestamp is None


# ---------------------------------------------------------------------------
# Test 2: strict_profile_fingerprint is deterministic
# ---------------------------------------------------------------------------


def test_strict_profile_fingerprint_deterministic() -> None:
    profile = StrictProfile(
        name="fi_ingestion_v1",
        allows_target_guessing=True,
        allows_estimated_dates=False,
    )
    fp1 = compute_strict_profile_fingerprint(profile)
    fp2 = compute_strict_profile_fingerprint(profile)
    assert fp1 == fp2
    assert len(fp1) == 64  # sha256 hex


def test_strict_profile_fingerprint_differs_across_profiles() -> None:
    p1 = StrictProfile(name="p1", allows_target_guessing=True)
    p2 = StrictProfile(name="p2", allows_target_guessing=False)
    assert compute_strict_profile_fingerprint(p1) != compute_strict_profile_fingerprint(p2)


def test_strict_profile_fingerprint_same_name_different_fields() -> None:
    p1 = StrictProfile(name="same", allows_estimated_dates=True)
    p2 = StrictProfile(name="same", allows_estimated_dates=False)
    assert compute_strict_profile_fingerprint(p1) != compute_strict_profile_fingerprint(p2)


# ---------------------------------------------------------------------------
# Test 3: required fields validated
# ---------------------------------------------------------------------------


def test_compile_metadata_required_fields_validated_empty_dict() -> None:
    with pytest.raises(ValueError, match="missing required keys"):
        CompileMetadata.from_metadata_dict({})


def test_compile_metadata_required_fields_validated_partial_dict() -> None:
    with pytest.raises(ValueError):
        CompileMetadata.from_metadata_dict({
            "lawvm.provenance_graph_hash": "a" * 64,
            # missing the other 4 required keys
        })


def test_compile_metadata_required_fields_validated_empty_string_value() -> None:
    d = {
        "lawvm.provenance_graph_hash": "",  # empty → should fail
        "lawvm.strict_profile_fingerprint": "b" * 64,
        "lawvm.evidence_policy_fingerprint": "c" * 64,
        "lawvm.source_bundle_hash": "d" * 64,
        "lawvm.attestation_kind_registry_hash": "e" * 64,
    }
    with pytest.raises(ValueError, match="must be non-empty"):
        CompileMetadata.from_metadata_dict(d)


# ---------------------------------------------------------------------------
# Test 4: build_compile_metadata factory
# ---------------------------------------------------------------------------


def test_build_compile_metadata_from_graph_profile_policy() -> None:
    graph = _make_empty_graph()
    profile = _make_minimal_profile()
    registry = _make_registry()

    meta = build_compile_metadata(
        graph=graph,
        profile=profile,
        evidence_policy=registry,
        source_bundle_hash="s" * 64,
        build_id="test-build-001",
        build_timestamp=datetime(2026, 6, 4, 0, 0, 0, tzinfo=timezone.utc),
    )

    assert meta.provenance_graph_hash == graph.snapshot_hash  # ty:ignore[unresolved-attribute]
    assert meta.strict_profile_fingerprint == compute_strict_profile_fingerprint(profile)
    assert meta.evidence_policy_fingerprint == registry.registry_hash
    assert meta.source_bundle_hash == "s" * 64
    assert meta.attestation_kind_registry_hash == attestation_kind_registry_hash()
    assert meta.build_id == "test-build-001"
    assert meta.build_timestamp is not None


def test_build_compile_metadata_stable_for_same_inputs() -> None:
    """Same (graph, profile, policy, source_bundle_hash) → identical fingerprints."""
    graph = _make_empty_graph()
    profile = _make_minimal_profile()
    registry = _make_registry()

    meta1 = build_compile_metadata(
        graph=graph,
        profile=profile,
        evidence_policy=registry,
        source_bundle_hash="s" * 64,
    )
    meta2 = build_compile_metadata(
        graph=graph,
        profile=profile,
        evidence_policy=registry,
        source_bundle_hash="s" * 64,
    )

    assert meta1.provenance_graph_hash == meta2.provenance_graph_hash
    assert meta1.strict_profile_fingerprint == meta2.strict_profile_fingerprint
    assert meta1.evidence_policy_fingerprint == meta2.evidence_policy_fingerprint


# ---------------------------------------------------------------------------
# Test: to_metadata_dict keys are all lawvm.* prefixed
# ---------------------------------------------------------------------------


def test_compile_metadata_dict_keys_prefixed() -> None:
    meta = _make_metadata()
    d = meta.to_metadata_dict()
    assert all(k.startswith("lawvm.") for k in d)


def test_compile_metadata_construction_validates_non_empty_required() -> None:
    with pytest.raises(ValueError, match="provenance_graph_hash"):
        CompileMetadata(
            provenance_graph_hash="",
            strict_profile_fingerprint="b" * 64,
            evidence_policy_fingerprint="c" * 64,
            source_bundle_hash="d" * 64,
            attestation_kind_registry_hash="e" * 64,
        )
