"""Gate for the delegates_to / sanctioned_by Layer-2 deontic NORM edges.

The next two Layer-2 deontic compositions (``norm_composition.DeonticFrameAttachmentPass``):
they join the dense ``deontic_core`` substrate to co-SENTENCE frame nodes another
lens already minted, SENTENCE-LOCAL and candidate-not-asserted:

  * ``delegates_to``  — a ``power``-kind deontic core → a co-sentence
    ``delegation_frame`` (the instrument it grants);
  * ``sanctioned_by`` — a ``prohibition``/``obligation`` deontic core → a
    co-sentence ``sanction_frame`` (the consequence that backs it).

  (a) a power core co-sentence with one delegation_frame → one delegates_to edge,
      status "candidate", src=deontic_core / dst=delegation_frame;
  (b) an obligation/prohibition core co-sentence with one sanction_frame → one
      sanctioned_by edge, status "candidate";
  (c) a core with SEVERAL co-sentence frames → one edge PER candidate frame,
      status "ambiguous", full candidate set in payload — never a silent pick;
  (d) a core with NO co-sentence frame → NO edge; a typed UnattachedCore
      diagnostic instead;
  (e) firewall: every emitted edge is surface_only / not replay_authorized;
  (f) determinism: building the same statute twice yields identical edges + graph_id;
  (g) sentence-local: every edge's core and frame co-occur in ONE sentence;
  (h) permission cores license NEITHER edge family;
  (i) graph invariants hold with the new edge kinds (edge_kind ∈ EDGE_KINDS).
"""
from __future__ import annotations

from lawvm.core.legal_surface_graph import EDGE_KINDS
from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph
from lawvm.finland.legal_surface.norm_composition import (
    EDGE_DELEGATES_TO,
    EDGE_SANCTIONED_BY,
    NO_FRAME_IN_SENTENCE,
    DeonticFrameAttachmentPass,
    deontic_frame_attachment_passes,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

_DEONTIC_FRAME_KINDS = {EDGE_DELEGATES_TO, EDGE_SANCTIONED_BY}


def _xml(*paragraphs: str) -> bytes:
    body = "\n".join(f"      <p>{p}</p>" for p in paragraphs)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<akomaNtoso xmlns="{_AKN}"><act><body>\n'
        f'  <section eId="sec_1"><num>1 §</num><content>\n{body}\n'
        f"  </content></section>\n"
        f"</body></act></akomaNtoso>\n"
    ).encode("utf-8")


# A statute exercising all the shapes in one body:
#  P1: power core ("annetaan") + ONE delegation_frame -> delegates_to (candidate)
#  P2: obligation core ("on") + ONE sanction_frame    -> sanctioned_by (candidate)
#  P3: prohibition core ("ei saa") + TWO sanction_frames -> sanctioned_by (ambiguous)
#  P4: obligation core ("on") + NO sanction_frame    -> no edge, tagged
_XML = _xml(
    "Tarkemmat saannokset taman lain taytantoonpanosta annetaan "
    "valtioneuvoston asetuksella.",
    "Joka rikkoo tata kieltoa, on tuomittava sakkoon.",
    "Toiminnanharjoittaja ei saa laiminlyoda velvollisuuttaan; "
    "rikkomuksesta tuomitaan sakkoon.",
    "Hakijan on toimitettava asiakirjat maaraajassa.",
)


def _build():
    return build_legal_surface_graph(_XML, "999/2025")


def _edges(graph):
    return [e for e in graph.edges if e.edge_kind in _DEONTIC_FRAME_KINDS]


# ── (a) power core → co-sentence delegation_frame (delegates_to) ─────────────


