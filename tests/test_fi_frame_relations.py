"""Gate for the EXPERIMENTAL cross-FRAME relation passes + sanction lint.

These exercise CANDIDATE-status frame↔frame / frame↔actor span-colocation
affordances (Pro r5 §D5) and a surface-only sanction lint (§D6), NOT settled
semantics. An ``exception_scopes_frame`` edge says ONLY "this exception/condition
cue span precedes/overlaps that frame's span in the same source unit"; a
``frame_has_colocated_actor`` edge says ONLY "an actor/modal frame sits in/near
this frame's text" — never that the exception legally governs the frame or that
the actor is its legal subject.

Cases:
  (a) an exception cue PRECEDING a sanction frame in the same unit
      -> ``exception_scopes_frame`` candidate edge appears;
  (b) a cue lying strictly AFTER a frame (beyond window) -> no edge to it;
  (c) a frame near an actor_modal_frame -> ``frame_has_colocated_actor`` edge,
      and an actor_modal_frame is never the source / never self-paired;
  (d) firewall: every emitted edge surface_only / not replay_authorized / candidate;
  (e) determinism: build + run twice -> identical edge sets and graph_id;
  (f) tighter window never adds edges;
  (g) sanction lint: a sanction unit with NO exception cue is flagged, surface-only.
"""
from __future__ import annotations

from lawvm.core.legal_surface_assembler import run_edge_passes
from lawvm.core.legal_surface_lints import run_lint_passes
from lawvm.finland.legal_surface.frame_relations import (
    ExceptionScopesFramePass,
    FrameActorColocationPass,
    SanctionConditionLintPass,
)
from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

_FRAME_KINDS = {
    "actor_modal_frame",
    "delegation_frame",
    "procedure_frame",
    "sanction_frame",
}

# Section 1 packs an EXCEPTION cue ("ei kuitenkaan") right before a sanction
# ("rangaistaan") in one sentence, so the cue PRECEDES the sanction frame within
# the window. It also carries a delegation ("asetuksella") next to an actor/modal
# shape ("Valtioneuvosto voi antaa"). Section 2 carries a LONE sanction far from
# any exception cue, padded with neutral prose, so the sanction-without-condition
# lint fires on it but NOT on a unit that has a co-located cue.
_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body>
    <section eId="sec_1"><num>1 §</num><content>
      <p>Edella saadettya ei kuitenkaan sovelleta vahaiseen toimintaan, vaan joka rikkoo tata saannosta, rangaistaan sakolla.</p>
      <p>Valtioneuvosto voi antaa asetuksella tarkempia saannoksia.</p>
    </content></section>
    <section eId="sec_2"><num>2 §</num><content>
      <p>Etuus maaraytyy hakemuksen perusteella ja se myonnetaan hakijalle erikseen kunkin kalenterivuoden alusta lukien yleisten perusteiden mukaisesti. Joka rikkoo tata lakia, rangaistaan sakolla.</p>
    </content></section>
  </body></act>
