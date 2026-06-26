"""Gate for the norm_has_subject + governed_by_procedure Layer-2 deontic edges,
plus the delegates_to RESOLVED-by-containment promotion.

Three additive Layer-2 deontic compositions on the dense ``deontic_core``
substrate, all surface_only / candidate-not-asserted:

  * ``norm_has_subject`` — bind each deontic core to the ``actor_modal_frame``
    whose span COVERS the core's ``addressee_span`` (the norm SUBJECT the modal
    parse already recorded). One covering actor → "candidate"; several →
    "ambiguous" (full set in payload); underspecified addressee → typed
    ``subject_underspecified`` diagnostic; overt subject with no covering actor
    node → typed ``no_actor_node_for_addressee_span`` diagnostic. Never a
    fabricated subject.
  * ``governed_by_procedure`` — obligation/power core → a co-SENTENCE
    ``procedure_frame``, sentence-local, same candidate/ambiguous/diagnostic
    discipline as delegates_to/sanctioned_by.
  * ``delegates_to`` promotion — a power core whose cue span lies INSIDE the
    delegation_frame span (the delegating verb that mints the core is PART of the
    frame) is a PRINCIPLED attachment → status "asserted", not mere co-occurrence.
"""
from __future__ import annotations

from lawvm.core.legal_surface_graph import EDGE_KINDS
from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph
from lawvm.finland.legal_surface.norm_composition import (
    EDGE_DELEGATES_TO,
    EDGE_GOVERNED_BY_PROCEDURE,
    EDGE_NORM_HAS_SUBJECT,
    NO_FRAME_IN_SENTENCE,
    NO_SUBJECT_NODE_FOR_ADDRESSEE,
    SUBJECT_UNDERSPECIFIED,
    NormSubjectAttachmentPass,
    ProcedureGovernancePass,
    norm_subject_attachment_passes,
    procedure_governance_passes,
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


# A body exercising the shapes:
#  P1: obligation with an actor-typed subject ("Viranomaisen on ...") +
#      a procedure noun ("kasittelyssa") -> norm_has_subject (candidate) AND
#      governed_by_procedure (candidate)
#  P2: obligation with an actor-typed subject ("Hakijan on ...") +
#      procedure noun -> norm_has_subject + governed_by_procedure
#  P3: power core whose cue ("annetaan") is INSIDE the delegation_frame ->
#      delegates_to (asserted, resolved-by-containment)
_XML = _xml(
    "Viranomaisen on tehtava paatos hakemuksen kasittelyssa.",
    "Hakijan on toimitettava asiakirjat hakemuksessa.",
    "Tarkemmat saannokset annetaan valtioneuvoston asetuksella.",
)


def _build():
    return build_legal_surface_graph(_XML, "999/2025")


def _edges(graph, kind):
    return [e for e in graph.edges if e.edge_kind == kind]


# ── norm_has_subject ─────────────────────────────────────────────────────────


def test_norm_has_subject_binds_core_to_covering_actor() -> None:
    graph = _build()
    edges = _edges(graph, EDGE_NORM_HAS_SUBJECT)
    assert edges, "expected norm_has_subject edges (actor-typed obligation cores)"
    for edge in edges:
        assert graph.nodes[edge.src].node_kind == "deontic_core"
        assert graph.nodes[edge.dst].node_kind == "actor_modal_frame"
        assert edge.payload.get("source") == "deontic_core_addressee"
        assert edge.surface_edge_status in ("candidate", "ambiguous")
        # the actor frame's span COVERS the core's addressee span
        actor_span = edge.payload["actor_span"]
        addressee = edge.payload["addressee_span"]
        assert actor_span[0] <= addressee[0] and addressee[1] <= actor_span[1]


def test_norm_has_subject_firewall() -> None:
    graph = _build()
    edges = _edges(graph, EDGE_NORM_HAS_SUBJECT)
    assert edges
    for edge in edges:
        assert edge.surface_only is True
        assert edge.replay_authorized is False


def test_underspecified_addressee_is_a_diagnostic_not_an_edge() -> None:
    # "On noudatettava ..." sentence-initial impersonal/passive register -> the
    # modal parse marks the addressee UNDERSPECIFIED; no subject is fixed.
    xml = _xml("On noudatettava huolellisuutta.")
    graph = build_legal_surface_graph(xml, "601/2025")
    bundle = build_surface_bundle(xml, "601/2025")
    (pass_,) = norm_subject_attachment_passes(bundle)
    pass_.run(graph)
    under = [u for u in pass_.unattached if u.reason == SUBJECT_UNDERSPECIFIED]
    assert under, "expected a subject_underspecified diagnostic"
    # and NO norm_has_subject edge was minted for an underspecified core
    assert not _edges(graph, EDGE_NORM_HAS_SUBJECT)


def test_overt_subject_without_actor_node_is_a_diagnostic() -> None:
    # "Asiasta on saadettava ..." — an overt subject NP precedes the cue, but the
    # production actor recognizer mints no actor_modal_frame covering it.
    xml = _xml("Asiasta on saadettava valtioneuvoston asetuksella.")
    graph = build_legal_surface_graph(xml, "602/2025")
    bundle = build_surface_bundle(xml, "602/2025")
    (pass_,) = norm_subject_attachment_passes(bundle)
    pass_.run(graph)
    miss = [
        u for u in pass_.unattached if u.reason == NO_SUBJECT_NODE_FOR_ADDRESSEE
    ]
    assert miss, "expected a no_actor_node_for_addressee_span diagnostic"


# ── governed_by_procedure ────────────────────────────────────────────────────


def test_governed_by_procedure_joins_core_to_co_sentence_procedure() -> None:
    graph = _build()
    edges = _edges(graph, EDGE_GOVERNED_BY_PROCEDURE)
    assert edges, "expected governed_by_procedure edges"
    for edge in edges:
        assert graph.nodes[edge.src].node_kind == "deontic_core"
        assert graph.nodes[edge.src].payload.get("kind") in {"obligation", "power"}
        assert graph.nodes[edge.dst].node_kind == "procedure_frame"
        assert edge.payload.get("source") == "deontic_procedure_sentence_local"
        assert edge.surface_edge_status in ("candidate", "ambiguous")
        assert edge.surface_only is True
        assert edge.replay_authorized is False


def test_governed_by_procedure_is_sentence_local() -> None:
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
    for edge in _edges(graph, EDGE_GOVERNED_BY_PROCEDURE):
        core_span = edge.payload["core_span"]
        frame_span = edge.payload["frame_span"]
        si = _sentence_of(core_span[0])
        assert si >= 0
        s, e = sentences[si]
        # the procedure frame's text overlaps the core's sentence
        assert not (frame_span[1] <= s or e <= frame_span[0])


def test_core_with_no_procedure_emits_no_edge_but_is_tagged() -> None:
    # A bare obligation with no procedure noun in its sentence.
    xml = _xml("Hakijan on maksettava maksu maaraajassa.")
    graph = build_legal_surface_graph(xml, "603/2025")
    bundle = build_surface_bundle(xml, "603/2025")
    (pass_,) = procedure_governance_passes(bundle)
    pass_.run(graph)
    # if the sentence has an obligation/power core but no procedure frame, it is
    # tagged NO_FRAME_IN_SENTENCE (and only those eligible kinds are tagged).
    for u in pass_.unattached:
        assert u.edge_kind == EDGE_GOVERNED_BY_PROCEDURE
        assert u.reason == NO_FRAME_IN_SENTENCE
        assert u.core_kind in {"obligation", "power"}


def test_permission_core_licenses_no_procedure_edge() -> None:
    xml = _xml("Hakija voi taydentaa hakemustaan kasittelyssa.")
    graph = build_legal_surface_graph(xml, "604/2025")
    bundle = build_surface_bundle(xml, "604/2025")
    (pass_,) = procedure_governance_passes(bundle)
    pass_.run(graph)
    # permission cores are not eligible for governed_by_procedure -> not tagged.
    assert all(
        u.core_kind in {"obligation", "power"} for u in pass_.unattached
    )
    perm_edges = [
        e
        for e in _edges(graph, EDGE_GOVERNED_BY_PROCEDURE)
        if graph.nodes[e.src].payload.get("kind") == "permission"
    ]
    assert not perm_edges


# ── delegates_to resolved-by-containment ─────────────────────────────────────


def test_delegates_to_resolves_by_containment() -> None:
    graph = _build()
    delegs = _edges(graph, EDGE_DELEGATES_TO)
    assert delegs, "expected a delegates_to edge (P3 power core in delegation frame)"
    resolved = [e for e in delegs if e.surface_edge_status == "asserted"]
    assert resolved, "the power core cue inside the delegation frame should RESOLVE"
    for edge in resolved:
        assert edge.payload.get("attachment") == "resolved_by_containment"
        assert edge.payload.get("source") == "deontic_frame_cue_in_frame"
        cs = edge.payload["core_span"]
        fs = edge.payload["frame_span"]
        assert fs[0] <= cs[0] and cs[1] <= fs[1]
        assert edge.surface_only is True and edge.replay_authorized is False


# ── determinism + invariants ─────────────────────────────────────────────────


def test_new_edges_are_deterministic() -> None:
    first = _build()
    second = _build()

    def _keys(graph):
        return sorted(
            (e.edge_id, e.edge_kind, e.src, e.dst, e.surface_edge_status, e.payload_hash)
            for e in graph.edges
            if e.edge_kind
            in {
                EDGE_NORM_HAS_SUBJECT,
                EDGE_GOVERNED_BY_PROCEDURE,
                EDGE_DELEGATES_TO,
            }
        )

    assert _keys(first) == _keys(second)
    assert first.graph_id == second.graph_id


def test_new_edge_kinds_are_registered() -> None:
    assert EDGE_NORM_HAS_SUBJECT in EDGE_KINDS
    assert EDGE_GOVERNED_BY_PROCEDURE in EDGE_KINDS
    graph = _build()
    for kind in (EDGE_NORM_HAS_SUBJECT, EDGE_GOVERNED_BY_PROCEDURE):
        for edge in _edges(graph, kind):
            assert edge.edge_kind in EDGE_KINDS
            assert edge.src in graph.nodes
            assert edge.dst in graph.nodes


def test_passes_declare_their_kinds() -> None:
    bundle = build_surface_bundle(_XML, "999/2025")
    (subj_pass,) = norm_subject_attachment_passes(bundle)
    (proc_pass,) = procedure_governance_passes(bundle)
    assert isinstance(subj_pass, NormSubjectAttachmentPass)
    assert isinstance(proc_pass, ProcedureGovernancePass)
    assert set(subj_pass.emits_edge_kinds) == {EDGE_NORM_HAS_SUBJECT}
    assert set(proc_pass.emits_edge_kinds) == {EDGE_GOVERNED_BY_PROCEDURE}
    assert "deontic_core" in subj_pass.reads_node_kinds
    assert "actor_modal_frame" in subj_pass.reads_node_kinds
    assert "deontic_core" in proc_pass.reads_node_kinds
    assert "procedure_frame" in proc_pass.reads_node_kinds
