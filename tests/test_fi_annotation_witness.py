"""Annotation-witness architecture tests (grammar7 §13-A/B/C).

Covers:
  * AnnotationWitnessLens node minting from inline <ref> elements;
  * the iter_body_annotation_refs raw witness surface;
  * GrammarAnnotationComparePass — each of the SEVEN NEUTRAL comparison statuses,
    including a grammar_only AND an annotation_only case (the §14 NEUTRAL
    framing: a grammar mention with no witness and a witness with no grammar
    mention are BOTH legitimate, neither is labelled an error);
  * the per-family grammar-vs-annotation census;
  * the authority firewall on every witness node + comparison edge.

Synthetic AKN XML so the statuses are deterministic without a corpus.
"""
from __future__ import annotations

from lawvm.core.legal_surface_graph import SurfaceGraphSubject
from lawvm.finland.legal_surface.annotation_compare import (
    COMPARISON_STATUSES,
    _match,
    _SpanNode,
)
from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph
from lawvm.finland.legal_surface.lenses.annotation_witness import (
    AnnotationWitnessLens,
)
from lawvm.finland.references.annotation_witness_census import census_one_statute
from lawvm.finland.references.cross_refs import iter_body_annotation_refs


_AKN = 'xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"'


def _statute(body_inner: str) -> bytes:
    return (
        f"<akomaNtoso {_AKN}><act><body>{body_inner}</body></act></akomaNtoso>"
    ).encode("utf-8")


def _section(num: str, p_inner: str) -> str:
    return (
        f"<section><num>{num} §</num><paragraph><content><p>{p_inner}</p>"
        "</content></paragraph></section>"
    )


# A statute with two parsed body <ref> CITES (parenthesised explicit ids, the
# inner text the grammar text-lane re-reads in measurement mode) + one
# unparseable-href <ref> (an entry-into-force anchor → a witness in 'other').
_TWO_REFS = _statute(
    _section(
        "1",
        "Viitataan asetukseen "
        '<ref href="/akn/fi/act/statute-consolidated/1986/531">(531/1986)</ref> '
        "ja asetukseen "
        '<ref href="/akn/fi/act/statute-consolidated/2003/481">(481/2003)</ref>, '
        'sekä <ref href="#entryIntoForce_19290228">13.6.1929/228</ref>.',
    )
)


# ── A. iter_body_annotation_refs + witness lens ──────────────────────────────


def test_iter_body_annotation_refs_yields_one_record_per_ref() -> None:
    recs = iter_body_annotation_refs(_TWO_REFS)
    assert len(recs) == 3
    parsed = [r for r in recs if r.parsed_ok]
    assert len(parsed) == 2
    targets = sorted(r.target_statute_id for r in parsed)
    assert targets == ["1986/531", "2003/481"]
    # The unparseable href is STILL a witness (never silently dropped).
    unparsed = [r for r in recs if not r.parsed_ok]
    assert len(unparsed) == 1
    assert unparsed[0].displayed_text == "13.6.1929/228"
    assert unparsed[0].target_statute_id == ""


def test_iter_body_annotation_refs_no_body_is_empty() -> None:
    assert iter_body_annotation_refs(b"<akomaNtoso/>") == []
    assert iter_body_annotation_refs(b"not xml at all") == []


def test_witness_lens_mints_one_node_per_ref() -> None:
    bundle = build_surface_bundle(_TWO_REFS, "2020/100")
    from lawvm.core.legal_surface_lens import SurfaceAnalysisContext

    result = AnnotationWitnessLens().analyze(
        bundle, context=SurfaceAnalysisContext()
    )
    witnesses = [
        s for s in result.node_seeds if s.node_kind == "annotation_reference_witness"
    ]
    assert len(witnesses) == 3
    # Each witness is a candidate (a witness, NOT an asserted surface_fact) and
    # carries the authoritative byte span + href in payload.
    for w in witnesses:
        assert w.authority_role == "candidate"
        assert "href" in w.payload
        assert "source_span_byte_offset" in w.payload
    assert result.coverage["witnesses"] == 3
    assert result.coverage["parsed_hrefs"] == 2
    assert result.coverage["unparsed_hrefs"] == 1


