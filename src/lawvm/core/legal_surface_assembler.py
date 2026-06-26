"""Deterministic assembler for the Legal Surface Graph (Phase 0).

Authoritative design: ``notes_internal/pro_on_fi_theory_grammar5.txt`` §D1
(stable identity), §D5 (cross-lens edge passes), §D7 (authority firewall).

Responsibilities (§D2):
  * MINT stable ``node_id`` values (sha256 over the §D1 identity tuple) and
    compute ``payload_hash`` separately.
  * MINT entity nodes (``entity:<canonical id>``).
  * VALIDATE seeds (reject unknown node_kind / status / authority_role, missing
    required fields) — fail loud, never a silent drop.
  * DEDUPLICATE nodes by minted id (identical payload) and edges by edge_id.
  * RUN cross-lens edge passes in declared order (§D5).
  * ENFORCE the authority firewall: refuse any node/edge with
    ``replay_authorized=True`` (§D7).
  * ASSEMBLE the graph with a deterministic ``graph_id`` (invariant under the
    order lenses ran in: node/edge ids are sorted before hashing).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable

from lawvm.core.legal_surface_graph import (
    AUTHORITY_ROLES,
    EDGE_ID_SCHEMA_TAG,
    EDGE_KINDS,
    EDGE_STATUSES,
    GRAPH_ID_SCHEMA_TAG,
    NODE_ID_SCHEMA_TAG,
    NODE_KINDS,
    NODE_STATUSES,
    SCHEMA_TAG,
    LegalSurfaceGraph,
    SourceUnitRef,
    SurfaceDiagnostic,
    SurfaceEdge,
    SurfaceGraphSubject,
    SurfaceLensRun,
    SurfaceNode,
)
from lawvm.core.legal_surface_lens import (
    SurfaceEdgeSeed,
    SurfaceLensResult,
    SurfaceNodeSeed,
    SurfaceResidualSeed,
)

# Node kinds that are entity handles: their node_id is "entity:<discriminator>"
# and the discriminator IS the canonical id (§D1 "For entity nodes").
ENTITY_NODE_KINDS: frozenset[str] = frozenset(
    {
        "source_unit",
        "legal_work_entity",
        "legal_address_entity",
        "actor_entity",
        "term_symbol_entity",
    }
)

ENTITY_ID_PREFIX = "entity:"


class SurfaceAssemblyError(Exception):
    """A seed/graph could not be assembled. Always typed; never a silent drop."""


class AuthorityFirewallError(SurfaceAssemblyError):
    """A node/edge attempted ``replay_authorized=True`` (§D7).

    The surface graph is structurally incapable of authorizing replay. A fact
    that becomes executable must LEAVE this graph through a named
    authorization/proof object.
    """


# ── Canonical hashing helpers ────────────────────────────────────────────────


def _canonical_json(payload: Mapping[str, object]) -> str:
    """Deterministic JSON of a payload (sorted keys, compact, ASCII)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(parts: tuple[str, ...]) -> str:
    """sha256 over a tuple of string parts joined with a NUL separator.

    NUL cannot occur in the identity components, so joining is unambiguous.
    """
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def compute_payload_hash(payload: Mapping[str, object]) -> str:
    """Hash of the exact current payload (§D1 ``payload_hash``)."""
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def mint_entity_node_id(canonical_id: str) -> str:
    """``entity:<canonical id>`` (§D1 entity-node identity)."""
    return f"{ENTITY_ID_PREFIX}{canonical_id}"


def mint_source_fact_node_id(
    *,
    jurisdiction: str,
    work_id: str | None,
    source_unit_id: str,
    span_start: int,
    span_end: int,
    lens_id: str,
    node_kind: str,
    local_discriminator: str,
) -> str:
    """Stable surface identity for a source-fact node (§D1).

    sha256 over the identity tuple. Stable across reruns and unchanged when
    payload details improve (payload travels in ``payload_hash``, not here).
    """
    return _sha256(
        (
            NODE_ID_SCHEMA_TAG,
            jurisdiction,
            work_id or "",
            source_unit_id,
            str(span_start),
            str(span_end),
            lens_id,
            node_kind,
            local_discriminator,
        )
    )


