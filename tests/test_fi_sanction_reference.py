"""Gate for the sanction_defers_to_provision Layer-2 penal-reference edge.

The PRINCIPLED sanction attachment (``norm_composition.SanctionReferencePass``):
where ``sanctioned_by`` can only sentence-locally CO-OCCUR (the duty↔consequence
link is NOT surface-recoverable from the modal-core ↔ sanction-frame join — a
verified finding, see the module docstring), the penal-DEFERRAL construction
("rangaistaan … niin kuin §:ssä säädetään" / "… §:n mukaan") DOES carry a
recoverable attachment index: the forward provision reference the penalty defers
to, bound by a closed deferral cue. This pass joins each ``sanction_frame`` to the
``reference_expr`` node(s) it defers to.

  (a) one deferral reference (pre-cue: "niin kuin §X säädetään") → one
      sanction_defers_to_provision edge, status "asserted",
      attachment="resolved_by_penal_reference";
  (b) one deferral reference (post-cue postposition: "§X mukaan") → resolved too;
  (c) SEVERAL deferral references → one edge PER reference, status "ambiguous",
      full candidate set in payload — never a silent pick;
  (d) a sanction with NO forward deferral reference (a standalone offence
      definition) → NO edge; a typed UnattachedSanction diagnostic instead — the
      co-occurrence sanctioned_by edge is the only thing that links it (kept);
  (e) firewall: every emitted edge is surface_only / not replay_authorized;
  (f) determinism: building the same statute twice yields identical edges + graph_id;
  (g) a citation that merely CO-OCCURS with a sanction but has NO deferral cue does
      NOT produce a penal-reference edge (cue-gated, not proximity);
  (h) graph invariants hold with the new edge kind (edge_kind ∈ EDGE_KINDS).
"""
from __future__ import annotations

from lawvm.core.legal_surface_graph import EDGE_KINDS
from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph
from lawvm.finland.legal_surface.norm_composition import (
    EDGE_SANCTION_DEFERS,
    NO_DEFERRAL_REFERENCE,
    SanctionReferencePass,
    sanction_reference_passes,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# A penal-deferral sanction ("rangaistaan … niin kuin §:ssä säädetään") carries no
# target actor and no trigger, so it is emitted as the demoted ``sanction_cue``
# kind (not ``sanction_frame``); both anchor the same marker span the pass reads.
_SANCTION_KINDS = {"sanction_frame", "sanction_cue"}


def _xml(*paragraphs: str) -> bytes:
    body = "\n".join(f"      <p>{p}</p>" for p in paragraphs)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<akomaNtoso xmlns="{_AKN}"><act><body>\n'
        f'  <section eId="sec_1"><num>1 §</num><content>\n{body}\n'
        f"  </content></section>\n"
        f"</body></act></akomaNtoso>\n"
    ).encode("utf-8")


# A statute exercising the penal-deferral shapes in one body:
#  P1: pre-cue deferral ("niin kuin … saadetaan") + ONE ref  -> resolved/asserted
#  P2: post-cue deferral postposition ("§:n mukaan")  + ONE ref -> resolved/asserted
#  P3: TWO deferral refs ("9 §:ssa ja 10 §:ssa saadetaan")  -> ambiguous
#  P4: standalone offence ("on tuomittava sakkoon", no ref)  -> no edge, tagged
#  P5: a citation co-occurs but NO deferral cue              -> no edge
_XML = _xml(
    "Joka rikkoo tata lakia, rangaistaan niin kuin rikoslain 38 luvussa saadetaan.",
    "Rangaistus taman lain rikkomisesta tuomitaan rikoslain 38 luvun 1 :n mukaan.",
    "Rangaistakoon niin kuin 9 §:ssa ja 10 §:ssa saadetaan.",
    "Joka laiminlyo velvollisuutensa, on tuomittava sakkoon.",
    "Sakko, josta 5 §:ssa on kyse, peritaan ulosottotoimin.",
)


def _build():
    return build_legal_surface_graph(_XML, "9001/2025")


def _edges(graph):
    return [e for e in graph.edges if e.edge_kind == EDGE_SANCTION_DEFERS]


# ── (a/b) single deferral reference resolves to "asserted" ───────────────────


def test_single_deferral_reference_resolves_asserted() -> None:
    graph = _build()
    edges = _edges(graph)
    asserted = [e for e in edges if e.surface_edge_status == "asserted"]
    assert asserted, "expected resolved (asserted) penal-deferral edges (P1/P2)"
    for edge in asserted:
        assert graph.nodes[edge.src].node_kind in _SANCTION_KINDS
        assert graph.nodes[edge.dst].node_kind == "reference_expr"
        assert edge.payload.get("attachment") == "resolved_by_penal_reference"
        assert edge.payload.get("source") == "sanction_penal_deferral"
        # the reference starts AT/AFTER the sanction marker (forward deferral)
        ms = edge.payload["marker_span"]
        rs = edge.payload["reference_span"]
        assert rs[0] >= ms[1], "deferral reference must be forward of the marker"


# ── (c) several deferral references → ambiguous, full candidate set ───────────


