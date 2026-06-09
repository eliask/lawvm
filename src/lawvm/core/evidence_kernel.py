"""EvidenceKernel — trusted evaluator for declarative evidence policies.

Piece 3 of Step 2 (UNIFIED_PROVENANCE_GRAPH_DESIGN_v3.md §5 + §13).

``authorize()`` is the single trusted entry point.  It is a pure function:
  same (subject, profile, policy, graph, assertion_index, attestation_index, at)
  → identical AuthorizationResult, bit-for-bit.

Design constraints (§12 anti-patterns)
---------------------------------------
  - NO mutation of graph nodes or edges.
  - NO arbitrary Python predicates — evaluator interprets declarative DSL only.
  - Unknown ``PolicyExpr.op`` raises ValueError immediately (§12.3).
  - ``evidence_bundle_hash`` covers only the reachable subgraph consulted.
  - NO producer-kind precedence rules (§12.4).

AGENTS.md §1.10: no broad try/except.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from lawvm.core.compile_result import StrictProfile
from lawvm.core.evidence_policy import (
    KNOWN_OPS,
    EvidenceGraphPredicate,
    IndependenceDimension,
    PolicyExpr,
    check_independence,
)
from lawvm.core.provenance_graph import (
    ArtifactRef,
    ProvenanceAssertion,
    ProvenanceAttestation,
    ProvenanceGraph,
)

Json = object


# ---------------------------------------------------------------------------
# AuthorizationResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthorizationResult:
    """Outcome of one authorization call.

    ``authorized`` is True iff all required clauses are satisfied and no
    forbidden clauses are present.  The hashes allow downstream consumers to
    verify that this result was produced from the expected graph state.
    """

    subject: ArtifactRef
    policy_id: str
    profile_name: str
    authorized: bool
    satisfied_clauses: tuple[str, ...]
    unsatisfied_clauses: tuple[str, ...]
    forbidden_present: tuple[str, ...]
    evidence_bundle_hash: str


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def authorize(
    subject: ArtifactRef,
    profile: StrictProfile,
    policy: EvidenceGraphPredicate,
    graph: ProvenanceGraph,
    *,
    assertion_index: Mapping[str, ProvenanceAssertion],
    attestation_index: Mapping[str, ProvenanceAttestation],
    at: datetime,
) -> AuthorizationResult:
    """Evaluate evidence policy for ``subject`` under ``profile``.

    Parameters
    ----------
    subject:
        The assertion (or other artifact) being authorized.
    profile:
        StrictProfile that governs which channels are admitted.
    policy:
        EvidenceGraphPredicate describing required + forbidden clauses.
    graph:
        Full provenance graph.
    assertion_index:
        assertion_id → ProvenanceAssertion mapping for O(1) lookup.
    attestation_index:
        attestation_id → ProvenanceAttestation mapping for O(1) lookup.
    at:
        Evaluation timestamp (for within_time checks).

    Returns
    -------
    AuthorizationResult — pure, deterministic, no side effects.
    """
    ctx = _EvalContext(
        subject=subject,
        graph=graph,
        assertion_index=assertion_index,
        attestation_index=attestation_index,
        at=at,
        consulted_node_ids=set(),
        consulted_edge_ids=set(),
    )

    satisfied: list[str] = []
    unsatisfied: list[str] = []
    forbidden_present: list[str] = []

    for expr in policy.required:
        label = _expr_label(expr)
        if _eval_expr(expr, ctx):
            satisfied.append(label)
        else:
            unsatisfied.append(label)

    for expr in policy.forbidden:
        label = _expr_label(expr)
        if _eval_expr(expr, ctx):
            forbidden_present.append(label)

    authorized = not unsatisfied and not forbidden_present

    bundle_hash = _compute_bundle_hash(
        graph, ctx.consulted_node_ids, ctx.consulted_edge_ids
    )

    return AuthorizationResult(
        subject=subject,
        policy_id=policy.predicate_id,
        profile_name=profile.name,
        authorized=authorized,
        satisfied_clauses=tuple(satisfied),
        unsatisfied_clauses=tuple(unsatisfied),
        forbidden_present=tuple(forbidden_present),
        evidence_bundle_hash=bundle_hash,
    )


# ---------------------------------------------------------------------------
# Internal evaluation context
# ---------------------------------------------------------------------------


@dataclass
class _EvalContext:
    subject: ArtifactRef
    graph: ProvenanceGraph
    assertion_index: Mapping[str, ProvenanceAssertion]
    attestation_index: Mapping[str, ProvenanceAttestation]
    at: datetime
    consulted_node_ids: set[str]
    consulted_edge_ids: set[str]


# ---------------------------------------------------------------------------
# DSL evaluator
# ---------------------------------------------------------------------------


def _expr_label(expr: PolicyExpr) -> str:
    """Human-readable label for a PolicyExpr, used in satisfied/unsatisfied clause lists."""
    kind = expr.args.get("attestation_kind", "")
    if kind:
        return f"{expr.op}:{kind}"
    return expr.op


def _eval_expr(expr: PolicyExpr, ctx: _EvalContext) -> bool:
    op = expr.op
    if op not in KNOWN_OPS:
        raise ValueError(
            f"EvidenceKernel: unknown PolicyExpr op {op!r}. "
            f"Known ops: {sorted(KNOWN_OPS)}"
        )
    if op == "exists":
        return _eval_exists(expr.args, ctx)
    if op == "none":
        return _eval_none(expr.args, ctx)
    if op == "count_distinct_at_least":
        return _eval_count_distinct_at_least(expr.args, ctx)
    if op == "all":
        return _eval_all(expr.args, ctx)
    if op == "within_time":
        return _eval_within_time(expr.args, ctx)
    if op == "signed_by":
        return _eval_signed_by(expr.args, ctx)
    if op == "not_retracted":
        return _eval_not_retracted(expr.args, ctx)
    if op == "reachable":
        return _eval_reachable(expr.args, ctx)
    if op == "independent":
        return _eval_independent(expr.args, ctx)
    if op == "materials_match_dependencies":
        return _eval_materials_match_dependencies(expr.args, ctx)
    raise ValueError(f"EvidenceKernel: unhandled op {op!r}")


# ---------------------------------------------------------------------------
# Per-op implementations
# ---------------------------------------------------------------------------


def _attestations_for_subject(ctx: _EvalContext) -> list[ProvenanceAttestation]:
    """Return all attestations whose subject matches ctx.subject."""
    subject_id = ctx.subject.artifact_id
    result = []
    for edge in ctx.graph.edges:
        if edge.edge_type in (
            "validates",
            "reviews",
            "refutes",
            "retracts",
            "supersedes",
            "disputes",
        ):
            if edge.src_node_id == subject_id or edge.payload.get("subject_id") == subject_id:
                ctx.consulted_edge_ids.add(edge.edge_id)
                attest = ctx.attestation_index.get(edge.dst_node_id)
                if attest is not None:
                    ctx.consulted_node_ids.add(edge.dst_node_id)
                    result.append(attest)
    # Also check attestations by direct subject field
    for attest in ctx.attestation_index.values():
        if attest.subject.artifact_id == subject_id:
            ctx.consulted_node_ids.add(attest.attestation_id)
            result.append(attest)
    # Deduplicate (subject scan may overlap edge scan)
    seen: set[str] = set()
    deduped = []
    for a in result:
        if a.attestation_id not in seen:
            seen.add(a.attestation_id)
            deduped.append(a)
    return deduped


def _filter_attestations(
    attestations: list[ProvenanceAttestation],
    kind: str | None,
    filters: Mapping[str, object],
) -> list[ProvenanceAttestation]:
    """Filter attestations by kind and payload filter matches."""
    out = []
    for a in attestations:
        if kind is not None and a.attestation_kind != kind:
            continue
        match = True
        for k, v in filters.items():
            if k == "attestation_kind":
                continue
            # Check producer fields
            if k.startswith("producer."):
                field = k[len("producer."):]
                actual = getattr(a.producer, field, None)
                if actual != v:
                    match = False
                    break
            # Check payload fields (dot notation for nested)
            elif "." in k:
                parts = k.split(".", 1)
                sub = a.payload.get(parts[0])
                if isinstance(sub, dict):
                    actual = sub.get(parts[1])
                else:
                    actual = None
                if actual != v:
                    match = False
                    break
            else:
                if a.payload.get(k) != v:
                    match = False
                    break
        if match:
            out.append(a)
    return out


def _eval_exists(args: Mapping[str, object], ctx: _EvalContext) -> bool:
    kind = str(args.get("attestation_kind", ""))
    attestations = _attestations_for_subject(ctx)
    filtered = _filter_attestations(attestations, kind if kind else None, args)
    return len(filtered) > 0


def _eval_none(args: Mapping[str, object], ctx: _EvalContext) -> bool:
    kind = str(args.get("attestation_kind", ""))
    attestations = _attestations_for_subject(ctx)
    filtered = _filter_attestations(attestations, kind if kind else None, args)
    return len(filtered) == 0


def _eval_count_distinct_at_least(args: Mapping[str, object], ctx: _EvalContext) -> bool:
    kind = str(args.get("attestation_kind", ""))
    path = str(args.get("path", ""))
    n = int(args.get("n", 1))  # ty:ignore[invalid-argument-type]
    attestations = _attestations_for_subject(ctx)
    filtered = _filter_attestations(attestations, kind if kind else None, {})
    values: set[str] = set()
    for a in filtered:
        val = _extract_path_value(a, path)
        if val is not None:
            values.add(str(val))
    return len(values) >= n


def _extract_path_value(a: ProvenanceAttestation, path: str) -> object:
    if path.startswith("producer."):
        field = path[len("producer."):]
        return getattr(a.producer, field, None)
    parts = path.split(".", 1)
    if len(parts) == 1:
        return a.payload.get(path)
    sub = a.payload.get(parts[0])
    if isinstance(sub, dict):
        return sub.get(parts[1])
    return None


def _eval_all(args: Mapping[str, object], ctx: _EvalContext) -> bool:
    sub_op = args.get("sub_op")
    if sub_op == "materials_match_dependencies":
        return _eval_materials_match_dependencies_all(ctx)
    return True


def _eval_within_time(args: Mapping[str, object], ctx: _EvalContext) -> bool:
    interval_start_str = args.get("interval_start")
    interval_end_str = args.get("interval_end")
    attestations = _attestations_for_subject(ctx)
    if not attestations:
        return False
    for a in attestations:
        ts = a.produced_at
        if interval_start_str:
            start = datetime.fromisoformat(str(interval_start_str))
            if ts < start:
                return False
        if interval_end_str:
            end = datetime.fromisoformat(str(interval_end_str))
            if ts > end:
                return False
    return True


def _eval_signed_by(args: Mapping[str, object], ctx: _EvalContext) -> bool:
    keyring = str(args.get("keyring", ""))
    attestations = _attestations_for_subject(ctx)
    for a in attestations:
        if a.signature is not None and a.signature.keyring_id == keyring:  # ty:ignore[unresolved-attribute]
            return True
    return False


def _eval_not_retracted(args: Mapping[str, object], ctx: _EvalContext) -> bool:
    subject_arg = str(args.get("subject", "self"))
    if subject_arg == "self":
        attestations = _attestations_for_subject(ctx)
    else:
        # Look up by the given subject id
        ref_id = subject_arg
        attestations = [
            a for a in ctx.attestation_index.values()
            if a.subject.artifact_id == ref_id
        ]
    for a in attestations:
        if a.attestation_kind == "retracted":
            return False
    return True


def _eval_reachable(args: Mapping[str, object], ctx: _EvalContext) -> bool:
    target_subject = str(args.get("subject", ""))
    raw_edge_types = args.get("edge_types", [])
    edge_types = frozenset(str(e) for e in (raw_edge_types if isinstance(raw_edge_types, list) else [raw_edge_types]))
    start_id = ctx.subject.artifact_id
    visited: set[str] = set()
    queue = [start_id]
    while queue:
        current = queue.pop()
        if current in visited:
            continue
        visited.add(current)
        ctx.consulted_node_ids.add(current)
        if current == target_subject:
            return True
        for edge in ctx.graph.edges:
            if edge.src_node_id == current and (not edge_types or edge.edge_type in edge_types):
                ctx.consulted_edge_ids.add(edge.edge_id)
                queue.append(edge.dst_node_id)
    return False


def _eval_independent(args: Mapping[str, object], ctx: _EvalContext) -> bool:
    kind = str(args.get("attestation_kind", ""))
    raw_by = args.get("by", [])
    by_strs: list[str] = [str(x) for x in (raw_by if isinstance(raw_by, list) else [raw_by])]
    attestations = _attestations_for_subject(ctx)
    filtered = _filter_attestations(attestations, kind if kind else None, {})
    dims: list[IndependenceDimension] = []
    for s in by_strs:
        for dim in IndependenceDimension:
            if dim.value == s:
                dims.append(dim)
                break
    if not dims:
        return True
    return check_independence(tuple(filtered), tuple(dims))


def _eval_materials_match_dependencies(args: Mapping[str, object], ctx: _EvalContext) -> bool:
    subject_id = ctx.subject.artifact_id
    assertion = ctx.assertion_index.get(subject_id)
    if assertion is None:
        return True
    dep_ids = frozenset(r.artifact_id for r in assertion.dependency_refs)
    attestations = _attestations_for_subject(ctx)
    for a in attestations:
        material_ids = frozenset(
            m.artifact_id if hasattr(m, "artifact_id") else m.artifact_digest
            for m in a.materials
        )
        if material_ids and not material_ids.issubset(dep_ids | {subject_id}):
            return False
    return True


def _eval_materials_match_dependencies_all(ctx: _EvalContext) -> bool:
    return _eval_materials_match_dependencies({}, ctx)


# ---------------------------------------------------------------------------
# Evidence bundle hash
# ---------------------------------------------------------------------------


def _compute_bundle_hash(
    graph: ProvenanceGraph,
    consulted_node_ids: set[str],
    consulted_edge_ids: set[str],
) -> str:
    """SHA-256 of the consulted subgraph in canonical order."""
    nodes = sorted(
        (n for n in graph.nodes if n.node_id in consulted_node_ids),
        key=lambda n: n.node_id,
    )
    edges = sorted(
        (e for e in graph.edges if e.edge_id in consulted_edge_ids),
        key=lambda e: e.edge_id,
    )
    payload: dict[str, object] = {
        "nodes": [
            {
                "node_id": n.node_id,
                "node_type": n.node_type,
                "payload_hash": n.payload_hash,
            }
            for n in nodes
        ],
        "edges": [
            {
                "edge_id": e.edge_id,
                "edge_type": e.edge_type,
                "src": e.src_node_id,
                "dst": e.dst_node_id,
            }
            for e in edges
        ],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Convenience: query retraction taint across builds (Piece 4 helper)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuildTaintFinding:
    """One tainted build ID and the retracted assertion that taints it."""

    build_id: str
    retracted_assertion_id: str
    retraction_attestation_id: str


def query_retraction_taint(
    graph: ProvenanceGraph,
    build_ids: tuple[str, ...],
    attestation_index: Mapping[str, ProvenanceAttestation],
) -> tuple[BuildTaintFinding, ...]:
    """Compute retraction taint for the given build IDs at query time.

    No stored taint in graph nodes (§9 of design memo).  Walks the graph to
    find retracted assertions consumed by any of the given builds.
    """
    retracted_ids: set[str] = set()
    for attest in attestation_index.values():
        if attest.attestation_kind == "retracted":
            retracted_ids.add(attest.subject.artifact_id)

    findings: list[BuildTaintFinding] = []
    for edge in graph.edges:
        if edge.edge_type == "consumed_by_build":
            build_id = str(edge.payload.get("build_id", ""))
            if build_id and build_id in build_ids:
                assertion_id = edge.src_node_id
                if assertion_id in retracted_ids:
                    # Find the retraction attestation
                    retraction_id = ""
                    for attest in attestation_index.values():
                        if (
                            attest.attestation_kind == "retracted"
                            and attest.subject.artifact_id == assertion_id
                        ):
                            retraction_id = attest.attestation_id
                            break
                    findings.append(
                        BuildTaintFinding(
                            build_id=build_id,
                            retracted_assertion_id=assertion_id,
                            retraction_attestation_id=retraction_id,
                        )
                    )

    return tuple(sorted(findings, key=lambda f: (f.build_id, f.retracted_assertion_id)))