def mint_edge_id(
    *,
    edge_kind: str,
    src_node_id: str,
    dst_node_id: str,
    rule_id: str,
    canonical_payload_subset: Mapping[str, object],
) -> str:
    """Stable edge identity (§D1)."""
    return _sha256(
        (
            EDGE_ID_SCHEMA_TAG,
            edge_kind,
            src_node_id,
            dst_node_id,
            rule_id,
            _canonical_json(canonical_payload_subset),
        )
    )


# ── Cross-lens edge passes (§D5) ─────────────────────────────────────────────


@runtime_checkable
class SurfaceEdgePass(Protocol):
    """A deterministic cross-lens edge computation (§D5).

    Passes run in declared order. Cross-lens edges are created ONLY here — never
    by lenses (a lens may emit only intrinsic edges within its own output).
    """

    pass_id: str
    reads_node_kinds: tuple[str, ...]
    emits_edge_kinds: tuple[str, ...]

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceEdgeSeed, ...]: ...


# ── Validation ───────────────────────────────────────────────────────────────


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SurfaceAssemblyError(message)


def _validate_node_seed(seed: SurfaceNodeSeed, *, lens_id: str) -> None:
    _require(
        seed.node_kind in NODE_KINDS,
        f"lens {lens_id!r}: unknown node_kind {seed.node_kind!r}; "
        f"allowed={sorted(NODE_KINDS)}",
    )
    _require(
        seed.node_status in NODE_STATUSES,
        f"lens {lens_id!r}: unknown node status {seed.node_status!r} "
        f"(node_kind={seed.node_kind!r}); allowed={sorted(NODE_STATUSES)}",
    )
    _require(
        seed.authority_role in AUTHORITY_ROLES,
        f"lens {lens_id!r}: unknown authority_role {seed.authority_role!r}; "
        f"allowed={sorted(AUTHORITY_ROLES)}",
    )
    _require(
        bool(seed.local_discriminator),
        f"lens {lens_id!r}: node seed (kind={seed.node_kind!r}) has empty local_discriminator",
    )
    _require(
        bool(seed.rule_id),
        f"lens {lens_id!r}: node seed (kind={seed.node_kind!r}) has empty rule_id",
    )
    if seed.node_kind not in ENTITY_NODE_KINDS:
        _require(
            seed.source_ref is not None,
            f"lens {lens_id!r}: source-fact node seed (kind={seed.node_kind!r}) "
            f"requires a source_ref",
        )


def _validate_edge_seed(seed: SurfaceEdgeSeed, *, origin: str) -> None:
    _require(
        seed.edge_kind in EDGE_KINDS,
        f"{origin}: unknown edge_kind {seed.edge_kind!r}; allowed={sorted(EDGE_KINDS)}",
    )
    _require(
        seed.surface_edge_status in EDGE_STATUSES,
        f"{origin}: unknown edge status {seed.surface_edge_status!r} "
        f"(edge_kind={seed.edge_kind!r}); allowed={sorted(EDGE_STATUSES)}",
    )
    _require(bool(seed.rule_id), f"{origin}: edge seed (kind={seed.edge_kind!r}) has empty rule_id")
    _require(
        bool(seed.src_local) and bool(seed.dst_local),
        f"{origin}: edge seed (kind={seed.edge_kind!r}) has empty endpoint reference",
    )


# ── Minting nodes from seeds ─────────────────────────────────────────────────


