"""Tests for CompileFacade.with_metadata factory — Step 5.

Covers:
  10. test_compile_facade_with_metadata_factory
"""
from __future__ import annotations


from lawvm.core.compile_facade import CompileFacade
from lawvm.core.compile_metadata import compute_strict_profile_fingerprint
from lawvm.core.compile_result import CanonicalBundle, StrictProfile
from lawvm.core.evidence_policy import EvidencePolicyRegistry
from lawvm.core.provenance_graph import (
    GraphBuilder,
    attestation_kind_registry_hash,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_empty_graph() -> object:
    builder = GraphBuilder(attestation_kind_registry_hash())
    return builder.finalize()


def _make_registry() -> EvidencePolicyRegistry:
    return EvidencePolicyRegistry.build(
        registry_id="test.policy.v0",
        registry_version="v0.0.1",
        predicates=(),
    )


# ---------------------------------------------------------------------------
# Test 10: with_metadata factory
# ---------------------------------------------------------------------------


def test_compile_facade_with_metadata_factory() -> None:
    graph = _make_empty_graph()
    profile = StrictProfile(name="test_profile")
    registry = _make_registry()

    facade = CompileFacade.with_metadata(
        bundle=CanonicalBundle(),
        finding_ledger=(),
        replay_mode="official_consolidation",
        graph=graph,  # ty:ignore[invalid-argument-type]
        strict_profile=profile,
        evidence_policy=registry,
        source_bundle_hash="s" * 64,
        build_id="test-001",
    )

    assert facade.compile_metadata is not None
    assert facade.compile_metadata.provenance_graph_hash == graph.snapshot_hash  # ty:ignore[unresolved-attribute]
    assert facade.compile_metadata.strict_profile_fingerprint == compute_strict_profile_fingerprint(profile)
    assert facade.compile_metadata.evidence_policy_fingerprint == registry.registry_hash
    assert facade.compile_metadata.source_bundle_hash == "s" * 64
    assert facade.compile_metadata.build_id == "test-001"
    assert facade.compile_metadata.attestation_kind_registry_hash == attestation_kind_registry_hash()


def test_compile_facade_without_metadata_is_backward_compatible() -> None:
    """Existing call sites that omit compile_metadata continue working."""
    facade = CompileFacade(
        bundle=CanonicalBundle(),
        finding_ledger=(),
        replay_mode="official_consolidation",
    )
    assert facade.compile_metadata is None


def test_compile_facade_from_phase_result_backward_compatible() -> None:
    """from_phase_result does not require compile_metadata."""
    from lawvm.core.phase_result import PhaseResult

    pr = PhaseResult(output=CanonicalBundle())
    facade = CompileFacade.from_phase_result(pr, replay_mode="official_consolidation")
    assert facade.compile_metadata is None


def test_compile_facade_with_metadata_replay_mode_preserved() -> None:
    graph = _make_empty_graph()
    profile = StrictProfile(name="p")
    registry = _make_registry()

    facade = CompileFacade.with_metadata(
        bundle=CanonicalBundle(),
        finding_ledger=(),
        replay_mode="legal_pit",
        graph=graph,  # ty:ignore[invalid-argument-type]
        strict_profile=profile,
        evidence_policy=registry,
        source_bundle_hash="s" * 64,
    )

    assert facade.replay_mode == "legal_pit"
