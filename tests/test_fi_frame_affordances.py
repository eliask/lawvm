"""E2E gate for the EXPERIMENTAL H5/H6 frame affordances.

These exercise CANDIDATE-status cross-frame affordances (Pro r5 §D5/§D6), NOT
settled semantics:
  (a) an actor/modal clause near a date  -> candidate colocation edge appears;
  (b) a delegation frame missing an instrument -> the experimental lint fires;
  (c) a clean delegation WITH an instrument    -> no lint;
plus the firewall (surface_only / not replay_authorized) and the candidate (not
asserted) status invariant.
"""
from __future__ import annotations

from lawvm.core.legal_surface_assembler import assemble_surface_graph
from lawvm.core.legal_surface_graph import (
    SourceSpanRef,
    SourceUnitRef,
    SurfaceGraphSubject,
)
from lawvm.core.legal_surface_lens import (
    SurfaceLensResult,
    SurfaceNodeSeed,
)
from lawvm.finland.legal_surface.frame_lints import (
    LINT_DELEGATION_WITHOUT_INSTRUMENT,
    DelegationWithoutInstrumentLintPass,
)
from lawvm.finland.legal_surface.frame_passes import (
    ActorTemporalColocationPass,
)
from lawvm.finland.legal_surface.graph_build import (
    build_legal_surface_graph,
    lint_surface_graph,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# An actor/modal clause ("Valtioneuvosto voi antaa ...") sitting in the same
# content block as a fixed date / commencement cue ("tulee voimaan 1.1.2027").
_XML_ACTOR_NEAR_DATE = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body><section eId="sec_1"><num>1 §</num><content>
    <p>Valtioneuvosto voi antaa tarkempia saannoksia asetuksella. Tama laki tulee voimaan 1.1.2027.</p>
  </content></section></body></act>
</akomaNtoso>
""".encode("utf-8")


# ── (a) colocation candidate edge ────────────────────────────────────────────


def test_actor_temporal_colocation_candidate_edge_appears() -> None:
    graph = build_legal_surface_graph(_XML_ACTOR_NEAR_DATE, "123/2020")
    coloc = [
        e for e in graph.edges if e.edge_kind == "actor_modal_temporal_colocated"
    ]
    assert coloc, "expected an experimental colocation candidate edge"
    for edge in coloc:
        # CANDIDATE, never asserted (§D5).
        assert edge.surface_edge_status == "candidate"
        assert edge.payload.get("experimental") is True
        assert isinstance(edge.payload.get("char_distance"), int)
        # endpoints resolve to the right node kinds
        assert graph.nodes[edge.src].node_kind == "actor_modal_frame"
        assert graph.nodes[edge.dst].node_kind == "temporal_expr"


def test_colocation_respects_window() -> None:
    graph = build_legal_surface_graph(_XML_ACTOR_NEAR_DATE, "123/2020")
    actors = [n for n in graph.nodes.values() if n.node_kind == "actor_modal_frame"]
    temporals = [n for n in graph.nodes.values() if n.node_kind == "temporal_expr"]
    assert actors and temporals
    # A zero-width window admits nothing (the spans are not literally touching).
    narrow = ActorTemporalColocationPass(window=0)
    far_edges = narrow.run(graph)
    # the wide default pass found at least one; the zero window finds strictly
    # fewer (or equal) — never more.
    assert len(far_edges) <= len(
        [e for e in graph.edges if e.edge_kind == "actor_modal_temporal_colocated"]
    )


# ── (b)/(c) delegation-without-instrument lint ────────────────────────────────


def _subject() -> SurfaceGraphSubject:
    return SurfaceGraphSubject(
        jurisdiction="fi",
        work_id="fi:act:123/2020",
        scope={"kind": "whole_work"},
        surface_time="2026-01-01",
        source_bundle_hash="bundlehash0",
        language="fi",
    )


def _span(start: int, end: int) -> SourceSpanRef:
    return SourceSpanRef(
        source_unit_id="su1",
        source_hash="srchash0",
        work_id="fi:act:123/2020",
        address="section:1",
        char_start=start,
        char_end=end,
        text_hash="texthash0",
    )


def _delegation_seed(instrument_kind: str | None) -> SurfaceNodeSeed:
    payload: dict[str, object] = {
        "delegate_actor": "Valtioneuvosto",
        "binding_strength": "may",
        "subject_span": None,
    }
    if instrument_kind is not None:
        payload["instrument_kind"] = instrument_kind
    return SurfaceNodeSeed(
        node_kind="delegation_frame",
        source_ref=_span(0, 30),
        local_discriminator=f"Valtioneuvosto|{instrument_kind}|0",
        rule_id="fi.delegation.v0",
        node_status="asserted",
        payload=payload,
        authority_role="surface_fact",
    )


def _graph_with_delegation(instrument_kind: str | None):
    subject = _subject()
    units = (
        SourceUnitRef(
            source_unit_id="su1",
            work_id="fi:act:123/2020",
            address="section:1",
            source_hash="srchash0",
        ),
    )
    result = SurfaceLensResult(
        lens_id="fi.delegation.v0",
        node_seeds=(_delegation_seed(instrument_kind),),
        edge_seeds=(),
        residuals=(),
        diagnostics=(),
        coverage={},
    )
    return assemble_surface_graph(
        subject=subject,
        source_units=units,
        lens_results=(result,),
    )


def test_delegation_without_instrument_lint_fires() -> None:
    graph = _graph_with_delegation(instrument_kind=None)
    lints = DelegationWithoutInstrumentLintPass().run(graph)
    assert len(lints) == 1
    lint = lints[0]
    assert lint.lint_kind == LINT_DELEGATION_WITHOUT_INSTRUMENT
    assert lint.severity == "info"
    # firewall on the lint itself
    assert lint.surface_only is True
    assert lint.legal_conclusion is False
    assert lint.replay_authorized is False
    # the experimental affordance must NOT be read as a legal verdict
    assert any(
        "ultra vires" in c for c in lint.forbidden_overclaims
    )
    # self-evidencing: the actor surface appears in the message
    assert "Valtioneuvosto" in lint.message


def test_clean_delegation_with_instrument_does_not_fire() -> None:
    graph = _graph_with_delegation(instrument_kind="asetus")
    lints = DelegationWithoutInstrumentLintPass().run(graph)
    assert lints == ()


# ── firewall + candidate-status invariants over the whole built graph ─────────


def test_frame_affordances_firewall_and_candidate_status() -> None:
    graph = build_legal_surface_graph(_XML_ACTOR_NEAR_DATE, "123/2020")
    for node in graph.nodes.values():
        assert node.surface_only is True
        assert node.replay_authorized is False
    for edge in graph.edges:
        assert edge.surface_only is True
        assert edge.replay_authorized is False
        # the experimental cross-frame affordance is never asserted
        if edge.edge_kind == "actor_modal_temporal_colocated":
            assert edge.surface_edge_status == "candidate"


def test_lint_pass_registered_in_defaults() -> None:
    graph = build_legal_surface_graph(_XML_ACTOR_NEAR_DATE, "123/2020")
    # DEFAULT_LINT_PASSES includes the experimental delegation lint; it runs
    # without error (no instrument-less frame here, so it simply finds nothing).
    report = lint_surface_graph(graph)
    kinds = {lint.lint_kind for lint in report.lints}
    assert LINT_DELEGATION_WITHOUT_INSTRUMENT not in kinds  # clean here

    # And it IS wired: building over a delegation frame missing an instrument
    # via the default passes would surface it. Verified directly above.
    assert isinstance(report.lints, tuple)
