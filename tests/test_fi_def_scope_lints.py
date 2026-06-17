"""Definition SCOPE / SHADOWING lints AS GRAPH QUERIES (Pro r5 §D6 + §D7).

The ``DefinitionShadowingLintPass`` surfaces, as a SURFACE FACT, that the same
canonical term carries bindings of differing *scope shape* (a statute-wide
definitions-section definition coexisting with a local alias) — the narrower
shape shadows the broader one. ``DefinitionScopeAnnotationLintPass`` surfaces the
declared scope + scope shape of each participating binding.

NOTE ON SCOPE DERIVABILITY: the H2 binder stamps every binding ``scope="statute"``
(there is no chapter-vs-statute distinction in the graph today). The shadowing
signal is therefore derived from the ``binding_kind`` *scope shape*
(``tarkoitetaan`` = broad statute-wide definition; ``parenthetical_alias`` /
``jaljempana`` = narrow local alias), purely from graph node data.

A graph carrying one canonical term with bindings of TWO distinct kinds is built
by feeding hand-built ``definition_binding`` / ``term_symbol_entity`` /
``defines_term`` seeds (the exact shape ``DefinitionLens`` emits) through the
core assembler — mirroring ``test_fi_ref_lints.py``. The DISJOINTNESS case (two
same-kind bindings → duplicate lint fires, shadowing does NOT) is exercised
END TO END through ``build_legal_surface_graph`` over synthetic statute XML.
"""
from __future__ import annotations

