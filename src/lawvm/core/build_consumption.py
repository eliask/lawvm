"""Build-consumption recorder — the retraction-taint chokepoint.

Governing rule: a persisted, content-addressed artifact that relies on a
ProvenanceAssertion MUST write a ``consumed_by_build`` edge, or it is not a
taint-checkable build.

This module is the ONLY minter of build identities (:meth:`BuildRef.mint`)
and the single chokepoint through which persisted artifacts register their
assertion consumption (:func:`persist_taintable_build_artifact`).  It is
deliberately OUTSIDE ``EvidenceKernel.authorize()`` — the kernel stays pure
(same graph + profile + policy → same result, no mutation).  Artifact
writers (certificate bundles, exports, …) are callers, never bespoke
recorders.

The build node (a :class:`BuildRecord`) is required even with zero
consumption, so "no edges" can distinguish:

* (A) known build, ``consumed_subject_count == 0`` → genuinely consumed
  nothing → clean;
* (B) unknown build → NOT clean (``BUILD_UNKNOWN``);
* (C) known build, ``consumption_instrumented == False`` → NOT clean
  (``BUILD_CONSUMPTION_UNINSTRUMENTED``);
* (D) known build, ``consumed_subject_count > 0`` but edges missing →
  INVALID consumption graph, never silently clean.

Slice limitation: consumed subjects are the DIRECT admitted
ProvenanceAssertion ids plus dependency_refs that are ProvenanceAssertion
ids (``consumption_role`` = ``direct_assertion`` / ``dependency_assertion``).
The full transitive ``depends_on`` closure is a later slice; until then a
retraction of a transitively-depended-on assertion (depth >= 2) does not
taint the build through this recorder.

Cert-root cycle guard: consumed_by_build edges live in the global persistent
provenance graph and are written AFTER artifact emission — never inside the
artifact's own content root (else certificate_root <-> graph cycle).  The
artifact may list admitted assertions internally; the taint edge is an
emission sidecar.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Literal, Mapping, Sequence

from lawvm.core.evidence_kernel import BuildTaintFinding, query_retraction_taint
from lawvm.core.provenance_graph import (
    ArtifactRef,
    GraphBuilder,
    GraphEdge,
    GraphNode,
    ProvenanceAttestation,
    ProvenanceGraph,
    _canonical_json,
    _sha256,
    attestation_kind_registry_hash,
)

if TYPE_CHECKING:
    from lawvm.core.provenance_graph_storage import GraphStore


BUILD_CONSUMPTION_EDGE_SCHEMA = "lawvm.consumed_by_build.v0"

BuildKind = Literal["cert", "bench", "export", "response"]
_BUILD_KINDS: frozenset[str] = frozenset({"cert", "bench", "export", "response"})

ConsumptionRole = Literal[
    "direct_assertion", "dependency_assertion", "authorization_evidence"
]


class BuildConsumptionError(RuntimeError):
    """Invalid build-consumption graph state.  Raised, never a silent miss."""


# ---------------------------------------------------------------------------
# BuildRef — typed build identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuildRef:
    """Typed identity of a taint-checkable build.

    ``build_id`` is canonically derived from content only:
    ``{kind}:{schema}:{sha256:...}``.  Git commits, bench labels, CLI args
    and timestamps are NEVER identity (display metadata only).  Identical
    content root → identical build_id.  Minted ONLY by :meth:`mint`.
    """

    build_kind: Literal["cert", "bench", "export", "response"]
    schema: str
    content_hash: str  # sha256:...
    build_id: str  # canonical derived string
    artifact_ref: ArtifactRef

    @classmethod
    def mint(
        cls,
        *,
        build_kind: BuildKind,
        schema: str,
        content_hash: str,
        artifact_ref: ArtifactRef,
    ) -> "BuildRef":
        if build_kind not in _BUILD_KINDS:
            raise BuildConsumptionError(
                f"BuildRef.mint: build_kind {build_kind!r} not in {sorted(_BUILD_KINDS)!r}"
            )
        if not schema:
            raise BuildConsumptionError("BuildRef.mint: schema must be non-empty")
        if not content_hash.startswith("sha256:"):
            raise BuildConsumptionError(
                f"BuildRef.mint: content_hash must be 'sha256:...'-prefixed, "
                f"got {content_hash!r}"
            )
        build_id = f"{build_kind}:{schema}:{content_hash}"
        return cls(
            build_kind=build_kind,
            schema=schema,
            content_hash=content_hash,
            build_id=build_id,
            artifact_ref=artifact_ref,
        )


# ---------------------------------------------------------------------------
# BuildRecord — the build node content (required even with zero consumption)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuildRecord:
    """Content of a build node in the provenance graph."""

    build_id: str
    build_kind: str
    schema: str
    artifact_ref: ArtifactRef
    consumption_instrumented: bool
    consumed_subject_count: int
    consumed_subject_root: str


def consumed_subject_root(subject_ids: Sequence[str]) -> str:
    """Deterministic root over the consumed-subject id set (sorted, deduped)."""
    canonical = _canonical_json(sorted(set(subject_ids)))
    return f"sha256:{_sha256(canonical)}"


def build_record_to_dict(record: BuildRecord) -> dict[str, Any]:
    return {
        "build_id": record.build_id,
        "build_kind": record.build_kind,
        "schema": record.schema,
        "artifact_ref": {
            "artifact_type": record.artifact_ref.artifact_type,
            "artifact_id": record.artifact_ref.artifact_id,
            "content_hash": record.artifact_ref.content_hash,
        },
        "consumption_instrumented": record.consumption_instrumented,
        "consumed_subject_count": record.consumed_subject_count,
        "consumed_subject_root": record.consumed_subject_root,
    }


def build_record_from_dict(d: Mapping[str, Any]) -> BuildRecord:
    ref_d = d["artifact_ref"]
    return BuildRecord(
        build_id=d["build_id"],
        build_kind=d["build_kind"],
        schema=d["schema"],
        artifact_ref=ArtifactRef(
            artifact_type=ref_d["artifact_type"],
            artifact_id=ref_d["artifact_id"],
            content_hash=ref_d["content_hash"],
        ),
        consumption_instrumented=bool(d["consumption_instrumented"]),
        consumed_subject_count=int(d["consumed_subject_count"]),
        consumed_subject_root=d["consumed_subject_root"],
    )


def build_record_content_hash(record: BuildRecord) -> str:
    """Content hash of the BuildRecord (used as the build node payload_hash)."""
    return _sha256(_canonical_json(build_record_to_dict(record)))


def build_node_for_record(record: BuildRecord) -> GraphNode:
    """The graph node wrapping a BuildRecord.  node_id IS the build_id."""
    return GraphNode(
        node_id=record.build_id,
        node_type="build",
        artifact_ref=record.artifact_ref,
        payload_hash=build_record_content_hash(record),
    )


# ---------------------------------------------------------------------------
# Edge payload (typed; serialized into GraphEdge.payload)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuildConsumptionEdgePayload:
    schema: Literal["lawvm.consumed_by_build.v0"]
    build_id: str
    build_kind: str
    build_schema: str
    build_artifact_id: str
    build_content_hash: str
    consumption_role: Literal[
        "direct_assertion", "dependency_assertion", "authorization_evidence"
    ]
    composition_decision_ids: tuple[str, ...] = ()
    authorization_result_ids: tuple[str, ...] = ()
    policy_ids: tuple[str, ...] = ()
    profile_fingerprint: str = ""
    source_bundle_hash: str = ""
    scope: Mapping[str, object] = field(default_factory=dict)
    time_scope: Mapping[str, object] = field(default_factory=dict)


def edge_payload_to_mapping(payload: BuildConsumptionEdgePayload) -> dict[str, Any]:
    return {
        "schema": payload.schema,
        "build_id": payload.build_id,
        "build_kind": payload.build_kind,
        "build_schema": payload.build_schema,
        "build_artifact_id": payload.build_artifact_id,
        "build_content_hash": payload.build_content_hash,
        "consumption_role": payload.consumption_role,
        "composition_decision_ids": list(payload.composition_decision_ids),
        "authorization_result_ids": list(payload.authorization_result_ids),
        "policy_ids": list(payload.policy_ids),
        "profile_fingerprint": payload.profile_fingerprint,
        "source_bundle_hash": payload.source_bundle_hash,
        "scope": dict(payload.scope),
        "time_scope": dict(payload.time_scope),
    }


def consumption_edge(
    *,
    consumed_subject_id: str,
    build_node_id: str,
    payload: BuildConsumptionEdgePayload,
) -> GraphEdge:
    """Build a consumed_by_build edge with a deterministic content-derived id.

    Validation invariant: ``payload.build_id`` MUST equal the destination
    build node id — mismatch is an invalid graph, refused at construction.
    """
    if payload.build_id != build_node_id:
        raise BuildConsumptionError(
            f"consumption_edge: payload.build_id {payload.build_id!r} != "
            f"dst build node id {build_node_id!r} (invalid graph, refused)"
        )
    payload_mapping = edge_payload_to_mapping(payload)
    edge_id = _sha256(
        _canonical_json(
            {
                "edge_type": "consumed_by_build",
                "src_node_id": consumed_subject_id,
                "dst_node_id": build_node_id,
                "payload": payload_mapping,
            }
        )
    )
    return GraphEdge(
        edge_id=edge_id,
        edge_type="consumed_by_build",
        src_node_id=consumed_subject_id,
        dst_node_id=build_node_id,
        payload=payload_mapping,
    )


# ---------------------------------------------------------------------------
# Chokepoint
# ---------------------------------------------------------------------------


def persist_taintable_build_artifact(
    *,
    graph_builder: GraphBuilder,
    artifact_ref: ArtifactRef,
    build_kind: BuildKind,
    build_schema: str,
    consumed_assertion_ids: Sequence[str],
    dependency_assertion_ids: Sequence[str] = (),
    composition_decision_ids: Sequence[str] = (),
    authorization_result_ids: Sequence[str] = (),
    policy_ids: Sequence[str] = (),
    profile_fingerprint: str = "",
    source_bundle_hash: str = "",
    scope: Mapping[str, object] | None = None,
    time_scope: Mapping[str, object] | None = None,
    record_sink: Callable[[BuildRecord], None] | None = None,
) -> BuildRef:
    """Register a persisted artifact as a taint-checkable build.

    Writes the build node and one consumed_by_build edge per
    (consumed subject, build) pair into ``graph_builder``.  Call AFTER the
    artifact root is computed and the artifact is persisted (emission-time
    sidecar, not composition-time); if this recorder fails, the caller MUST
    treat the artifact as not published.

    ``consumed_assertion_ids`` are the directly admitted ProvenanceAssertion
    ids (role ``direct_assertion``); ``dependency_assertion_ids`` are the
    dependency_refs of those assertions that are themselves assertion ids
    (role ``dependency_assertion``).  A subject appearing in both gets one
    edge with the direct role.

    ``record_sink``, when provided, receives the minted :class:`BuildRecord`
    so the caller can persist its content (graph nodes carry only the record
    hash); a sink failure propagates and fails the emission.
    """
    build_ref = BuildRef.mint(
        build_kind=build_kind,
        schema=build_schema,
        content_hash=artifact_ref.content_hash,
        artifact_ref=artifact_ref,
    )

    roles: dict[str, ConsumptionRole] = {}
    for subject_id in dependency_assertion_ids:
        roles[subject_id] = "dependency_assertion"
    for subject_id in consumed_assertion_ids:
        roles[subject_id] = "direct_assertion"  # direct wins over dependency

    record = BuildRecord(
        build_id=build_ref.build_id,
        build_kind=build_ref.build_kind,
        schema=build_ref.schema,
        artifact_ref=build_ref.artifact_ref,
        consumption_instrumented=True,
        consumed_subject_count=len(roles),
        consumed_subject_root=consumed_subject_root(tuple(roles)),
    )
    graph_builder.add_node(build_node_for_record(record))

    for subject_id in sorted(roles):
        payload = BuildConsumptionEdgePayload(
            schema="lawvm.consumed_by_build.v0",
            build_id=build_ref.build_id,
            build_kind=build_ref.build_kind,
            build_schema=build_ref.schema,
            build_artifact_id=build_ref.artifact_ref.artifact_id,
            build_content_hash=build_ref.content_hash,
            consumption_role=roles[subject_id],
            composition_decision_ids=tuple(composition_decision_ids),
            authorization_result_ids=tuple(authorization_result_ids),
            policy_ids=tuple(policy_ids),
            profile_fingerprint=profile_fingerprint,
            source_bundle_hash=source_bundle_hash,
            scope=dict(scope or {}),
            time_scope=dict(time_scope or {}),
        )
        graph_builder.add_edge(
            consumption_edge(
                consumed_subject_id=subject_id,
                build_node_id=build_ref.build_id,
                payload=payload,
            )
        )

    if record_sink is not None:
        record_sink(record)
    return build_ref


def record_build_in_store(
    store: "GraphStore",
    *,
    artifact_ref: ArtifactRef,
    build_kind: BuildKind,
    build_schema: str,
    consumed_assertion_ids: Sequence[str] = (),
    dependency_assertion_ids: Sequence[str] = (),
    composition_decision_ids: Sequence[str] = (),
    authorization_result_ids: Sequence[str] = (),
    policy_ids: Sequence[str] = (),
    profile_fingerprint: str = "",
    source_bundle_hash: str = "",
    scope: Mapping[str, object] | None = None,
    time_scope: Mapping[str, object] | None = None,
) -> BuildRef:
    """Persist a build node + consumption edges into a GraphStore.

    Storage adapter over :func:`persist_taintable_build_artifact` for callers
    that persist to the durable provenance graph store.  Any error propagates;
    per the governing rule the caller must then treat the artifact emission
    as failed (not published).
    """
    captured: list[BuildRecord] = []
    builder = GraphBuilder(
        attestation_kind_registry_hash_val=attestation_kind_registry_hash()
    )
    build_ref = persist_taintable_build_artifact(
        graph_builder=builder,
        artifact_ref=artifact_ref,
        build_kind=build_kind,
        build_schema=build_schema,
        consumed_assertion_ids=consumed_assertion_ids,
        dependency_assertion_ids=dependency_assertion_ids,
        composition_decision_ids=composition_decision_ids,
        authorization_result_ids=authorization_result_ids,
        policy_ids=policy_ids,
        profile_fingerprint=profile_fingerprint,
        source_bundle_hash=source_bundle_hash,
        scope=scope,
        time_scope=time_scope,
        record_sink=captured.append,
    )
    (record,) = captured
    store.write_build_record(record)
    graph = builder.finalize()
    store.write_graph(graph)
    return build_ref


# ---------------------------------------------------------------------------
# Validation + four-state taint query
# ---------------------------------------------------------------------------


class BuildConsumptionStatus(str, Enum):
    """Four-state (+ invalid) taint-checkability status of one build."""

    BUILD_UNKNOWN = "build_unknown"
    BUILD_CONSUMPTION_UNINSTRUMENTED = "build_consumption_uninstrumented"
    CLEAN = "clean"
    TAINTED = "tainted"
    INVALID_CONSUMPTION = "invalid_consumption"


@dataclass(frozen=True, slots=True)
class BuildTaintStatusFinding:
    """Per-build status from the four-state taint query."""

    build_id: str
    taint_status: BuildConsumptionStatus
    findings: tuple[BuildTaintFinding, ...]
    detail: str = ""


def _consumption_edges_by_build(graph: ProvenanceGraph) -> dict[str, list[GraphEdge]]:
    """Index consumed_by_build edges by destination, with structural checks.

    Pre-query validation: every consumed_by_build edge must carry
    ``payload["build_id"]`` equal to its dst node id.  Mismatch is an
    invalid graph and raises — never a silent miss.
    """
    by_build: dict[str, list[GraphEdge]] = {}
    for edge in graph.edges:
        if edge.edge_type != "consumed_by_build":
            continue
        payload_build_id = str(edge.payload.get("build_id", ""))
        if payload_build_id != edge.dst_node_id:
            raise BuildConsumptionError(
                f"consumed_by_build edge {edge.edge_id} has payload.build_id="
                f"{payload_build_id!r} but dst_node_id={edge.dst_node_id!r}; "
                "invalid consumption graph (refusing to query)"
            )
        by_build.setdefault(edge.dst_node_id, []).append(edge)
    return by_build


def _build_nodes(graph: ProvenanceGraph) -> dict[str, GraphNode]:
    return {n.node_id: n for n in graph.nodes if n.node_type == "build"}


def build_consumption_status(
    graph: ProvenanceGraph,
    build_id: str,
    attestation_index: Mapping[str, ProvenanceAttestation],
    build_record_index: Mapping[str, BuildRecord],
    *,
    _edges_by_build: Mapping[str, list[GraphEdge]] | None = None,
) -> BuildTaintStatusFinding:
    """Four-state taint status for one build id (see module docstring)."""
    edges_by_build = (
        _edges_by_build
        if _edges_by_build is not None
        else _consumption_edges_by_build(graph)
    )
    nodes = _build_nodes(graph)
    if build_id not in nodes:
        return BuildTaintStatusFinding(
            build_id=build_id,
            taint_status=BuildConsumptionStatus.BUILD_UNKNOWN,
            findings=(),
            detail="no build node in graph; not taint-checkable (not clean)",
        )
    record = build_record_index.get(build_id)
    if record is None:
        return BuildTaintStatusFinding(
            build_id=build_id,
            taint_status=BuildConsumptionStatus.BUILD_CONSUMPTION_UNINSTRUMENTED,
            findings=(),
            detail="build node present but no BuildRecord content available (not clean)",
        )
    node = nodes[build_id]
    if node.payload_hash != build_record_content_hash(record):
        raise BuildConsumptionError(
            f"build node {build_id!r} payload_hash does not match the stored "
            "BuildRecord content hash; invalid consumption graph"
        )
    if not record.consumption_instrumented:
        return BuildTaintStatusFinding(
            build_id=build_id,
            taint_status=BuildConsumptionStatus.BUILD_CONSUMPTION_UNINSTRUMENTED,
            findings=(),
            detail="emitter did not instrument consumption (not clean)",
        )
    edge_count = len(edges_by_build.get(build_id, []))
    if edge_count != record.consumed_subject_count:
        return BuildTaintStatusFinding(
            build_id=build_id,
            taint_status=BuildConsumptionStatus.INVALID_CONSUMPTION,
            findings=(),
            detail=(
                f"BuildRecord declares consumed_subject_count="
                f"{record.consumed_subject_count} but graph carries "
                f"{edge_count} consumed_by_build edge(s); invalid (never clean)"
            ),
        )
    if record.consumed_subject_count == 0:
        return BuildTaintStatusFinding(
            build_id=build_id,
            taint_status=BuildConsumptionStatus.CLEAN,
            findings=(),
            detail="instrumented build consumed zero assertions",
        )
    findings = query_retraction_taint(graph, (build_id,), attestation_index)
    if findings:
        return BuildTaintStatusFinding(
            build_id=build_id,
            taint_status=BuildConsumptionStatus.TAINTED,
            findings=findings,
        )
    return BuildTaintStatusFinding(
        build_id=build_id,
        taint_status=BuildConsumptionStatus.CLEAN,
        findings=(),
        detail="no consumed assertion is retracted",
    )


def query_retraction_taint_for_build_refs(
    graph: ProvenanceGraph,
    build_refs: Sequence[BuildRef],
    attestation_index: Mapping[str, ProvenanceAttestation],
    build_record_index: Mapping[str, BuildRecord],
) -> tuple[BuildTaintStatusFinding, ...]:
    """Four-state taint query over typed BuildRefs.

    Wrapper around the compatibility core
    :func:`lawvm.core.evidence_kernel.query_retraction_taint`; adds the
    build-node state machine and the structural pre-query validation.
    """
    edges_by_build = _consumption_edges_by_build(graph)
    return tuple(
        build_consumption_status(
            graph,
            ref.build_id,
            attestation_index,
            build_record_index,
            _edges_by_build=edges_by_build,
        )
        for ref in build_refs
    )


def validate_build_consumption(
    graph: ProvenanceGraph,
    build_ref: BuildRef,
    artifact_manifest: Mapping[str, Any],
    *,
    build_record: BuildRecord,
) -> None:
    """Checker invariant for one build's consumption graph.  Raises on INVALID.

    ``artifact_manifest`` must carry ``consumed_subject_ids`` (the subject ids
    the artifact declares it relied on) and may set
    ``allows_dependency_expansion`` to permit extra ``dependency_assertion``
    edges beyond the declared set (dependency-closure expansion).

    The BuildRecord content is passed explicitly because graph nodes commit
    to it only by hash (same pattern as the kernel's ``attestation_index``).
    """
    manifest_ids = tuple(artifact_manifest["consumed_subject_ids"])
    allows_expansion = bool(artifact_manifest.get("allows_dependency_expansion", False))

    if build_record.build_id != build_ref.build_id:
        raise BuildConsumptionError(
            f"BuildRecord.build_id {build_record.build_id!r} != "
            f"BuildRef.build_id {build_ref.build_id!r}"
        )
    nodes = _build_nodes(graph)
    if build_ref.build_id not in nodes:
        raise BuildConsumptionError(
            f"build {build_ref.build_id!r} has no build node in the graph"
        )
    if nodes[build_ref.build_id].payload_hash != build_record_content_hash(build_record):
        raise BuildConsumptionError(
            f"build node {build_ref.build_id!r} payload_hash does not commit "
            "to the supplied BuildRecord"
        )

    manifest_root = consumed_subject_root(manifest_ids)
    if manifest_root != build_record.consumed_subject_root:
        raise BuildConsumptionError(
            f"manifest consumed-subject root {manifest_root} != "
            f"BuildRecord.consumed_subject_root {build_record.consumed_subject_root}"
        )

    edges = _consumption_edges_by_build(graph).get(build_ref.build_id, [])
    if len(edges) != build_record.consumed_subject_count:
        raise BuildConsumptionError(
            f"BuildRecord.consumed_subject_count={build_record.consumed_subject_count} "
            f"but graph carries {len(edges)} consumed_by_build edge(s)"
        )

    edge_subjects = {e.src_node_id: e for e in edges}
    declared = set(manifest_ids)
    for subject_id in declared:
        if subject_id not in edge_subjects:
            raise BuildConsumptionError(
                f"declared consumed subject {subject_id!r} has no "
                "consumed_by_build edge (INVALID)"
            )
    for subject_id, edge in edge_subjects.items():
        if subject_id in declared:
            continue
        role = str(edge.payload.get("consumption_role", ""))
        if allows_expansion and role == "dependency_assertion":
            continue
        raise BuildConsumptionError(
            f"consumed_by_build edge from undeclared subject {subject_id!r} "
            f"(role={role!r}) and manifest does not permit expansion (INVALID)"
        )
