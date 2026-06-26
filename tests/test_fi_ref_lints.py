"""Reference lints AS GRAPH QUERIES (Pro r5 Phase 5, §D6 + §D7 firewall).

Each test builds a Legal Surface Graph that exercises one reference-resolution
status, runs the reference lint passes over it, and asserts:

  * the matching reference lint fires (self-evidencing, embeds the surface text);
  * the firewall holds on every lint (surface_only=True, legal_conclusion=False,
    never replay-authorized, forbidden_overclaims declared);
  * a clean all-resolved case fires NO reference lint.

OPEN and STATUTE_ONLY (by-name) statuses are produced END TO END through
``build_legal_surface_graph`` over synthetic statute XML (the recognizers assign
those cite_confidences directly). BROKEN and AMBIGUOUS are produced by feeding
hand-built reference_expr / reference_resolution seeds (the exact shape
``ReferenceLens`` emits) through the core assembler — those statuses come from a
separate broken-detector / registry pass that does not flow into the lens's node
status, so we mint them directly (§D6: a lint is a pure query over the assembled
graph regardless of how the nodes were minted).
"""
from __future__ import annotations

from lawvm.core.legal_surface_assembler import assemble_surface_graph
from lawvm.core.legal_surface_graph import SourceSpanRef, SourceUnitRef
from lawvm.core.legal_surface_lens import (
    SurfaceEdgeSeed,
    SurfaceLensResult,
    SurfaceNodeSeed,
)
from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.graph_build import (
    DEFAULT_LINT_PASSES,
    build_legal_surface_graph,
    lint_surface_graph,
)
from lawvm.finland.legal_surface.ref_lints import (
    LINT_AMBIGUOUS_REFERENCE,
    LINT_BROKEN_REFERENCE,
    LINT_OPEN_REFERENCE,
    LINT_UNRESOLVED_BY_NAME,
    AmbiguousReferenceLintPass,
    BrokenReferenceLintPass,
    OpenReferenceLintPass,
    StatuteOnlyMissLintPass,
    reference_lint_passes,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

_REF_LINT_KINDS = frozenset(
    {
        LINT_BROKEN_REFERENCE,
        LINT_OPEN_REFERENCE,
        LINT_UNRESOLVED_BY_NAME,
        LINT_AMBIGUOUS_REFERENCE,
    }
)


def _mk(body: str) -> bytes:
    return (
        f'<akomaNtoso xmlns="{_AKN}"><act><body>{body}</body></act></akomaNtoso>'
    ).encode("utf-8")


def _assert_firewall(lints) -> None:
    for lint in lints:
        assert lint.surface_only is True
        assert lint.legal_conclusion is False
        assert lint.replay_authorized is False
        assert lint.forbidden_overclaims  # non-empty
        # never a legal conclusion in the prose either
        assert "legally invalid" not in lint.message
        assert "void" not in lint.message


# ── E2E statuses via build_legal_surface_graph ───────────────────────────────


def test_open_reference_lint_fires_e2e() -> None:
    # A vague catch-all ("erikseen säädetään") → reference_resolution status open.
    xml = _mk(
        "<section><num>1 §</num><content>"
        "<p>Asiasta erikseen säädetään tarkemmin.</p>"
        "</content></section>"
    )
    graph = build_legal_surface_graph(xml, "1/2020")
    # precondition: at least one open reference_resolution node exists
    assert any(
        n.node_kind == "reference_resolution" and n.node_status == "open"
        for n in graph.nodes.values()
    )
    report = lint_surface_graph(graph)
    open_lints = [li for li in report.lints if li.lint_kind == LINT_OPEN_REFERENCE]
    assert open_lints
    assert all(li.severity == "info" for li in open_lints)
    # self-evidencing: the citation surface is embedded in the message
    assert any("erikseen säädetään" in li.message for li in open_lints)
    _assert_firewall(report.lints)


def test_statute_only_by_name_lint_fires_e2e() -> None:
    # A by-name reference ("lannoitelaissa") with no registry → statute_only with a
    # fi-name: target id = a registry coverage gap (reference.unresolved_by_name).
    xml = _mk(
        "<section><num>1 §</num><content>"
        "<p>Noudatetaan mitä lannoitelaissa säädetään.</p>"
        "</content></section>"
    )
    graph = build_legal_surface_graph(xml, "2/2020")
    # precondition: the expr carries a fi-name: target id
    assert any(
        n.node_kind == "reference_expr"
        and str(n.payload.get("target_id") or "").startswith("fi-name:")
        for n in graph.nodes.values()
    )
    report = lint_surface_graph(graph)
    miss = [li for li in report.lints if li.lint_kind == LINT_UNRESOLVED_BY_NAME]
    assert miss
    assert any("lannoitelaissa" in li.message for li in miss)
    # the lint supports its finding with the by-name reference_expr node
    for li in miss:
        assert li.support_node_ids
    _assert_firewall(report.lints)


def test_clean_resolved_reference_fires_no_reference_lint() -> None:
    # A by-name reference RESOLVED to a single id via a registry → status resolved,
    # no reference lint should fire (refers_to asserted, nothing unresolved).
    xml = _mk(
        "<section><num>1 §</num><content>"
        "<p>Noudatetaan mitä lannoitelaissa säädetään.</p>"
        "</content></section>"
    )
    registry = _StubStatuteRegistry({"lannoitelaki": ["2022/711"]})
    graph = build_legal_surface_graph(xml, "3/2020", statute_registry=registry)
    # precondition: the resolution endpoint asserted a refers_to to one entity
    assert any(e.edge_kind == "refers_to" for e in graph.edges)
    report = lint_surface_graph(graph)
    ref_lints = [li for li in report.lints if li.lint_kind in _REF_LINT_KINDS]
    assert ref_lints == []
    _assert_firewall(report.lints)


# ── BROKEN / AMBIGUOUS via hand-built seeds through the assembler ─────────────


def _bundle_subject_units():
    """A real bundle's subject + source units to anchor hand-built seeds."""
    xml = _mk(
        "<section><num>1 §</num><content>"
        "<p>Noudatetaan mitä viittauslaissa säädetään.</p>"
        "</content></section>"
    )
    bundle = build_surface_bundle(xml, "9/2020")
    unit = bundle.units[0]
    source_units = (
        SourceUnitRef(
            source_unit_id=unit.source_unit_id,
            work_id=unit.work_id,
            address=unit.address,
            source_hash=unit.source_hash,
        ),
    )
    return bundle.subject, source_units, unit


def _span(unit, char_start: int = 0, char_end: int = 10) -> SourceSpanRef:
    return SourceSpanRef(
        source_unit_id=unit.source_unit_id,
        source_hash=unit.source_hash,
        work_id=unit.work_id,
        address=unit.address,
        char_start=char_start,
        char_end=char_end,
        text_hash="t" * 16,
    )


def _ref_pair_result(
    *,
    status: str,
    surface_text: str,
    expr_payload: dict | None = None,
    resolution_payload: dict | None = None,
    unit,
) -> SurfaceLensResult:
    """A minimal lens result: one reference_expr + reference_resolution + edge,
    matching the exact node shape ``ReferenceLens`` emits."""
    span = _span(unit)
    expr_local = "synthetic::expr#0"
    res_local = "synthetic::resolution#0"
    exprp = {"surface_text": surface_text, "cite_kind": "cross_statute"}
    exprp.update(expr_payload or {})
    resp = {"surface_text": surface_text}
    resp.update(resolution_payload or {})
    return SurfaceLensResult(
        lens_id="fi.references.v0",
        node_seeds=(
            SurfaceNodeSeed(
                node_kind="reference_expr",
                source_ref=span,
                local_discriminator=expr_local,
                rule_id="fi.references.v0.reference_expr",
                node_status=status,
                payload=exprp,
                authority_role="surface_fact",
            ),
            SurfaceNodeSeed(
                node_kind="reference_resolution",
                source_ref=span,
                local_discriminator=res_local,
                rule_id="fi.references.v0.reference_resolution",
                node_status=status,
                payload=resp,
                authority_role="surface_fact",
            ),
        ),
        edge_seeds=(
            SurfaceEdgeSeed(
                edge_kind="resolution_of",
                src_local=res_local,
                dst_local=expr_local,
                rule_id="fi.references.v0.resolution_of",
                surface_edge_status="asserted",
                payload={},
            ),
        ),
        residuals=(),
        diagnostics=(),
        coverage={},
    )


def _assemble_one(result: SurfaceLensResult):
    subject, source_units, _unit = _bundle_subject_units()
    return assemble_surface_graph(
        subject=subject,
        source_units=source_units,
        lens_results=(result,),
    )


def test_broken_reference_lint_fires() -> None:
    _subject, _su, unit = _bundle_subject_units()
    result = _ref_pair_result(
        status="broken",
        surface_text="kumotussa laissa (123/1990)",
        unit=unit,
    )
    graph = _assemble_one(result)
    report = run_lint_passes_helper(graph)
    broken = [li for li in report if li.lint_kind == LINT_BROKEN_REFERENCE]
    assert broken
    assert all(li.severity == "warning" for li in broken)
    assert any("kumotussa laissa (123/1990)" in li.message for li in broken)
    # subject is the reference_resolution node
    for li in broken:
        assert graph.nodes[li.subject_node_id].node_kind == "reference_resolution"
        # support is the reference_expr it resolves
        assert li.support_node_ids
        assert all(
            graph.nodes[s].node_kind == "reference_expr" for s in li.support_node_ids
        )
    _assert_firewall(report)


def test_ambiguous_reference_lint_fires() -> None:
    _subject, _su, unit = _bundle_subject_units()
    result = _ref_pair_result(
        status="ambiguous",
        surface_text="kaivoslaissa",
        resolution_payload={"candidates": ["1965/503", "2011/621"]},
        unit=unit,
    )
    graph = _assemble_one(result)
    report = run_lint_passes_helper(graph)
    amb = [li for li in report if li.lint_kind == LINT_AMBIGUOUS_REFERENCE]
    assert amb
    assert all(li.severity == "warning" for li in amb)
    # self-evidencing + candidate count surfaced
    assert any("kaivoslaissa" in li.message and "2 candidate" in li.message for li in amb)
    _assert_firewall(report)


def test_numeric_statute_only_is_not_a_by_name_miss() -> None:
    # A statute_only resolution whose expr carries a NUMERIC target id (act known,
    # provision pending) is NOT a registry coverage gap → no unresolved_by_name.
    _subject, _su, unit = _bundle_subject_units()
    result = _ref_pair_result(
        status="statute_only",
        surface_text="lain 5 §",
        expr_payload={"target_id": "2022/711"},
        unit=unit,
    )
    graph = _assemble_one(result)
    report = run_lint_passes_helper(graph)
    miss = [li for li in report if li.lint_kind == LINT_UNRESOLVED_BY_NAME]
    assert miss == []


# ── pass-set + ordering sanity ───────────────────────────────────────────────


def run_lint_passes_helper(graph):
    """Run ONLY the reference lint passes over the graph (returns the lint tuple)."""
    return lint_surface_graph(graph, lint_passes=reference_lint_passes()).lints


def test_reference_lint_passes_are_surface_only() -> None:
    for lint_pass in reference_lint_passes():
        assert lint_pass.surface_only is True
        assert lint_pass.jurisdiction == "fi"


def test_default_lint_passes_include_reference_lints() -> None:
    pass_types = {type(p) for p in DEFAULT_LINT_PASSES}
    assert BrokenReferenceLintPass in pass_types
    assert OpenReferenceLintPass in pass_types
    assert StatuteOnlyMissLintPass in pass_types
    assert AmbiguousReferenceLintPass in pass_types


# ── Test doubles ─────────────────────────────────────────────────────────────


class _StubLookupResult:
    def __init__(self, candidates: list[str]) -> None:
        if len(candidates) == 0:
            self.status = "none"
        elif len(candidates) == 1:
            self.status = "single"
        else:
            self.status = "multiple"
        self.candidates = tuple(_StubCandidate(c) for c in candidates)


class _StubCandidate:
    def __init__(self, statute_id: str) -> None:
        self.statute_id = statute_id


class _StubStatuteRegistry:
    """Minimal StatuteNameRegistry stand-in for resolve_mentions routing."""

    def __init__(self, table: dict[str, list[str]]) -> None:
        self._table = table

    def lookup(self, name: str, as_of: object = None) -> _StubLookupResult:
        return _StubLookupResult(self._table.get(name, []))
