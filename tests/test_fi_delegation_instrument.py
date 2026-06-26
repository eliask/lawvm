"""Gate for the Layer-2 ``delegation_grants_instrument`` norm edge.

The north-star "norm -> authorized-instrument" link. A delegation grants the power
to issue a LOWER INSTRUMENT (asetus / määräys / päätös). The production
``delegation_frame`` node names the instrument only as a canonical kind STRING and
has no instrument span; the :class:`DelegatedInstrumentLens` mints a
``delegated_instrument`` node anchored on the construction delegation parse's precise
instrument anchor span (which sits INSIDE the recognizer frame's span). The
:class:`DelegationInstrumentPass` joins each frame to the instrument node(s) its span
CONTAINS — a structural containment attachment, never a proximity mesh.

  (a) a frame containing exactly one delegated_instrument -> ONE asserted edge
      frame -> instrument, attachment "resolved_by_containment";
  (b) the delegated_instrument node carries the instrument surface + kind + span,
      and its span lies INSIDE the frame it is granted by;
  (c) a coordinated grant (several instruments in one frame) -> one edge per
      instrument, status "ambiguous", each carrying the FULL contained set in
      payload — never a silent pick;
  (d) a frame whose instrument_kind the construction parse does NOT anchor (e.g.
      "ohje", outside the construction instrument set) -> NO edge, a typed
      UnattachedFrame diagnostic (NO_INSTRUMENT_IN_FRAME);
  (e) firewall: every delegated_instrument node + every edge is surface_only /
      not replay_authorized;
  (f) determinism: building the same statute twice yields identical edges +
      graph_id;
  (g) the construction DelegatedInstrumentLens runs ALONGSIDE the recognizer
      DelegationLens (both node kinds present, neither removed).
"""
from __future__ import annotations

from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph
from lawvm.finland.legal_surface.lenses.delegated_instrument import (
    DELEGATED_INSTRUMENT_NODE_KIND,
)
from lawvm.finland.legal_surface.norm_composition import (
    EDGE_DELEGATION_GRANTS_INSTRUMENT,
    NO_INSTRUMENT_IN_FRAME,
    delegation_instrument_passes,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _xml(*paragraphs: str) -> bytes:
    body = "\n".join(f"      <p>{p}</p>" for p in paragraphs)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<akomaNtoso xmlns="{_AKN}"><act><body>\n'
        f'  <section eId="sec_1"><num>1 §</num><content>\n{body}\n'
        f"  </content></section>\n"
        f"</body></act></akomaNtoso>\n"
    ).encode("utf-8")


# Canonical single-instrument delegation (one asetus the power grants).
_XML_RESOLVED = _xml(
    "Valtioneuvoston asetuksella voidaan antaa tarkempia saannoksia menettelysta."
)
# A coordinated grant: one frame, two instrument anchors (ambiguous attachment).
_XML_AMBIGUOUS = _xml(
    "Tarkemmista saannoksista annetaan valtioneuvoston asetuksella ja "
    "ministerion asetuksella."
)
# An "ohje" delegation: the recognizer types the frame, but the construction parse
# (whose instrument set is asetus/maarays/paatos) anchors no instrument -> diagnostic.
_XML_DIAGNOSTIC = _xml(
    "Elintarviketurvallisuusvirasto antaa tarkemmat ohjeet kuljetuksessa "
    "noudatettavasta hygieniasta."
)


def _instr_edges(graph):
    return [e for e in graph.edges if e.edge_kind == EDGE_DELEGATION_GRANTS_INSTRUMENT]


# ── (a) resolved: one contained instrument -> one asserted edge ───────────────


def test_resolved_frame_grants_one_instrument() -> None:
    graph = build_legal_surface_graph(_XML_RESOLVED, "100/2025")
    edges = _instr_edges(graph)
    assert edges, "expected a delegation_grants_instrument edge"
    asserted = [e for e in edges if e.surface_edge_status == "asserted"]
    assert asserted, "the single-instrument frame must yield an asserted edge"
    for edge in asserted:
        assert graph.nodes[edge.src].node_kind == "delegation_frame"
        assert graph.nodes[edge.dst].node_kind == DELEGATED_INSTRUMENT_NODE_KIND
        assert edge.payload.get("attachment") == "resolved_by_containment"
        assert edge.payload.get("frame_instrument_kind") == "asetus"


# ── (b) the instrument node carries surface+span, contained by its frame ──────


def test_instrument_node_payload_and_containment() -> None:
    graph = build_legal_surface_graph(_XML_RESOLVED, "100/2025")
    instruments = [
        n
        for n in graph.nodes.values()
        if n.node_kind == DELEGATED_INSTRUMENT_NODE_KIND
    ]
    assert instruments, "expected a delegated_instrument node"
    for n in instruments:
        assert n.payload.get("instrument_kind") == "asetus"
        # the anchored surface is the instrument noun (asetuksella), not the frame
        assert "asetuks" in str(n.payload.get("instrument_surface")).lower()
        assert n.source_ref is not None
    # every asserted edge: the instrument span lies inside the frame span
    for edge in _instr_edges(graph):
        if edge.surface_edge_status != "asserted":
            continue
        fref = graph.nodes[edge.src].source_ref
        iref = graph.nodes[edge.dst].source_ref
        assert fref is not None and iref is not None
        assert fref.char_start <= iref.char_start
        assert iref.char_end <= fref.char_end


