"""Provenance graph substrate — Step 1.

Core immutable types, GraphBuilder, and attestation_kind registry.

All constructors are pure dataclasses.  Graph operations go through
GraphBuilder explicitly.  No hidden global mutations.  See AGENTS.md §12.1.

Storage layout and read/write helpers live in provenance_graph_storage.py.
Conversion facades (graph_from_phase_result / findings_from_graph) live in
provenance_graph_facade.py.

API tier
--------
Core provenance substrate.  This is Step 1 of the v3 provenance graph
transition (notes_internal/UNIFIED_PROVENANCE_GRAPH_DESIGN_v3.md §13).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Mapping


# ---------------------------------------------------------------------------
# JSON-compatible leaf type alias (for type hints only)
# ---------------------------------------------------------------------------

Json = Any


# ---------------------------------------------------------------------------
# Interval
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Interval:
    """Closed-open date interval.  ``end=None`` means open-ended."""

    start: date
    end: date | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.start, date):
            raise TypeError("Interval.start must be a date")
        if self.end is not None and not isinstance(self.end, date):
            raise TypeError("Interval.end must be a date or None")
        if self.end is not None and self.end < self.start:
            raise ValueError("Interval.end must be >= Interval.start")


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Signature:
    """Detached signature over a content-addressed artifact."""

    algorithm: str
    public_key: str
    signature_bytes: bytes

    def __post_init__(self) -> None:
        if not self.algorithm:
            raise ValueError("Signature.algorithm must be non-empty")
        if not self.public_key:
            raise ValueError("Signature.public_key must be non-empty")
        if not isinstance(self.signature_bytes, bytes):
            raise TypeError("Signature.signature_bytes must be bytes")


# ---------------------------------------------------------------------------
# Producer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Producer:
    """Identity record for any agent that produces assertions or attestations.

    producer_kind is metadata for policy queries; it is never a precedence
    input (no 'human beats LLM' semantics in the kernel).
    """

    producer_id: str
    producer_kind: Literal["human", "llm", "service", "script", "institution"]
    public_key: str | None = None
    metadata: Mapping[str, Json] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.producer_id:
            raise ValueError("Producer.producer_id must be non-empty")
        valid_kinds = {"human", "llm", "service", "script", "institution"}
        if self.producer_kind not in valid_kinds:
            raise ValueError(
                f"Producer.producer_kind must be one of {sorted(valid_kinds)!r}, "
                f"got {self.producer_kind!r}"
            )


# ---------------------------------------------------------------------------
# SourceRef + ArtifactRef
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Content-addressed reference to a span within a source artifact.

    structural_locator must be non-empty (xpath / akn-locator / structured
    path).  Byte offsets alone are fragile across XML reformats; the
    structural locator makes the reference robust.
    """

    artifact_digest: str
    structural_locator: str
    bounded_quote_hash: str
    normalization_policy_id: str
    byte_range: tuple[int, int]

    def __post_init__(self) -> None:
        if not self.structural_locator:
            raise ValueError(
                "SourceRef.structural_locator must be non-empty; "
                "byte offsets alone are fragile — provide an xpath / akn-locator"
            )
        if not self.artifact_digest:
            raise ValueError("SourceRef.artifact_digest must be non-empty")
        if not isinstance(self.byte_range, tuple) or len(self.byte_range) != 2:
            raise TypeError("SourceRef.byte_range must be a 2-tuple of ints")
        start, end = self.byte_range
        if not isinstance(start, int) or not isinstance(end, int):
            raise TypeError("SourceRef.byte_range values must be ints")
        if start < 0 or end < start:
            raise ValueError("SourceRef.byte_range must satisfy 0 <= start <= end")


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """Content-addressed reference to any artifact in the provenance graph."""

    artifact_type: str
    artifact_id: str
    content_hash: str

    def __post_init__(self) -> None:
        if not self.artifact_type:
            raise ValueError("ArtifactRef.artifact_type must be non-empty")
        if not self.artifact_id:
            raise ValueError("ArtifactRef.artifact_id must be non-empty")
        if not self.content_hash:
            raise ValueError("ArtifactRef.content_hash must be non-empty")