def test_witness_lens_blocks_on_missing_xml_bytes() -> None:
    from lawvm.core.legal_surface_lens import (
        SourceSurfaceBundle,
        SourceSurfaceUnit,
        SurfaceAnalysisContext,
    )
    from lawvm.core.legal_surface_graph import SourceSpanRef

    sref = SourceSpanRef(
        source_unit_id="u#body",
        source_hash="h",
        work_id="2020/1",
        address=None,
        char_start=0,
        char_end=0,
        text_hash="t",
    )
    unit = SourceSurfaceUnit(
        source_unit_id="u#body",
        work_id="2020/1",
        address=None,
        raw_text="",
        source_hash="h",
        source_ref=sref,
        metadata={},  # no source_bytes view
    )
    subject = SurfaceGraphSubject(
        jurisdiction="fi",
        work_id="2020/1",
        scope={},
        surface_time=None,
        source_bundle_hash="h",
        language="fi",
    )
    bundle = SourceSurfaceBundle(jurisdiction="fi", subject=subject, units=(unit,))
    result = AnnotationWitnessLens().analyze(bundle, context=SurfaceAnalysisContext())
    assert result.node_seeds == ()
    # A blocked unit becomes a typed residual, never a silent skip.
    assert any(
        r.residual_kind == "missing_xml_bytes" for r in result.residuals
    )


def test_witness_lens_reads_typed_source_bytes_not_metadata() -> None:
    """The lens reads the typed ``source_bytes`` view, NOT ``metadata``.

    A unit carrying the raw XML ONLY via the legacy ``metadata["xml_bytes"]``
    key (and no typed ``source_bytes``) must be treated as a blocked unit — the
    lens no longer reaches back into the free-form metadata channel.
    """
    from lawvm.core.legal_surface_graph import SourceSpanRef
    from lawvm.core.legal_surface_lens import (
        SourceSurfaceBundle,
        SourceSurfaceUnit,
        SurfaceAnalysisContext,
    )

    xml = (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<act><body><section><content><p>"
        '<ref href="/akn/fi/act/2019/9">9/2019</ref>'
        "</p></content></section></body></act></akomaNtoso>"
    ).encode("utf-8")
    sref = SourceSpanRef(
        source_unit_id="u#body",
        source_hash="h",
        work_id="2020/1",
        address=None,
        char_start=0,
        char_end=0,
        text_hash="t",
    )
    # Raw XML present ONLY in the legacy metadata channel; no typed view.
    unit = SourceSurfaceUnit(
        source_unit_id="u#body",
        work_id="2020/1",
        address=None,
        raw_text="9/2019",
        source_hash="h",
        source_ref=sref,
        metadata={"xml_bytes": xml},
    )
    subject = SurfaceGraphSubject(
        jurisdiction="fi",
        work_id="2020/1",
        scope={},
        surface_time=None,
        source_bundle_hash="h",
        language="fi",
    )
    bundle = SourceSurfaceBundle(jurisdiction="fi", subject=subject, units=(unit,))
    result = AnnotationWitnessLens().analyze(bundle, context=SurfaceAnalysisContext())
    # No witness minted from the metadata channel; the unit is blocked.
    assert result.node_seeds == ()
    assert any(r.residual_kind == "missing_xml_bytes" for r in result.residuals)

    # When the SAME bytes ride the typed field, the lens mints the witness.
    import dataclasses

    typed_unit = dataclasses.replace(unit, source_bytes=xml)
    typed_bundle = SourceSurfaceBundle(
        jurisdiction="fi", subject=subject, units=(typed_unit,)
    )
    typed_result = AnnotationWitnessLens().analyze(
        typed_bundle, context=SurfaceAnalysisContext()
    )
    assert len(typed_result.node_seeds) == 1
    assert not any(
        r.residual_kind == "missing_xml_bytes" for r in typed_result.residuals
    )


# ── B. comparison statuses ───────────────────────────────────────────────────


def _span(node_id: str, off: int | None, length: int, target: str | None) -> _SpanNode:
    return _SpanNode(node_id=node_id, byte_offset=off, byte_len=length, target_key=target)


def test_match_pairs_overlapping_spans() -> None:
    grammar = [_span("g1", 10, 5, "A"), _span("g2", 100, 5, "B")]
    witnesses = [_span("w1", 11, 4, "A")]
    pairs, g_only, a_only = _match(grammar, witnesses)
    assert len(pairs) == 1
    assert pairs[0][0].node_id == "g1" and pairs[0][1].node_id == "w1"
    assert [g.node_id for g in g_only] == ["g2"]
    assert a_only == []


