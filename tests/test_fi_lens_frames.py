"""E2E: the H5/H6 frame lenses (Phase 8) as graph node families.

Builds a LegalSurfaceGraph via ``build_legal_surface_graph`` over a synthetic
statute carrying a delegation clause, a procedure noun, a sanction marker, and an
exception cue, and asserts the four new node kinds appear, their spans slice back
to the matched surface, statuses are in the closed NODE_STATUSES vocabulary, and
the authority firewall holds. Also asserts the existing families
(reference/definition/temporal/actor) still build.
"""
from __future__ import annotations

from lawvm.core.legal_surface_graph import NODE_KINDS, NODE_STATUSES
from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# A statute body exercising the H5/H6 frame lenses AND the existing lenses:
#   - delegation:  "Valtioneuvosto voi antaa asetuksella tarkempia saannoksia"
#   - procedure:   "hakemus" (HAKEMUS process noun)
#   - sanction:    "rangaistaan" (RANGAISTUS marker)
#   - exception:   "ei kuitenkaan ..." (EXCEPTION cue)
#   - references:  cross-statute id cite + internal section ref
#   - definition:  "tarkoitetaan jatteella ..." (term binding)
#   - temporal:    fixed date + commencement
#   - actor/modal: "Valtioneuvosto voi antaa ..."
_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act>
    <body>
      <section eId="sec_1">
        <num>1 §</num>
        <content>
          <p>Jatteella tarkoitetaan poistettavaa ainetta.</p>
          <p>Tata lakia sovelletaan ymparistonsuojelulain (527/2014) 5 §:ssa tarkoitettuun toimintaan.</p>
          <p>Edella 1 momentissa tarkoitettuun toimintaan sovelletaan myos 5 §:n saannoksia.</p>
          <p>Valtioneuvosto voi antaa asetuksella tarkempia saannoksia.</p>
          <p>Hakijan on toimitettava hakemus viranomaiselle.</p>
          <p>Joka rikkoo tata lakia, rangaistaan sakolla.</p>
          <p>Edella saadettya ei kuitenkaan sovelleta vahaiseen toimintaan.</p>
          <p>Tama laki tulee voimaan 1.1.2027.</p>
        </content>
      </section>
    </body>
  </act>
</akomaNtoso>
""".encode("utf-8")


def _build():
    return build_legal_surface_graph(_XML, "123/2020", surface_time="2026-01-01")


def test_frame_node_kinds_are_registered_in_core() -> None:
    # The Phase 8 core edit: the four families are members of the closed vocab.
    assert "delegation_frame" in NODE_KINDS
    assert "procedure_frame" in NODE_KINDS
    assert "sanction_frame" in NODE_KINDS
    assert "exception_condition_cue" in NODE_KINDS


def test_build_graph_has_all_frame_families() -> None:
    graph = _build()
    kinds = {n.node_kind for n in graph.nodes.values()}
    # H5/H6 frame families (Phase 8).
    assert "delegation_frame" in kinds
    assert "procedure_frame" in kinds
    assert "sanction_frame" in kinds
    assert "exception_condition_cue" in kinds


def test_existing_families_still_present() -> None:
    graph = _build()
    kinds = {n.node_kind for n in graph.nodes.values()}
    assert "reference_expr" in kinds
    assert "reference_resolution" in kinds
    assert "definition_binding" in kinds
    assert "temporal_expr" in kinds
    assert "actor_modal_frame" in kinds


def test_frame_lens_runs_registered() -> None:
    graph = _build()
    lens_ids = {r.lens_id for r in graph.lens_runs}
    assert "fi.delegation.v0" in lens_ids
    assert "fi.procedure.v0" in lens_ids
    assert "fi.sanction.v0" in lens_ids
    assert "fi.exception_condition.v0" in lens_ids


def test_frame_statuses_in_closed_vocabulary() -> None:
    graph = _build()
    frame_kinds = {
        "delegation_frame",
        "procedure_frame",
        "sanction_frame",
        "exception_condition_cue",
    }
    frame_nodes = [n for n in graph.nodes.values() if n.node_kind in frame_kinds]
    assert frame_nodes  # at least one of each was asserted above; not empty
    for node in frame_nodes:
        assert node.node_status in NODE_STATUSES
        # Phase 8 surface facts are asserted, not resolution outcomes.
        assert node.node_status == "asserted"


def test_frame_spans_slice_back_to_surface() -> None:
    # The whole-body raw_text is the coordinate space the span char offsets index.
    from lawvm.finland.legal_surface.bundle import decode_body_text

    raw_text = decode_body_text(_XML)
    graph = _build()

    # Each frame family's anchored span must be non-empty and lie within the text.
    expectations = {
        "delegation_frame": "asetuksella",
        "procedure_frame": "hakemus",
        "sanction_frame": "rangaistaan",
        "exception_condition_cue": "ei kuitenkaan",
    }
    for kind, needle in expectations.items():
        nodes = [n for n in graph.nodes.values() if n.node_kind == kind]
        assert nodes, f"no {kind} node built"
        matched = False
        for node in nodes:
            ref = node.source_ref
            assert ref is not None
            sliced = raw_text[ref.char_start : ref.char_end]
            assert sliced  # non-empty slice
            if needle in sliced:
                matched = True
        assert matched, f"no {kind} span contained {needle!r}"


def test_firewall_holds_for_frame_nodes() -> None:
    graph = _build()
    for node in graph.nodes.values():
        assert node.surface_only is True
        assert node.replay_authorized is False
    for edge in graph.edges:
        assert edge.surface_only is True
        assert edge.replay_authorized is False
