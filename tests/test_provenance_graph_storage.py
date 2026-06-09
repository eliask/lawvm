"""Tests for provenance_graph_storage.py — Step 1 acceptance criteria.

Tests cover:
  - GraphStore write/read round-trip (graph, node, assertion, attestation)
  - Load-time hash check rejects tampered assertion
  - Load-time hash check rejects tampered attestation
  - Registry write
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from lawvm.core.provenance_graph import (
    ATTESTATION_KIND_REGISTRY_V0_HASH,
    ArtifactRef,
    GraphBuilder,
    GraphEdge,
    GraphNode,
    Interval,
    Producer,
    ProvenanceAssertion,
    ProvenanceAttestation,
    ProvenanceGraph,
    _sha256,
    assertion_canonical_payload,
    attestation_canonical_payload,
)
from lawvm.core.provenance_graph_storage import GraphStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_interval() -> Interval:
    return Interval(start=date(2024, 1, 1), end=None)


def _make_artifact_ref(artifact_id: str = "ref_id") -> ArtifactRef:
    return ArtifactRef(
        artifact_type="assertion",
        artifact_id=artifact_id,
        content_hash=artifact_id,
    )


def _make_producer() -> Producer:
    return Producer(
        producer_id="test.producer.v0",
        producer_kind="script",
    )


def _make_valid_assertion() -> ProvenanceAssertion:
    """Build a ProvenanceAssertion with a correct content-addressed id."""
    provisional = ProvenanceAssertion(
        assertion_id="__placeholder__",
        schema_version="v0",
        jurisdiction="fi",
        kind="fi.v1.TEST",
        layer="extraction",
        scope={"stage": "elab"},
        target={"section": "1"},
        value={"text": "hello"},
        source_refs=(),
        dependency_refs=(),
        valid_at=_make_interval(),
    )
    assertion_id = _sha256(assertion_canonical_payload(provisional))
    return ProvenanceAssertion(
        assertion_id=assertion_id,
        schema_version="v0",
        jurisdiction="fi",
        kind="fi.v1.TEST",
        layer="extraction",
        scope={"stage": "elab"},
        target={"section": "1"},
        value={"text": "hello"},
        source_refs=(),
        dependency_refs=(),
        valid_at=_make_interval(),
    )


def _make_valid_attestation() -> ProvenanceAttestation:
    """Build a ProvenanceAttestation with a correct content-addressed id."""
    subject = _make_artifact_ref()
    producer = _make_producer()
    provisional = ProvenanceAttestation(
        attestation_id="__placeholder__",
        attestation_kind="claim_submitted",
        subject=subject,
        materials=(),
        producer=producer,
        produced_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        payload={"note": "test"},
    )
    attestation_id = _sha256(attestation_canonical_payload(provisional))
    return ProvenanceAttestation(
        attestation_id=attestation_id,
        attestation_kind="claim_submitted",
        subject=subject,
        materials=(),
        producer=producer,
        produced_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        payload={"note": "test"},
    )


def _make_simple_graph() -> "tuple[ProvenanceGraph, ...]":

    n1 = GraphNode(
        node_id="node_1",
        node_type="assertion",
        artifact_ref=_make_artifact_ref("node_1"),
        payload_hash="node_1",
    )
    n2 = GraphNode(
        node_id="node_2",
        node_type="attestation",
        artifact_ref=_make_artifact_ref("node_2"),
        payload_hash="node_2",
    )
    e1 = GraphEdge(
        edge_id="edge_1",
        edge_type="validates",
        src_node_id="node_2",
        dst_node_id="node_1",
        payload={},
    )

    builder = GraphBuilder(ATTESTATION_KIND_REGISTRY_V0_HASH)
    builder.add_node(n1)
    builder.add_node(n2)
    builder.add_edge(e1)
    graph = builder.finalize()
    return (graph,)


# ---------------------------------------------------------------------------
# test_graph_store_read_after_write_roundtrip
# ---------------------------------------------------------------------------


def test_graph_store_read_after_write_roundtrip(tmp_path: Path) -> None:
    """Write a graph, read it back by snapshot_hash; deep equality holds."""
    store = GraphStore(tmp_path)
    (graph,) = _make_simple_graph()

    store.write_graph(graph)
    recovered = store.read_graph(graph.snapshot_hash)

    assert recovered.snapshot_hash == graph.snapshot_hash
    assert recovered.attestation_kind_registry_hash == graph.attestation_kind_registry_hash
    assert len(recovered.nodes) == len(graph.nodes)
    assert len(recovered.edges) == len(graph.edges)

    # Node-level deep equality
    original_node_ids = {n.node_id for n in graph.nodes}
    recovered_node_ids = {n.node_id for n in recovered.nodes}
    assert original_node_ids == recovered_node_ids

    # Edge-level deep equality
    original_edge_ids = {e.edge_id for e in graph.edges}
    recovered_edge_ids = {e.edge_id for e in recovered.edges}
    assert original_edge_ids == recovered_edge_ids


def test_graph_store_write_is_idempotent(tmp_path: Path) -> None:
    """Writing the same graph twice does not raise."""
    store = GraphStore(tmp_path)
    (graph,) = _make_simple_graph()
    store.write_graph(graph)
    store.write_graph(graph)  # should not raise
    recovered = store.read_graph(graph.snapshot_hash)
    assert recovered.snapshot_hash == graph.snapshot_hash


# ---------------------------------------------------------------------------
# test_assertion_load_time_hash_check_rejects_tampered
# ---------------------------------------------------------------------------


def test_assertion_load_time_hash_check_rejects_tampered(tmp_path: Path) -> None:
    """Write assertion, tamper the file, read → RuntimeError."""
    store = GraphStore(tmp_path)
    assertion = _make_valid_assertion()
    store.write_assertion(assertion)

    # Tamper the JSON on disk
    assertion_path = tmp_path / "objects" / "sha256" / f"{assertion.assertion_id}.json"
    data = json.loads(assertion_path.read_text(encoding="utf-8"))
    data["value"]["text"] = "TAMPERED"
    assertion_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(RuntimeError, match="tampered"):
        store.read_assertion(assertion.assertion_id)


# ---------------------------------------------------------------------------
# test_attestation_load_time_hash_check_rejects_tampered
# ---------------------------------------------------------------------------


def test_attestation_load_time_hash_check_rejects_tampered(tmp_path: Path) -> None:
    """Write attestation, tamper the file, read → RuntimeError."""
    store = GraphStore(tmp_path)
    attestation = _make_valid_attestation()
    store.write_attestation(attestation)

    # Tamper the JSON on disk
    attestation_path = tmp_path / "objects" / "sha256" / f"{attestation.attestation_id}.json"
    data = json.loads(attestation_path.read_text(encoding="utf-8"))
    data["payload"]["note"] = "TAMPERED"
    attestation_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(RuntimeError, match="tampered"):
        store.read_attestation(attestation.attestation_id)


# ---------------------------------------------------------------------------
# Additional store tests
# ---------------------------------------------------------------------------


def test_assertion_roundtrip_via_store(tmp_path: Path) -> None:
    """Write and read a ProvenanceAssertion; content is preserved."""
    store = GraphStore(tmp_path)
    assertion = _make_valid_assertion()
    store.write_assertion(assertion)
    recovered = store.read_assertion(assertion.assertion_id)
    assert recovered.assertion_id == assertion.assertion_id
    assert recovered.kind == assertion.kind
    assert recovered.jurisdiction == assertion.jurisdiction
    assert dict(recovered.value) == dict(assertion.value)


def test_attestation_roundtrip_via_store(tmp_path: Path) -> None:
    """Write and read a ProvenanceAttestation; content is preserved."""
    store = GraphStore(tmp_path)
    attestation = _make_valid_attestation()
    store.write_attestation(attestation)
    recovered = store.read_attestation(attestation.attestation_id)
    assert recovered.attestation_id == attestation.attestation_id
    assert recovered.attestation_kind == attestation.attestation_kind
    assert dict(recovered.payload) == dict(attestation.payload)


def test_store_read_missing_raises_file_not_found(tmp_path: Path) -> None:
    """Reading a non-existent assertion raises FileNotFoundError."""
    store = GraphStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.read_assertion("nonexistent_hash")


def test_registry_write(tmp_path: Path) -> None:
    """write_registry creates the attestation_kinds_v0.json file."""
    store = GraphStore(tmp_path)
    store.write_registry()
    registry_path = tmp_path / "registry" / "attestation_kinds_v0.json"
    assert registry_path.exists()
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    assert "_registry_hash" in data
    assert "kinds" in data
    assert len(data["kinds"]) == 20
