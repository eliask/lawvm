"""Tests for the Finnish definition graph assembler + the first LawVM legal lint.

Exercises ``build_definition_graph`` end-to-end over synthetic AKN XML and the
``_compute_lints`` seam directly, covering EACH lint kind:

  * a CLEAN resolved case  -> exactly one edge, NO lints
  * DEFINITION_NEVER_USED  -> a dead definition
  * USED_BEFORE_DEFINITION -> a use that precedes its binding
  * DUPLICATE_DEFINITION   -> the same term bound twice
  * AMBIGUOUS_TERM_USE     -> a use matching >1 in-scope binding
  * UNBOUND_TERM           -> an open use with no definition reachable in scope

Every lint message is asserted to be SELF-EVIDENCING (embeds the offending term).
"""
from __future__ import annotations

from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.references import definition_graph as dg
from lawvm.finland.references.defined_terms import (
    BINDING_PARENTHETICAL_ALIAS,
    STATUS_OK,
    DefinedTermBinding,
)
from lawvm.finland.references.definition_graph import (
    LINT_AMBIGUOUS_TERM_USE,
    LINT_DEFINITION_NEVER_USED,
    LINT_DUPLICATE_DEFINITION,
    LINT_UNBOUND_TERM,
    LINT_USED_BEFORE_DEFINITION,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    build_definition_graph,
)
from lawvm.finland.references.term_use import (
    RULE_BEFORE_BINDING,
    STATUS_OPEN,
    TermUse,
)

_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _xml(*paragraphs: str) -> bytes:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        f'<akomaNtoso xmlns="{_NS}"><act><body>{body}</body></act></akomaNtoso>'
    ).encode("utf-8")


def _kinds(graph: dg.DefinitionGraph) -> set[str]:
    return {li.kind for li in graph.lints}


def _of_kind(graph: dg.DefinitionGraph, kind: str) -> list[dg.Lint]:
    return [li for li in graph.lints if li.kind == kind]


# ---------------------------------------------------------------------------
# Body extraction
# ---------------------------------------------------------------------------


def test_body_text_joins_p_elements() -> None:
    graph = build_definition_graph(_xml("Ensimmainen.", "Toinen."), "1/2020")
    assert graph.body_text == "Ensimmainen.\nToinen."
    assert graph.statute_id == "1/2020"


def test_empty_xml_yields_empty_graph_not_crash() -> None:
    graph = build_definition_graph(b"<akomaNtoso/>", "1/2020")
    assert graph.body_text == ""
    assert graph.bindings == ()
    assert graph.uses == ()
    assert graph.edges == ()
    assert graph.lints == ()


def test_unparseable_xml_yields_empty_graph_not_crash() -> None:
    graph = build_definition_graph(b"<not valid <<<", "1/2020")
    assert graph.body_text == ""
    assert graph.lints == ()


# ---------------------------------------------------------------------------
# CLEAN case: a resolved use -> one edge, no lints
# ---------------------------------------------------------------------------


def test_clean_resolved_case_has_edge_and_no_lints() -> None:
    graph = build_definition_graph(
        _xml(
            "Asetuksessa (EY) N:o 1069/2009 (sivutuoteasetus) saadetaan.",
            "Talla sivutuoteasetuksella tarkennetaan saannot.",
        ),
        "2014/527",
    )
    # one binding, one resolved use, one edge, zero lints.
    assert len(graph.bindings) == 1
    assert len(graph.edges) == 1
    assert graph.edges[0].binding is graph.bindings[0]
    assert graph.edges[0].use.use_status == "resolved"
    assert graph.lints == ()


# ---------------------------------------------------------------------------
# DEFINITION_NEVER_USED
# ---------------------------------------------------------------------------


def test_definition_never_used_lint() -> None:
    graph = build_definition_graph(
        _xml("Asetuksessa (EY) N:o 1069/2009 (sivutuoteasetus) saadetaan."),
        "x",
    )
    assert _kinds(graph) == {LINT_DEFINITION_NEVER_USED}
    lint = _of_kind(graph, LINT_DEFINITION_NEVER_USED)[0]
    assert lint.severity == SEVERITY_WARNING
    assert lint.term == "sivutuoteasetus"
    # self-evidencing: embeds the term.
    assert "sivutuoteasetus" in lint.message
    # span points at the binding.
    assert lint.source_span.byte_offset == graph.bindings[0].source_span.byte_offset


# ---------------------------------------------------------------------------
# USED_BEFORE_DEFINITION
# ---------------------------------------------------------------------------


def test_used_before_definition_lint() -> None:
    graph = build_definition_graph(
        _xml(
            "Tama sivutuoteasetuksella sailoo.",
            "Asetuksessa (EY) N:o 1069/2009 (sivutuoteasetus) annetaan.",
        ),
        "x",
    )
    assert LINT_USED_BEFORE_DEFINITION in _kinds(graph)
    lint = _of_kind(graph, LINT_USED_BEFORE_DEFINITION)[0]
    assert lint.severity == SEVERITY_ERROR
    assert lint.term == "sivutuoteasetus"
    # self-evidencing: embeds the inflected surface that was used too early.
    assert "sivutuoteasetuksella" in lint.message
    # the binding lies AFTER the use's span.
    use_span = lint.source_span
    binding_off = graph.bindings[0].source_span.byte_offset
    assert binding_off >= use_span.byte_offset + use_span.byte_len


