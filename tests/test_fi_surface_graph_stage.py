"""Legal-surface waist (StageResult endgame row #5) conversion tests.

Covers ``build_legal_surface_graph_staged`` returning a
``StageResult[LegalSurfaceGraph]`` whose ``coverage`` is the graph's node-status
taxonomy projected onto the canonical four-class partition (the 2D mapping:
``broken`` is the ONLY violation; ``ambiguous`` is a non-blocking frontier
residual), whose ``residuals`` carry one typed record per non-owned node, and
whose ``evidence`` re-carries the upstream #2 bundle witness.

The load-bearing FIRE-DRILL drives the PRODUCTION ``bill_analysis`` report
assembler (the sink ``main`` calls) and proves the broken-reference risk is
DERIVED FROM the typed ``unowned_violation`` residual — strip the residual and
the broken-ref signal disappears (the consumer is NOT re-scanning bare per-node
status strings).
"""
from __future__ import annotations

from typing import Any

import pytest

from lawvm.core.legal_surface_graph import (
    AuthorityRole,
    LegalSurfaceGraph,
    SourceSpanRef,
    SurfaceGraphSubject,
    SurfaceNode,
)
from lawvm.core.stage_result import (
    CoverageCertificate,
    StageResult,
)
from lawvm.finland.johtolause.types import ParsedOp
from lawvm.finland.legal_surface import graph_build
from lawvm.finland.legal_surface.graph_build import (
    build_legal_surface_graph,
    build_legal_surface_graph_staged,
)
from lawvm.tools import bill_analysis as ba

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# A clean statute body with one internal reference (no broken target).
_CLEAN_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act>
    <body>
      <section eId="sec_1">
        <num>1 §</num>
        <content>
          <p>Tata lakia sovelletaan 2 §:ssa tarkoitettuun toimintaan.</p>
        </content>
      </section>
      <section eId="sec_2">
        <num>2 §</num>
        <content>
          <p>Toiminnasta saadetaan tarkemmin valtioneuvoston asetuksella.</p>
        </content>
      </section>
    </body>
  </act>
