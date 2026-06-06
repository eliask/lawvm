"""Projection facade: ProofSurface -> ProvenanceGraph.

This module makes proof-surface rows graph-visible without changing their
authority.  Rows become facade observation assertions.  They do not become
source-span verified claims, execution authorizations, or replay operations.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Mapping

from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.proof_surfaces import (
    ProofSurface,
    ProofSurfaceRow,
    proof_surface_from_evidence_report,
)
from lawvm.core.provenance_graph import (
    ATTESTATION_KIND_REGISTRY_V0_HASH,
    ArtifactRef,
    GraphBuilder,
    GraphEdge,
    Interval,
    ProvenanceAssertion,
    ProvenanceGraph,
    _sha256,
    assertion_canonical_payload,
)

_PROOF_SURFACE_GRAPH_SCHEMA = "lawvm.proof_surface_graph.v0"
_PROOF_SURFACE_ROW_KIND = "lawvm.proof_surface.row.v0"
_PROOF_SURFACE_ROW_LAYER = "facade_observation"
_OPEN_INTERVAL = Interval(start=date(2000, 1, 1), end=None)

_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "proof_surface_row_as_replay_authorization",
    "proof_surface_row_as_canonical_operation",
    "proof_surface_row_as_source_span_verification",
    "graph_projection_as_execution_authority",
)


def graph_from_proof_surface(
    surface: ProofSurface | EvidenceSurfaceReport | Mapping[str, Any],
    *,
    surface_ref: ArtifactRef | None = None,
    source_bundle_hash: str = "",
) -> tuple[ProvenanceGraph, dict[str, ProvenanceAssertion]]:
    """Project a proof/evidence surface into graph observation assertions.

    Returns ``(graph, assertion_index)``.  The index is required for callers
    that need lossless in-memory access to assertion payloads; the graph only
    stores content-addressed nodes and edges.
    """
    proof_surface = _coerce_proof_surface(surface)
    ref = surface_ref or _default_surface_ref(proof_surface)
    bundle_hash = source_bundle_hash or proof_surface.source_bundle_hash

    builder = GraphBuilder(ATTESTATION_KIND_REGISTRY_V0_HASH)
    assertion_index: dict[str, ProvenanceAssertion] = {}

    for row in proof_surface.rows:
        assertion = _row_to_assertion(
            proof_surface,
            row,
            surface_ref=ref,
            source_bundle_hash=bundle_hash,
        )
        assertion_index[assertion.assertion_id] = assertion
        builder.add_assertion(assertion)
        builder.add_edge(_projection_edge(assertion, ref, source_bundle_hash=bundle_hash))

    return builder.finalize(), assertion_index


def _coerce_proof_surface(
    surface: ProofSurface | EvidenceSurfaceReport | Mapping[str, Any],
) -> ProofSurface:
    if isinstance(surface, ProofSurface):
        return surface
    return proof_surface_from_evidence_report(surface)


def _default_surface_ref(surface: ProofSurface) -> ArtifactRef:
    content_hash = _sha256(
        json.dumps(surface.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    )
    return ArtifactRef(
        artifact_type="proof_surface",
        artifact_id=surface.surface_id,
        content_hash=content_hash,
    )


def _row_to_assertion(
    surface: ProofSurface,
    row: ProofSurfaceRow,
    *,
    surface_ref: ArtifactRef,
    source_bundle_hash: str,
) -> ProvenanceAssertion:
    value: dict[str, Any] = {
        "row_id": row.row_id,
        "subject_id": row.subject_id,
        "row_kind": row.row_kind,
        "status": row.status,
        "source_refs": list(row.source_refs),
        "witness_refs": list(row.witness_refs),
        "assertion_refs": list(row.assertion_refs),
        "proof_refs": list(row.proof_refs),
        "authorization_ref": row.authorization_ref,
        "residual_refs": list(row.residual_refs),
        "frontier_ref": row.frontier_ref,
        "detail": dict(row.detail),
        "read_model_only": True,
        "replay_authorized": False,
        "forbidden_shortcuts": list(_FORBIDDEN_SHORTCUTS),
    }
    scope: dict[str, Any] = {
        "surface_id": surface.surface_id,
        "surface_kind": surface.surface_kind,
        "source_bundle_hash": source_bundle_hash,
        "profile_id": surface.profile_id,
        "graph_snapshot_hash": surface.graph_snapshot_hash,
    }
    target: dict[str, Any] = {
        "row_id": row.row_id,
        "subject_id": row.subject_id,
        "row_kind": row.row_kind,
    }
    provisional = ProvenanceAssertion(
        assertion_id="__placeholder__",
        schema_version=_PROOF_SURFACE_GRAPH_SCHEMA,
        jurisdiction=surface.jurisdiction,
        kind=_PROOF_SURFACE_ROW_KIND,
        layer=_PROOF_SURFACE_ROW_LAYER,
        scope=scope,
        target=target,
        value=value,
        source_refs=(),
        dependency_refs=(surface_ref,),
        valid_at=_OPEN_INTERVAL,
        supersedes=(),
        disputes=(),
        rationale="Proof-surface row projected as a read-model observation only.",
    )
    assertion_id = _sha256(assertion_canonical_payload(provisional))
    return ProvenanceAssertion(
        assertion_id=assertion_id,
        schema_version=provisional.schema_version,
        jurisdiction=provisional.jurisdiction,
        kind=provisional.kind,
        layer=provisional.layer,
        scope=provisional.scope,
        target=provisional.target,
        value=provisional.value,
        source_refs=(),
        dependency_refs=(surface_ref,),
        valid_at=provisional.valid_at,
        supersedes=(),
        disputes=(),
        rationale=provisional.rationale,
    )


def _projection_edge(
    assertion: ProvenanceAssertion,
    surface_ref: ArtifactRef,
    *,
    source_bundle_hash: str,
) -> GraphEdge:
    edge_id = _sha256(
        f"derives_projection:{assertion.assertion_id}:{surface_ref.artifact_id}"
    )
    return GraphEdge(
        edge_id=edge_id,
        edge_type="derives_projection",
        src_node_id=assertion.assertion_id,
        dst_node_id=surface_ref.artifact_id,
        payload={
            "surface_ref_artifact_id": surface_ref.artifact_id,
            "surface_ref_artifact_type": surface_ref.artifact_type,
            "source_bundle_hash": source_bundle_hash,
            "projection_claim": "read_model_only_not_replay_authority",
        },
    )
