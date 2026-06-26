"""Gate for the EXPERIMENTAL cross-lens frame↔reference / frame↔temporal passes.

These exercise CANDIDATE-status span-colocation affordances (Pro r5 §D5), NOT
settled semantics. A ``frame_contains_reference`` / ``frame_qualified_by_temporal``
edge says ONLY "this citation/date span sits inside (or within a small window of)
this frame's span in the same source unit" — never that the frame legally governs
the reference or that the date qualifies the frame.

Cases:
  (a) a sanction frame textually near a ``(123/2020) 5 §`` reference
      -> ``frame_contains_reference`` candidate edge appears;
  (b) a frame near a date -> ``frame_qualified_by_temporal`` candidate edge;
  (c) a reference OUTSIDE every frame's window -> no edge points at it;
  (d) firewall: every emitted edge surface_only / not replay_authorized / candidate;
  (e) determinism: build + run twice -> identical edge sets and graph_id.
"""
from __future__ import annotations

from lawvm.core.legal_surface_assembler import run_edge_passes
from lawvm.finland.legal_surface.cross_lens_passes import (
    FrameReferenceColocationPass,
    FrameTemporalColocationPass,
)
from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# Section 1 packs a sanction frame ("tuomitaan sakkoon"), a reference
# ("(123/2020) 5 §"), and a date ("1.1.2027") into one provision sentence — the
# span-colocation window should link the frame to both. Section 2 carries a LONE
# reference ("(456/2018) 3 §") far from any frame cue (padded with neutral
# prose), so it must stay UNLINKED — proof that the passes are span-anchored, not
# "everything in the body".
_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body>
    <section eId="sec_1"><num>1 §</num><content>
      <p>Joka rikkoo lapsilisalain (123/2020) 5 §:ssä säädettyä, tuomitaan sakkoon. Tämä laki tulee voimaan 1.1.2027.</p>
    </content></section>
    <section eId="sec_2"><num>2 §</num><content>
      <p>Etuus määräytyy hakemuksen perusteella ja se myönnetään hakijalle erikseen kunkin kalenterivuoden alusta lukien yleisten perusteiden mukaisesti soveltaen perhe-etuuksien valtuutuslain (456/2018) 3 §:n säännöksiä.</p>
    </content></section>
  </body></act>
</akomaNtoso>
""".encode("utf-8")

# Bare process/sanction nouns are demoted to ``*_cue`` kinds; they anchor the
# same span a ``*_frame`` does, so the colocation passes attach edges to them too.
_FRAME_KINDS = {
    "actor_modal_frame",
    "delegation_frame",
    "exception_condition_cue",
    "procedure_frame",
    "procedure_cue",
    "sanction_frame",
    "sanction_cue",
}


def _enriched():
    graph = build_legal_surface_graph(_XML, "999/2025")
    return run_edge_passes(
        graph,
        (FrameReferenceColocationPass(), FrameTemporalColocationPass()),
    )


# ── (a) frame_contains_reference appears ──────────────────────────────────────


def test_frame_contains_reference_candidate_edge_appears() -> None:
    graph = _enriched()
    edges = [e for e in graph.edges if e.edge_kind == "frame_contains_reference"]
    assert edges, "expected a candidate frame_contains_reference edge"
    for edge in edges:
        # endpoints resolve to a frame node and a reference_expr node
        assert graph.nodes[edge.src].node_kind in _FRAME_KINDS
        assert graph.nodes[edge.dst].node_kind == "reference_expr"
        # self-evidencing payload: WHY the edge exists travels with it
        assert isinstance(edge.payload.get("char_distance"), int)
        assert edge.payload.get("frame_kind") == graph.nodes[edge.src].node_kind
        assert edge.payload.get("child_kind") == "reference_expr"
        assert edge.payload.get("experimental") is True
        assert isinstance(edge.payload.get("frame_span"), list)
        assert isinstance(edge.payload.get("child_span"), list)


# ── (b) frame_qualified_by_temporal appears ───────────────────────────────────


def test_frame_qualified_by_temporal_candidate_edge_appears() -> None:
    graph = _enriched()
    edges = [
        e for e in graph.edges if e.edge_kind == "frame_qualified_by_temporal"
    ]
    assert edges, "expected a candidate frame_qualified_by_temporal edge"
    for edge in edges:
        assert graph.nodes[edge.src].node_kind in _FRAME_KINDS
        assert graph.nodes[edge.dst].node_kind == "temporal_expr"
        assert edge.payload.get("child_kind") == "temporal_expr"
        assert isinstance(edge.payload.get("char_distance"), int)


# ── (c) a reference outside every frame window is NOT linked ───────────────────


def test_reference_outside_all_frames_has_no_edge() -> None:
    graph = _enriched()

    # The far, lone reference is the reference_expr with the largest char_start
    # (section 2's "(456/2018) 3 §"), well beyond the window from any frame cue.
    ref_nodes = [
        (nid, n)
        for nid, n in graph.nodes.items()
        if n.node_kind == "reference_expr" and n.source_ref is not None
    ]
    assert len(ref_nodes) >= 2, "fixture should have a near AND a far reference"
    far_id, _ = max(ref_nodes, key=lambda kv: kv[1].source_ref.char_start)

    linked_to_far = [
        e
        for e in graph.edges
        if e.edge_kind == "frame_contains_reference" and e.dst == far_id
    ]
    assert linked_to_far == [], (
        "a reference outside every frame's span window must not be linked"
    )


# ── (d) firewall + candidate-status over every emitted edge ───────────────────


def test_cross_lens_edges_firewall_and_candidate_status() -> None:
    graph = _enriched()
    cross = [
        e
        for e in graph.edges
        if e.edge_kind in ("frame_contains_reference", "frame_qualified_by_temporal")
    ]
    assert cross, "expected the experimental cross-lens edges to be present"
    for edge in cross:
        assert edge.surface_only is True
        assert edge.replay_authorized is False
        assert edge.surface_edge_status == "candidate"


# ── (e) determinism ───────────────────────────────────────────────────────────


def test_cross_lens_passes_are_deterministic() -> None:
    first = _enriched()
    second = _enriched()

    def _edge_keys(graph):
        return sorted(
            (e.edge_id, e.edge_kind, e.src, e.dst, e.surface_edge_status, e.payload_hash)
            for e in graph.edges
            if e.edge_kind
            in ("frame_contains_reference", "frame_qualified_by_temporal")
        )

    assert _edge_keys(first) == _edge_keys(second)
    # the assembler recomputes graph_id over the full edge set; it must be stable
    assert first.graph_id == second.graph_id


# ── window monotonicity (a tighter window never adds edges) ───────────────────


def test_tighter_window_never_adds_reference_edges() -> None:
    graph = build_legal_surface_graph(_XML, "999/2025")
    wide = FrameReferenceColocationPass(window=120).run(graph)
    narrow = FrameReferenceColocationPass(window=10).run(graph)
    assert len(narrow) <= len(wide)
