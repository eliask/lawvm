"""E2E test for the H2 DEFINITIONS lens + definition lints (Pro r5 Phase 4).

Exercises the full surface pipeline for each definition lint:

    build_surface_bundle -> DefinitionLens.analyze -> assemble_surface_graph
    -> run_edge_passes (DefinitionClosurePass) -> run_lint_passes

and asserts (a) the expected lint fires per synthetic statute, (b) the clean
case fires nothing, and (c) the §D7 authority firewall holds on every emitted
node / edge / lint.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from lawvm.core.legal_surface_assembler import assemble_surface_graph
from lawvm.core.legal_surface_graph import (
    LegalSurfaceGraph,
    SourceSpanRef,
    SurfaceGraphSubject,
)
from lawvm.core.legal_surface_lens import (
    SurfaceAnalysisContext,
    SurfaceLens,
    SurfaceLensResult,
    SurfaceNodeSeed,
)
from lawvm.core.legal_surface_lints import (
    SurfaceLint,
    SurfaceLintPass,
    run_lint_passes,
)
from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.lenses.definitions import DefinitionLens
from lawvm.finland.legal_surface.lints import (
    LINT_AMBIGUOUS_TERM_USE,
    LINT_DEAD_DEFINITION,
    LINT_DUPLICATE_DEFINITION,
    LINT_UNBOUND_TERM,
    LINT_USED_BEFORE_DEFINITION,
    definition_lint_passes,
)
from lawvm.finland.legal_surface.passes import DefinitionClosurePass


# ── Synthetic statute helpers ────────────────────────────────────────────────


def _xml(*paragraphs: str) -> bytes:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        f"<body>{body}</body></akomaNtoso>"
    ).encode("utf-8")


def _run_pipeline(
    xml_bytes: bytes,
    *,
    extra_lens_results: tuple[SurfaceLensResult, ...] = (),
) -> tuple[LegalSurfaceGraph, tuple[SurfaceLint, ...]]:
    """Run the FULL surface pipeline and return (graph, lints)."""
    bundle = build_surface_bundle(xml_bytes, "test/1")
    lens = DefinitionLens()
    result = lens.analyze(bundle, context=SurfaceAnalysisContext())
    graph = assemble_surface_graph(
        subject=bundle.subject,
        source_units=(),
        lens_results=(result, *extra_lens_results),
        edge_passes=(DefinitionClosurePass(),),
    )
    report = run_lint_passes(graph, definition_lint_passes())
    return graph, report.lints


def _kinds(lints: Iterable[SurfaceLint]) -> set[str]:
    return {li.lint_kind for li in lints}


# ── Protocol conformance ─────────────────────────────────────────────────────


def test_definition_lens_conforms_to_protocol() -> None:
    lens = DefinitionLens()
    assert isinstance(lens, SurfaceLens)
    assert lens.lens_id == "fi.definitions.v0"
    assert set(lens.produces_node_kinds) == {
        "definition_binding",
        "term_use",
        "term_symbol_entity",
    }
    assert set(lens.produces_edge_kinds) == {"defines_term", "uses_term"}


def test_lint_passes_conform_to_protocol() -> None:
    for lint_pass in definition_lint_passes():
        assert isinstance(lint_pass, SurfaceLintPass)
        assert lint_pass.surface_only is True


# ── Node / edge kinds emitted ────────────────────────────────────────────────


def test_lens_emits_expected_node_and_edge_kinds() -> None:
    xml = _xml(
        "Asetus (EY) N:o 1069/2009 (sivutuoteasetus) on voimassa.",
        "Sivutuoteasetus toimii hyvin.",
    )
    graph, _ = _run_pipeline(xml)
    node_kinds = {n.node_kind for n in graph.nodes.values()}
    assert "definition_binding" in node_kinds
    assert "term_use" in node_kinds
    assert "term_symbol_entity" in node_kinds
    edge_kinds = {e.edge_kind for e in graph.edges}
    assert "defines_term" in edge_kinds
    assert "uses_term" in edge_kinds


# ── The clean case fires NOTHING ─────────────────────────────────────────────


def test_clean_statute_fires_no_lint() -> None:
    xml = _xml(
        "Asetus (EY) N:o 1069/2009 (sivutuoteasetus) on voimassa.",
        "Sivutuoteasetus toimii hyvin.",
    )
    _, lints = _run_pipeline(xml)
    assert lints == ()


# ── One test per lint ────────────────────────────────────────────────────────


def test_dead_definition_lint_fires() -> None:
    # A definition that is never used → dead_definition.
    xml = _xml("Asetus (EY) N:o 1069/2009 (sivutuoteasetus) on voimassa.")
    _, lints = _run_pipeline(xml)
    assert LINT_DEAD_DEFINITION in _kinds(lints)
    dead = next(li for li in lints if li.lint_kind == LINT_DEAD_DEFINITION)
    assert "sivutuoteasetus" in dead.message  # self-evidencing


def test_used_before_definition_lint_fires() -> None:
    # The term is used in the first paragraph, defined only in the second.
    xml = _xml(
        "Sivutuoteasetus on tarkea.",
        "Asetus (EY) N:o 1069/2009 (sivutuoteasetus) maaritellaan.",
    )
    graph, lints = _run_pipeline(xml)
    assert LINT_USED_BEFORE_DEFINITION in _kinds(lints)
    # The cross-lens closure pass minted the resolving edge to the later binding.
    assert any(e.edge_kind == "term_use_resolves_to" for e in graph.edges)
    ubd = next(li for li in lints if li.lint_kind == LINT_USED_BEFORE_DEFINITION)
    assert "Sivutuoteasetus" in ubd.message
    assert ubd.support_node_ids  # points at the later binding


def test_duplicate_and_ambiguous_lints_fire() -> None:
    # Same surface term defined twice, then used → duplicate + ambiguous.
    xml = _xml(
        "Laki (111/2020) (testilaki) saadetaan.",
        "Toinen laki (222/2021) (testilaki) saadetaan.",
        "Testilaki on voimassa.",
    )
    _, lints = _run_pipeline(xml)
    kinds = _kinds(lints)
    assert LINT_DUPLICATE_DEFINITION in kinds
    assert LINT_AMBIGUOUS_TERM_USE in kinds
    dup = next(li for li in lints if li.lint_kind == LINT_DUPLICATE_DEFINITION)
    assert "testilaki" in dup.message
    assert len(dup.support_node_ids) == 2  # two binding nodes
    amb = next(li for li in lints if li.lint_kind == LINT_AMBIGUOUS_TERM_USE)
    assert "Testilaki" in amb.message


def test_unbound_term_lint_fires_on_orphan_open_use() -> None:
    """``unbound_term`` = an open term_use with no resolving edge.

    The single H2 recognizer always mints a binding entity for any open use, so
    a truly unbound use is a state a FUTURE lens / cross-unit assembly produces.
    We exercise the graph query honestly via a tiny extra lens that emits one
    orphan open ``term_use`` node (no binding) into the same assembled graph.
    """
    base_xml = _xml(
        "Asetus (EY) N:o 1069/2009 (sivutuoteasetus) on voimassa.",
        "Sivutuoteasetus toimii hyvin.",
    )

    orphan_ref = SourceSpanRef(
        source_unit_id="orphan#body",
        source_hash="orphanhash",
        work_id="orphan",
        address=None,
        char_start=0,
        char_end=9,
        text_hash="orphanth",
    )
    orphan_seed = SurfaceNodeSeed(
        node_kind="term_use",
        source_ref=orphan_ref,
        local_discriminator="orphan-open-use",
        rule_id="test.orphan_use",
        node_status="open",
        payload={
            "term_surface": "orpotermi",
            "lemma": "orpotermi",
            "resolver_status": "open",
            "candidate_count": 0,
        },
    )
    orphan_result = SurfaceLensResult(
        lens_id="test.orphan",
        node_seeds=(orphan_seed,),
        edge_seeds=(),
        residuals=(),
        diagnostics=(),
        coverage={},
    )
    _, lints = _run_pipeline(base_xml, extra_lens_results=(orphan_result,))
    assert LINT_UNBOUND_TERM in _kinds(lints)
    unbound = next(li for li in lints if li.lint_kind == LINT_UNBOUND_TERM)
    assert "orpotermi" in unbound.message


# ── Authority firewall holds across the whole pipeline ───────────────────────


def test_authority_firewall_holds_everywhere() -> None:
    xml = _xml(
        "Laki (111/2020) (testilaki) saadetaan.",
        "Toinen laki (222/2021) (testilaki) saadetaan.",
        "Testilaki on voimassa.",
    )
    graph, lints = _run_pipeline(xml)
    for node in graph.nodes.values():
        assert node.surface_only is True
        assert node.replay_authorized is False
    for edge in graph.edges:
        assert edge.surface_only is True
        assert edge.replay_authorized is False
    for lint in lints:
        assert lint.surface_only is True
        assert lint.legal_conclusion is False
        assert lint.replay_authorized is False
        assert lint.forbidden_overclaims  # every lint names what it is NOT


def test_subject_is_finnish() -> None:
    xml = _xml("Asetus (EY) N:o 1069/2009 (sivutuoteasetus) on voimassa.")
    graph, _ = _run_pipeline(xml)
    assert isinstance(graph.subject, SurfaceGraphSubject)
    assert graph.subject.jurisdiction == "fi"


# ── Whitespace-spanning definiendum must not crash the build ─────────────────


def test_oblique_definiendum_spanning_whitespace_anchors_without_crash() -> None:
    """A multi-word oblique-case definiendum the recognizer matched across runs
    of whitespace (the indentation real Finlex bodies carry between the words of
    an enumerated definiendum) must still produce a properly anchored
    ``definition_binding`` source fact — never a contract-violating seed that
    aborts the whole statute's graph.

    The ``tarkoitetaan`` recognizer normalises the captured definiendum surface
    to single spaces, so it does not round-trip through ``str.find`` of the body
    text. The lens must fall back to the recognizer's own (exact) construct span,
    so the binding still carries a ``source_ref``. This is the ``rikoslaki``
    1889/39-001 ``moottorikäyttöisellä ajoneuvolla tarkoitetaan …`` crash shape.
    """
    # Multiple spaces between the definiendum words mimic Finlex indentation; the
    # recognizer collapses them, so the normalised term is NOT a substring of the
    # body — the locate-by-surface path misses and the construct-span anchor must
    # take over.
    xml = _xml(
        "Tässä laissa moottorikäyttöisellä   ajoneuvolla tarkoitetaan "
        "konevoimalla kulkevaa ajoneuvoa."
    )
    # Must not raise SurfaceAssemblyError.
    graph, _ = _run_pipeline(xml)

    bindings = [
        n for n in graph.nodes.values() if n.node_kind == "definition_binding"
    ]
    assert bindings, "expected the oblique-case definition to bind"
    target = next(
        (
            n
            for n in bindings
            if "moottorikäyttöisellä" in str(cast(dict[str, object], n.payload).get("term") or "")
        ),
        None,
    )
    assert target is not None, "the oblique definiendum binding should be present"
    # The fix's invariant: the source-fact binding carries a real source_ref
    # (never None — that would be the contract violation that crashed the build).
    assert target.source_ref is not None
    assert target.payload.get("binder_status") == "unsupported_morphology"