def test_multi_reference_emits_full_candidate_set_not_a_pick() -> None:
    graph = _build()
    amb = [e for e in _edges(graph) if e.surface_edge_status == "ambiguous"]
    assert amb, "expected ambiguous penal-deferral edges (P3 two refs)"
    for edge in amb:
        cand = edge.payload.get("candidate_reference_spans")
        assert isinstance(cand, list) and len(cand) >= 2, (
            "an ambiguous edge must carry the FULL candidate-reference set (>=2)"
        )
        # the dst this edge attaches to is one OF the candidates (never invented)
        assert edge.payload["reference_span"] in cand
        assert edge.payload.get("attachment") == "ambiguous_by_penal_reference"
    # all ambiguous edges from the same sanction share one source frame
    p3_amb = [
        e
        for e in amb
        if len(e.payload.get("candidate_reference_spans", [])) == 2
    ]
    assert p3_amb, "P3 should yield a 2-candidate ambiguous set"


# ── (d) standalone offence (no deferral) → no edge, tagged diagnostic ────────


def test_standalone_offence_emits_no_edge_but_is_tagged() -> None:
    bundle = build_surface_bundle(_XML, "9001/2025")
    graph = _build()
    (pass_,) = sanction_reference_passes(bundle)
    pass_.run(graph)
    tagged = [u for u in pass_.unattached if u.reason == NO_DEFERRAL_REFERENCE]
    assert tagged, "expected a NO_DEFERRAL_REFERENCE diagnostic (P4 standalone)"
    # P4's "on tuomittava sakkoon" sanction has no forward deferral reference, so it
    # produced NO penal-deferral edge; it is recorded, never fabricated into one.
    kinds = {u.sanction_kind for u in tagged}
    assert "sakko" in kinds or "rangaistus" in kinds


# ── (e) firewall over every penal-deferral edge ──────────────────────────────


def test_penal_deferral_edges_obey_the_firewall() -> None:
    graph = _build()
    edges = _edges(graph)
    assert edges, "expected penal-deferral edges to exercise the firewall assertion"
    for edge in edges:
        assert edge.surface_only is True
        assert edge.replay_authorized is False
        assert edge.surface_edge_status in ("asserted", "ambiguous")


# ── (f) determinism ──────────────────────────────────────────────────────────


def test_penal_deferral_composition_is_deterministic() -> None:
    first = _build()
    second = _build()

    def _keys(graph):
        return sorted(
            (e.edge_id, e.edge_kind, e.src, e.dst, e.surface_edge_status, e.payload_hash)
            for e in _edges(graph)
        )

    assert _keys(first) == _keys(second)
    assert first.graph_id == second.graph_id


# ── (g) cue-gated, not proximity: a co-occurring citation with no deferral cue
#        produces no penal-reference edge ─────────────────────────────────────


def test_citation_without_deferral_cue_does_not_resolve() -> None:
    # P5 ("Sakko, josta 5 §:ssa on kyse, peritaan ulosottotoimin.") has a sanction
    # marker and a citation in the same sentence but NO deferral cue (mukaan / niin
    # kuin / saadetaan / …) binding them — the sanction does not DEFER to §5. It
    # must NOT yield a penal-deferral edge.
    graph = _build()
    for edge in _edges(graph):
        rt = build_surface_bundle(_XML, "9001/2025").units[0].raw_text
        ref = edge.payload["reference_span"]
        # none of the resolved/ambiguous edges should point at the P5 "5 §" cite
        # (which sits after "josta" with no deferral cue). Assert by checking the
        # marker span belongs to a sentence containing a deferral cue.
        ms = edge.payload["marker_span"]
        window = rt[ms[1] : ref[0] + 30]
        assert any(
            cue in window.lower()
            for cue in (
                "mukaan",
                "niin kuin",
                "siten kuin",
                "saadetaan",
                "säädetään",
                "noudatet",
                "nojalla",
                "mainit",
                "saadet",
                "saadett",
                "saadetty",
            )
        ), f"penal-deferral edge without a binding deferral cue: {window!r}"


# ── (h) graph invariants: the new edge kind is in the closed vocabulary ──────


def test_new_edge_kind_is_registered() -> None:
    assert EDGE_SANCTION_DEFERS in EDGE_KINDS
    graph = _build()
    for edge in _edges(graph):
        assert edge.edge_kind in EDGE_KINDS
        assert edge.src in graph.nodes
        assert edge.dst in graph.nodes


# ── pass contract sanity ──────────────────────────────────────────────────────


def test_pass_declares_its_kinds() -> None:
    bundle = build_surface_bundle(_XML, "9001/2025")
    (pass_,) = sanction_reference_passes(bundle)
    assert isinstance(pass_, SanctionReferencePass)
    assert set(pass_.emits_edge_kinds) == {EDGE_SANCTION_DEFERS}
    assert "sanction_frame" in pass_.reads_node_kinds
    assert "reference_expr" in pass_.reads_node_kinds


# ── sanctioned_by co-occurrence is preserved (kept, not removed) ─────────────


def test_sanctioned_by_co_occurrence_is_preserved() -> None:
    # The principled penal-reference edge is ADDITIVE: the sentence-local
    # co-occurrence sanctioned_by edge (the only thing that links a standalone
    # duty to its consequence) still exists. P4's "on tuomittava sakkoon" duty core
    # co-occurs with the sakko sanction frame.
    graph = _build()
    sancts = [e for e in graph.edges if e.edge_kind == "sanctioned_by"]
    assert sancts, "sanctioned_by co-occurrence edges must be preserved (additive)"
    for edge in sancts:
        # co-occurrence stays candidate/ambiguous — never silently upgraded to
        # asserted (cue-containment for sanctions is spurious, see module docstring).
        assert edge.surface_edge_status in ("candidate", "ambiguous")
