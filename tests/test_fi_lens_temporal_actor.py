"""E2E test for the H3 temporal + H4 actor/modal surface lenses (Pro r5 Phase 6).

Builds a SourceSurfaceBundle from a synthetic statute containing a date and a
modal clause, runs both lenses, assembles via ``assemble_surface_graph``, and
asserts:
  * ``temporal_expr`` + ``actor_modal_frame`` nodes exist,
  * each node's source span slices back to the surface in raw_text,
  * statuses are in the closed NODE_STATUSES vocabulary,
  * the actor/modal recognizer's residual became a ``surface_residual`` node,
  * the authority firewall holds (every node surface_only / not replay_authorized),
  * no cross-lens edges were emitted (Phase 6 = nodes only).
"""
from __future__ import annotations

from lawvm.core.legal_surface_assembler import assemble_surface_graph
from lawvm.core.legal_surface_graph import NODE_STATUSES, SourceUnitRef
from lawvm.core.legal_surface_lens import SurfaceAnalysisContext
from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.lenses.actor_modal import ActorModalLens
from lawvm.finland.legal_surface.lenses.temporal import TemporalLens

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act>
    <body>
      <section eId="sec_1">
        <num>1 §</num>
        <content>
          <p>Tama laki tulee voimaan 1.1.2027.</p>
          <p>Valtioneuvosto antaa asetuksella tarkemmat saannokset.</p>
        </content>
      </section>
    </body>
  </act>
</akomaNtoso>
""".encode("utf-8")


def _build_graph():
    bundle = build_surface_bundle(_XML, "999/2026", surface_time="2026-01-01")
    ctx = SurfaceAnalysisContext(surface_time="2026-01-01")
    temporal = TemporalLens().analyze(bundle, context=ctx)
    actor_modal = ActorModalLens().analyze(bundle, context=ctx)
    unit = bundle.units[0]
    source_units = (
        SourceUnitRef(
            source_unit_id=unit.source_unit_id,
            work_id=unit.work_id,
            address=unit.address,
            source_hash=unit.source_hash,
        ),
    )
    graph = assemble_surface_graph(
        subject=bundle.subject,
        source_units=source_units,
        lens_results=(temporal, actor_modal),
    )
    return bundle, graph


def test_temporal_and_actor_modal_nodes_exist() -> None:
    _bundle, graph = _build_graph()
    kinds = {n.node_kind for n in graph.nodes.values()}
    assert "temporal_expr" in kinds
    assert "actor_modal_frame" in kinds


def test_temporal_node_payload_and_date_value() -> None:
    _bundle, graph = _build_graph()
    temporal_nodes = [n for n in graph.nodes.values() if n.node_kind == "temporal_expr"]
    # the synthetic text has a commencement cue and a fixed date
    by_kind = {n.payload["temporal_kind"]: n for n in temporal_nodes}
    assert "fixed_date" in by_kind
    assert "commencement" in by_kind
    fixed = by_kind["fixed_date"]
    assert fixed.payload["date_value"] == "2027-01-01"
    assert fixed.payload["node_status"] == "resolved"
    assert fixed.node_status == "resolved"
    # commencement is resolved but carries no calendar date
    assert by_kind["commencement"].payload["date_value"] is None


def test_actor_modal_node_payload() -> None:
    _bundle, graph = _build_graph()
    frames = [n for n in graph.nodes.values() if n.node_kind == "actor_modal_frame"]
    assert frames
    frame = frames[0]
    assert frame.payload["actor_surface"] == "Valtioneuvosto"
    assert frame.payload["modal_token"] == "antaa"
    assert frame.payload["polarity"] == "positive"
    assert frame.payload["voice"] == "active"
    assert frame.node_status == "asserted"
    # object span is a [start, end] pair into raw_text
    assert isinstance(frame.payload["object_span"], list)
    assert len(frame.payload["object_span"]) == 2


def test_actor_modal_residual_became_surface_residual_node() -> None:
    _bundle, graph = _build_graph()
    residuals = [n for n in graph.nodes.values() if n.node_kind == "surface_residual"]
    # "tulee voimaan" -> "tulee" fires as a modal_without_actor residual.
    assert any(
        n.payload.get("reason_code") == "modal_without_actor" for n in residuals
    )


def test_spans_slice_back_to_surface() -> None:
    bundle, graph = _build_graph()
    raw = bundle.units[0].raw_text
    surface_kinds = {"temporal_expr", "actor_modal_frame", "surface_residual"}
    checked = 0
    for node in graph.nodes.values():
        if node.node_kind not in surface_kinds:
            continue
        ref = node.source_ref
        assert ref is not None
        sliced = raw[ref.char_start : ref.char_end]
        # the whole-frame surface for actor_modal must contain the actor surface;
        # the exact sliced surface is the recognizer span, which is always real text.
        assert sliced != ""
        if node.node_kind == "temporal_expr":
            assert sliced == node.payload["surface_text"]
        if node.node_kind == "actor_modal_frame":
            assert node.payload["actor_surface"] in sliced
        checked += 1
    assert checked >= 3


def test_statuses_are_valid() -> None:
    _bundle, graph = _build_graph()
    for node in graph.nodes.values():
        assert node.node_status in NODE_STATUSES


def test_firewall_holds_no_edges() -> None:
    _bundle, graph = _build_graph()
    for node in graph.nodes.values():
        assert node.surface_only is True
        assert node.replay_authorized is False
    # Phase 6 = nodes only: neither lens emits any edge.
    assert graph.edges == ()


def test_temporal_lens_protocol_attrs() -> None:
    lens = TemporalLens()
    assert lens.lens_id == "fi.temporal.v0"
    assert lens.produces_node_kinds == ("temporal_expr",)
    assert lens.produces_edge_kinds == ()
    assert lens.required_views == ("raw_text",)


def test_actor_modal_lens_protocol_attrs() -> None:
    lens = ActorModalLens()
    assert lens.lens_id == "fi.actor_modal.v0"
    assert lens.produces_node_kinds == ("actor_modal_frame",)
    assert lens.produces_edge_kinds == ()
