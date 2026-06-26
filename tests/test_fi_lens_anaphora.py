"""AnaphoraLens — discourse anaphora references as Legal Surface Graph seeds."""
from __future__ import annotations

from typing import cast

from lawvm.core.legal_surface_lens import SurfaceAnalysisContext
from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph
from lawvm.finland.legal_surface.lenses.anaphora import LENS_ID, AnaphoraLens

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _xml(*paragraphs: str) -> bytes:
    body = "\n".join(f"<p>{p}</p>" for p in paragraphs)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body><section eId="sec_1"><num>1 §</num><content>
    {body}
  </content></section></body></act>
</akomaNtoso>
""".encode("utf-8")


# A concrete by-name antecedent (ympäristönsuojelulakia) followed by an anaphor
# (mainitun lain) that binds back to it -> RESOLVED.
_XML_RESOLVED = _xml(
    "Sovelletaan ympäristönsuojelulakia.",
    "Mainitun lain nojalla annetaan tarkempia säännöksiä.",
)

# An anaphor with no preceding antecedent of the matching kind -> OPEN.
_XML_OPEN = _xml("Mainitun lain nojalla toimitaan ilman edeltävää viittausta.")


def _run(xml: bytes, statute_id: str = "123/2020"):
    bundle = build_surface_bundle(xml, statute_id)
    context = SurfaceAnalysisContext(surface_time=None, options={})
    return AnaphoraLens().analyze(bundle, context=context)


def test_anaphora_resolved_emits_expr_and_resolution() -> None:
    result = _run(_XML_RESOLVED)
    assert result.lens_id == LENS_ID

    exprs = [n for n in result.node_seeds if n.node_kind == "reference_expr"]
    resolutions = [n for n in result.node_seeds if n.node_kind == "reference_resolution"]
    assert len(exprs) == 1
    assert len(resolutions) == 1

    expr = exprs[0]
    assert expr.payload["anaphor_surface"] == "Mainitun lain"
    assert expr.node_status == "resolved"

    resolution = resolutions[0]
    assert resolution.node_status == "resolved"
    # The recognizer bound the anaphor to its in-text antecedent (the named act).
    assert resolution.payload["target_provision_ref"] is not None
    assert "ympäristönsuojelulaki" in str(resolution.payload["target_provision_ref"])

    # Exactly one resolution_of edge: resolution -> expr.
    edges = [e for e in result.edge_seeds if e.edge_kind == "resolution_of"]
    assert len(edges) == 1
    assert edges[0].src_local == resolution.local_discriminator
    assert edges[0].dst_local == expr.local_discriminator
    assert edges[0].surface_edge_status == "asserted"


def test_anaphora_open_emits_expr_and_open_resolution_not_dropped() -> None:
    result = _run(_XML_OPEN)
    exprs = [n for n in result.node_seeds if n.node_kind == "reference_expr"]
    resolutions = [n for n in result.node_seeds if n.node_kind == "reference_resolution"]
    # An OPEN anaphor still emits BOTH nodes — never a silent drop.
    assert len(exprs) == 1
    assert len(resolutions) == 1
    assert exprs[0].node_status == "open"
    assert resolutions[0].node_status == "open"
    # No target is fabricated for an unresolvable discourse reference.
    assert resolutions[0].payload["target_provision_ref"] is None
    # And the intrinsic resolution_of edge is still present.
    assert any(e.edge_kind == "resolution_of" for e in result.edge_seeds)


def test_anaphora_lens_firewall_surface_only() -> None:
    # The lens emits only surface_fact nodes (never replay_authorized); confirm
    # at the assembled-graph level via build_legal_surface_graph.
    graph = build_legal_surface_graph(_XML_RESOLVED, "123/2020")
    anaphora_nodes = [n for n in graph.nodes.values() if n.lens_id == LENS_ID]
    assert anaphora_nodes  # the lens produced nodes
    for node in anaphora_nodes:
        assert node.surface_only is True
        assert node.replay_authorized is False
        assert node.authority_role == "surface_fact"


def test_anaphora_lens_deterministic() -> None:
    r1 = _run(_XML_RESOLVED)
    r2 = _run(_XML_RESOLVED)
    # Deterministic global index -> identical discriminators / payloads / edges.
    assert [n.local_discriminator for n in r1.node_seeds] == [
        n.local_discriminator for n in r2.node_seeds
    ]
    assert [n.payload for n in r1.node_seeds] == [n.payload for n in r2.node_seeds]
    assert [(e.src_local, e.dst_local) for e in r1.edge_seeds] == [
        (e.src_local, e.dst_local) for e in r2.edge_seeds
    ]


def test_anaphora_no_id_collision_with_reference_lens() -> None:
    # The distinct lens_id keeps the anaphora reference_expr/resolution node ids
    # disjoint from the H1 ReferenceLens nodes even at the same span/kind: a
    # build with the full default lens set assembles without an id collision and
    # the anaphora nodes carry the distinct lens_id.
    graph = build_legal_surface_graph(_XML_RESOLVED, "123/2020")
    lens_ids = {n.lens_id for n in graph.nodes.values()}
    assert LENS_ID in lens_ids
    assert "fi.references.v0" in lens_ids
    # Both lenses can mint reference_expr nodes; with distinct lens_ids their ids
    # never collide (assembly above would have raised on a divergent-payload
    # collision). Confirm both contribute reference_expr nodes.
    expr_lens_ids = {
        n.lens_id for n in graph.nodes.values() if n.node_kind == "reference_expr"
    }
    assert {LENS_ID, "fi.references.v0"} <= expr_lens_ids


def test_anaphora_end_to_end_in_lens_runs() -> None:
    graph = build_legal_surface_graph(_XML_RESOLVED, "123/2020")
    run = next((r for r in graph.lens_runs if r.lens_id == LENS_ID), None)
    assert run is not None
    assert "reference_expr" in run.produced_node_kinds
    assert "reference_resolution" in run.produced_node_kinds
    assert "resolution_of" in run.produced_edge_kinds
    # The run actually produced anaphor nodes (coverage tallies an anaphor).
    coverage = cast(dict[str, int], run.coverage)
    assert coverage["anaphors"] >= 1
    assert coverage["resolved"] >= 1