from lawvm.core.legal_surface_assembler import assemble_surface_graph
from lawvm.core.legal_surface_graph import (
    LegalSurfaceGraph,
    SourceSpanRef,
    SourceUnitRef,
)
from lawvm.core.legal_surface_lens import (
    SurfaceEdgeSeed,
    SurfaceLensResult,
    SurfaceNodeSeed,
)
from lawvm.core.legal_surface_lints import run_lint_passes
from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.def_scope_lints import (
    LINT_SCOPE_ANNOTATION,
    LINT_SHADOWED_TERM,
    DefinitionScopeAnnotationLintPass,
    DefinitionShadowingLintPass,
    definition_scope_lint_passes,
)
from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph
from lawvm.finland.legal_surface.lints import (
    LINT_DUPLICATE_DEFINITION,
    DuplicateDefinitionLintPass,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


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
        assert "controls" not in lint.message


# ── Hand-built-seed graph: one term, two distinct binding kinds ──────────────


def _bundle_unit():
    xml = _mk(
        "<section><num>1 §</num><content>"
        "<p>Tassa laissa sivutuotteella tarkoitetaan jatetta. "
        "Asetuksessa (EY) N:o 1069/2009 (sivutuote) saadetaan.</p>"
        "</content></section>"
    )
    bundle = build_surface_bundle(xml, "1/2020")
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


def _span(unit, char_start: int, char_end: int) -> SourceSpanRef:
    return SourceSpanRef(
        source_unit_id=unit.source_unit_id,
        source_hash=unit.source_hash,
        work_id=unit.work_id,
        address=unit.address,
        char_start=char_start,
        char_end=char_end,
        text_hash="t" * 16,
    )


def _binding_seed(
    unit, *, local: str, term: str, binding_kind: str, a: int, b: int
) -> SurfaceNodeSeed:
    return SurfaceNodeSeed(
        node_kind="definition_binding",
        source_ref=_span(unit, a, b),
        local_discriminator=local,
        rule_id="fi.definitions.binding",
        status="asserted",
        payload={
            "term": term,
            "binding_kind": binding_kind,
            "scope": "statute",
            "term_id": f"fi.term:{term}",
        },
    )


def _two_kind_graph(
    *, kind_a: str, kind_b: str, term: str = "sivutuote"
) -> LegalSurfaceGraph:
    """A graph with ONE canonical term bound twice, with the two given kinds."""
    subject, source_units, unit = _bundle_unit()
    term_id = f"fi.term:{term}"
    b1 = _binding_seed(unit, local="b1", term=term, binding_kind=kind_a, a=13, b=24)
    b2 = _binding_seed(unit, local="b2", term=term, binding_kind=kind_b, a=82, b=91)
    ent = SurfaceNodeSeed(
        node_kind="term_symbol_entity",
        source_ref=None,
        local_discriminator=term_id,
        rule_id="fi.definitions.term_symbol",
        status="asserted",
        payload={"term": term},
        authority_role="entity_handle",
    )
    edges = tuple(
        SurfaceEdgeSeed(
            edge_kind="defines_term",
            src_local=src,
            dst_local=term_id,
            rule_id="fi.definitions.defines_term",
            status="asserted",
            payload={},
        )
        for src in ("b1", "b2")
    )
    result = SurfaceLensResult(
        lens_id="fi.definitions.v0",
        node_seeds=(b1, b2, ent),
        edge_seeds=edges,
        residuals=(),
        diagnostics=(),
        coverage={},
    )
    return assemble_surface_graph(
        subject=subject, source_units=source_units, lens_results=(result,)
    )


def _scope_lints(graph: LegalSurfaceGraph):
    return run_lint_passes(graph, definition_scope_lint_passes()).lints


# ── Shadowing fires when scope shapes differ ─────────────────────────────────


def test_shadowing_fires_on_differing_scope_shapes() -> None:
    # A statute-wide definitions-section binding (tarkoitetaan) AND a local alias
    # (parenthetical) of the SAME canonical term → the narrow shape shadows the
    # broad one.
    graph = _two_kind_graph(kind_a="tarkoitetaan", kind_b="parenthetical_alias")
    shadow = [li for li in _scope_lints(graph) if li.lint_kind == LINT_SHADOWED_TERM]
    assert len(shadow) == 1
    lint = shadow[0]
    assert lint.severity == "info"
    # self-evidencing: the term is embedded.
    assert "sivutuote" in lint.message
    # both scope shapes named.
    assert "statute_definition" in lint.message
    assert "local_alias" in lint.message
    # subject is the term_symbol_entity; support is BOTH binding nodes.
    assert graph.nodes[lint.subject_node_id].node_kind == "term_symbol_entity"
    assert len(lint.support_node_ids) == 2
    for s in lint.support_node_ids:
        assert graph.nodes[s].node_kind == "definition_binding"
    # both binding spans carried.
    assert len(lint.source_refs) == 2
    _assert_firewall(_scope_lints(graph))


def test_shadowing_fires_for_two_distinct_alias_kinds() -> None:
    # parenthetical_alias + jaljempana are BOTH "local alias" shape but DISTINCT
    # binding kinds → the kinds-set has cardinality 2, so shadowing still fires
    # (the disjointness rule is on binding_kind cardinality, not shape labels).
    graph = _two_kind_graph(kind_a="parenthetical_alias", kind_b="jaljempana")
    shadow = [li for li in _scope_lints(graph) if li.lint_kind == LINT_SHADOWED_TERM]
    assert len(shadow) == 1


# ── Single definition → no lint ───────────────────────────────────────────────


def test_single_definition_term_fires_no_scope_lint() -> None:
    # One binding of a term → nothing to shadow, no scope annotation flood.
    xml = _mk(
        "<section><num>1 §</num><content>"
        "<p>Asetuksessa (EY) N:o 1069/2009 (sivutuote) saadetaan ja "
        "sivutuote kasitellaan.</p></content></section>"
    )
    graph = build_legal_surface_graph(xml, "1/2020")
    # precondition: exactly one definition_binding for the term.
    bindings = [
        n for n in graph.nodes.values() if n.node_kind == "definition_binding"
    ]
    assert len(bindings) == 1
    lints = _scope_lints(graph)
    assert [li for li in lints if li.lint_kind == LINT_SHADOWED_TERM] == []
    assert [li for li in lints if li.lint_kind == LINT_SCOPE_ANNOTATION] == []


# ── Scope annotation surfaces declared scope + shape ──────────────────────────


def test_scope_annotation_surfaces_declared_scope_and_shape() -> None:
    graph = _two_kind_graph(kind_a="tarkoitetaan", kind_b="parenthetical_alias")
    annots = [
        li for li in _scope_lints(graph) if li.lint_kind == LINT_SCOPE_ANNOTATION
    ]
    # one annotation per participating binding.
    assert len(annots) == 2
    messages = sorted(li.message for li in annots)
    # the declared scope (always 'statute' per the binder) is surfaced honestly.
    assert all("'statute'" in m for m in messages)
    # both scope shapes are surfaced, one per binding.
    assert any("'statute_definition'" in m for m in messages)
    assert any("'local_alias'" in m for m in messages)
    # subject of each is the binding node it annotates.
    for li in annots:
        assert graph.nodes[li.subject_node_id].node_kind == "definition_binding"


# ── Disjointness from definition.duplicate_definition ─────────────────────────


def test_pure_exact_duplicate_does_not_fire_shadowing_e2e() -> None:
    # Two parenthetical aliases of the SAME term — an EXACT duplicate (same scope
    # shape). The duplicate lint owns this; shadowing must NOT double-report it.
    xml = _mk(
        "<section><num>1 §</num><content>"
        "<p>Asetuksessa (EY) N:o 1069/2009 (sivutuote) saadetaan.</p>"
        "</content></section>"
        "<section><num>5 §</num><content>"
        "<p>Asetuksessa (EY) N:o 2222/2010 (sivutuote) saadetaan ja "
        "sivutuote kasitellaan.</p></content></section>"
    )
    graph = build_legal_surface_graph(xml, "1/2020")
    # precondition: the SAME canonical term is bound twice, both parenthetical.
    bindings = [
        n for n in graph.nodes.values() if n.node_kind == "definition_binding"
    ]
    assert len(bindings) == 2
    assert {b.payload.get("binding_kind") for b in bindings} == {
        "parenthetical_alias"
    }
    # the duplicate lint DOES fire (proving this is a genuine duplicate scenario).
    dup = run_lint_passes(graph, (DuplicateDefinitionLintPass(),)).lints
    assert [li for li in dup if li.lint_kind == LINT_DUPLICATE_DEFINITION]
    # but the SCOPE lints stay silent — disjoint, no double-report.
    scope = _scope_lints(graph)
    assert [li for li in scope if li.lint_kind == LINT_SHADOWED_TERM] == []
    assert [li for li in scope if li.lint_kind == LINT_SCOPE_ANNOTATION] == []


def test_hand_built_exact_duplicate_does_not_fire_shadowing() -> None:
    # Same disjointness, asserted directly: two SAME-kind bindings → no shadowing.
    graph = _two_kind_graph(
        kind_a="parenthetical_alias", kind_b="parenthetical_alias"
    )
    scope = _scope_lints(graph)
    assert [li for li in scope if li.lint_kind == LINT_SHADOWED_TERM] == []
    assert [li for li in scope if li.lint_kind == LINT_SCOPE_ANNOTATION] == []


# ── Firewall + pass-set + determinism ─────────────────────────────────────────


def test_scope_lint_passes_are_surface_only() -> None:
    for lint_pass in definition_scope_lint_passes():
        assert lint_pass.surface_only is True
        assert lint_pass.jurisdiction == "fi"


def test_pass_set_contains_both_passes() -> None:
    pass_types = {type(p) for p in definition_scope_lint_passes()}
    assert DefinitionShadowingLintPass in pass_types
    assert DefinitionScopeAnnotationLintPass in pass_types


def test_lints_are_deterministic() -> None:
    graph = _two_kind_graph(kind_a="tarkoitetaan", kind_b="parenthetical_alias")
    first = _scope_lints(graph)
    second = _scope_lints(graph)
    assert [(li.lint_id, li.lint_kind, li.message) for li in first] == [
        (li.lint_id, li.lint_kind, li.message) for li in second
    ]
    # lint ids are stable across both passes (sorted by lint_id by the runner).
    ids = [li.lint_id for li in first]
    assert ids == sorted(ids)
