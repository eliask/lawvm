"""``lawvm corpus-graph`` — exportable, claim-backed cross-statute surface graph.

LawVM already MERGES per-statute Legal Surface Graphs into ONE cross-statute
network where the same cited target collapses to a single shared entity node
(``lawvm.finland.legal_surface.corpus_graph.build_corpus_surface_graph``): a
``refers_to`` edge from statute A and one from statute C land on the SAME
``legal_address_entity`` node, so "what cites this act/provision" becomes a graph
query. That merge is fail-loud (same node_id + divergent payload_hash RAISES),
runs every cross-statute edge through the assembler's firewall-enforcing minting
path (every node/edge is ``surface_only`` — NEVER legal authority), and
tag-don't-guess (an ambiguous resolution becomes ``has_candidate``, never an
invented target). Until now that graph was consumed ONLY by ``corpus_lints`` —
there was no exported artifact and it was not a declared claim.

This tool turns the corpus graph into an EXPORTABLE artifact: it builds the graph
over a DECLARED corpus slice (an explicit id list OR a ``--limit`` prefix of the
corpus — never a silent truncation), serializes the node set + edge set (each edge
carrying ``edge_kind``, endpoints, provenance, resolution status, and the
``surface_only`` firewall flag) plus a census (node-kind / edge-kind counts, the
cross-statute interlink-fabric subset, the resolution-status breakdown, and the
count of genuinely inter-statute reference edges), and prints the census. The
artifact is deterministic (nodes/edges sorted by id; counts sorted).

Backed by ``CLAIM_LEGAL_SURFACE_GRAPH`` (``lawvm.fi.legal_surface_graph.v1``).

HONESTY BOUNDARY (v1). The corpus graph merges the edge families the per-statute
reference + anaphora lenses produce (the cross-statute ``refers_to`` /
``has_candidate`` backbone, plus the intra-statute structural edges those lenses
emit). The NEWER typed relation families — derivation edges, EU transposition
edges, definition-use edges, dangling-reference status — are NOT yet merged into
this graph; they are the DECLARED v2 extension. Resolution is recall-bounded (an
open / statute_only mention stays as-is, never promoted); the graph is an AS-OF
(``surface_time``) surface projection over the DECLARED slice, never a legal
conclusion. Reuses the store / registry / body-loading helpers from
``surface_lints`` so all the surface tools agree on data sourcing. Archive-only
reads; no replay.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass

from lawvm.core.legal_surface_graph import LegalSurfaceGraph
from lawvm.finland.legal_surface.corpus_graph import build_corpus_surface_graph
from lawvm.tools.surface_lints import _get_store, _load_registries

# The export schema tag (a stable identity prefix for the artifact body).
EXPORT_SCHEMA = "lawvm.corpus_surface_graph_export.v1"

# Edge kinds that constitute the CROSS-STATUTE interlink fabric in the corpus
# graph (the navigable-network payoff), as opposed to the intra-statute
# structural edges. ``refers_to`` / ``has_candidate`` are the corpus backbone;
# ``resolution_of`` joins a resolution to its expr WITHIN a statute, so it is
# intra-statute and excluded.
_INTERLINK_EDGE_KINDS = frozenset({"refers_to", "has_candidate"})


# ── typed export rows ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CorpusNodeRow:
    """One exported node of the corpus surface graph (a surface fact)."""

    node_id: str
    node_kind: str
    authority_role: str
    status: str
    work_id: str | None
    payload_hash: str
    surface_only: bool

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "node_kind": self.node_kind,
            "authority_role": self.authority_role,
            "status": self.status,
            "work_id": self.work_id,
            "payload_hash": self.payload_hash,
            "surface_only": self.surface_only,
        }


@dataclass(frozen=True, slots=True)
class CorpusEdgeRow:
    """One exported edge of the corpus surface graph (a surface fact).

    ``cross_statute`` is True iff the citing node's source work differs from the
    target entity's work — the genuine inter-statute link. ``surface_only`` is the
    authority-firewall flag (always True; the assembler refuses any other value).
    """

    edge_id: str
    edge_kind: str
    src: str
    dst: str
    rule_id: str
    status: str
    citing_work_id: str | None
    target_work_id: str | None
    cross_statute: bool
    payload_hash: str
    surface_only: bool

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "edge_id": self.edge_id,
            "edge_kind": self.edge_kind,
            "src": self.src,
            "dst": self.dst,
            "rule_id": self.rule_id,
            "status": self.status,
            "citing_work_id": self.citing_work_id,
            "target_work_id": self.target_work_id,
            "cross_statute": self.cross_statute,
            "payload_hash": self.payload_hash,
            "surface_only": self.surface_only,
        }


@dataclass(frozen=True, slots=True)
class CorpusGraphCensus:
    """The census over the exported corpus graph (deterministic counts)."""

    nodes_total: int
    edges_total: int
    node_kinds: dict[str, int]
    edge_kinds: dict[str, int]
    interlink_edges: dict[str, int]
    interlink_edges_total: int
    cross_statute_reference_edges: int
    resolution_status: dict[str, int]
    firewall_holds: bool

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "nodes_total": self.nodes_total,
            "edges_total": self.edges_total,
            "node_kinds": dict(self.node_kinds),
            "edge_kinds": dict(self.edge_kinds),
            "interlink_edges": dict(self.interlink_edges),
            "interlink_edges_total": self.interlink_edges_total,
            "cross_statute_reference_edges": self.cross_statute_reference_edges,
            "resolution_status": dict(self.resolution_status),
            "firewall_holds": self.firewall_holds,
        }


@dataclass(frozen=True, slots=True)
class CorpusSurfaceGraphExport:
    """``lawvm.corpus_surface_graph_export.v1`` — the exported corpus graph.

    A typed projection of the merged cross-statute surface graph: the declared
    slice it was built over, the graph identity, the node + edge rows, and the
    census. Surface-only — never a legal conclusion (the firewall holds and is
    re-asserted in the census).
    """

    claim_id: str
    graph_id: str
    slice_statute_ids: tuple[str, ...]
    surface_time: str | None
    nodes: tuple[CorpusNodeRow, ...]
    edges: tuple[CorpusEdgeRow, ...]
    census: CorpusGraphCensus

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "schema": EXPORT_SCHEMA,
            "claim_id": self.claim_id,
            "graph_id": self.graph_id,
            "slice_statute_ids": list(self.slice_statute_ids),
            "surface_time": self.surface_time,
            "census": self.census.to_canonical_dict(),
            "nodes": [n.to_canonical_dict() for n in self.nodes],
            "edges": [e.to_canonical_dict() for e in self.edges],
        }


# ── build the export from a merged corpus graph ──────────────────────────────


_CLAIM_ID = "lawvm.fi.legal_surface_graph.v1"


def _firewall_holds(graph: LegalSurfaceGraph) -> bool:
    for node in graph.nodes.values():
        if not node.surface_only or node.replay_authorized:
            return False
    for edge in graph.edges:
        if not edge.surface_only or edge.replay_authorized:
            return False
    return True


def _node_work_id(node) -> str | None:
    """Best work-id discriminator for a node (source anchor or payload work_id)."""
    if node.source_ref is not None and node.source_ref.work_id is not None:
        return node.source_ref.work_id
    work = node.payload.get("work_id")
    return work if isinstance(work, str) else None


def _resolution_status_census(graph: LegalSurfaceGraph) -> dict[str, int]:
    c: Counter[str] = Counter()
    for node in graph.nodes.values():
        if node.node_kind == "reference_resolution":
            c[str(node.payload.get("resolution_status", "unknown"))] += 1
    return dict(sorted(c.items()))


def build_export(graph: LegalSurfaceGraph) -> CorpusSurfaceGraphExport:
    """Project a merged corpus surface graph into the typed export artifact.

    Deterministic: node rows sorted by node_id, edge rows by edge_id, every count
    map sorted by key. The ``cross_statute`` flag on each reference/candidate edge
    is computed from the citing node's source work vs the target entity's work.
    """
    node_rows = tuple(
        CorpusNodeRow(
            node_id=node.node_id,
            node_kind=node.node_kind,
            authority_role=str(node.authority_role),
            status=str(node.status),
            work_id=_node_work_id(node),
            payload_hash=node.payload_hash,
            surface_only=node.surface_only,
        )
        for node in sorted(graph.nodes.values(), key=lambda n: n.node_id)
    )

    edge_rows: list[CorpusEdgeRow] = []
    cross_statute_reference_edges = 0
    for edge in sorted(graph.edges, key=lambda e: e.edge_id):
        src = graph.nodes.get(edge.src)
        dst = graph.nodes.get(edge.dst)
        citing_work = _node_work_id(src) if src is not None else None
        target_work = _node_work_id(dst) if dst is not None else None
        cross = (
            edge.edge_kind in _INTERLINK_EDGE_KINDS
            and citing_work is not None
            and target_work is not None
            and citing_work != target_work
        )
        if cross:
            cross_statute_reference_edges += 1
        edge_rows.append(
            CorpusEdgeRow(
                edge_id=edge.edge_id,
                edge_kind=edge.edge_kind,
                src=edge.src,
                dst=edge.dst,
                rule_id=edge.rule_id,
                status=edge.status,
                citing_work_id=citing_work,
                target_work_id=target_work,
                cross_statute=cross,
                payload_hash=edge.payload_hash,
                surface_only=edge.surface_only,
            )
        )

    node_kinds = Counter(n.node_kind for n in graph.nodes.values())
    edge_kinds = Counter(e.edge_kind for e in graph.edges)
    interlink = {k: v for k, v in edge_kinds.items() if k in _INTERLINK_EDGE_KINDS}

    census = CorpusGraphCensus(
        nodes_total=len(graph.nodes),
        edges_total=len(graph.edges),
        node_kinds=dict(sorted(node_kinds.items())),
        edge_kinds=dict(sorted(edge_kinds.items())),
        interlink_edges=dict(sorted(interlink.items())),
        interlink_edges_total=sum(interlink.values()),
        cross_statute_reference_edges=cross_statute_reference_edges,
        resolution_status=_resolution_status_census(graph),
        firewall_holds=_firewall_holds(graph),
    )

    raw_slice = graph.subject.scope.get("statute_ids", ())
    slice_ids: tuple[str, ...] = (
        tuple(str(s) for s in raw_slice)
        if isinstance(raw_slice, (tuple, list))
        else ()
    )
    return CorpusSurfaceGraphExport(
        claim_id=_CLAIM_ID,
        graph_id=graph.graph_id,
        slice_statute_ids=slice_ids,
        surface_time=graph.subject.surface_time,
        nodes=node_rows,
        edges=tuple(edge_rows),
        census=census,
    )


# ── slice resolution (DECLARED, never a silent truncation) ───────────────────


def _resolve_slice(store, ids_arg: str | None, limit: int) -> list[str]:
    """The DECLARED corpus slice: an explicit id list OR a ``--limit`` prefix.

    ``--ids`` (comma-separated) takes precedence and is used verbatim. Otherwise
    the first ``--limit`` statute ids of the corpus are used (limit > 0 required:
    the full 3545-statute build is heavy, so the scope MUST be declared — there is
    no silent "all" default).
    """
    if ids_arg:
        return [s.strip() for s in ids_arg.split(",") if s.strip()]
    if limit <= 0:
        raise SystemExit(
            "corpus-graph: declare a slice — pass --ids <a,b,c> or --limit N "
            "(the corpus build is heavy; the scope must be explicit, never a "
            "silent full-corpus truncation)"
        )
    return store.list_statute_ids()[:limit]


# ── census printing ───────────────────────────────────────────────────────────


def _print_census(export: CorpusSurfaceGraphExport) -> None:
    out = sys.stdout.write
    c = export.census
    out(f"corpus-graph  (fi)  claim={export.claim_id}\n")
    out(f"  graph_id                {export.graph_id}\n")
    out(f"  surface_time            {export.surface_time}\n")
    out(f"  slice statutes          {len(export.slice_statute_ids)}\n")
    out(f"  firewall_holds          {c.firewall_holds}\n")
    out(f"  nodes / edges           {c.nodes_total} / {c.edges_total}\n")
    out("  node kinds:\n")
    for k, v in c.node_kinds.items():
        out(f"    {v:>8}  {k}\n")
    out("  edge kinds:\n")
    for k, v in c.edge_kinds.items():
        tag = "  *interlink" if k in _INTERLINK_EDGE_KINDS else ""
        out(f"    {v:>8}  {k}{tag}\n")
    out(f"  interlink edges         {c.interlink_edges_total} "
        f"(refers_to / has_candidate fabric)\n")
    out(f"  cross-statute ref edges {c.cross_statute_reference_edges} "
        f"(citing work != target work)\n")
    if c.resolution_status:
        out("  reference resolution:\n")
        for k, v in c.resolution_status.items():
            out(f"    {v:>8}  {k}\n")


# ── entrypoint ────────────────────────────────────────────────────────────────


def run(args) -> None:
    ids_arg = getattr(args, "ids", None)
    limit = getattr(args, "limit", 0) or 0
    out_path = getattr(args, "out", None)
    as_json = getattr(args, "json", False)
    surface_time = getattr(args, "surface_time", None)

    store = _get_store()
    statute_registry, eu_registry = _load_registries()
    slice_ids = _resolve_slice(store, ids_arg, limit)

    graph = build_corpus_surface_graph(
        slice_ids,
        store,
        statute_registry=statute_registry,
        eu_registry=eu_registry,
        surface_time=surface_time,
    )
    export = build_export(graph)
    payload = export.to_canonical_dict()

    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        sys.stderr.write(
            f"corpus-graph: wrote {export.census.nodes_total} nodes / "
            f"{export.census.edges_total} edges to {out_path}\n"
        )

    if as_json:
        json.dump(payload, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        _print_census(export)


def main(args) -> None:
    """Dispatch on the global -j/--jurisdiction flag (only fi has a graph today)."""
    jur = (getattr(args, "jurisdiction", None) or "fi").lower()
    if jur != "fi":
        print(
            f"corpus-graph: the Legal Surface Graph is defined for fi only; "
            f"{jur!r} has no corpus surface graph."
        )
        return
    run(args)