def test_power_core_delegates_to_delegation_frame() -> None:
    graph = _build()
    delegs = [e for e in graph.edges if e.edge_kind == EDGE_DELEGATES_TO]
    assert delegs, "expected a delegates_to edge (P1 power core + delegation frame)"
    for edge in delegs:
        assert graph.nodes[edge.src].node_kind == "deontic_core"
        assert graph.nodes[edge.src].payload.get("kind") == "power"
        assert graph.nodes[edge.dst].node_kind == "delegation_frame"
        assert edge.payload.get("core_kind") == "power"
        # delegates_to now resolves by a PRINCIPLED attachment index: the
        # delegating verb that mints the power core is PART of the delegation_frame
        # span, so a frame whose span CONTAINS the core cue is the instrument this
        # power grants → status "asserted" (resolved), source "deontic_frame_cue_in_frame".
        # The sentence-local co-occurrence fallback (no containment / several
        # containers) stays "candidate"/"ambiguous", source "deontic_frame_sentence_local".
        assert edge.surface_edge_status in ("asserted", "candidate", "ambiguous")
        if edge.surface_edge_status == "asserted":
            assert edge.payload.get("attachment") == "resolved_by_containment"
            assert edge.payload.get("source") == "deontic_frame_cue_in_frame"
            # the core cue span lies INSIDE the frame span
            cs = edge.payload["core_span"]
            fs = edge.payload["frame_span"]
            assert fs[0] <= cs[0] and cs[1] <= fs[1]


# ── (b) obligation/prohibition core → co-sentence sanction_frame ─────────────


def test_duty_core_is_sanctioned_by_sanction_frame() -> None:
    graph = _build()
    sancts = [e for e in graph.edges if e.edge_kind == EDGE_SANCTIONED_BY]
    assert sancts, "expected a sanctioned_by edge"
    for edge in sancts:
        assert graph.nodes[edge.src].node_kind == "deontic_core"
        assert graph.nodes[edge.src].payload.get("kind") in {
            "obligation",
            "prohibition",
        }
        assert graph.nodes[edge.dst].node_kind == "sanction_frame"
        assert edge.payload.get("source") == "deontic_frame_sentence_local"
    # the single-sanction obligation sentence (P2) yields a "candidate" edge
    candidate = [e for e in sancts if e.surface_edge_status == "candidate"]
    assert candidate, "expected at least one candidate (single-frame) sanctioned_by"


# ── (c) several co-sentence frames → one edge per candidate, ambiguous ───────


def test_multi_frame_core_emits_full_candidate_set_not_a_pick() -> None:
    graph = _build()
    amb = [
        e
        for e in _edges(graph)
        if e.surface_edge_status == "ambiguous"
    ]
    assert amb, "expected ambiguous sanctioned_by edges (P3 two-sanction sentence)"
    # all ambiguous edges from the SAME core carry the SAME candidate set, one edge
    # per candidate frame that the sentence contains.
    for edge in amb:
        cand = edge.payload.get("candidate_frame_spans")
        assert isinstance(cand, list) and len(cand) >= 2, (
            "an ambiguous edge must carry the FULL candidate-frame set (>=2)"
        )
        # the dst this edge attaches to is one OF the candidates (never invented)
        assert edge.payload["frame_span"] in cand
        # all candidates share the same source core
    src_ids = {e.src for e in amb}
    assert len(src_ids) == 1, "the two ambiguous edges share one source core"


# ── (d) no co-sentence frame → no edge; tagged UnattachedCore ────────────────


def test_core_with_no_frame_emits_no_edge_but_is_tagged() -> None:
    bundle = build_surface_bundle(_XML, "999/2025")
    graph = _build()
    (pass_,) = deontic_frame_attachment_passes(bundle)
    pass_.run(graph)
    # the P4 "Hakijan on toimitettava ..." obligation core has NO sanction frame
    no_frame = [u for u in pass_.unattached if u.reason == NO_FRAME_IN_SENTENCE]
    assert no_frame, "expected a NO_FRAME_IN_SENTENCE unattached core (P4)"
    assert any(u.edge_kind == EDGE_SANCTIONED_BY for u in no_frame), (
        "the frameless obligation core should be tagged for sanctioned_by"
    )
    # and that core produced NO edge anywhere in the graph
    for u in no_frame:
        for edge in _edges(graph):
            assert edge.payload.get("core_span") != [
                u.core_char_start,
                u.core_char_end,
            ], "a frameless core must NOT yield an edge"


# ── (e) firewall over every deontic-frame edge ───────────────────────────────


def test_deontic_frame_edges_obey_the_firewall() -> None:
    graph = _build()
    edges = _edges(graph)
    assert edges, "expected deontic-frame edges to exercise the firewall assertion"
    for edge in edges:
        assert edge.surface_only is True
        assert edge.replay_authorized is False
        # delegates_to resolves by containment to "asserted"; the co-occurrence
        # cases stay "candidate"/"ambiguous". The firewall holds for all.
        assert edge.surface_edge_status in ("asserted", "candidate", "ambiguous")