def _mint_node_from_seed(
    seed: SurfaceNodeSeed,
    *,
    jurisdiction: str,
    work_id: str | None,
    lens_id: str,
) -> SurfaceNode:
    """Mint a SurfaceNode from a validated seed, enforcing the firewall."""
    if seed.node_kind in ENTITY_NODE_KINDS:
        node_id = mint_entity_node_id(seed.local_discriminator)
    else:
        # source-fact node — _validate_node_seed guarantees a source_ref here.
        ref = seed.source_ref
        if ref is None:  # pragma: no cover — defensive; validation already enforced this
            raise SurfaceAssemblyError(
                f"lens {lens_id!r}: source-fact node seed (kind={seed.node_kind!r}) lost its source_ref"
            )
        node_id = mint_source_fact_node_id(
            jurisdiction=jurisdiction,
            work_id=work_id,
            source_unit_id=ref.source_unit_id,
            span_start=ref.char_start,
            span_end=ref.char_end,
            lens_id=lens_id,
            node_kind=seed.node_kind,
            local_discriminator=seed.local_discriminator,
        )
    payload_hash = compute_payload_hash(seed.payload)
    # Firewall (§D7): seeds carry no replay flag, but we construct nodes with the
    # safe defaults explicitly and assert them below.
    node = SurfaceNode(
        node_id=node_id,
        node_kind=seed.node_kind,
        authority_role=seed.authority_role,
        jurisdiction=jurisdiction,
        source_ref=seed.source_ref,
        lens_id=lens_id,
        rule_id=seed.rule_id,
        node_status=seed.node_status,
        payload_hash=payload_hash,
        payload=seed.payload,
    )
    _enforce_node_firewall(node)
    return node


def _residual_to_node_seed(seed: SurfaceResidualSeed) -> SurfaceNodeSeed:
    """A residual materializes into a ``surface_residual`` node (§D2/§D8)."""
    payload: dict[str, object] = {
        "residual_kind": seed.residual_kind,
        "reason_code": seed.reason_code,
        **dict(seed.payload),
    }
    return SurfaceNodeSeed(
        node_kind="surface_residual",
        source_ref=seed.source_ref,
        local_discriminator=seed.local_discriminator,
        rule_id=seed.rule_id,
        node_status=seed.residual_status,
        payload=payload,
        authority_role="residual",
    )


def _enforce_node_firewall(node: SurfaceNode) -> None:
    if node.replay_authorized:
        raise AuthorityFirewallError(
            f"node {node.node_id!r} (kind={node.node_kind!r}) has replay_authorized=True; "
            f"the surface graph can never authorize replay (§D7)"
        )
    if not node.surface_only:
        raise AuthorityFirewallError(
            f"node {node.node_id!r} (kind={node.node_kind!r}) has surface_only=False; "
            f"all surface nodes must be surface_only (§D7)"
        )


def _enforce_edge_firewall(edge: SurfaceEdge) -> None:
    if edge.replay_authorized:
        raise AuthorityFirewallError(
            f"edge {edge.edge_id!r} (kind={edge.edge_kind!r}) has replay_authorized=True; "
            f"the surface graph can never authorize replay (§D7)"
        )
    if not edge.surface_only:
        raise AuthorityFirewallError(
            f"edge {edge.edge_id!r} (kind={edge.edge_kind!r}) has surface_only=False; "
            f"all surface edges must be surface_only (§D7)"
        )


# ── Edge resolution & minting ────────────────────────────────────────────────


def _resolve_endpoint(
    local_ref: str,
    *,
    nodes: Mapping[str, SurfaceNode],
    local_index: Mapping[str, str],
    origin: str,
) -> str:
    """Resolve an edge endpoint to a minted node_id, validating it exists.

    An endpoint reference is either an already-minted node_id (cross-lens edge
    passes work in minted ids) or a lens-local discriminator. Either way it
    must resolve to a node that exists in the graph (§D5 endpoint validation).
    """
    if local_ref in nodes:
        return local_ref
    resolved = local_index.get(local_ref)
    if resolved is not None and resolved in nodes:
        return resolved
    raise SurfaceAssemblyError(
        f"{origin}: edge endpoint {local_ref!r} does not resolve to any node in the graph"
    )


def _mint_edge_from_seed(
    seed: SurfaceEdgeSeed,
    *,
    nodes: Mapping[str, SurfaceNode],
    local_index: Mapping[str, str],
    origin: str,
) -> SurfaceEdge:
    src = _resolve_endpoint(seed.src_local, nodes=nodes, local_index=local_index, origin=origin)
    dst = _resolve_endpoint(seed.dst_local, nodes=nodes, local_index=local_index, origin=origin)
    edge_id = mint_edge_id(
        edge_kind=seed.edge_kind,
        src_node_id=src,
        dst_node_id=dst,
        rule_id=seed.rule_id,
        canonical_payload_subset=seed.payload,
    )
    edge = SurfaceEdge(
        edge_id=edge_id,
        edge_kind=seed.edge_kind,
        src=src,
        dst=dst,
        rule_id=seed.rule_id,
        surface_edge_status=seed.surface_edge_status,
        payload_hash=compute_payload_hash(seed.payload),
        payload=seed.payload,
    )
    _enforce_edge_firewall(edge)
    return edge


