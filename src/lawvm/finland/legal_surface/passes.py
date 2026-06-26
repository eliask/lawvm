"""Cross-lens edge passes for the Finnish definitions surface (Pro r5 §D5).

§D5: cross-lens edges are computed ONLY by edge passes — a lens may emit only
intrinsic edges within its own output. The H2 :class:`DefinitionLens` resolves
every use it can pin to EXACTLY ONE in-scope binding INTRINSICALLY (it has both
the use seed and the binding seed in one analysis pass, so it mints ``uses_term``
directly). It deliberately leaves two cases unwired, because they need the
WHOLE assembled graph to close:

  * an ``open`` use whose term entity IS defined in the graph but only by a
    binding the intrinsic resolver could not pin in scope (e.g. the binding lies
    AFTER the use — a use-before-definition);
  * an ``ambiguous`` use whose term entity is defined by more than one binding.

For these the pass mints a cross-lens ``term_use_resolves_to`` edge (term_use →
definition_binding) for every candidate binding of the use's term entity that the
lens did not already wire intrinsically. The query:

    for each term_use node U with a recoverable term entity E:
        candidate_bindings = bindings with a defines_term edge to E
        already = bindings U already reaches via an intrinsic uses_term edge
        if U is already intrinsically resolved: skip (closure complete)
        else: mint term_use_resolves_to U -> each candidate not in `already`

This is what makes the ``used_before_definition`` lint observable: it surfaces a
term_use_resolves_to edge to a binding whose span starts after the use. The pass
is also the seam for a genuine second lens / source unit contributing bindings —
nothing in the query is single-unit-specific.
"""
from __future__ import annotations

from typing import Mapping

from lawvm.core.legal_surface_assembler import ENTITY_ID_PREFIX, mint_entity_node_id
from lawvm.core.legal_surface_graph import LegalSurfaceGraph, SurfaceNode
from lawvm.core.legal_surface_lens import SurfaceEdgeSeed

PASS_ID = "fi.definition_closure.v0"
RULE_CROSS_LENS_RESOLVES = "fi.definition_closure.term_use_resolves_to"


def _term_id_of_use(node: SurfaceNode) -> str | None:
    """The canonical term id a ``term_use`` node is about, if recoverable.

    A use seed records ``lemma`` (the binding term it matched). We map it through
    the same canonical id rule the lens uses; ``None`` when the lemma is absent
    (an unresolvable/open use carries its own surface as lemma, which is fine —
    the entity lookup below simply misses and no edge is minted).
    """
    lemma = node.payload.get("lemma")
    if not isinstance(lemma, str) or not lemma.strip():
        return None
    return f"fi.term:{lemma.strip().lower()}"


class DefinitionClosurePass:
    """SurfaceEdgePass: cross-lens definition resolution (§D5 seam).

    Implements ``lawvm.core.legal_surface_assembler.SurfaceEdgePass``. In v0 it
    is a documented no-op (all resolution is intrinsic); it becomes load-bearing
    when a second lens/unit contributes bindings for a term used here.
    """

    pass_id: str = PASS_ID
    reads_node_kinds: tuple[str, ...] = (
        "term_use",
        "definition_binding",
        "term_symbol_entity",
    )
    emits_edge_kinds: tuple[str, ...] = ("term_use_resolves_to",)

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceEdgeSeed, ...]:
        nodes = graph.nodes

        # Index every definition_binding by the term entity it defines (via the
        # intrinsic defines_term edges already in the graph). This is the set of
        # bindings reachable for cross-lens closure.
        bindings_by_term: dict[str, list[str]] = {}
        for edge in graph.edges:
            if edge.edge_kind != "defines_term":
                continue
            if not edge.dst.startswith(ENTITY_ID_PREFIX):
                continue
            bindings_by_term.setdefault(edge.dst, []).append(edge.src)

        # Term_use nodes already tied to a binding intrinsically: skip them.
        intrinsic_use_dsts: dict[str, set[str]] = {}
        for edge in graph.edges:
            if edge.edge_kind != "uses_term":
                continue
            intrinsic_use_dsts.setdefault(edge.src, set()).add(edge.dst)

        seeds: list[SurfaceEdgeSeed] = []
        for node_id, node in _iter_kind(nodes, "term_use"):
            term_id = _term_id_of_use(node)
            if term_id is None:
                continue
            entity_id = mint_entity_node_id(term_id)
            candidate_bindings = bindings_by_term.get(entity_id)
            if not candidate_bindings:
                continue
            # Bindings this use is already intrinsically tied to.
            already = intrinsic_use_dsts.get(node_id, set())
            # Cross-lens closure: a binding for this term entity that the lens did
            # NOT already wire intrinsically (e.g. from another lens/unit). In the
            # v0 single-lens setup this set is empty, so no edge is minted.
            cross = [b for b in candidate_bindings if b not in already]
            if not cross or already:
                # If the use is already intrinsically resolved, the closure is
                # complete; do not duplicate it as a cross-lens edge.
                continue
            for binding_id in sorted(cross):
                seeds.append(
                    SurfaceEdgeSeed(
                        edge_kind="term_use_resolves_to",
                        src_local=node_id,
                        dst_local=binding_id,
                        rule_id=RULE_CROSS_LENS_RESOLVES,
                        surface_edge_status="asserted",
                        payload={"term_entity": entity_id},
                    )
                )
        return tuple(seeds)


def _iter_kind(
    nodes: Mapping[str, SurfaceNode], kind: str
) -> list[tuple[str, SurfaceNode]]:
    return [(nid, n) for nid, n in nodes.items() if n.node_kind == kind]


__all__ = ["DefinitionClosurePass", "PASS_ID", "RULE_CROSS_LENS_RESOLVES"]