# ── (f) determinism ──────────────────────────────────────────────────────────


def test_deontic_frame_composition_is_deterministic() -> None:
    first = _build()
    second = _build()

    def _keys(graph):
        return sorted(
            (e.edge_id, e.edge_kind, e.src, e.dst, e.surface_edge_status, e.payload_hash)
            for e in _edges(graph)
        )

    assert _keys(first) == _keys(second)
    assert first.graph_id == second.graph_id


# ── (g) sentence-local: core and frame co-occur in ONE sentence ──────────────


def test_attachment_is_sentence_local() -> None:
    from lawvm.finland.legal_surface.clause_segment import build_clause_index

    bundle = build_surface_bundle(_XML, "999/2025")
    unit = bundle.units[0]
    index = build_clause_index(unit.source_unit_id, unit.raw_text)
    sentences = [(s.char_start, s.char_end) for s in index.sentences]

    def _sentence_of(pos: int) -> int:
        for i, (s, e) in enumerate(sentences):
            if s <= pos < e:
                return i
        return -1

    def _frame_overlaps_sentence(frame_span, sent_i: int) -> bool:
        s, e = sentences[sent_i]
        return not (frame_span[1] <= s or e <= frame_span[0])

    graph = _build()
    edges = _edges(graph)
    assert edges, "expected deontic-frame edges"
    for edge in edges:
        core_span = edge.payload["core_span"]
        frame_span = edge.payload["frame_span"]
        # the CORE (a tiny modal cue) lands cleanly in one sentence; the FRAME (a
        # whole recognised clause) may straddle a boundary, so the requirement is
        # that the frame's text OVERLAPS the core's sentence — sentence-local
        # co-occurrence, not a whole-body proximity mesh.
        core_sent = _sentence_of(core_span[0])
        assert core_sent >= 0, f"core not in any sentence: {core_span}"
        assert _frame_overlaps_sentence(frame_span, core_sent), (
            f"edge frame does not overlap core's sentence: "
            f"core@{core_span} (sent {core_sent}) frame@{frame_span}"
        )


# ── (h) permission cores license neither edge family ─────────────────────────


def test_permission_core_licenses_no_edge() -> None:
    # "voi" is a permission core; with no frame it gets no edge and (since neither
    # delegates_to nor sanctioned_by applies to permission) is not even tagged.
    xml = _xml("Hakija voi taydentaa hakemustaan maaraajassa.")
    graph = build_legal_surface_graph(xml, "1000/2025")
    perm_cores = [
        n
        for n in graph.nodes.values()
        if n.node_kind == "deontic_core" and n.payload.get("kind") == "permission"
    ]
    assert perm_cores, "fixture should contain a permission core"
    assert not _edges(graph), "a permission core must license no deontic-frame edge"

    bundle = build_surface_bundle(xml, "1000/2025")
    (pass_,) = deontic_frame_attachment_passes(bundle)
    pass_.run(graph)
    # permission cores are never even eligible → not tagged as unattached
    assert all(
        u.core_kind in {"power", "obligation", "prohibition"}
        for u in pass_.unattached
    )


# ── (i) graph invariants: new edge kinds are in the closed vocabulary ────────


def test_new_edge_kinds_are_registered() -> None:
    assert EDGE_DELEGATES_TO in EDGE_KINDS
    assert EDGE_SANCTIONED_BY in EDGE_KINDS
    graph = _build()
    for edge in _edges(graph):
        assert edge.edge_kind in EDGE_KINDS
        # src/dst resolve to real nodes (assembler invariant)
        assert edge.src in graph.nodes
        assert edge.dst in graph.nodes


# ── pass contract sanity ──────────────────────────────────────────────────────


def test_pass_declares_its_kinds() -> None:
    bundle = build_surface_bundle(_XML, "999/2025")
    (pass_,) = deontic_frame_attachment_passes(bundle)
    assert isinstance(pass_, DeonticFrameAttachmentPass)
    assert set(pass_.emits_edge_kinds) == _DEONTIC_FRAME_KINDS
    assert "deontic_core" in pass_.reads_node_kinds
    assert "delegation_frame" in pass_.reads_node_kinds
    assert "sanction_frame" in pass_.reads_node_kinds