# ---------------------------------------------------------------------------
# ProvenanceAssertion
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProvenanceAssertion:
    """Immutable, content-addressed epistemic assertion.

    assertion_id is sha256(canonical_payload).  Load-time hash verification
    rejects tampered artifacts.  No status/review_status/confidence fields —
    state is a query over the attestation graph reachable from this assertion.

    Layer values: extraction | correction | adjudication | source_acquisition |
    semantic_compilation | facade_observation | facade_obligation |
    facade_violation
    """

    assertion_id: str
    schema_version: str
    jurisdiction: str
    kind: str
    layer: str
    scope: Mapping[str, Json]
    target: Mapping[str, Json]
    value: Mapping[str, Json]
    source_refs: tuple[SourceRef, ...]
    dependency_refs: tuple[ArtifactRef, ...]
    valid_at: Interval
    supersedes: tuple[str, ...] = ()
    disputes: tuple[str, ...] = ()
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.assertion_id:
            raise ValueError("ProvenanceAssertion.assertion_id must be non-empty")
        if not self.schema_version:
            raise ValueError("ProvenanceAssertion.schema_version must be non-empty")
        if not self.kind:
            raise ValueError("ProvenanceAssertion.kind must be non-empty")
        if not self.layer:
            raise ValueError("ProvenanceAssertion.layer must be non-empty")
        if not isinstance(self.valid_at, Interval):
            raise TypeError("ProvenanceAssertion.valid_at must be an Interval")
        if not isinstance(self.source_refs, tuple):
            raise TypeError("ProvenanceAssertion.source_refs must be a tuple")
        if not isinstance(self.dependency_refs, tuple):
            raise TypeError("ProvenanceAssertion.dependency_refs must be a tuple")


# ---------------------------------------------------------------------------
# ProvenanceAttestation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProvenanceAttestation:
    """Immutable, content-addressed attestation about a graph artifact.

    Every operation that touches an assertion emits a ProvenanceAttestation.
    attestation_kind must be registered in ATTESTATION_KIND_REGISTRY_V0;
    unknown kinds are rejected at construction.
    """

    attestation_id: str
    attestation_kind: str
    subject: ArtifactRef
    materials: tuple[ArtifactRef | SourceRef, ...]
    producer: Producer
    produced_at: datetime
    payload: Mapping[str, Json]
    signature: Signature | None = None

    def __post_init__(self) -> None:
        if not self.attestation_id:
            raise ValueError("ProvenanceAttestation.attestation_id must be non-empty")
        if not self.attestation_kind:
            raise ValueError("ProvenanceAttestation.attestation_kind must be non-empty")
        if self.attestation_kind not in _ATTESTATION_KIND_REGISTRY_V0:
            raise ValueError(
                f"ProvenanceAttestation.attestation_kind={self.attestation_kind!r} "
                "is not registered in ATTESTATION_KIND_REGISTRY_V0"
            )
        if not isinstance(self.subject, ArtifactRef):
            raise TypeError("ProvenanceAttestation.subject must be an ArtifactRef")
        if not isinstance(self.materials, tuple):
            raise TypeError("ProvenanceAttestation.materials must be a tuple")
        if not isinstance(self.produced_at, datetime):
            raise TypeError("ProvenanceAttestation.produced_at must be a datetime")


# ---------------------------------------------------------------------------
# Graph nodes and edges
# ---------------------------------------------------------------------------


EDGE_TYPES: frozenset[str] = frozenset({
    "cites_source",
    "depends_on",
    "generated_by",
    "validates",
    "reviews",
    "refutes",
    "retracts",
    "supersedes",
    "disputes",
    "resolves_obligation",
    "augments_finding",
    "emits_artifact",
    "consumed_by_build",
    "derives_projection",
})


