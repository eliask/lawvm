"""Tests for provenance_graph.py — Step 1 acceptance criteria.

Tests cover:
  - snapshot_hash determinism
  - attestation kind registry hash stability
  - unknown edge type rejected
  - unknown attestation kind rejected
  - no constructor mutation
  - SourceRef requires structural_locator
  - GraphBuilder finalize idempotent
  - negative evidence kinds flagged
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from lawvm.core.provenance_graph import (
    ATTESTATION_KIND_REGISTRY_V0_HASH,
    EDGE_TYPES,
    ArtifactRef,
    GraphBuilder,
    GraphEdge,
    GraphNode,
    Interval,
    Producer,
    ProvenanceAssertion,
    ProvenanceAttestation,
    ProvenanceGraph,
    Signature,
    SourceRef,
    _ATTESTATION_KIND_REGISTRY_V0,
    _compute_registry_hash,
    attestation_kind_registry_hash,
    get_attestation_kind,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_artifact_ref(artifact_type: str = "assertion", artifact_id: str = "abc123") -> ArtifactRef:
    return ArtifactRef(
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        content_hash=artifact_id,
    )


def _make_interval() -> Interval:
    return Interval(start=date(2024, 1, 1), end=None)


def _make_producer() -> Producer:
    return Producer(
        producer_id="test.producer.v0",
        producer_kind="script",
    )


def _make_source_ref() -> SourceRef:
    return SourceRef(
        artifact_digest="abc123",
        structural_locator="/akn/fi/act/2002/738/sec_1",
        bounded_quote_hash="deadbeef",
        normalization_policy_id="fi.akn.v1",
        byte_range=(0, 100),
    )


def _make_assertion(kind: str = "fi.v1.TEST", assertion_id: str = "test_id") -> ProvenanceAssertion:
    return ProvenanceAssertion(
        assertion_id=assertion_id,
        schema_version="v0",
        jurisdiction="fi",
        kind=kind,
        layer="extraction",
        scope={"stage": "elab"},
        target={"section": "1"},
        value={"foo": "bar"},
        source_refs=(),
        dependency_refs=(),
        valid_at=_make_interval(),
    )


def _make_attestation(
    attestation_kind: str = "claim_submitted",
    attestation_id: str = "attest_id",
) -> ProvenanceAttestation:
    subject = _make_artifact_ref()
    producer = _make_producer()
    return ProvenanceAttestation(
        attestation_id=attestation_id,
        attestation_kind=attestation_kind,
        subject=subject,
        materials=(),
        producer=producer,
        produced_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        payload={},
    )


def _make_graph_node(node_id: str = "n1", node_type: str = "assertion") -> GraphNode:
    ref = _make_artifact_ref(artifact_id=node_id)
    return GraphNode(
        node_id=node_id,
        node_type=node_type,
        artifact_ref=ref,
        payload_hash=node_id,
    )


def _make_graph_edge(
    edge_id: str = "e1",
    edge_type: str = "cites_source",
    src: str = "n1",
    dst: str = "n2",
) -> GraphEdge:
    return GraphEdge(
        edge_id=edge_id,
        edge_type=edge_type,
        src_node_id=src,
        dst_node_id=dst,
        payload={},
    )


def _build_simple_graph(*node_ids: str) -> ProvenanceGraph:
    builder = GraphBuilder(ATTESTATION_KIND_REGISTRY_V0_HASH)
    for i, nid in enumerate(node_ids):
        node = _make_graph_node(node_id=nid)
        builder.add_node(node)
    return builder.finalize()


# ---------------------------------------------------------------------------
# test_graph_snapshot_hash_deterministic
# ---------------------------------------------------------------------------


def test_graph_snapshot_hash_deterministic() -> None:
    """Same nodes+edges in any insertion order → same snapshot_hash."""
    n1 = _make_graph_node("node_alpha")
    n2 = _make_graph_node("node_beta")
    n3 = _make_graph_node("node_gamma")

    e1 = _make_graph_edge("edge_1", "cites_source", "node_alpha", "node_beta")
    e2 = _make_graph_edge("edge_2", "depends_on", "node_beta", "node_gamma")

    # Order 1
    builder_a = GraphBuilder(ATTESTATION_KIND_REGISTRY_V0_HASH)
    for n in (n1, n2, n3):
        builder_a.add_node(n)
    for e in (e1, e2):
        builder_a.add_edge(e)
    graph_a = builder_a.finalize()

    # Order 2 (reversed)
    builder_b = GraphBuilder(ATTESTATION_KIND_REGISTRY_V0_HASH)
    for n in (n3, n1, n2):
        builder_b.add_node(n)
    for e in (e2, e1):
        builder_b.add_edge(e)
    graph_b = builder_b.finalize()

    assert graph_a.snapshot_hash == graph_b.snapshot_hash
    assert len(graph_a.nodes) == 3
    assert len(graph_a.edges) == 2


# ---------------------------------------------------------------------------
# test_attestation_kind_registry_hash_stable
# ---------------------------------------------------------------------------


def test_attestation_kind_registry_hash_stable() -> None:
    """Registry hash is reproducible across calls."""
    h1 = attestation_kind_registry_hash()
    h2 = attestation_kind_registry_hash()
    h3 = _compute_registry_hash(_ATTESTATION_KIND_REGISTRY_V0)

    assert h1 == h2
    assert h1 == h3
    assert len(h1) == 64  # sha256 hex digest


def test_attestation_kind_registry_has_20_kinds() -> None:
    """Registry contains exactly 20 kinds as specified."""
    assert len(_ATTESTATION_KIND_REGISTRY_V0) == 20


# ---------------------------------------------------------------------------
# test_no_constructor_mutation
# ---------------------------------------------------------------------------


def test_no_constructor_mutation() -> None:
    """Instantiating graph types has zero side effects on global state."""
    registry_hash_before = attestation_kind_registry_hash()
    registry_len_before = len(_ATTESTATION_KIND_REGISTRY_V0)

    # Construct many objects
    assertion = _make_assertion()
    attestation = _make_attestation()
    node = _make_graph_node()
    edge = _make_graph_edge()
    source_ref = _make_source_ref()
    artifact_ref = _make_artifact_ref()
    producer = _make_producer()
    interval = _make_interval()
    sig = Signature(algorithm="ed25519", public_key="pk", signature_bytes=b"\x00\x01")

    builder = GraphBuilder(ATTESTATION_KIND_REGISTRY_V0_HASH)
    builder.add_node(node)
    builder.add_edge(edge)
    _ = builder.finalize()

    # Registry must be unchanged
    assert attestation_kind_registry_hash() == registry_hash_before
    assert len(_ATTESTATION_KIND_REGISTRY_V0) == registry_len_before


# ---------------------------------------------------------------------------
# test_source_ref_requires_structural_locator
# ---------------------------------------------------------------------------


def test_source_ref_requires_structural_locator() -> None:
    """SourceRef with empty structural_locator raises ValueError."""
    with pytest.raises(ValueError, match="structural_locator"):
        SourceRef(
            artifact_digest="abc123",
            structural_locator="",
            bounded_quote_hash="deadbeef",
            normalization_policy_id="fi.akn.v1",
            byte_range=(0, 100),
        )


def test_source_ref_valid_construction() -> None:
    """SourceRef with valid structural_locator succeeds."""
    ref = _make_source_ref()
    assert ref.structural_locator == "/akn/fi/act/2002/738/sec_1"


# ---------------------------------------------------------------------------
# test_graph_builder_finalize_is_idempotent
# ---------------------------------------------------------------------------


def test_graph_builder_finalize_is_idempotent() -> None:
    """Same builder state → same graph on repeated finalize() calls."""
    n1 = _make_graph_node("node_x")
    n2 = _make_graph_node("node_y")
    e1 = _make_graph_edge("edge_xy", "cites_source", "node_x", "node_y")

    builder = GraphBuilder(ATTESTATION_KIND_REGISTRY_V0_HASH)
    builder.add_node(n1)
    builder.add_node(n2)
    builder.add_edge(e1)

    graph_a = builder.finalize()
    graph_b = builder.finalize()

    assert graph_a.snapshot_hash == graph_b.snapshot_hash
    assert len(graph_a.nodes) == len(graph_b.nodes)
    assert len(graph_a.edges) == len(graph_b.edges)


# ---------------------------------------------------------------------------
# test_unknown_edge_type_rejected
# ---------------------------------------------------------------------------


def test_unknown_edge_type_rejected() -> None:
    """GraphEdge with edge_type not in EDGE_TYPES raises ValueError."""
    with pytest.raises(ValueError, match="EDGE_TYPES"):
        GraphEdge(
            edge_id="e_bad",
            edge_type="invented_type_xyz",
            src_node_id="n1",
            dst_node_id="n2",
        )


def test_all_canonical_edge_types_accepted() -> None:
    """Every canonical edge type can be constructed."""
    for edge_type in EDGE_TYPES:
        edge = GraphEdge(
            edge_id=f"e_{edge_type}",
            edge_type=edge_type,
            src_node_id="n1",
            dst_node_id="n2",
        )
        assert edge.edge_type == edge_type


# ---------------------------------------------------------------------------
# test_unknown_attestation_kind_rejected
# ---------------------------------------------------------------------------


def test_unknown_attestation_kind_rejected() -> None:
    """ProvenanceAttestation with kind not in registry raises ValueError."""
    with pytest.raises(ValueError, match="ATTESTATION_KIND_REGISTRY_V0"):
        ProvenanceAttestation(
            attestation_id="test_id",
            attestation_kind="invented_kind_not_in_registry",
            subject=_make_artifact_ref(),
            materials=(),
            producer=_make_producer(),
            produced_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            payload={},
        )


def test_all_registered_attestation_kinds_accepted() -> None:
    """Every registered attestation kind can construct an attestation."""
    for kind in _ATTESTATION_KIND_REGISTRY_V0:
        att = ProvenanceAttestation(
            attestation_id=f"id_{kind}",
            attestation_kind=kind,
            subject=_make_artifact_ref(),
            materials=(),
            producer=_make_producer(),
            produced_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            payload={},
        )
        assert att.attestation_kind == kind


# ---------------------------------------------------------------------------
# test_negative_evidence_kinds_flagged
# ---------------------------------------------------------------------------


def test_negative_evidence_kinds_flagged() -> None:
    """Attestation kinds for negative evidence have is_negative_evidence=True."""
    expected_negative_kinds = {
        "no_candidate_found",
        "corpus_search_exhausted",
        "no_later_amendment_found",
        "no_refutation_found",
    }
    for kind_name in expected_negative_kinds:
        spec = get_attestation_kind(kind_name)
        assert spec.is_negative_evidence is True, (
            f"Expected {kind_name!r} to have is_negative_evidence=True"
        )


def test_positive_evidence_kinds_not_flagged() -> None:
    """Standard attestation kinds do not have is_negative_evidence=True."""
    positive_kinds = {
        "claim_submitted",
        "schema_validated",
        "span_verified",
        "reviewed",
        "retracted",
    }
    for kind_name in positive_kinds:
        spec = get_attestation_kind(kind_name)
        assert spec.is_negative_evidence is False, (
            f"Expected {kind_name!r} to have is_negative_evidence=False"
        )


# ---------------------------------------------------------------------------
# Additional structural tests
# ---------------------------------------------------------------------------


def test_interval_end_none_is_open_ended() -> None:
    interval = Interval(start=date(2020, 1, 1), end=None)
    assert interval.end is None


def test_interval_rejects_end_before_start() -> None:
    with pytest.raises(ValueError, match="end must be >="):
        Interval(start=date(2024, 6, 1), end=date(2024, 1, 1))


def test_producer_kind_validated() -> None:
    with pytest.raises(ValueError, match="producer_kind"):
        Producer(producer_id="test", producer_kind="robot")  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]


def test_graph_builder_deduplicates_nodes() -> None:
    """Adding the same node_id twice results in only one node."""
    n = _make_graph_node("dup_node")
    builder = GraphBuilder(ATTESTATION_KIND_REGISTRY_V0_HASH)
    builder.add_node(n)
    builder.add_node(n)  # duplicate
    graph = builder.finalize()
    assert len(graph.nodes) == 1


def test_graph_builder_deduplicates_edges() -> None:
    """Adding the same edge_id twice results in only one edge."""
    e = _make_graph_edge("dup_edge", "cites_source", "n1", "n2")
    builder = GraphBuilder(ATTESTATION_KIND_REGISTRY_V0_HASH)
    builder.add_edge(e)
    builder.add_edge(e)  # duplicate
    graph = builder.finalize()
    assert len(graph.edges) == 1


def test_empty_graph_has_deterministic_hash() -> None:
    """Empty graph produces a consistent snapshot_hash."""
    builder_a = GraphBuilder(ATTESTATION_KIND_REGISTRY_V0_HASH)
    builder_b = GraphBuilder(ATTESTATION_KIND_REGISTRY_V0_HASH)
    assert builder_a.finalize().snapshot_hash == builder_b.finalize().snapshot_hash


def test_provenance_assertion_kind_required() -> None:
    with pytest.raises(ValueError, match="kind must be non-empty"):
        ProvenanceAssertion(
            assertion_id="test",
            schema_version="v0",
            jurisdiction="fi",
            kind="",
            layer="extraction",
            scope={},
            target={},
            value={},
            source_refs=(),
            dependency_refs=(),
            valid_at=_make_interval(),
        )


def test_graph_builder_add_assertion_returns_artifact_ref() -> None:
    """GraphBuilder.add_assertion returns an ArtifactRef for the assertion."""
    assertion = _make_assertion()
    builder = GraphBuilder(ATTESTATION_KIND_REGISTRY_V0_HASH)
    ref = builder.add_assertion(assertion)
    assert isinstance(ref, ArtifactRef)
    assert ref.artifact_type == "assertion"
    assert ref.artifact_id == assertion.assertion_id


def test_graph_builder_add_attestation_returns_artifact_ref() -> None:
    """GraphBuilder.add_attestation returns an ArtifactRef for the attestation."""
    attestation = _make_attestation()
    builder = GraphBuilder(ATTESTATION_KIND_REGISTRY_V0_HASH)
    ref = builder.add_attestation(attestation)
    assert isinstance(ref, ArtifactRef)
    assert ref.artifact_type == "attestation"
    assert ref.artifact_id == attestation.attestation_id