</akomaNtoso>
""".encode("utf-8")

_CLEAN_ID = "200/2025"


# ---------------------------------------------------------------------------
# (a) staged producer shape + value 0-delta + partition holds
# ---------------------------------------------------------------------------


def test_staged_returns_stage_result_of_graph() -> None:
    stage = build_legal_surface_graph_staged(_CLEAN_XML, _CLEAN_ID)
    assert isinstance(stage, StageResult)
    assert isinstance(stage.value, LegalSurfaceGraph)
    # value path is byte-identical to the value-only wrapper (0-delta).
    wrapped = build_legal_surface_graph(_CLEAN_XML, _CLEAN_ID)
    assert stage.value == wrapped


def test_coverage_is_total_partition_over_nodes() -> None:
    stage = build_legal_surface_graph_staged(_CLEAN_XML, _CLEAN_ID)
    coverage = stage.coverage
    assert isinstance(coverage, CoverageCertificate)
    assert coverage.unit == "surface_nodes"
    # total counts every node; every node carries exactly one status class.
    assert coverage.total == len(stage.value.nodes)
    assert coverage.is_partition()


def test_clean_statute_has_no_violation_and_neutral_authority() -> None:
    stage = build_legal_surface_graph_staged(_CLEAN_XML, _CLEAN_ID)
    # A clean statute carries no broken reference -> no violation class.
    assert stage.coverage.violation == 0
    # No blocking residual on a clean statute.
    assert not stage.has_blocking_residual
    # Surface facts are never replay authority (ESCALATE-1D / firewall default).
    assert stage.authority.is_neutral
    assert not stage.authority.replay_authorized
    # Evidence re-carries the upstream #2 bundle witness footing (not empty).
    assert not stage.evidence.is_empty


def test_residuals_partition_matches_coverage_classes() -> None:
    stage = build_legal_surface_graph_staged(_CLEAN_XML, _CLEAN_ID)
    blocking = [r for r in stage.residuals if r.kind == "unowned_violation"]
    typed = [r for r in stage.residuals if r.kind == "typed_residual"]
    # one blocking residual per violation, one typed residual per residual-class node.
    assert len(blocking) == stage.coverage.violation
    assert len(typed) == stage.coverage.residual
    assert all(r.blocking for r in blocking)
    assert all(not r.blocking for r in typed)


# ---------------------------------------------------------------------------
# Synthetic-graph helpers for the broken-reference cases (corpus-free).
# A `broken` node requires a cite naming a target that does not exist; building
# one from registry resolution is corpus-heavy, so the broken case uses a
# synthetic graph routed through the PRODUCTION account projection
# (_surface_graph_stage_account) — the same code path the real producer runs.
# ---------------------------------------------------------------------------

_BODY = "Kumotaan 7 pykala. Tama viittaa 7 pykalaan jota ei ole."


def _span(start: int, end: int) -> SourceSpanRef:
    return SourceSpanRef(
        source_unit_id="u#1",
        source_hash="h",
        work_id="999/1",
        address=None,
        char_start=start,
        char_end=end,
        text_hash="t",
    )


def _node(
    node_id: str,
    node_kind: str,
    *,
    status: str,
    payload: dict[str, Any],
    span: SourceSpanRef | None,
    authority_role: AuthorityRole = "surface_fact",
) -> SurfaceNode:
    return SurfaceNode(
        node_id=node_id,
        node_kind=node_kind,
        authority_role=authority_role,
        jurisdiction="fi",
        source_ref=span,
        lens_id="test",
        rule_id="test",
        status=status,
        payload_hash="ph",
        payload=payload,
    )


def _graph(nodes: list[SurfaceNode]) -> LegalSurfaceGraph:
    subject = SurfaceGraphSubject(
        jurisdiction="fi",
        work_id="999/1",
        scope={},
        surface_time=None,
        source_bundle_hash="b",
        language="fi",
    )
    return LegalSurfaceGraph(
        schema="test",
        graph_id="g#1",
        subject=subject,
        source_units=(),
        lens_runs=(),
        nodes={n.node_id: n for n in nodes},
        edges=(),
        build_diagnostics=(),
    )


def _broken_graph() -> LegalSurfaceGraph:
    """A graph carrying a single BROKEN reference (named a nonexistent target)."""
    return _graph(
        [
            _node(
                "ref#broken",
                "reference_resolution",
                status="broken",
                payload={"surface_text": "7 pykala", "candidates": []},
                span=_span(9, 17),
            ),
            _node(
                "ref#ok",
                "reference_resolution",
                status="resolved",
                payload={
                    "surface_text": "2 pykala",
                    "work_id": "999/1",
                    "candidates": [],
                },
                span=None,
            ),
        ]
    )


def _staged(graph: LegalSurfaceGraph) -> StageResult[LegalSurfaceGraph]:
    coverage, residuals = graph_build._surface_graph_stage_account(graph)
    return StageResult(value=graph, residuals=residuals, coverage=coverage)


# ---------------------------------------------------------------------------
# (b) a broken ref -> coverage.violation > 0 + blocking residual
# ---------------------------------------------------------------------------


def test_broken_ref_yields_violation_and_blocking_residual() -> None:
    stage = _staged(_broken_graph())
    assert stage.coverage.violation == 1
    assert not stage.coverage.is_clean
    assert stage.coverage.is_partition()
    blocking = [r for r in stage.residuals if r.kind == "unowned_violation"]
    assert len(blocking) == 1
    res = blocking[0]
    assert res.blocking is True
    assert res.scope == "ref#broken"
    assert res.text == "7 pykala"
    assert stage.has_blocking_residual


def test_status_outside_taxonomy_fails_loud() -> None:
    bad = _graph(
        [
            _node(
                "ref#bad",
                "reference_resolution",
                status="totally_made_up_status",
                payload={"surface_text": "x"},
                span=None,
            )
        ]
    )
    with pytest.raises(ValueError, match="outside the closed surface coverage"):
        graph_build._surface_graph_stage_account(bad)


# ---------------------------------------------------------------------------
# (c) FIRE-DRILL — drive the PRODUCTION bill_analysis report assembler and prove
#     the broken-ref risk is DERIVED FROM the typed unowned_violation residual.
# ---------------------------------------------------------------------------


def _repeal_op() -> ParsedOp:
    return ParsedOp(
        verb="K",
        kind="P",
        chapter="",
        number="7",
        momentti=0,
        item="",
        raw="K P 7",
        part="",
    )


def test_fire_drill_bill_report_broken_ref_rides_typed_residual() -> None:
    """The production sink (build_bill_report) surfaces the broken-ref risk from
    the typed residual, NOT a bare-string node scan.

    PROOF IT BITES (severance): the SAME graph value with its residuals stripped
    must produce NO broken-ref risk. If the consumer were reverted to bare-string
    scanning (``status == "broken"`` over the graph nodes), the stripped-residual
    stage would STILL find the broken node and this assertion would go RED — which
    is exactly the regression guard.
    """
    ops = [_repeal_op()]
    graph = _broken_graph()
    stage = _staged(graph)

    # 1. Drive the PRODUCTION report assembler (what main() calls after
    #    _build_graph_stage). The broken-ref risk must be present AND it must be
    #    backed by a kind="unowned_violation" residual in the SAME stage.
    report = ba.build_bill_report("999/1", ops, stage, _BODY)
    brr = report["surface_delta"]["broken_ref_risk"]
    assert len(brr["status_broken"]) == 1
    assert brr["status_broken"][0]["surface_text"] == "7 pykala"
    # the consumer's signal IS derived from the typed residual:
    assert any(r.kind == "unowned_violation" for r in stage.residuals)

    # 2. SEVERANCE: strip the residual (simulate the typed channel being cut /
    #    the consumer reverted to bare-string scanning). With the residual gone
    #    the broken-ref risk MUST disappear — proving the branch rides the typed
    #    residual, not the bare node status.
    severed = StageResult(value=graph, residuals=(), coverage=stage.coverage)
    severed_report = ba.build_bill_report("999/1", ops, severed, _BODY)
    severed_brr = severed_report["surface_delta"]["broken_ref_risk"]
    assert len(severed_brr["status_broken"]) == 0

    # 3. the unowned-channel candidate layer (repeal_strands_reference) feeds off
    #    status_broken (the typed-residual arm) PLUS the within-bill heuristic arm.
    #    Severing the residual must DROP exactly the status_broken-sourced
    #    candidate(s) — the within-bill arm (a textual cite of the repealed number)
    #    correctly survives, so the rule's candidate count falls by exactly the
    #    number of broken-status entries the residual contributed.
    def _strand_count(rep: dict[str, Any]) -> int:
        return sum(
            1
            for c in rep["unowned_channel_candidates"]["candidates"]
            if c["rule"] == "repeal_strands_reference"
        )

    full_strands = _strand_count(report)
    severed_strands = _strand_count(severed_report)
    assert full_strands - severed_strands == len(brr["status_broken"])
    assert len(brr["status_broken"]) == 1