@dataclass(frozen=True, slots=True)
class GraphNode:
    """One node in the provenance graph, wrapping a content-addressed artifact."""

    node_id: str
    node_type: str
    artifact_ref: ArtifactRef
    payload_hash: str

    def __post_init__(self) -> None:
        if not self.node_id:
            raise ValueError("GraphNode.node_id must be non-empty")
        if not self.node_type:
            raise ValueError("GraphNode.node_type must be non-empty")
        if not isinstance(self.artifact_ref, ArtifactRef):
            raise TypeError("GraphNode.artifact_ref must be an ArtifactRef")
        if not self.payload_hash:
            raise ValueError("GraphNode.payload_hash must be non-empty")


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """Directed edge in the provenance graph.

    edge_type must be in EDGE_TYPES; unknown types are rejected at construction.
    """

    edge_id: str
    edge_type: str
    src_node_id: str
    dst_node_id: str
    payload: Mapping[str, Json] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.edge_id:
            raise ValueError("GraphEdge.edge_id must be non-empty")
        if self.edge_type not in EDGE_TYPES:
            raise ValueError(
                f"GraphEdge.edge_type={self.edge_type!r} is not in EDGE_TYPES; "
                f"known types: {sorted(EDGE_TYPES)!r}"
            )
        if not self.src_node_id:
            raise ValueError("GraphEdge.src_node_id must be non-empty")
        if not self.dst_node_id:
            raise ValueError("GraphEdge.dst_node_id must be non-empty")


# ---------------------------------------------------------------------------
# ProvenanceGraph
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProvenanceGraph:
    """Immutable, content-addressed provenance graph snapshot.

    snapshot_hash is sha256 over canonical (sorted) node+edge serialization.
    Same nodes+edges in any order → same snapshot_hash (canonical ordering
    is applied before hashing).
    """

    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    snapshot_hash: str
    attestation_kind_registry_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.nodes, tuple):
            raise TypeError("ProvenanceGraph.nodes must be a tuple")
        if not isinstance(self.edges, tuple):
            raise TypeError("ProvenanceGraph.edges must be a tuple")
        if not self.snapshot_hash:
            raise ValueError("ProvenanceGraph.snapshot_hash must be non-empty")
        if not self.attestation_kind_registry_hash:
            raise ValueError(
                "ProvenanceGraph.attestation_kind_registry_hash must be non-empty"
            )


# ---------------------------------------------------------------------------
# Attestation kind registry (v0)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttestationKindSpec:
    """Metadata for one attestation kind in the governed registry."""

    kind: str
    description: str
    payload_schema_summary: str
    is_negative_evidence: bool = False

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("AttestationKindSpec.kind must be non-empty")
        if not self.description:
            raise ValueError("AttestationKindSpec.description must be non-empty")


