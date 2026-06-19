"""``lawvm surface-graph`` — end-to-end Legal Surface Graph inspector.

Builds the FULL surface graph for one statute (all 8 lenses -> assembler ->
cross-lens/frame edge passes -> lints) and prints a single legible view of the
"middle semantics": lens coverage, the node-kind and edge-kind census (so the
interlink fabric is visible), the reference resolution-status breakdown (how far
resolution got: resolved / statute_only / ambiguous / open / broken), and the
derived lints. This is the read-only, projection-side counterpart to the
recogniser/registry machinery: it shows what the graph KNOWS about a statute as
typed surface facts, never a legal conclusion (the authority firewall holds —
every node/edge is surface_only).

Reuses the store / registry / body-loading helpers from ``surface_lints`` so the
two tools agree on data sourcing. Archive-only reads; no replay.
"""
from __future__ import annotations

import json
import sys
from collections import Counter

from lawvm.tools.surface_lints import (
    _get_store,
    _load_registries,
    _read_body,
)

# Edge kinds that constitute the cross-lens INTERLINK fabric (as opposed to the
# intra-lens structural edges). Surfaced separately so the "8 parallel overlays
# vs one fabric" distinction is visible at a glance.
_INTERLINK_EDGE_KINDS = frozenset(
    {
        "term_use_resolves_to",
        "refers_to",
        "has_candidate",
        "actor_modal_temporal_colocated",
        "delegation_grants_instrument",
        "frame_contains_reference",
        "frame_qualified_by_temporal",
        "exception_scopes_frame",
        "frame_has_colocated_actor",
    }
)


def _resolution_status_census(graph) -> Counter:
    """Count reference_resolution nodes by their resolution_status payload."""
    c: Counter = Counter()
    for node in graph.nodes.values():
        if node.node_kind == "reference_resolution":
            c[str(node.payload.get("resolution_status", "unknown"))] += 1
    return c


def _build(sid: str):
    from lawvm.finland.legal_surface.graph_build import (
        build_legal_surface_graph,
        lint_surface_graph,
    )

    store = _get_store()
    xb = _read_body(store, sid)
    if not xb:
        return None, None, "no body XML in the store"
    statute_reg, eu_reg = _load_registries()
    graph = build_legal_surface_graph(
        xb,
        sid,
        statute_registry=statute_reg,
        eu_registry=eu_reg,
    )
    report = lint_surface_graph(graph)
    return graph, report, None


def _firewall_holds(graph) -> bool:
    for node in graph.nodes.values():
        if not node.surface_only or node.replay_authorized:
            return False
    for edge in graph.edges:
        if not edge.surface_only or edge.replay_authorized:
            return False
    return True


def _payload(sid: str, graph, report) -> dict:
    node_kinds = Counter(n.node_kind for n in graph.nodes.values())
    edge_kinds = Counter(e.edge_kind for e in graph.edges)
    res_status = _resolution_status_census(graph)
    lint_kinds = Counter(lint.lint_kind for lint in report.lints)
    interlink = {k: v for k, v in edge_kinds.items() if k in _INTERLINK_EDGE_KINDS}
    return {
        "jurisdiction": "fi",
        "statute_id": sid,
        "graph_id": graph.graph_id,
        "firewall_holds": _firewall_holds(graph),
        "nodes_total": len(graph.nodes),
        "edges_total": len(graph.edges),
        "lens_runs": sorted(r.lens_id for r in graph.lens_runs),
        "node_kinds": dict(sorted(node_kinds.items())),
        "edge_kinds": dict(sorted(edge_kinds.items())),
        "interlink_edges": dict(sorted(interlink.items())),
        "interlink_edges_total": sum(interlink.values()),
        "resolution_status": dict(sorted(res_status.items())),
        "lints_total": len(report.lints),
        "lint_kinds": dict(sorted(lint_kinds.items())),
    }


def _print_text(p: dict) -> None:
    out = sys.stdout.write
    out(f"surface-graph  {p['statute_id']}  (fi)\n")
    out(f"  graph_id            {p['graph_id']}\n")
    out(f"  firewall_holds      {p['firewall_holds']}\n")
    out(f"  nodes / edges       {p['nodes_total']} / {p['edges_total']}\n")
    out(f"  lenses              {', '.join(p['lens_runs'])}\n")
    out("  node kinds:\n")
    for k, v in p["node_kinds"].items():
        out(f"    {v:>6}  {k}\n")
    out("  edge kinds:\n")
    for k, v in p["edge_kinds"].items():
        tag = "  *interlink" if k in _INTERLINK_EDGE_KINDS else ""
        out(f"    {v:>6}  {k}{tag}\n")
    out(f"  interlink edges     {p['interlink_edges_total']} "
        f"(the cross-lens fabric)\n")
    if p["resolution_status"]:
        out("  reference resolution:\n")
        for k, v in p["resolution_status"].items():
            out(f"    {v:>6}  {k}\n")
    out(f"  lints               {p['lints_total']}\n")
    for k, v in p["lint_kinds"].items():
        out(f"    {v:>6}  {k}\n")


def run(args) -> None:
    sid = args.statute_id
    as_json = getattr(args, "json", False)
    graph, report, err = _build(sid)
    if err is not None:
        msg = {"statute_id": sid, "error": err}
        if as_json:
            json.dump(msg, sys.stdout)
            sys.stdout.write("\n")
        else:
            sys.stderr.write(f"surface-graph: {sid}: {err}\n")
        raise SystemExit(2)
    payload = _payload(sid, graph, report)
    if as_json:
        json.dump(payload, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        _print_text(payload)


def main(args) -> None:
    run(args)