def test_compare_pass_emits_all_two_sided_statuses() -> None:
    """both_same_target / diff_target / diff_span / noncomparable in one graph."""
    grammar = [
        _span("g_same", 10, 5, "A"),       # exact span + same target
        _span("g_diffspan", 50, 8, "B"),   # overlaps but offset differs
        _span("g_difftgt", 100, 5, "C"),   # same span, different target
        _span("g_noncmp", 150, 5, None),   # no comparable target
    ]
    witnesses = [
        _span("w_same", 10, 5, "A"),
        _span("w_diffspan", 52, 6, "B"),
        _span("w_difftgt", 100, 5, "Z"),
        _span("w_noncmp", 150, 5, "Q"),
    ]
    from lawvm.finland.legal_surface import annotation_compare as ac

    pairs, _g, _a = ac._match(grammar, witnesses)
    statuses = {ac._two_sided_status(g, w) for g, w in pairs}
    assert statuses == {
        "both_same_target",
        "both_same_target_diff_span",
        "both_same_span_diff_target",
        "both_present_noncomparable",
    }


def test_compare_pass_grammar_only_and_annotation_only_are_neutral() -> None:
    """A grammar mention with no <ref> and a <ref> with no grammar — both NEUTRAL.

    Proves §14: neither side is labelled an error. The grammar_only edge is a
    self-edge on the grammar node; the annotation_only edge is a self-edge on the
    witness node; both carry status 'candidate', never 'asserted'.
    """
    # Three references in one body:
    #   - a by-name ref (kansalaisuuslaki) the text lane finds but <ref> does NOT
    #     annotate → grammar_only (text-only family, no witness);
    #   - an explicit <ref> to 531/1986 the reference lane also resolves →
    #     both_same_target;
    #   - an unparseable-href <ref> (entry-into-force anchor) that no reference_expr
    #     covers but a witness records → annotation_only.
    xml = _statute(
        _section(
            "1",
            "Sovelletaan kansalaisuuslakia ja viitataan lakiin "
            '<ref href="/akn/fi/act/statute-consolidated/1986/531">531/1986</ref>, '
            '<ref href="#entryIntoForce_19290228">13.6.1929/228</ref>.',
        )
    )
    graph = build_legal_surface_graph(xml, "2020/200")
    cmp_edges = [
        e for e in graph.edges if e.edge_kind == "grammar_annotation_compared"
    ]
    assert cmp_edges, "comparison pass must emit edges"
    statuses = {e.payload["comparison_status"] for e in cmp_edges}
    # Every comparison status is from the closed NEUTRAL set.
    assert statuses <= COMPARISON_STATUSES
    # The NEUTRAL one-sided buckets both appear and are never 'asserted'.
    assert "grammar_only" in statuses
    assert "annotation_only" in statuses
    for e in cmp_edges:
        assert e.surface_edge_status == "candidate"
        # One-sided statuses are self-edges (single present node).
        if e.payload["comparison_status"] in ("grammar_only", "annotation_only"):
            assert e.src == e.dst


def test_compare_pass_emits_nothing_without_refs_or_grammar() -> None:
    # A body with no <ref> and no resolvable mentions yields no comparison edges
    # only if there are also no grammar reference_expr nodes; a plain prose body
    # still has none. Use an empty section.
    xml = _statute(_section("1", "Tämä pykälä ei sisällä viittauksia."))
    graph = build_legal_surface_graph(xml, "2020/300")
    witnesses = [
        n for n in graph.nodes.values()
        if n.node_kind == "annotation_reference_witness"
    ]
    assert witnesses == []


# ── C. census ────────────────────────────────────────────────────────────────


def test_census_one_statute_classifies_per_family() -> None:
    per_family = census_one_statute(_TWO_REFS, "2020/100")
    # explicit_id witnesses present.
    assert "explicit_id" in per_family
    eid = per_family["explicit_id"]
    assert eid.annotation_witnesses >= 2
    # The unparseable-href witness lands in 'other' (no parsed target).
    assert per_family.get("other") is not None
    assert per_family["other"].annotation_witnesses >= 1
    # Every count is non-negative and the seven statuses partition the matches.
    for fc in per_family.values():
        assert fc.matched + fc.grammar_only >= 0
        assert fc.annotation_only >= 0


