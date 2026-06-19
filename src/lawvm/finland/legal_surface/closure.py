"""Transitive-closure (reachability) queries over the cross-statute corpus graph.

The corpus Legal Surface Graph (``corpus_graph.build_corpus_surface_graph``)
merges per-statute graphs so that the SAME target collapses to one shared
``entity:<id>`` node, and resolved provision-level citations become ``refers_to``
edges (ambiguous ones ``has_candidate``) from a ``reference_resolution`` node
into that shared entity. This module walks that typed reference graph to realize
the "transitive closure of law": *from provision/act X, what is the full set of
law reachable by following typed references, and by what paths.*

This is PURE GRAPH REACHABILITY over surface facts. It invents NO edges and
follows only edges present in the graph. The result is NAVIGATION over surface
facts, never a legal conclusion: a node reached only via ``has_candidate``
(ambiguous) edges is reported as *candidate-reachable*, never asserted-reachable,
so the multiplicative per-hop epistemic decay stays visible.

Direction semantics (read carefully — they are NOT symmetric in the graph):

  * FORWARD — outbound references. From entity X (a work or provision, whose
    work_id is W), the citations that LIVE IN W are the ``reference_resolution``
    nodes whose ``source_ref.work_id == W``; their ``refers_to`` / ``has_candidate``
    edges land on the entities X cites. To take a SECOND forward hop you need the
    target entity's OWN outbound references — which exist in the merged graph
    ONLY if that target statute was included in the corpus slice. So forward
    closure is BOUNDED BY THE CORPUS SLICE: a target whose work is outside the
    slice (no outbound references present) is returned as a FRONTIER node
    (reached, not expanded) — never silently dropped, never conflated with a
    fully expanded node.

  * BACKWARD — incoming citations (``citations_of`` made transitive). From X,
    who cites X; then who cites those citers; and so on. The citers are recovered
    from each citing node's ``source_ref.work_id`` (the work the citation lives
    in), lifted to an act-level entity ``entity:<work_id>`` for the next hop.

Determinism: BFS expands frontier in sorted entity-id order, hop distance is the
BFS layer, and every output list is sorted. Two runs over the same graph yield
identical ``ClosureResult``.

Fail-loud: ``forward_closure`` / ``backward_closure`` raise if ``start_entity_id``
is not a node in the graph (no silent empty result for a typo'd start).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from lawvm.core.legal_surface_assembler import ENTITY_ID_PREFIX, mint_entity_node_id
from lawvm.core.legal_surface_graph import LegalSurfaceGraph

__all__ = [
    "ClosureError",
    "ClosureResult",
    "ReachStep",
    "forward_closure",
    "backward_closure",
]

# Edge kinds this reachability walk follows. ``refers_to`` is an ASSERTED link;
# ``has_candidate`` is an AMBIGUOUS (candidate) link — reachability through it is
# candidate-reachability, reported separately so it is never presented as
# asserted (honest multiplicative per-hop decay).
_ASSERTED_KIND = "refers_to"
_CANDIDATE_KIND = "has_candidate"
_FOLLOWED_KINDS = frozenset({_ASSERTED_KIND, _CANDIDATE_KIND})


class ClosureError(ValueError):
    """The closure query cannot run (e.g. start node absent from the graph)."""


@dataclass(frozen=True, slots=True)
class ReachStep:
    """One edge actually traversed on a shortest path to a reached entity.

    ``edge_kind`` is ``"refers_to"`` (asserted) or ``"has_candidate"``
    (candidate); ``candidate`` is True for the latter so a path's epistemic
    quality is legible edge-by-edge without re-deriving it.
    """

    src_entity: str
    dst_entity: str
    edge_kind: str
    edge_id: str
    candidate: bool


@dataclass(frozen=True, slots=True)
class ClosureResult:
    """Result of a transitive-closure reachability walk over the corpus graph.

    Attributes
    ----------
    start:
        The entity id the walk started from (always present in the graph).
    direction:
        ``"forward"`` (outbound references) or ``"backward"`` (incoming
        citations).
    reached:
        Sorted ``(entity_id, hop_distance)`` pairs for every entity reached
        (excludes ``start``). ``hop_distance`` is the BFS layer (shortest).
    paths:
        For each reached entity, the shortest path from ``start`` as an ordered
        tuple of :class:`ReachStep` (one entry per edge). Sorted by entity id.
    frontier:
        Sorted entity ids that were REACHED but NOT EXPANDED — either outside
        the corpus slice (no outbound references present to continue a forward
        hop) or cut off by ``max_hops``. Frontier ids are a subset of the
        ``reached`` entities; listing them explicitly keeps "reached but its own
        references are unknown to this graph" from being conflated with a fully
        expanded node.
    candidate_reachable:
        Sorted entity ids whose SHORTEST path includes at least one
        ``has_candidate`` (ambiguous) edge — i.e. reachable only by tolerating a
        candidate hop. These are candidate-reachable, NOT asserted-reachable.
    resolution_quality:
        Per-hop accounting of edges traversed by kind, so the multiplicative
        candidate decay is visible. Keys: ``"asserted_edges"`` /
        ``"candidate_edges"`` (totals), and ``"by_hop"`` mapping hop-distance ->
        ``{"asserted": n, "candidate": m}`` (edges entering that hop layer).
    """

    start: str
    direction: str
    reached: tuple[tuple[str, int], ...]
    paths: tuple[tuple[str, tuple[ReachStep, ...]], ...]
    frontier: tuple[str, ...]
    candidate_reachable: tuple[str, ...]
    resolution_quality: dict[str, object] = field(default_factory=dict)


# ── entity / work-id helpers ──────────────────────────────────────────────────


def _work_id_of_entity(graph: LegalSurfaceGraph, entity_id: str) -> str | None:
    """The work id a (work or provision) entity belongs to.

    Prefers the node payload's ``work_id`` (authoritative). Falls back to
    parsing the minted id ``entity:<work_id>[#<tail>]`` when the node carries no
    payload work_id, so a synthetic start id still resolves. Returns ``None`` if
    no work id can be determined.
    """
    node = graph.nodes.get(entity_id)
    if node is not None:
        wid = node.payload.get("work_id")
        if isinstance(wid, str) and wid:
            return wid
    if entity_id.startswith(ENTITY_ID_PREFIX):
        canonical = entity_id[len(ENTITY_ID_PREFIX):]
        work = canonical.split("#", 1)[0]
        return work or None
    return None


# ── adjacency builders (one pass over edges) ─────────────────────────────────


def _forward_adjacency(
    graph: LegalSurfaceGraph,
) -> dict[str, list[tuple[str, str, str]]]:
    """work_id -> list of (dst_entity, edge_kind, edge_id) outbound references.

    A ``refers_to`` / ``has_candidate`` edge's source is a
    ``reference_resolution`` node whose ``source_ref.work_id`` is the work the
    citation lives in; the edge dst is the cited entity. So outbound references
    of work W are exactly the edges whose citing node lives in W. Sorted for
    determinism.
    """
    out: dict[str, list[tuple[str, str, str]]] = {}
    for edge in graph.edges:
        if edge.edge_kind not in _FOLLOWED_KINDS:
            continue
        citing = graph.nodes.get(edge.src)
        if citing is None or citing.source_ref is None:
            continue
        citing_work = citing.source_ref.work_id
        if not citing_work:
            continue
        out.setdefault(citing_work, []).append(
            (edge.dst, edge.edge_kind, edge.edge_id)
        )
    for work in out:
        out[work].sort()
    return out


def _backward_adjacency(
    graph: LegalSurfaceGraph,
) -> dict[str, list[tuple[str, str, str]]]:
    """target_entity -> list of (citer_entity, edge_kind, edge_id).

    Inverse of forward: for each citation edge, the cited entity (dst) is cited
    BY the act-level entity of the work the citing node lives in. The citer is
    lifted to ``entity:<work_id>`` so the next backward hop expands that work's
    incoming citations. Sorted for determinism.
    """
    out: dict[str, list[tuple[str, str, str]]] = {}
    for edge in graph.edges:
        if edge.edge_kind not in _FOLLOWED_KINDS:
            continue
        citing = graph.nodes.get(edge.src)
        if citing is None or citing.source_ref is None:
            continue
        citing_work = citing.source_ref.work_id
        if not citing_work:
            continue
        citer_entity = mint_entity_node_id(citing_work)
        out.setdefault(edge.dst, []).append(
            (citer_entity, edge.edge_kind, edge.edge_id)
        )
    for tgt in out:
        out[tgt].sort()
    return out


# ── the BFS core ─────────────────────────────────────────────────────────────


def _bfs(
    *,
    graph: LegalSurfaceGraph,
    start_entity_id: str,
    max_hops: int | None,
    direction: str,
    neighbours_of,
    has_expansion,
) -> ClosureResult:
    """Deterministic BFS shared by forward/backward closure.

    ``neighbours_of(entity) -> sorted list of (next_entity, edge_kind, edge_id)``
    yields the next-hop entities along followed edges. ``has_expansion(entity)``
    reports whether ``entity`` could be expanded at all in this graph (used to
    classify slice-boundary frontier vs a leaf with genuinely no references —
    both are non-expandable, but only the former is "outside the slice"; we mark
    BOTH as frontier since neither continues, which is the honest statement).
    """
    if start_entity_id not in graph.nodes:
        raise ClosureError(
            f"closure start {start_entity_id!r} is not a node in the corpus "
            f"graph; refusing to return an empty reachability set for an "
            f"unknown start (graph has {len(graph.nodes)} nodes)"
        )
    if max_hops is not None and max_hops < 0:
        raise ClosureError(f"max_hops must be >= 0, got {max_hops!r}")

    # Shortest hop distance + shortest path (first time we pop an entity wins,
    # because BFS by layer). best_path stores the edge list to each entity.
    dist: dict[str, int] = {start_entity_id: 0}
    best_path: dict[str, tuple[ReachStep, ...]] = {start_entity_id: ()}
    candidate_path: dict[str, bool] = {start_entity_id: False}
    frontier: set[str] = set()
    # Per-hop edge accounting: hop-layer -> [asserted, candidate].
    by_hop: dict[int, list[int]] = {}
    total_asserted = 0
    total_candidate = 0

    queue: deque[str] = deque([start_entity_id])
    while queue:
        cur = queue.popleft()
        cur_dist = dist[cur]

        # max_hops boundary: reached at the cap is frontier (not expanded).
        if max_hops is not None and cur_dist >= max_hops:
            if cur != start_entity_id or max_hops == 0:
                frontier.add(cur)
            continue

        neighbours = neighbours_of(cur)
        if not neighbours:
            # Nothing to expand: a leaf or an outside-slice target. Frontier
            # (but never the start unless it truly has no outbound/inbound).
            if cur != start_entity_id:
                frontier.add(cur)
            continue

        for nxt, edge_kind, edge_id in neighbours:
            is_candidate = edge_kind == _CANDIDATE_KIND
            if nxt not in dist:
                dist[nxt] = cur_dist + 1
                step = ReachStep(
                    src_entity=cur,
                    dst_entity=nxt,
                    edge_kind=edge_kind,
                    edge_id=edge_id,
                    candidate=is_candidate,
                )
                best_path[nxt] = best_path[cur] + (step,)
                candidate_path[nxt] = candidate_path[cur] or is_candidate
                hop = cur_dist + 1
                slot = by_hop.setdefault(hop, [0, 0])
                if is_candidate:
                    slot[1] += 1
                    total_candidate += 1
                else:
                    slot[0] += 1
                    total_asserted += 1
                queue.append(nxt)

    # Any reached entity that is not itself expandable in this graph is frontier
    # (outside-slice target or a leaf). Recompute over reached set so entities
    # found but never dequeued-as-expandable are captured.
    for ent in dist:
        if ent == start_entity_id:
            continue
        if not has_expansion(ent):
            frontier.add(ent)

    reached = tuple(
        sorted(
            ((ent, dist[ent]) for ent in dist if ent != start_entity_id),
            key=lambda pair: (pair[1], pair[0]),
        )
    )
    paths = tuple(
        sorted(
            (
                (ent, best_path[ent])
                for ent in dist
                if ent != start_entity_id
            ),
            key=lambda pair: pair[0],
        )
    )
    candidate_reachable = tuple(
        sorted(
            ent
            for ent in dist
            if ent != start_entity_id and candidate_path.get(ent, False)
        )
    )
    resolution_quality: dict[str, object] = {
        "asserted_edges": total_asserted,
        "candidate_edges": total_candidate,
        "by_hop": {
            hop: {"asserted": slot[0], "candidate": slot[1]}
            for hop, slot in sorted(by_hop.items())
        },
    }
    return ClosureResult(
        start=start_entity_id,
        direction=direction,
        reached=reached,
        paths=paths,
        frontier=tuple(sorted(frontier)),
        candidate_reachable=candidate_reachable,
        resolution_quality=resolution_quality,
    )


# ── public API ─────────────────────────────────────────────────────────────


def forward_closure(
    graph: LegalSurfaceGraph,
    start_entity_id: str,
    *,
    max_hops: int | None = None,
) -> ClosureResult:
    """Forward (outbound-reference) transitive closure from ``start_entity_id``.

    From the start entity (whose work is W), follow the ``refers_to`` (asserted)
    and ``has_candidate`` (candidate) edges that LIVE IN W to the entities it
    cites, then repeat from each cited entity's own work. Bounded by the corpus
    slice: a cited entity whose work has no outbound references in this graph is
    returned in ``frontier`` (reached, not expanded) — outside-slice targets are
    never silently dropped and never conflated with fully expanded nodes.

    Reachability through a ``has_candidate`` edge is candidate-reachability
    (see ``candidate_reachable`` / ``resolution_quality``), never asserted.

    Raises :class:`ClosureError` if ``start_entity_id`` is not in the graph.
    """
    adjacency = _forward_adjacency(graph)

    def neighbours_of(entity: str) -> list[tuple[str, str, str]]:
        work = _work_id_of_entity(graph, entity)
        if work is None:
            return []
        return adjacency.get(work, [])

    def has_expansion(entity: str) -> bool:
        work = _work_id_of_entity(graph, entity)
        return bool(work and adjacency.get(work))

    return _bfs(
        graph=graph,
        start_entity_id=start_entity_id,
        max_hops=max_hops,
        direction="forward",
        neighbours_of=neighbours_of,
        has_expansion=has_expansion,
    )


def backward_closure(
    graph: LegalSurfaceGraph,
    start_entity_id: str,
    *,
    max_hops: int | None = None,
) -> ClosureResult:
    """Backward (incoming-citation) transitive closure: transitive ``citations_of``.

    From the start entity, find who cites it (the act-level entity of each citing
    work), then who cites those citers, and so on. Citers are recovered from each
    citing node's ``source_ref.work_id`` (the work the citation lives in).

    Reachability through a ``has_candidate`` edge is candidate-reachability,
    reported separately. An entity with no incoming citations in this graph is
    returned in ``frontier``.

    Raises :class:`ClosureError` if ``start_entity_id`` is not in the graph.
    """
    adjacency = _backward_adjacency(graph)

    def neighbours_of(entity: str) -> list[tuple[str, str, str]]:
        return adjacency.get(entity, [])

    def has_expansion(entity: str) -> bool:
        return bool(adjacency.get(entity))

    return _bfs(
        graph=graph,
        start_entity_id=start_entity_id,
        max_hops=max_hops,
        direction="backward",
        neighbours_of=neighbours_of,
        has_expansion=has_expansion,
    )