# ── (c) coordinated grant -> ambiguous, full candidate set, never a pick ──────


def test_coordinated_grant_is_ambiguous_with_full_set() -> None:
    graph = build_legal_surface_graph(_XML_AMBIGUOUS, "101/2025")
    amb = [e for e in _instr_edges(graph) if e.surface_edge_status == "ambiguous"]
    assert amb, "a coordinated two-instrument grant must produce ambiguous edges"
    for edge in amb:
        assert edge.payload.get("attachment") == "ambiguous_by_containment"
        cand = edge.payload.get("candidate_instrument_spans")
        assert isinstance(cand, list) and len(cand) >= 2, (
            "an ambiguous edge must carry the FULL contained-instrument set, "
            "not just the chosen one"
        )
        # the edge's own dst is one OF the candidates (never invented)
        iref = graph.nodes[edge.dst].source_ref
        assert iref is not None
        assert [iref.char_start, iref.char_end] in cand


# ── (d) ohje frame -> NOW anchored to an ohje delegated_instrument node ───────


def test_ohje_frame_is_anchored_after_canonical_cutover() -> None:
    # DELEGATION-UNIFY-VERDICT step 5 / FRONTIER adjudication: ``ohje`` IS in the
    # canonical instrument set. The construction parse (delegated_instrument lens)
    # now calls the canonical parser, which mints an ``ohje`` delegated_instrument
    # node for ``antaa … ohjeet …``. The ohje delegation_frame is therefore no
    # longer "unanchored": the containment pass joins it to its ohje instrument
    # node with a real edge. The old test asserted no edge could exist because the
    # old two-anchor C model lacked ``ohje``; that miss is now adjudicated-fixed.
    graph = build_legal_surface_graph(_XML_DIAGNOSTIC, "102/2025")
    frames = [n for n in graph.nodes.values() if n.node_kind == "delegation_frame"]
    assert frames, "expected the recognizer to type the ohje delegation frame"
    instr = [
        n
        for n in graph.nodes.values()
        if n.node_kind == DELEGATED_INSTRUMENT_NODE_KIND
        and n.payload.get("instrument_kind") == "ohje"
    ]
    assert instr, "the canonical parser now mints an ohje delegated_instrument node"
    edges = [e for e in _instr_edges(graph) if e.surface_edge_status == "asserted"]
    assert edges, "the ohje frame is now anchored -> a real containment edge"
    # no NO_INSTRUMENT_IN_FRAME diagnostic remains for the (now anchored) ohje frame
    bundle = build_surface_bundle(_XML_DIAGNOSTIC, "102/2025")
    (pass_,) = delegation_instrument_passes(bundle)
    pass_.run(graph)
    assert not [u for u in pass_.unattached if u.reason == NO_INSTRUMENT_IN_FRAME]


# ── (e) firewall ─────────────────────────────────────────────────────────────


def test_instrument_nodes_and_edges_obey_the_firewall() -> None:
    graph = build_legal_surface_graph(_XML_AMBIGUOUS, "101/2025")
    for n in graph.nodes.values():
        if n.node_kind == DELEGATED_INSTRUMENT_NODE_KIND:
            assert n.surface_only is True
            assert n.replay_authorized is False
    edges = _instr_edges(graph)
    assert edges, "expected edges to exercise the firewall assertion"
    for edge in edges:
        assert edge.surface_only is True
        assert edge.replay_authorized is False
        assert edge.surface_edge_status in ("asserted", "ambiguous")


# ── (f) determinism ──────────────────────────────────────────────────────────


def test_delegation_instrument_is_deterministic() -> None:
    first = build_legal_surface_graph(_XML_RESOLVED, "100/2025")
    second = build_legal_surface_graph(_XML_RESOLVED, "100/2025")

    def _keys(graph):
        return sorted(
            (e.edge_id, e.edge_kind, e.src, e.dst, e.surface_edge_status, e.payload_hash)
            for e in _instr_edges(graph)
        )

    assert _keys(first) == _keys(second)
    assert first.graph_id == second.graph_id


# ── (g) the construction lens runs alongside the recognizer lens ──────────────


def test_both_delegation_node_kinds_present() -> None:
    graph = build_legal_surface_graph(_XML_RESOLVED, "100/2025")
    kinds = {n.node_kind for n in graph.nodes.values()}
    assert "delegation_frame" in kinds, "recognizer lens still runs"
    assert DELEGATED_INSTRUMENT_NODE_KIND in kinds, "construction lens runs alongside"


def test_pass_declares_its_kinds() -> None:
    bundle = build_surface_bundle(_XML_RESOLVED, "100/2025")
    (pass_,) = delegation_instrument_passes(bundle)
    assert pass_.emits_edge_kinds == (EDGE_DELEGATION_GRANTS_INSTRUMENT,)
    assert "delegation_frame" in pass_.reads_node_kinds
    assert DELEGATED_INSTRUMENT_NODE_KIND in pass_.reads_node_kinds