_ATTESTATION_KIND_REGISTRY_V0: dict[str, AttestationKindSpec] = {
    spec.kind: spec
    for spec in (
        AttestationKindSpec(
            kind="claim_submitted",
            description="An assertion was filed into the graph by a producer.",
            payload_schema_summary="{}",
        ),
        AttestationKindSpec(
            kind="schema_validated",
            description="The assertion payload was validated against its kind schema.",
            payload_schema_summary="{schema_id: str, schema_version: str}",
        ),
        AttestationKindSpec(
            kind="span_verified",
            description="The assertion's source span was verified against the artifact digest.",
            payload_schema_summary="{artifact_digest: str, byte_range: [int, int]}",
        ),
        AttestationKindSpec(
            kind="source_hash_verified",
            description="The source artifact content hash was verified.",
            payload_schema_summary="{artifact_digest: str}",
        ),
        AttestationKindSpec(
            kind="dependency_resolved",
            description="All dependency_refs on the assertion were resolved in the graph.",
            payload_schema_summary="{resolved_ids: [str]}",
        ),
        AttestationKindSpec(
            kind="entailment_verified",
            description="The assertion value was verified to follow from its source spans.",
            payload_schema_summary="{verifier_id: str}",
        ),
        AttestationKindSpec(
            kind="invariant_checked",
            description="An invariant predicate over the assertion was evaluated and passed.",
            payload_schema_summary="{invariant_id: str, result: bool}",
        ),
        AttestationKindSpec(
            kind="dry_run_replayed",
            description="The assertion was applied in a dry-run compile and produced valid output.",
            payload_schema_summary="{build_id: str, result: str}",
        ),
        AttestationKindSpec(
            kind="candidate_set_attested",
            description="The full candidate set considered before selection was recorded.",
            payload_schema_summary="{candidates: [str], count: int}",
        ),
        AttestationKindSpec(
            kind="selection_justified",
            description="The selection from the candidate set was given a documented reason.",
            payload_schema_summary="{selected: str, reason: str}",
        ),
        AttestationKindSpec(
            kind="reviewed",
            description="The assertion was reviewed by a producer.",
            payload_schema_summary="{verdict: str, notes: str}",
        ),
        AttestationKindSpec(
            kind="refutation_attempted",
            description="An adversarial producer attempted to refute the assertion.",
            payload_schema_summary="{succeeded: bool, counterexample: str}",
        ),
        AttestationKindSpec(
            kind="contradiction_found",
            description="A contradiction between this assertion and another was documented.",
            payload_schema_summary="{other_assertion_id: str, description: str}",
        ),
        AttestationKindSpec(
            kind="retracted",
            description="The assertion was retracted; downstream consumers are tainted.",
            payload_schema_summary="{reason: str, retracted_by: str}",
        ),
        AttestationKindSpec(
            kind="superseded",
            description="The assertion was superseded by a newer assertion.",
            payload_schema_summary="{superseding_assertion_id: str, delta_reason: str}",
        ),
        AttestationKindSpec(
            kind="transparency_logged",
            description="The assertion was recorded in an external transparency log.",
            payload_schema_summary="{log_id: str, log_entry_hash: str}",
        ),
        AttestationKindSpec(
            kind="no_candidate_found",
            description="A bounded candidate enumeration ran and found no candidates.",
            payload_schema_summary="{search_scope: str, bound: int}",
            is_negative_evidence=True,
        ),
        AttestationKindSpec(
            kind="corpus_search_exhausted",
            description="A bounded corpus graph search completed without finding a hit.",
            payload_schema_summary="{corpus_id: str, search_depth: int, found_target: bool}",
            is_negative_evidence=True,
        ),
        AttestationKindSpec(
            kind="no_later_amendment_found",
            description="A provision timeline search within an interval found no later amendment.",
            payload_schema_summary="{interval_start: str, interval_end: str}",
            is_negative_evidence=True,
        ),
        AttestationKindSpec(
            kind="no_refutation_found",
            description="An adversarial verifier ran and did not produce a counterexample.",
            payload_schema_summary="{verifier_id: str, search_budget: str}",
            is_negative_evidence=True,
        ),
    )
}

_ATTESTATION_KIND_REGISTRY_V0_LOAD_TIME_CHECK: None = None


