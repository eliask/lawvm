"""End-to-end: build_legal_surface_graph over a synthetic Finnish statute."""
from __future__ import annotations

from lawvm.finland.legal_surface.graph_build import (
    build_legal_surface_graph,
    lint_surface_graph,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# A statute body exercising multiple lenses: a cross-statute id cite + internal §
# (references), a fixed date + commencement (temporal), and a modal clause
# (actor/modal).
_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act>
    <body>
      <section eId="sec_1">
        <num>1 §</num>
        <content>
          <p>Tata lakia sovelletaan ymparistonsuojelulain (527/2014) 5 §:ssa tarkoitettuun toimintaan.</p>
          <p>Edella 1 momentissa tarkoitettuun toimintaan sovelletaan myos 5 §:n saannoksia.</p>
          <p>Valtioneuvosto voi antaa tarkempia saannoksia asetuksella.</p>
          <p>Tama laki tulee voimaan 1.1.2027.</p>
        </content>
      </section>
    </body>
  </act>
</akomaNtoso>
""".encode("utf-8")


def test_build_graph_has_nodes_from_each_lens() -> None:
    graph = build_legal_surface_graph(_XML, "123/2020", surface_time="2026-01-01")
    kinds = {n.node_kind for n in graph.nodes.values()}
    # references lens
    assert "reference_expr" in kinds
    assert "reference_resolution" in kinds
    # temporal lens (the fixed date / commencement)
    assert "temporal_expr" in kinds
    # actor/modal lens (Valtioneuvosto voi antaa ...)
    assert "actor_modal_frame" in kinds


def test_build_graph_firewall_holds() -> None:
    graph = build_legal_surface_graph(_XML, "123/2020")
    for node in graph.nodes.values():
        assert node.surface_only is True
        assert node.replay_authorized is False
    for edge in graph.edges:
        assert edge.surface_only is True
        assert edge.replay_authorized is False


def test_build_graph_deterministic_graph_id() -> None:
    g1 = build_legal_surface_graph(_XML, "123/2020", surface_time="2026-01-01")
    g2 = build_legal_surface_graph(_XML, "123/2020", surface_time="2026-01-01")
    assert g1.graph_id == g2.graph_id


def test_build_graph_subject_and_lens_runs() -> None:
    graph = build_legal_surface_graph(_XML, "123/2020")
    assert graph.subject.work_id == "123/2020"
    lens_ids = {r.lens_id for r in graph.lens_runs}
    assert "fi.references.v0" in lens_ids
    assert "fi.definitions.v0" in lens_ids
    assert "fi.temporal.v0" in lens_ids
    assert "fi.actor_modal.v0" in lens_ids


def test_lint_surface_graph_runs_and_is_surface_only() -> None:
    # A statute defining a term that is never used -> dead_definition lint.
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body><section eId="sec_1"><num>1 §</num><content>
    <p>Tassa laissa tarkoitetaan jatteella poistettavaa ainetta.</p>
    <p>Sovelletaan yleisia saannoksia.</p>
  </content></section></body></act>
</akomaNtoso>
""".encode("utf-8")
    graph = build_legal_surface_graph(xml, "123/2020")
    report = lint_surface_graph(graph)
    # The report is well-formed and every lint is a surface fact, never a legal
    # conclusion (the firewall on the lint type).
    for lint in report.lints:
        assert lint.surface_only is True
        assert lint.legal_conclusion is False
        assert lint.lint_kind.startswith("definition.")
