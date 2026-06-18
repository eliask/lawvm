"""Gate for the Layer-2 construction-derived deontic NORM edges.

The FIRST real Layer-2 composition (``norm_composition.ConditionAttachmentPass``):
it composes the condition/exception construction parse's COMPUTED ATTACHMENT into
deontic NORM edges, replacing the over-generating 120-char proximity join.

  * ``condition_attaches_norm`` — a CONDITION qualifier → its attached deontic core
  * ``exception_excepts_norm``  — an EXCEPTION qualifier → its attached deontic core

The edge is sourced from the construction's ``attached_core_index`` /
``attachment_status`` (NOT a proximity window):

  (a) a RESOLVED (single-core) condition emits one edge, edge status "asserted",
      attaching to the correct core node;
  (b) an EXCEPTION qualifier emits an ``exception_excepts_norm`` edge;
  (c) an AMBIGUOUS qualifier (several cores) emits one edge PER candidate core,
      edge status "ambiguous", carrying the full candidate set — never a silent
      pick;
  (d) a CANDIDATE qualifier (no deontic core in the sentence) emits NO asserted
      edge — recorded as a typed unattached diagnostic instead;
  (e) firewall: every emitted edge is surface_only / not replay_authorized;
  (f) determinism: building the same statute twice yields identical NORM edges +
      graph_id;
  (g) precision over proximity: the construction NORM edge count is <= the
      proximity ``exception_scopes_frame`` count on the same statute, and each
      construction edge attaches WITHIN the qualifier's own sentence.
"""
from __future__ import annotations

from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph
from lawvm.finland.legal_surface.norm_composition import (
    EDGE_CONDITION_ATTACHES,
    EDGE_EXCEPTION_EXCEPTS,
    NO_CORE_IN_SENTENCE,
    ConditionAttachmentPass,
    condition_attachment_passes,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

_NORM_KINDS = {EDGE_CONDITION_ATTACHES, EDGE_EXCEPTION_EXCEPTS}


def _xml(*paragraphs: str) -> bytes:
    body = "\n".join(f"      <p>{p}</p>" for p in paragraphs)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<akomaNtoso xmlns="{_AKN}"><act><body>\n'
        f'  <section eId="sec_1"><num>1 §</num><content>\n{body}\n'
        f"  </content></section>\n"
        f"</body></act></akomaNtoso>\n"
    ).encode("utf-8")


# A statute exercising all four attachment shapes in one body:
#  P1: resolved condition (one core "voi")
#  P2: an exception ("ei kuitenkaan") + a condition ("jos"), both -> the lone core
#  P3: ambiguous condition ("jos") -> two cores ("voi" / "saa" split by ';')
#  P4: candidate condition ("jos") -> NO deontic core in the sentence
_XML = _xml(
    "Jos hakemus on puutteellinen, viranomainen voi hylata sen.",
    "Valtioneuvosto ei kuitenkaan voi kumota paatosta, jos asia on vireilla.",
    "Jos ehto tayttyy: ministerio voi myontaa luvan; hakija saa korvauksen.",
    "Jos hakemus on puutteellinen, asia raukeaa ja kasittely paattyy.",
)


def _build():
    return build_legal_surface_graph(_XML, "999/2025")


def _norm_edges(graph):
    return [e for e in graph.edges if e.edge_kind in _NORM_KINDS]


# ── (a) resolved condition → one edge attaching to the correct core ──────────


def test_resolved_condition_attaches_to_its_core() -> None:
    graph = _build()
    conds = [e for e in graph.edges if e.edge_kind == EDGE_CONDITION_ATTACHES]
    assert conds, "expected a condition_attaches_norm edge"
    # the P1 resolved condition: src is the cue, dst is the actor_modal_frame core
    resolved = [e for e in conds if e.payload.get("attachment_status") == "resolved"]
    assert resolved, "expected a resolved condition attachment"
    for edge in resolved:
        assert graph.nodes[edge.src].node_kind == "exception_condition_cue"
        assert graph.nodes[edge.dst].node_kind == "deontic_core"
        assert edge.status == "asserted"
        assert edge.payload.get("source") == "construction_attachment"
        assert edge.payload.get("qualifier_kind") == "condition"
        # the attached core span lies inside the dst frame's span (the frame
        # CONTAINS the modal cue) — proves it is THE attached core, not a window.
        core_span = edge.payload["core_span"]
        dst_ref = graph.nodes[edge.dst].source_ref
        assert dst_ref is not None
        assert dst_ref.char_start <= core_span[0] and core_span[1] <= dst_ref.char_end


# ── (b) exception qualifier → exception_excepts_norm edge ────────────────────


def test_exception_qualifier_emits_excepts_edge() -> None:
    graph = _build()
    excs = [e for e in graph.edges if e.edge_kind == EDGE_EXCEPTION_EXCEPTS]
    assert excs, "expected an exception_excepts_norm edge"
    for edge in excs:
        assert graph.nodes[edge.src].node_kind == "exception_condition_cue"
        assert graph.nodes[edge.dst].node_kind == "deontic_core"
        assert edge.payload.get("qualifier_kind") == "exception"
        assert edge.payload.get("cue") == "ei kuitenkaan"


# ── (c) ambiguous → one edge per candidate core, full set, never a silent pick ─