# ── The assembler ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _MintedSeeds:
    nodes: dict[str, SurfaceNode]
    local_index: dict[str, str]
    diagnostics: list[SurfaceDiagnostic]


def _assemble_nodes(
    lens_results: tuple[SurfaceLensResult, ...],
    *,
    jurisdiction: str,
    work_id: str | None,
) -> _MintedSeeds:
    nodes: dict[str, SurfaceNode] = {}
    # local_index maps a per-lens "<lens_id>::<discriminator>" AND the bare
    # discriminator to a minted node_id, so intrinsic edge seeds can refer to
    # node seeds by their discriminator.
    local_index: dict[str, str] = {}
    diagnostics: list[SurfaceDiagnostic] = []

    for result in lens_results:
        lens_id = result.lens_id
        seeds: list[SurfaceNodeSeed] = list(result.node_seeds)
        seeds.extend(_residual_to_node_seed(r) for r in result.residuals)
        for seed in seeds:
            _validate_node_seed(seed, lens_id=lens_id)
            node = _mint_node_from_seed(
                seed, jurisdiction=jurisdiction, work_id=work_id, lens_id=lens_id
            )
            _dedup_node(nodes, node)
            _index_local(local_index, lens_id, seed.local_discriminator, node.node_id)

    return _MintedSeeds(nodes=nodes, local_index=local_index, diagnostics=diagnostics)


def _dedup_node(nodes: dict[str, SurfaceNode], node: SurfaceNode) -> None:
    existing = nodes.get(node.node_id)
    if existing is None:
        nodes[node.node_id] = node
        return
    # Same id is allowed only if the payload (and thus payload_hash) matches.
    if existing.payload_hash != node.payload_hash:
        raise SurfaceAssemblyError(
            f"node id collision with divergent payload for {node.node_id!r} "
            f"(kind={node.node_kind!r}): {existing.payload_hash} != {node.payload_hash}"
        )
    # identical duplicate — drop silently is fine (same fact, two lenses/seeds)


def _index_local(local_index: dict[str, str], lens_id: str, discriminator: str, node_id: str) -> None:
    keyed = f"{lens_id}::{discriminator}"
    local_index[keyed] = node_id
    # Bare discriminator is convenient but ambiguous across lenses; only keep it
    # if unambiguous (first writer wins is non-deterministic, so refuse on clash).
    prior = local_index.get(discriminator)
    if prior is None:
        local_index[discriminator] = node_id
    elif prior != node_id:
        # Mark as ambiguous so endpoint resolution by bare ref fails loudly.
        local_index[discriminator] = _AMBIGUOUS_REF


_AMBIGUOUS_REF = "__ambiguous_local_ref__"


def _assemble_edges(
    lens_results: tuple[SurfaceLensResult, ...],
    *,
    nodes: Mapping[str, SurfaceNode],
    local_index: Mapping[str, str],
) -> dict[str, SurfaceEdge]:
    edges: dict[str, SurfaceEdge] = {}
    for result in lens_results:
        origin = f"lens {result.lens_id!r}"
        for seed in result.edge_seeds:
            _validate_edge_seed(seed, origin=origin)
            edge = _mint_edge_from_seed(
                seed, nodes=nodes, local_index=local_index, origin=origin
            )
            _dedup_edge(edges, edge, origin=origin)
    return edges


def _dedup_edge(edges: dict[str, SurfaceEdge], edge: SurfaceEdge, *, origin: str) -> None:
    existing = edges.get(edge.edge_id)
    if existing is None:
        edges[edge.edge_id] = edge
        return
    if (
        existing.payload_hash != edge.payload_hash
        or existing.surface_edge_status != edge.surface_edge_status
    ):
        raise SurfaceAssemblyError(
            f"{origin}: edge id collision with divergent payload/status for {edge.edge_id!r}"
        )