</akomaNtoso>
""".encode("utf-8")


def _build():
    return build_legal_surface_graph(_XML, "999/2025")


def _with_edges():
    graph = _build()
    return run_edge_passes(
        graph,
        (ExceptionScopesFramePass(), FrameActorColocationPass()),
    )


# ── (a) exception_scopes_frame appears (cue precedes sanction) ────────────────


def test_exception_scopes_frame_candidate_edge_appears() -> None:
    graph = _with_edges()
    edges = [e for e in graph.edges if e.edge_kind == "exception_scopes_frame"]
    assert edges, "expected a candidate exception_scopes_frame edge"
    for edge in edges:
        # src is the exception cue, dst is one of the (non-cue) frame kinds
        assert graph.nodes[edge.src].node_kind == "exception_condition_cue"
        assert graph.nodes[edge.dst].node_kind in _FRAME_KINDS
        # directional: the cue starts at/before the frame end (precedes/overlaps)
        cue_ref = graph.nodes[edge.src].source_ref
        frame_ref = graph.nodes[edge.dst].source_ref
        assert cue_ref is not None and frame_ref is not None
        assert cue_ref.char_start <= frame_ref.char_end
        # self-evidencing payload
        assert isinstance(edge.payload.get("char_distance"), int)
        assert edge.payload.get("cue_kind") == "exception_condition_cue"
        assert edge.payload.get("frame_kind") == graph.nodes[edge.dst].node_kind
        assert edge.payload.get("experimental") is True
        assert isinstance(edge.payload.get("cue_span"), list)
        assert isinstance(edge.payload.get("frame_span"), list)


# ── (b) a cue strictly after a frame (beyond window) earns no edge to it ──────


def test_exception_edge_is_directional_and_windowed() -> None:
    graph = _build()
    # With a tiny window, only the cue/frame pair that actually touches survives;
    # the directional + windowed filter must never produce an edge whose cue lies
    # strictly after the frame.
    seeds = ExceptionScopesFramePass(window=120).run(graph)
    nodes = graph.nodes
    for seed in seeds:
        cue_ref = nodes[seed.src_local].source_ref
        frame_ref = nodes[seed.dst_local].source_ref
        assert cue_ref is not None and frame_ref is not None
        # a cue lying entirely after the frame (start > frame end) is excluded
        assert cue_ref.char_start <= frame_ref.char_end


# ── (c) frame_has_colocated_actor; actor is never source / never self-paired ──


def test_frame_has_colocated_actor_candidate_edge_appears() -> None:
    graph = _with_edges()
    edges = [
        e for e in graph.edges if e.edge_kind == "frame_has_colocated_actor"
    ]
    assert edges, "expected a candidate frame_has_colocated_actor edge"
    for edge in edges:
        src_kind = graph.nodes[edge.src].node_kind
        dst_kind = graph.nodes[edge.dst].node_kind
        # source is a NON-actor frame; target is the actor_modal_frame
        assert src_kind in {"delegation_frame", "procedure_frame", "sanction_frame"}
        assert dst_kind == "actor_modal_frame"
        # never self-paired
        assert edge.src != edge.dst
        assert edge.payload.get("actor_kind") == "actor_modal_frame"
        assert isinstance(edge.payload.get("char_distance"), int)


# ── (d) firewall + candidate-status over every emitted edge ───────────────────


def test_frame_relation_edges_firewall_and_candidate_status() -> None:
    graph = _with_edges()
    rel = [
        e
        for e in graph.edges
        if e.edge_kind in ("exception_scopes_frame", "frame_has_colocated_actor")
    ]
    assert rel, "expected the experimental frame-relation edges to be present"
    for edge in rel:
        assert edge.surface_only is True
        assert edge.replay_authorized is False
        assert edge.surface_edge_status == "candidate"


# ── (e) determinism ───────────────────────────────────────────────────────────


def test_frame_relation_passes_are_deterministic() -> None:
    first = _with_edges()
    second = _with_edges()

    def _edge_keys(graph):
        return sorted(
            (e.edge_id, e.edge_kind, e.src, e.dst, e.surface_edge_status, e.payload_hash)
            for e in graph.edges
            if e.edge_kind
            in ("exception_scopes_frame", "frame_has_colocated_actor")
        )

    assert _edge_keys(first) == _edge_keys(second)
    assert first.graph_id == second.graph_id


# ── (f) tighter window never adds edges ───────────────────────────────────────


def test_tighter_window_never_adds_actor_edges() -> None:
    graph = _build()
    wide = FrameActorColocationPass(window=120).run(graph)
    narrow = FrameActorColocationPass(window=5).run(graph)
    assert len(narrow) <= len(wide)


def test_tighter_window_never_adds_exception_edges() -> None:
    graph = _build()
    wide = ExceptionScopesFramePass(window=120).run(graph)
    narrow = ExceptionScopesFramePass(window=5).run(graph)
    assert len(narrow) <= len(wide)


# ── (g) sanction lint: a unit with no exception cue is flagged, surface-only ──


def _cue_refs(graph):
    return tuple(
        n.source_ref
        for n in graph.nodes.values()
        if n.node_kind == "exception_condition_cue" and n.source_ref is not None
    )


def _min_cue_gap(ref, cue_refs) -> int | None:
    gaps = []
    for cue in cue_refs:
        if cue.source_unit_id != ref.source_unit_id:
            continue
        if ref.char_end <= cue.char_start:
            gaps.append(cue.char_start - ref.char_end)
        elif cue.char_end <= ref.char_start:
            gaps.append(ref.char_start - cue.char_end)
        else:
            gaps.append(0)
    return min(gaps) if gaps else None


def test_sanction_without_condition_lint() -> None:
    graph = _build()
    report = run_lint_passes(graph, (SanctionConditionLintPass(window=120),))
    lints = [
        lint
        for lint in report.lints
        if lint.lint_kind == "sanction.without_colocated_condition"
    ]
    assert lints, "expected a sanction.without_colocated_condition lint"
    cue_refs = _cue_refs(graph)
    for lint in lints:
        # surface-only firewall
        assert lint.surface_only is True
        assert lint.legal_conclusion is False
        assert lint.replay_authorized is False
        assert lint.severity == "info"
        assert lint.forbidden_overclaims
        # subject is a sanction frame with NO exception cue within the window
        subject = graph.nodes[lint.subject_node_id]
        assert subject.node_kind == "sanction_frame"
        ref = subject.source_ref
        assert ref is not None
        gap = _min_cue_gap(ref, cue_refs)
        assert gap is None or gap > 120


def test_sanction_with_colocated_condition_not_flagged() -> None:
    # Section 1's sanction sits within window of the "ei kuitenkaan" cue -> NOT
    # flagged. Only the far section-2 sanction (no nearby cue) is flagged.
    graph = _build()
    report = run_lint_passes(graph, (SanctionConditionLintPass(window=120),))
    cue_refs = _cue_refs(graph)
    flagged = [
        lint
        for lint in report.lints
        if lint.lint_kind == "sanction.without_colocated_condition"
    ]
    for lint in flagged:
        ref = graph.nodes[lint.subject_node_id].source_ref
        assert ref is not None
        gap = _min_cue_gap(ref, cue_refs)
        # a flagged sanction must NOT have a cue within the window
        assert gap is None or gap > 120