# ---------------------------------------------------------------------------
# DUPLICATE_DEFINITION (and the AMBIGUOUS_TERM_USE it induces)
# ---------------------------------------------------------------------------


def test_duplicate_definition_and_ambiguous_use_lints() -> None:
    graph = build_definition_graph(
        _xml(
            "Asetuksessa (EY) N:o 1069/2009 (sivutuoteasetus) saadetaan.",
            "Asetuksessa (EY) N:o 2222/2010 (sivutuoteasetus) saadetaan.",
            "Sivutuoteasetuksella tarkennetaan.",
        ),
        "x",
    )
    kinds = _kinds(graph)
    assert LINT_DUPLICATE_DEFINITION in kinds
    assert LINT_AMBIGUOUS_TERM_USE in kinds

    # duplicate: one lint per duplicate binding (all listed).
    dups = _of_kind(graph, LINT_DUPLICATE_DEFINITION)
    assert len(dups) == 2
    for lint in dups:
        assert lint.severity == SEVERITY_ERROR
        assert lint.term == "sivutuoteasetus"
        # self-evidencing: term + how many times + offsets.
        assert "sivutuoteasetus" in lint.message
        assert "2 times" in lint.message

    # ambiguous: one use matched both bindings; both candidates listed.
    amb = _of_kind(graph, LINT_AMBIGUOUS_TERM_USE)[0]
    assert amb.severity == SEVERITY_ERROR
    assert "Sivutuoteasetuksella" in amb.message
    assert "2 definitions" in amb.message


# ---------------------------------------------------------------------------
# UNBOUND_TERM (driven through the _compute_lints seam)
# ---------------------------------------------------------------------------


def test_unbound_term_lint_via_compute_seam() -> None:
    # An ``open`` use whose only matching binding is NOT positioned after it:
    # the term is used but has no definition reachable in scope -> UNBOUND_TERM.
    body = "kohde maaritetaan myohemmin mutta kohde kaytetaan tassa"
    binding = DefinedTermBinding(
        term="kohde",
        target_ref="1/2020",
        expansion=None,
        scope="statute",
        source_span=SourceSpan("x", 0, 5),
        binding_kind=BINDING_PARENTHETICAL_ALIAS,
        binding_status=STATUS_OK,
    )
    use = TermUse(
        term_surface="kohde",
        lemma="kohde",
        binding=None,
        source_span=SourceSpan("x", 34, 5),  # AFTER the binding -> not "later"
        use_status=STATUS_OPEN,
        rule_id=RULE_BEFORE_BINDING,
        bindings=(),
    )
    lints = dg._compute_lints(
        body, (binding,), (use,), (), source_file="x"
    )
    unbound = [li for li in lints if li.kind == LINT_UNBOUND_TERM]
    assert len(unbound) == 1
    lint = unbound[0]
    assert lint.severity == SEVERITY_ERROR
    assert lint.term == "kohde"
    # self-evidencing.
    assert "kohde" in lint.message
    assert "no definition reachable" in lint.message


def test_open_use_with_later_binding_is_used_before_definition_not_unbound() -> None:
    # Same surface, but the binding lies AFTER the use -> the recoverable
    # USED_BEFORE_DEFINITION kind, NOT UNBOUND_TERM.
    body = "kohde kaytetaan tassa ja kohde maaritetaan myohemmin"
    binding = DefinedTermBinding(
        term="kohde",
        target_ref="1/2020",
        expansion=None,
        scope="statute",
        source_span=SourceSpan("x", 25, 5),
        binding_kind=BINDING_PARENTHETICAL_ALIAS,
        binding_status=STATUS_OK,
    )
    use = TermUse(
        term_surface="kohde",
        lemma="kohde",
        binding=None,
        source_span=SourceSpan("x", 0, 5),  # BEFORE the binding
        use_status=STATUS_OPEN,
        rule_id=RULE_BEFORE_BINDING,
        bindings=(),
    )
    lints = dg._compute_lints(body, (binding,), (use,), (), source_file="x")
    kinds = {li.kind for li in lints}
    assert LINT_USED_BEFORE_DEFINITION in kinds
    assert LINT_UNBOUND_TERM not in kinds


# ---------------------------------------------------------------------------
# Invariants: lints sorted, frozen, never silently dropped
# ---------------------------------------------------------------------------


def test_lints_are_sorted_by_offset() -> None:
    graph = build_definition_graph(
        _xml(
            "Tama sivutuoteasetuksella sailoo.",
            "Asetuksessa (EY) N:o 1069/2009 (sivutuoteasetus) annetaan.",
        ),
        "x",
    )
    offsets = [li.source_span.byte_offset for li in graph.lints]
    assert offsets == sorted(offsets)


def test_graph_and_lint_are_frozen() -> None:
    graph = build_definition_graph(
        _xml("Asetuksessa (EY) N:o 1069/2009 (sivutuoteasetus) saadetaan."),
        "x",
    )
    lint = graph.lints[0]
    for obj, attr in ((graph, "statute_id"), (lint, "kind")):
        try:
            setattr(obj, attr, "mutated")
        except (AttributeError, TypeError):
            continue
        raise AssertionError(f"{type(obj).__name__} should be frozen")
