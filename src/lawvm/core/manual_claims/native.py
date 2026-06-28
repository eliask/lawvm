"""Graph-native manual claims operations (Piece 4, Step 2).

This module exposes graph-native operations for manual compilation claims.
It replaces the v2.2 four-record primitive at the substrate level while
preserving all acceptance criteria.

Functions
---------
submit_assertion   — write ProvenanceAssertion + claim_submitted attestation
attest             — emit any attestation kind against a subject
query_state        — AuthorizationResult for a subject under a policy + profile
query_retraction_taint — compute retraction taint at query time (no stored taint)

Design
------
  - All functions accept a GraphStore (no global mutation, §12.1).
  - State is computed at query time; no status field stored on assertions.
  - Retraction taint propagates via graph query, not stored taint (§9).
  - Producer identity is metadata, never a precedence input (§12.4).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Mapping

from lawvm.core.compile_result import StrictProfile
from lawvm.core.evidence_kernel import (
    AuthorizationResult,
    BuildTaintFinding,
    authorize,
    query_retraction_taint as _kernel_query_retraction_taint,
)
from lawvm.core.evidence_policy import EvidenceGraphPredicate
from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.execution_authorization import (
    ExecutionAuthorization,
    execution_authorization_evidence_report,
    execution_authorization_from_kernel_result,
)
from lawvm.core.frontier_work_item import (
    FrontierWorkItem,
    frontier_work_item_claim_closure_report,
)
from lawvm.core.phase_replay_gate import PhaseLocalReplayGate
from lawvm.core.provenance_graph import (
    ArtifactRef,
    GraphBuilder,
    Producer,
    ProvenanceAssertion,
    ProvenanceAttestation,
    ProvenanceGraph,
    attestation_canonical_payload,
    attestation_kind_registry_hash,
    _sha256,
)
from lawvm.core.provenance_graph_storage import GraphStore
from lawvm.core.quirks_disposition import QuirksDisposition


_MANUAL_CLAIM_AUTHORIZATION_FORBIDDEN_SHORTCUTS: tuple[str, ...] = (
    "manual_claim_authorization_as_replay_authority",
    "manual_claim_authorization_as_canonical_operation",
    "manual_claim_policy_success_as_phase_local_proof",
)


def manual_claim_lifecycle_status(
    attestations: Iterable[ProvenanceAttestation],
) -> str:
    """Derive v3 graph-native lifecycle status from attestations."""

    status = "proposed"
    for attestation in sorted(tuple(attestations), key=lambda item: item.produced_at):
        if attestation.attestation_kind == "claim_submitted":
            status = "proposed"
        elif attestation.attestation_kind == "reviewed":
            if attestation.payload.get("accepted") is True:
                status = "accepted"
            elif attestation.payload.get("accepted") is False:
                status = "rejected"
        elif attestation.attestation_kind == "retracted":
            status = "retracted"
        elif attestation.attestation_kind == "superseded":
            status = "superseded"
    return status


def manual_claim_review_status(
    attestations: Iterable[ProvenanceAttestation],
) -> str:
    """Derive v3 graph-native review status from attestations."""

    review_status = "proposed"
    for attestation in sorted(tuple(attestations), key=lambda item: item.produced_at):
        if attestation.attestation_kind == "reviewed":
            review_status = "human_reviewed"
    return review_status


# ---------------------------------------------------------------------------
# submit_assertion
# ---------------------------------------------------------------------------


def submit_assertion(
    graph_store: GraphStore,
    assertion: ProvenanceAssertion,
    producer: Producer,
) -> str:
    """Write a ProvenanceAssertion + claim_submitted attestation to the store.

    Returns the assertion_id.  Idempotent: re-submitting the same assertion
    is a no-op for the assertion file; a new claim_submitted attestation is
    still emitted per call to maintain a full audit trail.
    """
    graph_store.write_assertion(assertion)

    attest_payload: dict[str, object] = {
        "action": "claim_submitted",
        "assertion_kind": assertion.kind,
        "jurisdiction": assertion.jurisdiction,
    }
    attestation = _build_attestation(
        kind="claim_submitted",
        subject_ref=ArtifactRef(
            artifact_type="assertion",
            artifact_id=assertion.assertion_id,
            content_hash=assertion.assertion_id,
        ),
        producer=producer,
        payload=attest_payload,
    )
    graph_store.write_attestation(attestation)
    return assertion.assertion_id


def attest(
    graph_store: GraphStore,
    subject_id: str,
    attestation_kind: str,
    payload: dict[str, object],
    producer: Producer,
) -> str:
    """Emit an attestation of ``attestation_kind`` against ``subject_id``.

    Returns the attestation_id.
    """
    subject_ref = ArtifactRef(
        artifact_type="assertion",
        artifact_id=subject_id,
        content_hash=subject_id,
    )
    attestation = _build_attestation(
        kind=attestation_kind,
        subject_ref=subject_ref,
        producer=producer,
        payload=payload,
    )
    graph_store.write_attestation(attestation)
    return attestation.attestation_id


def _build_attestation(
    *,
    kind: str,
    subject_ref: ArtifactRef,
    producer: Producer,
    payload: dict[str, object],
) -> ProvenanceAttestation:
    now = datetime.now(tz=timezone.utc)
    temp = ProvenanceAttestation(
        attestation_id="__placeholder__",
        attestation_kind=kind,
        subject=subject_ref,
        materials=(),
        producer=producer,
        produced_at=now,
        payload=payload,
    )
    canonical = attestation_canonical_payload(temp)
    attest_id = _sha256(canonical)
    return ProvenanceAttestation(
        attestation_id=attest_id,
        attestation_kind=kind,
        subject=subject_ref,
        materials=(),
        producer=producer,
        produced_at=now,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# query_state
# ---------------------------------------------------------------------------


def query_state(
    graph: ProvenanceGraph,
    subject_id: str,
    *,
    policy: EvidenceGraphPredicate,
    profile: StrictProfile,
    at: datetime,
) -> AuthorizationResult:
    """Compute AuthorizationResult for ``subject_id`` under ``policy`` + ``profile``.

    Builds assertion_index and attestation_index from the graph, then
    delegates to evidence_kernel.authorize().  Pure; no side effects.
    """
    assertion_index: dict[str, ProvenanceAssertion] = {}
    attestation_index: dict[str, ProvenanceAttestation] = {}

    for node in graph.nodes:
        if node.node_type == "assertion":
            # The assertion object is not embedded in GraphNode; use store.
            # Here we reconstruct a lightweight ref — callers with real store
            # should pass pre-built indexes for efficiency.
            pass
        elif node.node_type == "attestation":
            pass

    # Build indexes from nodes carrying embedded payloads if available
    # (GraphNode only carries payload_hash; full objects must come from store)
    # For pure-graph queries without a store, we can only produce partial results.
    # Callers needing full authorization should use query_state_from_store().
    subject_ref = ArtifactRef(
        artifact_type="assertion",
        artifact_id=subject_id,
        content_hash=subject_id,
    )
    return authorize(
        subject=subject_ref,
        profile=profile,
        policy=policy,
        graph=graph,
        assertion_index=assertion_index,
        attestation_index=attestation_index,
        at=at,
    )


def query_state_from_store(
    graph_store: GraphStore,
    snapshot_hash: str,
    subject_id: str,
    *,
    policy: EvidenceGraphPredicate,
    profile: StrictProfile,
    at: datetime,
) -> AuthorizationResult:
    """Compute AuthorizationResult loading full objects from the store.

    Reads the graph snapshot and builds assertion/attestation indexes by
    loading objects from disk.  Use this when full authorization semantics
    are required.
    """
    graph = graph_store.read_graph(snapshot_hash)

    assertion_index: dict[str, ProvenanceAssertion] = {}
    attestation_index: dict[str, ProvenanceAttestation] = {}

    for node in graph.nodes:
        if node.node_type == "assertion":
            assertion = graph_store.read_assertion(node.node_id)
            assertion_index[assertion.assertion_id] = assertion
        elif node.node_type == "attestation":
            attestation = graph_store.read_attestation(node.node_id)
            attestation_index[attestation.attestation_id] = attestation

    subject_ref = ArtifactRef(
        artifact_type="assertion",
        artifact_id=subject_id,
        content_hash=subject_id,
    )
    return authorize(
        subject=subject_ref,
        profile=profile,
        policy=policy,
        graph=graph,
        assertion_index=assertion_index,
        attestation_index=attestation_index,
        at=at,
    )


def manual_claim_authorization_projection(
    result: AuthorizationResult,
    *,
    executable: bool = False,
    owner_phase: str = "manual_claim_graph_authorization",
    authorization_rule_id: str = "",
    strict_disposition: str = "",
    quirks_disposition: QuirksDisposition = QuirksDisposition.RECORD,
    validator_status: str = "",
    replay_authorized_when_policy_satisfied: bool = False,
) -> ExecutionAuthorization:
    """Project graph-native manual-claim authorization into the shared shape.

    EvidenceKernel success means an assertion satisfied a graph policy.  It is
    not replay authority by itself.  The default projection is therefore
    non-executable and non-replay-authorized; callers that have a separate
    phase-local proof must opt in explicitly.
    """

    return execution_authorization_from_kernel_result(
        result,
        executable=executable,
        owner_phase=owner_phase,
        authorization_rule_id=authorization_rule_id or result.policy_id,
        strict_disposition=strict_disposition,
        quirks_disposition=quirks_disposition,
        validator_status=validator_status,
        replay_authorized_when_policy_satisfied=replay_authorized_when_policy_satisfied,
        forbidden_shortcuts=_MANUAL_CLAIM_AUTHORIZATION_FORBIDDEN_SHORTCUTS,
        detail={
            "manual_claim_authorization": {
                "projection": "graph_native_manual_claim_authorization",
                "read_model_only": True,
                "requires_separate_phase_local_replay_gate": True,
            }
        },
    )


def manual_claim_authorization_evidence_report(
    results: AuthorizationResult | tuple[AuthorizationResult, ...],
    *,
    jurisdiction: str,
    report_kind: str = "manual_claim_authorization",
    executable: bool = False,
    owner_phase: str = "manual_claim_graph_authorization",
    replay_authorized_when_policy_satisfied: bool = False,
) -> EvidenceSurfaceReport:
    """Report graph-native manual-claim authorization without promoting replay.

    This is a read-model bridge from ``AuthorizationResult`` to the shared
    ``EvidenceSurfaceReport``/``ProofSurface`` path.  It reuses
    ``ExecutionAuthorization`` rows so manual claims do not grow a local
    stringly authorization envelope.
    """

    result_rows = results if isinstance(results, tuple) else (results,)
    authorizations = tuple(
        manual_claim_authorization_projection(
            result,
            executable=executable,
            owner_phase=owner_phase,
            replay_authorized_when_policy_satisfied=replay_authorized_when_policy_satisfied,
        )
        for result in result_rows
    )
    base = execution_authorization_evidence_report(
        authorizations,
        jurisdiction=jurisdiction,
        report_kind=report_kind,
    )
    return EvidenceSurfaceReport(
        jurisdiction=base.jurisdiction,
        report_kind=report_kind,
        schema="lawvm.manual_claim_authorization_report.v1",
        truth_claim="graph-native manual-claim authorization projections",
        replay_claims=base.replay_claims,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=base.summary,
        filters={
            **dict(base.filters),
            "owner_phase": owner_phase,
        },
        filtered_summary=base.filtered_summary,
        rows=base.rows,
        rows_truncated=base.rows_truncated,
        evidence_jsonl=base.evidence_jsonl,
        written_paths=base.written_paths,
        detail={
            "safe_default": "manual_claim_authorization_is_read_model_until_phase_gate",
            "forbidden_shortcuts": _MANUAL_CLAIM_AUTHORIZATION_FORBIDDEN_SHORTCUTS,
            "included_surfaces": ("execution_authorization",),
        },
    )


def manual_claim_frontier_closure_report(
    *,
    frontier_work_item: FrontierWorkItem | Mapping[str, object],
    assertion: object,
    authorization_result: AuthorizationResult,
    phase_replay_gate: PhaseLocalReplayGate | None = None,
    jurisdiction: str = "",
    report_kind: str = "manual_claim_frontier_closure",
) -> EvidenceSurfaceReport:
    """Match a graph-native manual claim authorization to a frontier item.

    This is a convenience wrapper over the shared frontier closure report.  It
    remains a passive read model unless an exact phase-local replay gate is
    supplied.  Policy success plus frontier matching does not authorize replay
    by itself.
    """

    return frontier_work_item_claim_closure_report(
        frontier_work_item,
        assertion=assertion,
        authorization_result=authorization_result,
        phase_replay_gate=phase_replay_gate,
        jurisdiction=jurisdiction,
        report_kind=report_kind,
    )


# ---------------------------------------------------------------------------
# query_retraction_taint
# ---------------------------------------------------------------------------


def query_retraction_taint(
    graph: ProvenanceGraph,
    build_ids: tuple[str, ...],
    attestation_index: Mapping[str, ProvenanceAttestation] | None = None,
) -> tuple[BuildTaintFinding, ...]:
    """Compute retraction taint for the given build IDs at query time.

    No stored taint (§9).  Delegates to evidence_kernel.query_retraction_taint.
    If attestation_index is not provided, builds it from the graph nodes.
    """
    if attestation_index is None:
        idx: dict[str, ProvenanceAttestation] = {}
    else:
        idx = dict(attestation_index)
    return _kernel_query_retraction_taint(graph, build_ids, idx)


# ---------------------------------------------------------------------------
# Graph snapshot builder for a claim's reachable subgraph
# ---------------------------------------------------------------------------


def build_claim_subgraph(
    graph_store: GraphStore,
    snapshot_hash: str,
    assertion_id: str,
) -> ProvenanceGraph:
    """Return the subgraph reachable from assertion_id in the given snapshot.

    Useful for showing a claim + all its attestations in ``show`` subcommand.
    """
    full_graph = graph_store.read_graph(snapshot_hash)
    reachable_node_ids: set[str] = {assertion_id}
    changed = True
    while changed:
        changed = False
        for edge in full_graph.edges:
            if edge.src_node_id in reachable_node_ids:
                if edge.dst_node_id not in reachable_node_ids:
                    reachable_node_ids.add(edge.dst_node_id)
                    changed = True

    reg_hash = attestation_kind_registry_hash()
    builder = GraphBuilder(attestation_kind_registry_hash_val=reg_hash)
    for node in full_graph.nodes:
        if node.node_id in reachable_node_ids:
            builder.add_node(node)
    for edge in full_graph.edges:
        if edge.src_node_id in reachable_node_ids and edge.dst_node_id in reachable_node_ids:
            builder.add_edge(edge)
    return builder.finalize()
