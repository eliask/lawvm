"""Pure conversion facades: PhaseResult ↔ ProvenanceGraph.

graph_from_phase_result  — convert a PhaseResult's finding ledger into a
                           ProvenanceGraph; findings become facade-projected
                           ProvenanceAssertions.  Also returns an in-memory
                           assertion index for lossless in-memory round-trips.

findings_from_graph      — project facade-namespaced assertion nodes back to
                           tuple[Finding, ...].  Requires either a GraphStore
                           or an in-memory assertion_index kwarg.

Round-trip property (in-memory):
    graph, idx = graph_from_phase_result(pr, ...)
    recovered = findings_from_graph(graph, build_id=..., assertion_index=idx)
    # recovered is set-equivalent to pr.findings()

NO existing call sites are changed.  This module is additive only.

Anti-pattern §12.1 (v3 spec): no hidden global mutations.
Anti-pattern §12.5 (v3 spec): no competing sources of truth.

API tier
--------
Step 1 conversion boundary (notes_internal/UNIFIED_PROVENANCE_GRAPH_DESIGN_v3.md §13).
"""
from __future__ import annotations

from datetime import date
from typing import Any

from lawvm.core.phase_result import Finding, PhaseResult
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


# ---------------------------------------------------------------------------
# Facade namespace constants
# ---------------------------------------------------------------------------

_FACADE_SCHEMA_VERSION = "lawvm.facade.v0"
_FACADE_JURISDICTION = "lawvm"

_LAYER_OBSERVATION = "facade_observation"
_LAYER_OBLIGATION = "facade_obligation"
_LAYER_VIOLATION = "facade_violation"

_KIND_OBSERVATION = "lawvm.facade.observation.v0"
_KIND_OBLIGATION = "lawvm.facade.obligation.v0"
_KIND_VIOLATION = "lawvm.facade.violation.v0"

_ROLE_TO_LAYER: dict[str, str] = {
    "observation": _LAYER_OBSERVATION,
    "obligation": _LAYER_OBLIGATION,
    "violation": _LAYER_VIOLATION,
}

_ROLE_TO_KIND: dict[str, str] = {
    "observation": _KIND_OBSERVATION,
    "obligation": _KIND_OBLIGATION,
    "violation": _KIND_VIOLATION,
}

_FACADE_KINDS: frozenset[str] = frozenset({
    _KIND_OBSERVATION,
    _KIND_OBLIGATION,
    _KIND_VIOLATION,
})

_OPEN_INTERVAL = Interval(start=date(2000, 1, 1), end=None)


# ---------------------------------------------------------------------------
# graph_from_phase_result
# ---------------------------------------------------------------------------


def graph_from_phase_result(
    pr: "PhaseResult[Any]",
    *,
    phase_ref: ArtifactRef,
    source_bundle_hash: str,
) -> "tuple[ProvenanceGraph, dict[str, ProvenanceAssertion]]":
    """Convert a PhaseResult's finding ledger to a ProvenanceGraph.

    Returns (graph, assertion_index) where assertion_index maps each
    assertion_id → ProvenanceAssertion.  Pass assertion_index to
    findings_from_graph() for lossless in-memory round-trip.

    Each Finding becomes a facade-projected ProvenanceAssertion whose
    ``kind`` carries 'lawvm.facade.observation.v0' / '.obligation.v0' /
    '.violation.v0' as a namespace marker.  Original Finding data is
    preserved in assertion.value for lossless reconstruction.

    Does NOT mutate any global state.  Returns new objects only.
    """
    builder = GraphBuilder(ATTESTATION_KIND_REGISTRY_V0_HASH)
    assertion_index: dict[str, ProvenanceAssertion] = {}

    for finding in pr.findings():
        assertion = _finding_to_assertion(finding)
        assertion_index[assertion.assertion_id] = assertion
        builder.add_assertion(assertion)

        # Edge: this assertion derives_projection from phase_ref
        edge_id = _sha256(
            f"derives_projection:{assertion.assertion_id}:{phase_ref.artifact_id}"
        )
        edge = GraphEdge(
            edge_id=edge_id,
            edge_type="derives_projection",
            src_node_id=assertion.assertion_id,
            dst_node_id=phase_ref.artifact_id,
            payload={
                "phase_ref_artifact_id": phase_ref.artifact_id,
                "source_bundle_hash": source_bundle_hash,
            },
        )
        builder.add_edge(edge)

    graph = builder.finalize()
    return graph, assertion_index