def _compute_registry_hash(registry: dict[str, AttestationKindSpec]) -> str:
    """Deterministic hash of the registry: sorted by kind, stable field ordering."""
    canonical: list[dict[str, Any]] = []
    for kind_key in sorted(registry):
        spec = registry[kind_key]
        canonical.append({
            "kind": spec.kind,
            "description": spec.description,
            "payload_schema_summary": spec.payload_schema_summary,
            "is_negative_evidence": spec.is_negative_evidence,
        })
    payload = json.dumps(canonical, separators=(",", ":"), ensure_ascii=True, sort_keys=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


ATTESTATION_KIND_REGISTRY_V0_HASH: str = _compute_registry_hash(_ATTESTATION_KIND_REGISTRY_V0)


def get_attestation_kind(kind: str) -> AttestationKindSpec:
    """Return the AttestationKindSpec for the given kind; raise KeyError if unknown."""
    try:
        return _ATTESTATION_KIND_REGISTRY_V0[kind]
    except KeyError:
        raise KeyError(
            f"attestation kind {kind!r} is not registered in ATTESTATION_KIND_REGISTRY_V0"
        )


def attestation_kind_registry_hash() -> str:
    """Return the canonical hash of the v0 attestation kind registry."""
    return ATTESTATION_KIND_REGISTRY_V0_HASH


# ---------------------------------------------------------------------------
# Content-hashing helpers
# ---------------------------------------------------------------------------


def _canonical_json(obj: Any) -> str:
    """Produce deterministic JSON for content-addressing.

    Mappings are sorted by key.  Tuples become arrays.  Dates/datetimes
    become ISO strings.  bytes become hex strings.
    """
    if isinstance(obj, bool):
        return json.dumps(obj)
    if isinstance(obj, (int, float, str, type(None))):
        return json.dumps(obj)
    if isinstance(obj, bytes):
        return json.dumps(obj.hex())
    if isinstance(obj, (date, datetime)):
        return json.dumps(obj.isoformat())
    if isinstance(obj, (list, tuple)):
        items = ", ".join(_canonical_json(v) for v in obj)
        return f"[{items}]"
    if isinstance(obj, dict):
        pairs = ", ".join(
            f"{json.dumps(k)}: {_canonical_json(v)}"
            for k, v in sorted(obj.items())
        )
        return "{" + pairs + "}"
    # dataclasses: serialize as dict of fields
    import dataclasses  # noqa: PLC0415
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        d = dataclasses.asdict(obj)  # type: ignore[call-overload]
        return _canonical_json(d)
    raise TypeError(f"_canonical_json: unsupported type {type(obj).__qualname__!r}")


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assertion_canonical_payload(a: ProvenanceAssertion) -> str:
    """Canonical JSON payload for hash computation.

    Covers all semantic fields; assertion_id itself is excluded (it IS the hash).
    """
    d: dict[str, Any] = {
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
    return _canonical_json(d)


def attestation_canonical_payload(a: ProvenanceAttestation) -> str:
    """Canonical JSON payload for hash computation."""

    def _ref_to_dict(r: ArtifactRef | SourceRef) -> dict[str, Any]:
        if isinstance(r, ArtifactRef):
            return {
                "_type": "ArtifactRef",
                "artifact_type": r.artifact_type,
                "artifact_id": r.artifact_id,
                "content_hash": r.content_hash,
            }
        return {
            "_type": "SourceRef",
            "artifact_digest": r.artifact_digest,
            "structural_locator": r.structural_locator,
            "bounded_quote_hash": r.bounded_quote_hash,
            "normalization_policy_id": r.normalization_policy_id,
            "byte_range": list(r.byte_range),
        }

    d: dict[str, Any] = {
        "attestation_kind": a.attestation_kind,
        "subject": {
            "artifact_type": a.subject.artifact_type,
            "artifact_id": a.subject.artifact_id,
            "content_hash": a.subject.content_hash,
        },
        "materials": [_ref_to_dict(m) for m in a.materials],
        "producer": {
            "producer_id": a.producer.producer_id,
            "producer_kind": a.producer.producer_kind,
            "public_key": a.producer.public_key,
            "metadata": dict(a.producer.metadata),
        },
        "produced_at": a.produced_at.isoformat(),
        "payload": dict(a.payload),
    }
    return _canonical_json(d)


def _graph_snapshot_canonical(
    nodes: tuple[GraphNode, ...], edges: tuple[GraphEdge, ...]
) -> str:
    """Canonical serialization of nodes+edges for snapshot_hash computation.

    Nodes sorted by node_id; edges sorted by edge_id.  Same content in any
    input order → same canonical string.
    """
    sorted_nodes = sorted(nodes, key=lambda n: n.node_id)
    sorted_edges = sorted(edges, key=lambda e: e.edge_id)

    node_dicts = [
        {
            "node_id": n.node_id,
            "node_type": n.node_type,
            "artifact_ref": {
                "artifact_type": n.artifact_ref.artifact_type,
                "artifact_id": n.artifact_ref.artifact_id,
                "content_hash": n.artifact_ref.content_hash,
            },
            "payload_hash": n.payload_hash,
        }
        for n in sorted_nodes
    ]
    edge_dicts = [
        {
            "edge_id": e.edge_id,
            "edge_type": e.edge_type,
            "src_node_id": e.src_node_id,
            "dst_node_id": e.dst_node_id,
            "payload": dict(e.payload),
        }
        for e in sorted_edges
    ]
    return _canonical_json({"nodes": node_dicts, "edges": edge_dicts})


# ---------------------------------------------------------------------------
# GraphBuilder — mutable accumulator
# ---------------------------------------------------------------------------


class GraphBuilder:
    """Mutable accumulator for provenance graph construction.

    Use this to assemble a ProvenanceGraph; finalize() emits an immutable
    ProvenanceGraph with computed snapshot_hash.  Constructors on GraphNode /
    GraphEdge / ProvenanceAssertion etc. do NOT mutate global state.
    """

    def __init__(self, attestation_kind_registry_hash_val: str) -> None:
        if not attestation_kind_registry_hash_val:
            raise ValueError(
                "GraphBuilder requires a non-empty attestation_kind_registry_hash"
            )
        self._attestation_kind_registry_hash = attestation_kind_registry_hash_val
        self._nodes: list[GraphNode] = []
        self._edges: list[GraphEdge] = []
        self._node_ids: set[str] = set()
        self._edge_ids: set[str] = set()

    def add_node(self, node: GraphNode) -> None:
        """Add a node.  Duplicate node_ids are silently deduplicated (idempotent)."""
        if node.node_id not in self._node_ids:
            self._nodes.append(node)
            self._node_ids.add(node.node_id)

    def add_edge(self, edge: GraphEdge) -> None:
        """Add an edge.  Duplicate edge_ids are silently deduplicated (idempotent)."""
        if edge.edge_id not in self._edge_ids:
            self._edges.append(edge)
            self._edge_ids.add(edge.edge_id)

    def add_assertion(self, assertion: ProvenanceAssertion) -> ArtifactRef:
        """Wrap a ProvenanceAssertion as a graph node and return its ArtifactRef."""
        payload_hash = assertion.assertion_id
        ref = ArtifactRef(
            artifact_type="assertion",
            artifact_id=assertion.assertion_id,
            content_hash=assertion.assertion_id,
        )
        node = GraphNode(
            node_id=assertion.assertion_id,
            node_type="assertion",
            artifact_ref=ref,
            payload_hash=payload_hash,
        )
        self.add_node(node)
        return ref

    def add_attestation(self, attestation: ProvenanceAttestation) -> ArtifactRef:
        """Wrap a ProvenanceAttestation as a graph node and return its ArtifactRef."""
        payload_hash = attestation.attestation_id
        ref = ArtifactRef(
            artifact_type="attestation",
            artifact_id=attestation.attestation_id,
            content_hash=attestation.attestation_id,
        )
        node = GraphNode(
            node_id=attestation.attestation_id,
            node_type="attestation",
            artifact_ref=ref,
            payload_hash=payload_hash,
        )
        self.add_node(node)
        return ref

    def finalize(self) -> ProvenanceGraph:
        """Produce an immutable ProvenanceGraph with computed snapshot_hash.

        Calling finalize() multiple times on the same accumulated state
        produces identical graphs (idempotent).
        """
        nodes = tuple(self._nodes)
        edges = tuple(self._edges)
        canonical = _graph_snapshot_canonical(nodes, edges)
        snapshot_hash = _sha256(canonical)
        return ProvenanceGraph(
            nodes=nodes,
            edges=edges,
            snapshot_hash=snapshot_hash,
            attestation_kind_registry_hash=self._attestation_kind_registry_hash,
        )
