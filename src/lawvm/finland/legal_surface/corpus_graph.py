"""Lift the Legal Surface Graph from per-statute to a CROSS-STATUTE corpus graph.

Each ``build_legal_surface_graph`` call produces ONE statute's graph: its
reference nodes carry a resolved target ``work_id`` / provision, but those
targets are *payload*, not navigable EDGES into a shared network. This module is
the keystone that realizes "semantic links across statutes": it builds each
statute's surface graph and MERGES them into one graph over a ``corpus_slice``
subject, where the SAME target across statutes collapses to ONE shared entity
node — so a ``refers_to`` edge from statute A and one from statute C land on the
same node, and "what cites this act/provision" becomes a graph query.

Two mechanisms make the network:

  * MERGE (``build_corpus_surface_graph``) — entity nodes are minted by the core
    assembler as ``entity:<canonical id>``, so they are already corpus-stable:
    merging two statute graphs that both resolve to ``711/2022`` yields ONE
    ``entity:711/2022`` node. Merge reuses the assembler's discipline: same
    node_id with a divergent payload_hash FAILS LOUD (never a silent overwrite).

  * CROSS-STATUTE EDGE PASS (``CorpusReferenceEdgePass``) — the per-statute
    ReferenceLens already asserts a statute-level ``refers_to`` to a
    ``legal_work_entity``. This pass adds the PROVISION-level target: for each
    resolved ``reference_resolution`` whose citing ``reference_expr`` names a
    concrete provision (e.g. ``711/2022/7``), it ensures a
    ``legal_address_entity`` node for that provision and asserts a ``refers_to``
    edge into it. Ambiguous resolutions get ``has_candidate`` (no asserted
    target); open / statute_only resolutions are left as-is. The pass NEVER
    invents a target id — it only promotes a target the citing text already
    committed (fail-loud by omission).

Firewall (§D7) is inherited: every merged node/edge is surface_only, and the
edge pass runs through the assembler's firewall-enforcing minting path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from lawvm.core.legal_surface_assembler import (
    SurfaceAssemblyError,
    _compute_graph_id,
    mint_entity_node_id,
    run_edge_passes,
)
from lawvm.core.legal_surface_graph import (
    SCHEMA_TAG,
    LegalSurfaceGraph,
    SourceUnitRef,
    SurfaceDiagnostic,
    SurfaceEdge,
    SurfaceGraphSubject,
    SurfaceLensRun,
    SurfaceNode,
)
from lawvm.core.legal_surface_lens import SurfaceEdgeSeed
from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph
from lawvm.finland.legal_surface.lenses.references import ReferenceLens

# Rule ids for the cross-statute edges this module asserts (stable witnesses,
# distinct from the per-statute reference lens rules so provenance is legible).
_RULE_CORPUS_REFERS_TO = "fi.corpus.v0.refers_to_address"
_RULE_CORPUS_HAS_CANDIDATE = "fi.corpus.v0.has_candidate_address"

# Entity node kinds whose node_id is ``entity:<discriminator>`` — corpus-stable
# by construction, so they dedup across statute graphs on merge.
_ENTITY_KINDS = frozenset(
    {
        "source_unit",
        "legal_work_entity",
        "legal_address_entity",
        "actor_entity",
        "term_symbol_entity",
    }
)


class _StoreLike(Protocol):
    def read_oracle(self, sid: str) -> bytes | None: ...
    def read_source(self, sid: str) -> bytes | None: ...
    def read_amendment(self, sid: str) -> bytes | None: ...


def _read_body(store: _StoreLike, sid: str) -> bytes | None:
    """Best available body XML for surface-graph building (oracle preferred).

    The reference surface lives in the consolidated body, so prefer the oracle;
    fall back to enacted source or the amendment act so non-consolidated
    statutes still contribute. Archive-only reads — no replay. Mirrors the
    ``surface-lints`` body-selection policy.
    """
    try:
        xb = store.read_oracle(sid)
    except Exception:  # noqa: BLE001 — oracle absence is normal, fall back
        xb = None
    if xb:
        return xb
    return store.read_source(sid) or store.read_amendment(sid)


# ── address-entity identity ──────────────────────────────────────────────────


def _provision_address_id(target_id: str, target_provision_ref: str) -> str | None:
    """Canonical id for a provision-level target, or ``None`` for statute-level.

    ``target_provision_ref`` is ``ProvisionRef.serialized()``: the statute id
    followed by ``/<section>[/<subsection>[/<item>]]`` when a provision is named
    (e.g. ``"711/2022/7/3"``), or just the statute id when only the act is cited.

    Returns the canonical address id ``"<target_id>#<provision-tail>"`` when a
    provision tail is present (so it is a distinct, navigable address node), else
    ``None`` (the citation is statute-level — the lens already owns the
    work-entity edge, nothing to promote).

    Fail-loud: a provision ref that does not begin with the resolved target id is
    a contradiction (the resolver said one act, the surface another) and raises,
    never silently fabricating an address.
    """
    if not target_provision_ref:
        return None
    if target_provision_ref == target_id:
        return None
    prefix = target_id + "/"
    if not target_provision_ref.startswith(prefix):
        raise SurfaceAssemblyError(
            "corpus reference edge: resolved target "
            f"{target_id!r} disagrees with citing provision ref "
            f"{target_provision_ref!r}; refusing to fabricate an address node"
        )
    tail = target_provision_ref[len(prefix):]
    if not tail:
        return None
    return f"{target_id}#{tail}"


# ── the cross-statute edge pass ──────────────────────────────────────────────


@dataclass(frozen=True)
class CorpusReferenceEdgePass:
    """Cross-statute reference edges: provision-level targets into shared nodes.

    Promotes each resolved provision-level citation to a ``refers_to`` edge into
    a shared ``legal_address_entity``. The entity node is minted by the assembler
    as ``entity:<address id>`` — so two statutes citing ``711/2022/7`` land on
    the SAME node, which is the navigable-network payoff. The pass emits only
    EDGE seeds; the address-entity node is created by referencing its minted id
    as the edge ``dst`` (the assembler validates the endpoint exists — so this
    pass also seeds the node by appending it first, below).

    Because a ``SurfaceEdgePass`` may only emit edge seeds, the address-entity
    nodes are materialized by :func:`_inject_address_entities` BEFORE the pass
    runs (the assembler then resolves the edge dst to the already-present node).
    """

    pass_id: str = "fi.corpus.reference_edges.v0"
    reads_node_kinds: tuple[str, ...] = (
        "reference_resolution",
        "reference_expr",
        "legal_address_entity",
    )
    emits_edge_kinds: tuple[str, ...] = ("refers_to", "has_candidate")

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceEdgeSeed, ...]:
        seeds: list[SurfaceEdgeSeed] = []
        expr_by_resolution = _resolution_to_expr(graph)
        for node in graph.nodes.values():
            if node.node_kind != "reference_resolution":
                continue
            target = _resolved_provision_target(node, graph, expr_by_resolution)
            if target is None:
                continue
            address_id, status = target
            entity_node_id = mint_entity_node_id(address_id)
            if entity_node_id not in graph.nodes:
                # Defensive: the node must have been injected first. Fail loud
                # rather than asserting an edge into a missing endpoint.
                raise SurfaceAssemblyError(
                    f"{self.pass_id}: address entity {entity_node_id!r} was not "
                    f"injected before the edge pass ran"
                )
            edge_kind = "refers_to" if status == "asserted" else "has_candidate"
            rule_id = (
                _RULE_CORPUS_REFERS_TO
                if status == "asserted"
                else _RULE_CORPUS_HAS_CANDIDATE
            )
            seeds.append(
                SurfaceEdgeSeed(
                    edge_kind=edge_kind,
                    src_local=node.node_id,
                    dst_local=entity_node_id,
                    rule_id=rule_id,
                    status=status,
                    payload={"address_id": address_id},
                )
            )
        return tuple(seeds)


def _resolution_to_expr(graph: LegalSurfaceGraph) -> Mapping[str, str]:
    """Map each ``reference_resolution`` node id to its ``reference_expr`` id.

    The per-statute lens emits a ``resolution_of`` edge (resolution -> expr) for
    every mention, so the paired expr (which carries the ``target_provision_ref``
    the resolution status applies to) is recovered from that edge.
    """
    out: dict[str, str] = {}
    for edge in graph.edges:
        if edge.edge_kind == "resolution_of":
            out[edge.src] = edge.dst
    return out


def _resolved_provision_target(
    resolution: SurfaceNode,
    graph: LegalSurfaceGraph,
    expr_by_resolution: Mapping[str, str],
) -> tuple[str, str] | None:
    """The (address_id, edge_status) a resolution promotes, or ``None``.

    ``edge_status`` is ``"asserted"`` for a single resolved target and
    ``"candidate"`` for an ambiguous one. Open / statute_only / broken
    resolutions, and statute-level (no provision) targets, return ``None`` (left
    as-is; the lens already owns any statute-level work edge).
    """
    res_status = resolution.payload.get("resolution_status")
    expr_id = expr_by_resolution.get(resolution.node_id)
    if expr_id is None:
        return None
    expr = graph.nodes.get(expr_id)
    if expr is None:
        return None
    target_id = expr.payload.get("target_id")
    target_provision_ref = expr.payload.get("target_provision_ref")
    if not isinstance(target_id, str) or not isinstance(target_provision_ref, str):
        return None

    if res_status in ("resolved", "unchanged"):
        address_id = _provision_address_id(target_id, target_provision_ref)
        if address_id is None:
            return None
        return address_id, "asserted"

    if res_status == "ambiguous":
        # An ambiguous resolution has no single target id; only promote a
        # provision-level candidate when the citing surface itself names a
        # concrete provision under the (textual) target id.
        address_id = _provision_address_id(target_id, target_provision_ref)
        if address_id is None:
            return None
        return address_id, "candidate"

    return None


def _address_entity_node(graph: LegalSurfaceGraph, address_id: str) -> SurfaceNode:
    """A ``legal_address_entity`` handle for a provision target (firewall-safe).

    Minted directly (not via a lens seed) because it is a pure entity handle the
    cross-statute pass points at; the id is ``entity:<address_id>`` so it dedups
    across statutes exactly like the assembler's entity minting.
    """
    statute_id, _, tail = address_id.partition("#")
    payload: dict[str, object] = {
        "work_id": statute_id,
        "address": tail,
        "address_id": address_id,
    }
    return SurfaceNode(
        node_id=mint_entity_node_id(address_id),
        node_kind="legal_address_entity",
        authority_role="entity_handle",
        jurisdiction=graph.subject.jurisdiction,
        source_ref=None,
        lens_id=None,
        rule_id=_RULE_CORPUS_REFERS_TO,
        status="present",
        payload_hash=_address_payload_hash(payload),
        payload=payload,
    )


def _address_payload_hash(payload: Mapping[str, object]) -> str:
    from lawvm.core.legal_surface_assembler import compute_payload_hash

    return compute_payload_hash(payload)


def _inject_address_entities(graph: LegalSurfaceGraph) -> LegalSurfaceGraph:
    """Add the ``legal_address_entity`` nodes the edge pass will point at.

    Scans resolved/ambiguous provision-level resolutions, mints one shared
    address entity per distinct provision target, and returns a graph with those
    nodes merged in. Same-id collision with a divergent payload FAILS LOUD
    (assembler discipline). The edge pass then runs against this enriched graph.
    """
    nodes: dict[str, SurfaceNode] = dict(graph.nodes)
    expr_by_resolution = _resolution_to_expr(graph)
    added = False
    for node in graph.nodes.values():
        if node.node_kind != "reference_resolution":
            continue
        target = _resolved_provision_target(node, graph, expr_by_resolution)
        if target is None:
            continue
        address_id, _ = target
        entity = _address_entity_node(graph, address_id)
        _merge_node(nodes, entity)
        added = True
    if not added:
        return graph
    graph_id = _compute_graph_id(
        subject=graph.subject,
        nodes=nodes,
        edges={e.edge_id: e for e in graph.edges},
    )
    return LegalSurfaceGraph(
        schema=graph.schema,
        graph_id=graph_id,
        subject=graph.subject,
        source_units=graph.source_units,
        lens_runs=graph.lens_runs,
        nodes=nodes,
        edges=graph.edges,
        build_diagnostics=graph.build_diagnostics,
    )


# ── merge ─────────────────────────────────────────────────────────────────────


def _merge_node(nodes: dict[str, SurfaceNode], node: SurfaceNode) -> None:
    """Merge one node into the union, reusing the assembler's dedup discipline.

    Identical node_id with the SAME payload_hash is an idempotent duplicate
    (entity nodes that the same target produced from two statute graphs).
    Divergent payload for the same id FAILS LOUD (never a silent overwrite).
    """
    existing = nodes.get(node.node_id)
    if existing is None:
        nodes[node.node_id] = node
        return
    if existing.payload_hash != node.payload_hash:
        raise SurfaceAssemblyError(
            f"corpus merge: node id collision with divergent payload for "
            f"{node.node_id!r} (kind={node.node_kind!r}): "
            f"{existing.payload_hash} != {node.payload_hash}"
        )


def _merge_edge(edges: dict[str, SurfaceEdge], edge: SurfaceEdge) -> None:
    existing = edges.get(edge.edge_id)
    if existing is None:
        edges[edge.edge_id] = edge
        return
    if existing.payload_hash != edge.payload_hash or existing.status != edge.status:
        raise SurfaceAssemblyError(
            f"corpus merge: edge id collision with divergent payload/status for "
            f"{edge.edge_id!r} (kind={edge.edge_kind!r})"
        )


# ── public API ─────────────────────────────────────────────────────────────


def build_corpus_surface_graph(
    statute_ids: tuple[str, ...] | list[str],
    store: _StoreLike,
    *,
    statute_registry: object | None = None,
    eu_registry: object | None = None,
    surface_time: str | None = None,
) -> LegalSurfaceGraph:
    """Build ONE cross-statute Legal Surface Graph over a ``corpus_slice``.

    Builds each statute's surface graph (reference lens only, for speed),
    merges the node maps and edge sets into one union — entity nodes pointing at
    the same target collapse to a single shared node (corpus-stable
    ``entity:<id>`` minting); divergent same-id payloads FAIL LOUD — then runs
    the cross-statute reference edge pass so resolved provision-level citations
    become ``refers_to`` edges into shared ``legal_address_entity`` nodes. The
    ``graph_id`` is recomputed over the union.

    Pass ``statute_registry`` / ``eu_registry`` to enable target resolution;
    without them the per-statute graphs carry no resolved targets and the corpus
    graph has only the structural (intra-statute) edges.
    """
    ids = list(statute_ids)
    merged_nodes: dict[str, SurfaceNode] = {}
    merged_edges: dict[str, SurfaceEdge] = {}
    source_units: list[SourceUnitRef] = []
    lens_runs: list[SurfaceLensRun] = []
    diagnostics: list[SurfaceDiagnostic] = []
    built_ids: list[str] = []

    for sid in ids:
        xb = _read_body(store, sid)
        if not xb:
            diagnostics.append(
                SurfaceDiagnostic(
                    code="corpus.no_body",
                    severity="info",
                    message=f"statute {sid!r} has no body XML in the store; skipped",
                )
            )
            continue
        statute_graph = build_legal_surface_graph(
            xb,
            sid,
            statute_registry=statute_registry,
            eu_registry=eu_registry,
            surface_time=surface_time,
            lenses=(ReferenceLens(),),
        )
        for node in statute_graph.nodes.values():
            _merge_node(merged_nodes, node)
        for edge in statute_graph.edges:
            _merge_edge(merged_edges, edge)
        source_units.extend(statute_graph.source_units)
        lens_runs.extend(statute_graph.lens_runs)
        diagnostics.extend(statute_graph.build_diagnostics)
        built_ids.append(sid)

    subject = SurfaceGraphSubject(
        jurisdiction="fi",
        work_id=None,
        scope={"kind": "corpus_slice", "statute_ids": tuple(built_ids)},
        surface_time=surface_time,
        source_bundle_hash=_corpus_bundle_hash(built_ids),
        language="fi",
    )
    ordered_edges = tuple(merged_edges[eid] for eid in sorted(merged_edges))
    graph_id = _compute_graph_id(
        subject=subject, nodes=merged_nodes, edges=merged_edges
    )
    merged = LegalSurfaceGraph(
        schema=SCHEMA_TAG,
        graph_id=graph_id,
        subject=subject,
        source_units=tuple(source_units),
        lens_runs=tuple(lens_runs),
        nodes=merged_nodes,
        edges=ordered_edges,
        build_diagnostics=tuple(diagnostics),
    )

    # Materialize shared provision-target entity nodes, then assert the
    # cross-statute reference edges into them via the firewall-enforcing path.
    merged = _inject_address_entities(merged)
    return run_edge_passes(merged, (CorpusReferenceEdgePass(),))


def _corpus_bundle_hash(statute_ids: list[str]) -> str:
    import hashlib

    h = hashlib.sha256()
    for sid in statute_ids:
        h.update(sid.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


# ── navigation query ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Citation:
    """One incoming citation of a target entity (the navigable-network payoff)."""

    edge_id: str
    edge_kind: str  # "refers_to" (asserted) | "has_candidate"
    status: str
    citing_node_id: str  # the reference_resolution node that cites the target
    citing_work_id: str | None  # the statute the citation lives in


def citations_of(
    graph: LegalSurfaceGraph, target_entity_id: str
) -> list[Citation]:
    """All citations pointing at ``target_entity_id`` ("what cites this?").

    ``target_entity_id`` is a minted entity node id (``entity:<work_id>`` for an
    act, ``entity:<work_id>#<provision>`` for a provision). Returns every
    ``refers_to`` / ``has_candidate`` edge whose ``dst`` is that node, with the
    citing statute resolved from the citing node's source anchor — so the result
    answers "which statutes (and where) cite this provision/act". Sorted by
    edge_id for determinism.
    """
    out: list[Citation] = []
    for edge in graph.edges:
        if edge.dst != target_entity_id:
            continue
        if edge.edge_kind not in ("refers_to", "has_candidate"):
            continue
        citing = graph.nodes.get(edge.src)
        citing_work_id: str | None = None
        if citing is not None and citing.source_ref is not None:
            citing_work_id = citing.source_ref.work_id
        out.append(
            Citation(
                edge_id=edge.edge_id,
                edge_kind=edge.edge_kind,
                status=edge.status,
                citing_node_id=edge.src,
                citing_work_id=citing_work_id,
            )
        )
    out.sort(key=lambda c: c.edge_id)
    return out