def test_ambiguous_attachment_emits_candidate_set_not_a_pick() -> None:
    graph = _build()
    amb = [
        e
        for e in _norm_edges(graph)
        if e.payload.get("attachment_status") == "ambiguous"
    ]
    assert amb, "expected ambiguous NORM edges (the two-core P3 sentence)"
    # all ambiguous edges from the SAME qualifier carry the SAME candidate set,
    # and there is one edge per candidate core that has a backing graph node.
    for edge in amb:
        assert edge.status == "ambiguous"
        cand = edge.payload.get("candidate_core_spans")
        assert isinstance(cand, list) and len(cand) >= 2, (
            "an ambiguous edge must carry the FULL candidate set (>=2 cores), "
            "not just the chosen one"
        )
        # the dst this edge attaches to is one OF the candidates (never invented)
        assert edge.payload["core_span"] in cand


def test_ambiguous_carries_full_set_but_only_backed_cores_get_edges() -> None:
    # An ambiguous qualifier carries the FULL candidate set in payload, but emits
    # an asserted edge ONLY for candidate cores that have a backing deontic_core
    # node. The deontic_core lens mints one per construction core, so every
    # candidate is normally backed; the edge count <= candidate count — never an
    # invented edge to a core with no graph node. This is the honest
    # coordinate-bridge behaviour.
    graph = _build()
    amb = [
        e
        for e in _norm_edges(graph)
        if e.payload.get("attachment_status") == "ambiguous"
    ]
    assert amb, "expected at least one ambiguous NORM edge"
    for edge in amb:
        cand = edge.payload["candidate_core_spans"]
        # the edge's own attached core is a member of the carried candidate set
        assert edge.payload["core_span"] in cand
        # and the dst node's span actually covers that attached core
        dst_ref = graph.nodes[edge.dst].source_ref
        assert dst_ref is not None
        cs = edge.payload["core_span"]
        assert dst_ref.char_start <= cs[0] and cs[1] <= dst_ref.char_end


# ── (d) candidate (no core) → NO asserted edge; tagged unattached ────────────


def test_candidate_no_core_emits_no_edge_but_is_tagged() -> None:
    bundle = build_surface_bundle(_XML, "999/2025")
    graph = _build()
    (pass_,) = condition_attachment_passes(bundle)
    pass_.run(graph)
    # the P4 "jos ... asia raukeaa" sentence has NO deontic core → tagged candidate
    no_core = [u for u in pass_.unattached if u.reason == NO_CORE_IN_SENTENCE]
    assert no_core, "expected a NO_CORE_IN_SENTENCE unattached qualifier (P4)"
    # and that qualifier produced NO edge anywhere in the graph
    for u in no_core:
        for edge in _norm_edges(graph):
            assert not (
                edge.payload.get("cue_span") == [u.cue_char_start, u.cue_char_end]
            ), "a no-core candidate qualifier must NOT yield an asserted edge"


# ── (e) firewall over every NORM edge ────────────────────────────────────────


def test_norm_edges_obey_the_firewall() -> None:
    graph = _build()
    edges = _norm_edges(graph)
    assert edges, "expected NORM edges to exercise the firewall assertion"
    for edge in edges:
        assert edge.surface_only is True
        assert edge.replay_authorized is False
        assert edge.status in ("asserted", "ambiguous")


# ── (f) determinism ──────────────────────────────────────────────────────────


def test_norm_composition_is_deterministic() -> None:
    first = _build()
    second = _build()

    def _keys(graph):
        return sorted(
            (e.edge_id, e.edge_kind, e.src, e.dst, e.status, e.payload_hash)
            for e in _norm_edges(graph)
        )

    assert _keys(first) == _keys(second)
    assert first.graph_id == second.graph_id


# ── (g) precision over proximity: fewer edges, sentence-local attachment ──────


def test_construction_is_more_precise_than_proximity() -> None:
    graph = _build()
    norm = _norm_edges(graph)
    proximity = [e for e in graph.edges if e.edge_kind == "exception_scopes_frame"]
    # the proximity pass over-generates a near-complete mesh; the construction
    # attaches to the right core or flags ambiguous -> never MORE edges.
    assert len(norm) <= len(proximity), (
        f"construction NORM edges ({len(norm)}) should not exceed the proximity "
        f"mesh ({len(proximity)})"
    )
    assert norm, "expected some construction NORM edges"


def test_attachment_is_sentence_local() -> None:
    # every construction edge's cue and attached core must co-occur in ONE sentence
    # (the construction never crosses a sentence boundary, unlike the 120-char
    # proximity window which can join across them).
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

    graph = _build()
    for edge in _norm_edges(graph):
        cue_span = edge.payload["cue_span"]
        core_span = edge.payload["core_span"]
        cue_sent = _sentence_of(cue_span[0])
        core_sent = _sentence_of(core_span[0])
        assert cue_sent == core_sent and cue_sent >= 0, (
            f"construction edge crosses sentences: cue@{cue_span} core@{core_span}"
        )


# ── pass contract sanity ──────────────────────────────────────────────────────


def test_pass_declares_its_kinds() -> None:
    bundle = build_surface_bundle(_XML, "999/2025")
    (pass_,) = condition_attachment_passes(bundle)
    assert isinstance(pass_, ConditionAttachmentPass)
    assert set(pass_.emits_edge_kinds) == _NORM_KINDS
    assert "exception_condition_cue" in pass_.reads_node_kinds
    assert "deontic_core" in pass_.reads_node_kinds
