"""Content-addressed object store for ProvenanceGraph artifacts.

Storage layout under data/{jurisdiction}/v{N}/provenance_graph/:

    objects/sha256/{assertion_id}.json      — ProvenanceAssertion objects
    objects/sha256/{attestation_id}.json    — ProvenanceAttestation objects
    nodes/sha256/{node_id}.json             — GraphNode objects
    edges/sha256/{edge_id}.json             — GraphEdge objects
    snapshots/{snapshot_hash}.json          — graph snapshot index
    registry/attestation_kinds_v0.json      — the attestation kind registry

Every read recomputes the hash from the payload and hard-fails on mismatch.
Writes are idempotent: writing the same content twice is safe.

API tier
--------
Internal storage substrate for Step 1.  All operations are explicit; no
hidden global mutations.  Callers must construct a GraphStore with a concrete
root path.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from lawvm.core.provenance_graph import (
    ArtifactRef,
    AttestationKindSpec,
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
    _sha256,
    assertion_canonical_payload,
    attestation_canonical_payload,
    _graph_snapshot_canonical,
)


# ---------------------------------------------------------------------------
# JSON serialization helpers
# ---------------------------------------------------------------------------


def _serialize_assertion(a: ProvenanceAssertion) -> dict[str, Any]:
    return {
        "assertion_id": a.assertion_id,
        "schema_version": a.schema_version,
        "jurisdiction": a.jurisdiction,
        "kind": a.kind,
        "layer": a.layer,
        "scope": dict(a.scope),
        "target": dict(a.target),
        "value": dict(a.value),
        "source_refs": [
            {
                "artifact_digest": r.artifact_digest,
                "structural_locator": r.structural_locator,
                "bounded_quote_hash": r.bounded_quote_hash,
                "normalization_policy_id": r.normalization_policy_id,
                "byte_range": list(r.byte_range),
            }
            for r in a.source_refs
        ],
        "dependency_refs": [
            {
                "artifact_type": r.artifact_type,
                "artifact_id": r.artifact_id,
                "content_hash": r.content_hash,
            }
            for r in a.dependency_refs
        ],
        "valid_at": {
            "start": a.valid_at.start.isoformat(),
            "end": a.valid_at.end.isoformat() if a.valid_at.end else None,
        },
        "supersedes": list(a.supersedes),
        "disputes": list(a.disputes),
        "rationale": a.rationale,
    }


def _deserialize_assertion(d: dict[str, Any]) -> ProvenanceAssertion:
    valid_at_d = d["valid_at"]
    valid_at = Interval(
        start=date.fromisoformat(valid_at_d["start"]),
        end=date.fromisoformat(valid_at_d["end"]) if valid_at_d.get("end") else None,
    )
    source_refs = tuple(
        SourceRef(
            artifact_digest=r["artifact_digest"],
            structural_locator=r["structural_locator"],
            bounded_quote_hash=r["bounded_quote_hash"],
            normalization_policy_id=r["normalization_policy_id"],
            byte_range=(r["byte_range"][0], r["byte_range"][1]),
        )
        for r in d.get("source_refs", [])
    )
    dependency_refs = tuple(
        ArtifactRef(
            artifact_type=r["artifact_type"],
            artifact_id=r["artifact_id"],
            content_hash=r["content_hash"],
        )
        for r in d.get("dependency_refs", [])
    )
    return ProvenanceAssertion(
        assertion_id=d["assertion_id"],
        schema_version=d["schema_version"],
        jurisdiction=d["jurisdiction"],
        kind=d["kind"],
        layer=d["layer"],
        scope=d.get("scope", {}),
        target=d.get("target", {}),
        value=d.get("value", {}),
        source_refs=source_refs,
        dependency_refs=dependency_refs,
        valid_at=valid_at,
        supersedes=tuple(d.get("supersedes", [])),
        disputes=tuple(d.get("disputes", [])),
        rationale=d.get("rationale", ""),
    )


def _serialize_attestation(a: ProvenanceAttestation) -> dict[str, Any]:
    def _ser_material(m: ArtifactRef | SourceRef) -> dict[str, Any]:
        if isinstance(m, ArtifactRef):
            return {
                "_type": "ArtifactRef",
                "artifact_type": m.artifact_type,
                "artifact_id": m.artifact_id,
                "content_hash": m.content_hash,
            }
        return {
            "_type": "SourceRef",
            "artifact_digest": m.artifact_digest,
            "structural_locator": m.structural_locator,
            "bounded_quote_hash": m.bounded_quote_hash,
            "normalization_policy_id": m.normalization_policy_id,
            "byte_range": list(m.byte_range),
        }

    sig_d: dict[str, Any] | None = None
    if a.signature is not None:
        sig_d = {
            "algorithm": a.signature.algorithm,
            "public_key": a.signature.public_key,
            "signature_bytes": a.signature.signature_bytes.hex(),
        }

    return {
        "attestation_id": a.attestation_id,
        "attestation_kind": a.attestation_kind,
        "subject": {
            "artifact_type": a.subject.artifact_type,
            "artifact_id": a.subject.artifact_id,
            "content_hash": a.subject.content_hash,
        },
        "materials": [_ser_material(m) for m in a.materials],
        "producer": {
            "producer_id": a.producer.producer_id,
            "producer_kind": a.producer.producer_kind,
            "public_key": a.producer.public_key,
            "metadata": dict(a.producer.metadata),
        },
        "produced_at": a.produced_at.isoformat(),
        "payload": dict(a.payload),
        "signature": sig_d,
    }


def _deserialize_attestation(d: dict[str, Any]) -> ProvenanceAttestation:
    def _deser_material(m: dict[str, Any]) -> ArtifactRef | SourceRef:
        if m.get("_type") == "ArtifactRef":
            return ArtifactRef(
                artifact_type=m["artifact_type"],
                artifact_id=m["artifact_id"],
                content_hash=m["content_hash"],
            )
        return SourceRef(
            artifact_digest=m["artifact_digest"],
            structural_locator=m["structural_locator"],
            bounded_quote_hash=m["bounded_quote_hash"],
            normalization_policy_id=m["normalization_policy_id"],
            byte_range=(m["byte_range"][0], m["byte_range"][1]),
        )

    producer_d = d["producer"]
    producer = Producer(
        producer_id=producer_d["producer_id"],
        producer_kind=producer_d["producer_kind"],
        public_key=producer_d.get("public_key"),
        metadata=producer_d.get("metadata", {}),
    )

    sig: Signature | None = None
    if d.get("signature"):
        sig_d = d["signature"]
        sig = Signature(
            algorithm=sig_d["algorithm"],
            public_key=sig_d["public_key"],
            signature_bytes=bytes.fromhex(sig_d["signature_bytes"]),
        )

    subject_d = d["subject"]
    subject = ArtifactRef(
        artifact_type=subject_d["artifact_type"],
        artifact_id=subject_d["artifact_id"],
        content_hash=subject_d["content_hash"],
    )

    return ProvenanceAttestation(
        attestation_id=d["attestation_id"],
        attestation_kind=d["attestation_kind"],
        subject=subject,
        materials=tuple(_deser_material(m) for m in d.get("materials", [])),
        producer=producer,
        produced_at=datetime.fromisoformat(d["produced_at"]),
        payload=d.get("payload", {}),
        signature=sig,
    )


def _serialize_node(n: GraphNode) -> dict[str, Any]:
    return {
        "node_id": n.node_id,
        "node_type": n.node_type,
        "artifact_ref": {
            "artifact_type": n.artifact_ref.artifact_type,
            "artifact_id": n.artifact_ref.artifact_id,
            "content_hash": n.artifact_ref.content_hash,
        },
        "payload_hash": n.payload_hash,
    }


def _deserialize_node(d: dict[str, Any]) -> GraphNode:
    ref_d = d["artifact_ref"]
    return GraphNode(
        node_id=d["node_id"],
        node_type=d["node_type"],
        artifact_ref=ArtifactRef(
            artifact_type=ref_d["artifact_type"],
            artifact_id=ref_d["artifact_id"],
            content_hash=ref_d["content_hash"],
        ),
        payload_hash=d["payload_hash"],
    )


def _serialize_edge(e: GraphEdge) -> dict[str, Any]:
    return {
        "edge_id": e.edge_id,
        "edge_type": e.edge_type,
        "src_node_id": e.src_node_id,
        "dst_node_id": e.dst_node_id,
        "payload": dict(e.payload),
    }


def _deserialize_edge(d: dict[str, Any]) -> GraphEdge:
    return GraphEdge(
        edge_id=d["edge_id"],
        edge_type=d["edge_type"],
        src_node_id=d["src_node_id"],
        dst_node_id=d["dst_node_id"],
        payload=d.get("payload", {}),
    )


# ---------------------------------------------------------------------------
# GraphStore
# ---------------------------------------------------------------------------


class GraphStore:
    """Content-addressed object store for ProvenanceGraph artifacts.

    Every read recomputes the payload hash and raises RuntimeError on mismatch.
    Every write is idempotent: writing the same content twice is safe.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def _objects_dir(self) -> Path:
        return self._root / "objects" / "sha256"

    def _nodes_dir(self) -> Path:
        return self._root / "nodes" / "sha256"

    def _edges_dir(self) -> Path:
        return self._root / "edges" / "sha256"

    def _snapshots_dir(self) -> Path:
        return self._root / "snapshots"

    def _registry_dir(self) -> Path:
        return self._root / "registry"

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, indent=2, ensure_ascii=True)
        if not path.exists():
            path.write_text(text, encoding="utf-8")

    def _read_json(self, path: Path) -> Any:
        if not path.exists():
            raise FileNotFoundError(f"GraphStore: file not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    # --- assertion ---

    def write_assertion(self, a: ProvenanceAssertion) -> None:
        path = self._objects_dir() / f"{a.assertion_id}.json"
        data = _serialize_assertion(a)
        data["_content_hash"] = a.assertion_id
        self._write_json(path, data)

    def read_assertion(self, assertion_id: str) -> ProvenanceAssertion:
        path = self._objects_dir() / f"{assertion_id}.json"
        d = self._read_json(path)
        a = _deserialize_assertion(d)
        recomputed = _sha256(assertion_canonical_payload(a))
        if recomputed != assertion_id:
            raise RuntimeError(
                f"GraphStore: assertion hash mismatch for {assertion_id!r}; "
                f"file may be tampered. Recomputed: {recomputed!r}"
            )
        return a

    # --- attestation ---

    def write_attestation(self, a: ProvenanceAttestation) -> None:
        path = self._objects_dir() / f"{a.attestation_id}.json"
        data = _serialize_attestation(a)
        data["_content_hash"] = a.attestation_id
        self._write_json(path, data)

    def read_attestation(self, attestation_id: str) -> ProvenanceAttestation:
        path = self._objects_dir() / f"{attestation_id}.json"
        d = self._read_json(path)
        a = _deserialize_attestation(d)
        recomputed = _sha256(attestation_canonical_payload(a))
        if recomputed != attestation_id:
            raise RuntimeError(
                f"GraphStore: attestation hash mismatch for {attestation_id!r}; "
                f"file may be tampered. Recomputed: {recomputed!r}"
            )
        return a

    # --- node ---

    def write_node(self, n: GraphNode) -> None:
        path = self._nodes_dir() / f"{n.node_id}.json"
        self._write_json(path, _serialize_node(n))

    def read_node(self, node_id: str) -> GraphNode:
        path = self._nodes_dir() / f"{node_id}.json"
        d = self._read_json(path)
        return _deserialize_node(d)

    # --- edge ---

    def write_edge(self, e: GraphEdge) -> None:
        path = self._edges_dir() / f"{e.edge_id}.json"
        self._write_json(path, _serialize_edge(e))

    def read_edge(self, edge_id: str) -> GraphEdge:
        path = self._edges_dir() / f"{edge_id}.json"
        d = self._read_json(path)
        return _deserialize_edge(d)

    # --- graph ---

    def write_graph(self, graph: ProvenanceGraph) -> None:
        """Write a ProvenanceGraph to the store.  Idempotent; verifies hash."""
        # Verify snapshot_hash before writing
        canonical = _graph_snapshot_canonical(graph.nodes, graph.edges)
        expected = _sha256(canonical)
        if expected != graph.snapshot_hash:
            raise RuntimeError(
                f"GraphStore.write_graph: snapshot_hash mismatch; "
                f"expected {expected!r}, graph carries {graph.snapshot_hash!r}"
            )
        # Write nodes and edges individually
        for node in graph.nodes:
            self.write_node(node)
        for edge in graph.edges:
            self.write_edge(edge)
        # Write snapshot index
        snapshot_path = self._snapshots_dir() / f"{graph.snapshot_hash}.json"
        snapshot_data = {
            "snapshot_hash": graph.snapshot_hash,
            "attestation_kind_registry_hash": graph.attestation_kind_registry_hash,
            "nodes": [n.node_id for n in graph.nodes],
            "edges": [e.edge_id for e in graph.edges],
        }
        self._write_json(snapshot_path, snapshot_data)

    def read_graph(self, snapshot_hash: str) -> ProvenanceGraph:
        """Read a ProvenanceGraph by snapshot_hash.  Hard-fails on hash mismatch."""
        snapshot_path = self._snapshots_dir() / f"{snapshot_hash}.json"
        d = self._read_json(snapshot_path)
        nodes = tuple(self.read_node(node_id) for node_id in d["nodes"])
        edges = tuple(self.read_edge(edge_id) for edge_id in d["edges"])
        canonical = _graph_snapshot_canonical(nodes, edges)
        recomputed = _sha256(canonical)
        if recomputed != snapshot_hash:
            raise RuntimeError(
                f"GraphStore.read_graph: snapshot_hash mismatch for {snapshot_hash!r}; "
                f"recomputed {recomputed!r}"
            )
        return ProvenanceGraph(
            nodes=nodes,
            edges=edges,
            snapshot_hash=recomputed,
            attestation_kind_registry_hash=d.get("attestation_kind_registry_hash", ""),
        )

    def write_registry(self) -> None:
        """Write the v0 attestation kind registry to the store.  Idempotent."""
        registry_data = {
            "_registry_hash": _compute_registry_hash(_ATTESTATION_KIND_REGISTRY_V0),
            "kinds": [
                {
                    "kind": spec.kind,
                    "description": spec.description,
                    "payload_schema_summary": spec.payload_schema_summary,
                    "is_negative_evidence": spec.is_negative_evidence,
                }
                for spec in sorted(
                    _ATTESTATION_KIND_REGISTRY_V0.values(), key=lambda s: s.kind
                )
            ],
        }
        path = self._registry_dir() / "attestation_kinds_v0.json"
        self._write_json(path, registry_data)