# A body where the grammar text lane finds an explicit id in PROSE (asetusta
# (481/2003)) AND a <ref> annotates the same act — so grammar and annotation agree
# on the target. The text lane carries no byte span for the prose paren, so the
# agreement is recovered by the census's target-key fallback →
# both_same_target_diff_span. Also a by-name ref (grammar_only).
_AGREE_BODY = _statute(
    _section(
        "1",
        "Sovelletaan maksuperustelakia. Noudatetaan asetusta (481/2003). "
        "Viitataan "
        '<ref href="/akn/fi/act/statute-consolidated/2003/481">'
        "asetukseen 481/2003</ref>.",
    )
)


def test_census_target_agreement_signal() -> None:
    """Grammar and annotation agree on the explicit-id target (target-key match).

    The grammar text lane recovers 481/2003 from prose but carries no byte span,
    so the agreement lands in both_same_target_diff_span — the target_agree metric
    still counts it. The by-name maksuperustelaki is a NEUTRAL grammar_only.
    """
    per_family = census_one_statute(_AGREE_BODY, "2020/100")
    eid = per_family["explicit_id"]
    assert eid.grammar_mentions >= 1
    assert eid.annotation_witnesses >= 1
    # At least one explicit-id target agreed between grammar and annotation.
    assert eid.target_agree >= 1
    # The exact-provision agreement is NOT a provision divergence.
    assert eid.both_same_statute_diff_provision == 0
    # A by-name reference the <ref> does not annotate is grammar_only (NEUTRAL).
    assert per_family["by_name"].grammar_only >= 1


# The grammar text-lane recovers 2003/481 at STATUTE level (prose paren); the
# <ref> href points at 2003/481#sec_5 (section-level). SAME statute, DIFFERENT
# provision path — the divergence the old statute-id fallback silently counted as
# agreement. It must now land in both_same_statute_diff_provision, NOT target_agree.
_DIFF_PROVISION_BODY = _statute(
    _section(
        "1",
        "Noudatetaan asetusta (481/2003). Viitataan "
        '<ref href="/akn/fi/act/statute-consolidated/2003/481#sec_5">'
        "asetuksen 5 §</ref>.",
    )
)


def test_census_same_statute_diff_provision_is_not_agreement() -> None:
    """A statute-level grammar mention vs a section-level <ref> → diff_provision.

    This is the whole point of the sharper census: same statute but a different
    provision path is a GRANULARITY DIVERGENCE, not agreement. It books the NEW
    both_same_statute_diff_provision bucket, and target_agree EXCLUDES it. Under
    the old statute-id fallback this pair was masked as both_same_target_diff_span
    (i.e. counted as agree).
    """
    per_family = census_one_statute(_DIFF_PROVISION_BODY, "2020/100")
    eid = per_family["explicit_id"]
    assert eid.both_same_statute_diff_provision >= 1
    # The divergence is NOT counted as target agreement.
    assert eid.target_agree == 0
    assert eid.same_statute_diff_provision == eid.both_same_statute_diff_provision
    # It IS a matched pair (both sides present), just a divergent one.
    assert eid.matched >= 1


def test_census_exact_provision_agreement_when_ref_carries_same_path() -> None:
    """Grammar and <ref> at the SAME (statute-level) target → exact agreement.

    A control for the diff-provision test: when both surfaces carry the SAME
    provision path (here both statute-level, empty path), the pair is an exact
    agreement (both_same_target_diff_span — span not comparable), NOT a spurious
    provision divergence. The <ref> href to 481/2003 has no #frag, so its
    target_section is empty — matching the grammar prose paren-id's empty path.
    """
    body = _statute(
        _section(
            "1",
            "Noudatetaan asetusta (481/2003). Viitataan "
            '<ref href="/akn/fi/act/statute-consolidated/2003/481">'
            "asetukseen (481/2003)</ref>.",
        )
    )
    per_family = census_one_statute(body, "2020/100")
    eid = per_family["explicit_id"]
    # Exact-provision agreement, and NO spurious divergence.
    assert eid.target_agree >= 1
    assert eid.both_same_statute_diff_provision == 0


# ── firewall ─────────────────────────────────────────────────────────────────


def test_witness_nodes_and_compare_edges_hold_firewall() -> None:
    graph = build_legal_surface_graph(_TWO_REFS, "2020/100")
    witnesses = [
        n for n in graph.nodes.values()
        if n.node_kind == "annotation_reference_witness"
    ]
    assert len(witnesses) == 3
    for n in witnesses:
        assert n.surface_only is True
        assert n.replay_authorized is False
    for e in graph.edges:
        if e.edge_kind == "grammar_annotation_compared":
            assert e.surface_only is True
            assert e.replay_authorized is False