# ---------------------------------------------------------------------------
# findings_from_graph
# ---------------------------------------------------------------------------


def findings_from_graph(
    graph: ProvenanceGraph,
    *,
    build_id: str,
    assertion_index: "dict[str, ProvenanceAssertion] | None" = None,
) -> "tuple[Finding, ...]":
    """Project facade-namespaced assertion nodes back to Finding rows.

    assertion_index: mapping from assertion_id → ProvenanceAssertion, as
    returned by graph_from_phase_result().  Required for in-memory round-trips.
    When None, returns an empty tuple (no store access in Step 1).

    build_id: identifier for the build requesting this projection (metadata
    only at Step 1; used by future EvidenceKernel steps).

    Round-trip property: if assertion_index is the one returned by
    graph_from_phase_result(pr, ...), then the returned Finding tuple is
    set-equivalent to pr.findings().
    """
    if assertion_index is None:
        return ()

    findings: list[Finding] = []
    for node in graph.nodes:
        if node.node_type != "assertion":
            continue
        assertion = assertion_index.get(node.node_id)
        if assertion is None:
            continue
        if assertion.kind not in _FACADE_KINDS:
            continue
        finding = _assertion_to_finding(assertion)
        if finding is not None:
            findings.append(finding)

    return tuple(findings)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _finding_to_assertion(finding: Finding) -> ProvenanceAssertion:
    """Convert one Finding to a content-addressed ProvenanceAssertion."""
    layer = _ROLE_TO_LAYER.get(finding.role, _LAYER_OBSERVATION)
    kind = _ROLE_TO_KIND.get(finding.role, _KIND_OBSERVATION)

    scope: dict[str, Any] = {"stage": finding.stage}
    target: dict[str, Any] = {"kind": finding.kind}
    value: dict[str, Any] = {
        "original_kind": finding.kind,
        "original_role": finding.role,
        "original_stage": finding.stage,
        "original_detail": dict(finding.detail),
        "original_blocking": finding.blocking,
        "original_source_statute": finding.source_statute,
    }

    # Compute assertion_id from canonical payload (excluding id itself)
    provisional = ProvenanceAssertion(
        assertion_id="__placeholder__",
        schema_version=_FACADE_SCHEMA_VERSION,
        jurisdiction=_FACADE_JURISDICTION,
        kind=kind,
        layer=layer,
        scope=scope,
        target=target,
        value=value,
        source_refs=(),
        dependency_refs=(),
        valid_at=_OPEN_INTERVAL,
        supersedes=(),
        disputes=(),
        rationale="",
    )
    assertion_id = _sha256(assertion_canonical_payload(provisional))

    return ProvenanceAssertion(
        assertion_id=assertion_id,
        schema_version=_FACADE_SCHEMA_VERSION,
        jurisdiction=_FACADE_JURISDICTION,
        kind=kind,
        layer=layer,
        scope=scope,
        target=target,
        value=value,
        source_refs=(),
        dependency_refs=(),
        valid_at=_OPEN_INTERVAL,
        supersedes=(),
        disputes=(),
        rationale="",
    )


def _assertion_to_finding(assertion: ProvenanceAssertion) -> "Finding | None":
    """Reconstruct a Finding from the value mapping of a facade assertion."""
    value = assertion.value
    if not isinstance(value, dict):
        return None

    original_kind = value.get("original_kind")
    original_role = value.get("original_role")
    original_stage = value.get("original_stage")
    original_detail = value.get("original_detail", {})
    original_blocking = value.get("original_blocking", False)
    original_source_statute = value.get("original_source_statute", "")

    if not original_kind or not original_role or not original_stage:
        return None

    return Finding(
        kind=str(original_kind),
        role=str(original_role),  # type: ignore[arg-type]  # ty:ignore[invalid-argument-type]
        stage=str(original_stage),
        detail=original_detail if isinstance(original_detail, dict) else {},
        blocking=bool(original_blocking),
        source_statute=str(original_source_statute),
    )