def _compute_graph_id(
    *,
    subject: SurfaceGraphSubject,
    nodes: Mapping[str, SurfaceNode],
    edges: Mapping[str, SurfaceEdge],
) -> str:
    """Deterministic snapshot identity (§D1).

    Hash over the subject + SORTED node/edge ids + their payload hashes. Sorting
    makes the id invariant under the order lenses ran in, while any payload
    change flips a payload hash and thus the graph id.
    """
    subject_repr = _canonical_json(
        {
            "jurisdiction": subject.jurisdiction,
            "work_id": subject.work_id,
            "scope": dict(subject.scope),
            "surface_time": subject.surface_time,
            "source_bundle_hash": subject.source_bundle_hash,
            "language": subject.language,
        }
    )
    node_lines = [f"{nid}={nodes[nid].payload_hash}" for nid in sorted(nodes)]
    edge_lines = [f"{eid}={edges[eid].payload_hash}" for eid in sorted(edges)]
    return _sha256(
        (
            GRAPH_ID_SCHEMA_TAG,
            subject_repr,
            "|".join(node_lines),
            "|".join(edge_lines),
        )
    )


def run_edge_passes(
    graph: LegalSurfaceGraph,
    edge_passes: tuple[SurfaceEdgePass, ...],
) -> LegalSurfaceGraph:
    """Run cross-lens edge passes in declared order and re-assemble (§D5).

    Each pass reads the current graph and emits edge seeds referencing minted
    node ids. Endpoint validation and the firewall apply. The graph_id is
    recomputed because the edge set changed.
    """
    nodes = graph.nodes
    edges: dict[str, SurfaceEdge] = {e.edge_id: e for e in graph.edges}
    # No local index for cross-lens passes: endpoints must already be minted ids.
    empty_index: dict[str, str] = {}
    for edge_pass in edge_passes:
        origin = f"edge_pass {edge_pass.pass_id!r}"
        for seed in edge_pass.run(graph):
            _validate_edge_seed(seed, origin=origin)
            edge = _mint_edge_from_seed(
                seed, nodes=nodes, local_index=empty_index, origin=origin
            )
            _dedup_edge(edges, edge, origin=origin)

    ordered_edges = tuple(edges[eid] for eid in sorted(edges))
    graph_id = _compute_graph_id(subject=graph.subject, nodes=nodes, edges=edges)
    return LegalSurfaceGraph(
        schema=graph.schema,
        graph_id=graph_id,
        subject=graph.subject,
        source_units=graph.source_units,
        lens_runs=graph.lens_runs,
        nodes=nodes,
        edges=ordered_edges,
        build_diagnostics=graph.build_diagnostics,
    )


def assemble_surface_graph(
    *,
    subject: SurfaceGraphSubject,
    source_units: tuple[SourceUnitRef, ...],
    lens_results: tuple[SurfaceLensResult, ...],
    lens_runs: tuple[SurfaceLensRun, ...] = (),
    edge_passes: tuple[SurfaceEdgePass, ...] = (),
    build_diagnostics: tuple[SurfaceDiagnostic, ...] = (),
) -> LegalSurfaceGraph:
    """Assemble a LegalSurfaceGraph from lens results (§D1/§D5).

    Order-independent: lens results may be permuted and the resulting graph_id
    is identical (node/edge ids are minted from stable identity tuples and
    sorted before hashing).
    """
    jurisdiction = subject.jurisdiction
    work_id = subject.work_id

    minted = _assemble_nodes(lens_results, jurisdiction=jurisdiction, work_id=work_id)
    nodes = minted.nodes
    edges = _assemble_edges(lens_results, nodes=nodes, local_index=minted.local_index)

    diagnostics = tuple(minted.diagnostics) + tuple(build_diagnostics)
    ordered_edges = tuple(edges[eid] for eid in sorted(edges))
    graph_id = _compute_graph_id(subject=subject, nodes=nodes, edges=edges)

    graph = LegalSurfaceGraph(
        schema=SCHEMA_TAG,
        graph_id=graph_id,
        subject=subject,
        source_units=source_units,
        lens_runs=lens_runs,
        nodes=nodes,
        edges=ordered_edges,
        build_diagnostics=diagnostics,
    )

    if edge_passes:
        graph = run_edge_passes(graph, edge_passes)
    return graph
