"""Differential: the SourceSyntaxGraph forest's reference projection vs the lens.

This is the FIRST lens→forest projection strangle (L3) — the TEMPLATE the L4/L5
lens projections follow. It proves the forest can REPRODUCE the
citation-construction SUBSET of the converged ``ReferenceLens`` (the differential
ORACLE), and CHARACTERISES the reference families the forest does not yet own.

OUTCOME (B): the forest's ``reference_np`` leaf is sourced from ONE family — the
inline-(id) plain-text citation construction (``parse_citation_sentence``), the
``citation_construction`` lane of ``extract_all_reference_mentions``. That is a
STRICT SUBSET of the seven-lane lens. So the differential is run on THAT subset,
and the other six families are an explicit, surfaced residual worklist
(:data:`FOREST_UNOWNED_REFERENCE_FAMILIES`).

The differential compares CANONICAL target-provision identity keys (statute id in
the corpus YEAR/NUMBER orientation + chapter/section/momentti/kohta), so it is
robust to the representational fields the two lanes fill differently
(statute-id orientation, derived ``provision_path``).

These fixtures are body text WITHOUT ``<ref>`` markup, so the forest (which reads
``decode_body_text`` = itertext over ``<p>``) and the lens's citation lane (which
reads ``_collect_non_ref_text``) see the SAME text → 0-delta on the citation
subset BY CONSTRUCTION. Where Finlex wraps a cite in ``<ref>`` markup the forest —
being annotation-INDEPENDENT — recovers it while the lens routes it to the
``<ref>`` lane (lane 1); that annotation-boundary behaviour is the documented
residual, not a parser miss (grammar7 §"delete annotation DEPENDENCE not USE").
"""
from __future__ import annotations

from lawvm.core.legal_surface_graph import SurfaceGraphSubject
from lawvm.finland.legal_surface.bundle import decode_body_text
from lawvm.finland.legal_surface.reference_projection import (
    FOREST_OWNED_PHRASE_LEMMA,
    FOREST_UNOWNED_REFERENCE_FAMILIES,
    diff_forest_vs_lens_citation_subset,
    forest_reference_target_keys,
    lens_citation_subset_target_keys,
    project_forest_references,
)
from lawvm.finland.legal_surface.source_syntax_graph import assemble_source_syntax_graph
from lawvm.finland.references.ref_mention_extractor import (
    extract_all_reference_mentions,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

_SUBJECT = SurfaceGraphSubject(
    jurisdiction="fi",
    work_id="test/1",
    scope={},
    surface_time=None,
    source_bundle_hash="",
    language="fi",
)


def _xml(body_paragraphs: str) -> bytes:
    return (
        f'<akomaNtoso xmlns="{_AKN}" '
        'xmlns:finlex="http://data.finlex.fi/schema/finlex"><act><body>'
        "<section><num>5 §</num><paragraph><content>"
        f"{body_paragraphs}"
        "</content></paragraph></section></body></act></akomaNtoso>"
    ).encode("utf-8")


def _forest_keys_for(xml_bytes: bytes, statute_id: str) -> set[str]:
    body = decode_body_text(xml_bytes)
    forest = assemble_source_syntax_graph(
        subject=_SUBJECT, source_units=(), statute_id=statute_id, body=body
    )
    return forest_reference_target_keys(forest, body, source_statute_id=statute_id)


def _lens_mentions(xml_bytes: bytes, statute_id: str):
    return extract_all_reference_mentions(xml_bytes, statute_id).mentions


# ── outcome characterisation ────────────────────────────────────────────────


def test_outcome_is_subset_plus_characterised_residual() -> None:
    """The forest owns ONE reference family; the other six are a surfaced worklist.

    Documents the strangle's frontier: the forest-owned subset is exactly the
    ``citation_construction`` lane, and the residual worklist names the families
    the forest does not yet produce — surfaced, never hidden.
    """
    assert FOREST_OWNED_PHRASE_LEMMA == "citation_construction"
    # The six other lanes (ref_element / affected_document / EU / preparatory /
    # surface-grammar / nojalla) are the residual worklist — none overlaps the
    # owned phrase lemma (no silent claim of ownership).
    assert FOREST_OWNED_PHRASE_LEMMA not in FOREST_UNOWNED_REFERENCE_FAMILIES
    assert "ref_element" in FOREST_UNOWNED_REFERENCE_FAMILIES
    assert "affected_document" in FOREST_UNOWNED_REFERENCE_FAMILIES
    assert "eu_text_pattern" in FOREST_UNOWNED_REFERENCE_FAMILIES
    assert "ISSUED_UNDER" in FOREST_UNOWNED_REFERENCE_FAMILIES


# ── 0-delta on the citation subset (the flip gate, by construction) ──────────


def test_zero_delta_on_provision_precise_citation() -> None:
    """A provision-precise inline-(id) cite: forest projection == lens subset.

    The cite is NOT ``<ref>``-wrapped, so the forest body text and the lens's
    non-ref text are identical → the citation subset matches with 0 delta.
    """
    statute_id = "2020/999"
    xml = _xml(
        "<p>Noudatetaan, mitä ympäristönsuojelulaissa (527/2014) 5 a §:ssä "
        "säädetään.</p>"
    )
    forest_keys = _forest_keys_for(xml, statute_id)
    lens_keys = lens_citation_subset_target_keys(_lens_mentions(xml, statute_id))

    diff = diff_forest_vs_lens_citation_subset(forest_keys, lens_keys)
    assert diff.is_zero_delta, (
        f"missing={sorted(diff.forest_missing)} extra={sorted(diff.forest_extra)}"
    )
    # And it really is the provision-precise cite (sec 5a), not a statute-level miss.
    assert "2014/527//5a//" in forest_keys, sorted(forest_keys)


def test_zero_delta_on_coordinated_provision_targets() -> None:
    """A cite naming several provisions of one act: each target matches 1:1."""
    statute_id = "1990/211"
    xml = _xml(
        "<p>Muutetaan päätöksen (1296/89) 1 §:n 1, 2 ja 4 momenttia, 2 §:n 1 "
        "momenttia, 4 §:ää ja 6 §:n 1 momenttia.</p>"
    )
    forest_keys = _forest_keys_for(xml, statute_id)
    lens_keys = lens_citation_subset_target_keys(_lens_mentions(xml, statute_id))

    diff = diff_forest_vs_lens_citation_subset(forest_keys, lens_keys)
    assert diff.is_zero_delta, (
        f"missing={sorted(diff.forest_missing)} extra={sorted(diff.forest_extra)}"
    )
    # The cited act is canonicalised YEAR/NUMBER on BOTH sides (orientation-robust).
    assert all(k.startswith("1989/1296/") for k in forest_keys), sorted(forest_keys)
    # Six coordinated targets, none collapsed.
    assert len(forest_keys) == 6, sorted(forest_keys)


def test_zero_delta_on_statute_level_citation() -> None:
    """A whole-act inline-(id) cite (no § tail) → one STATUTE_ONLY-keyed target."""
    statute_id = "2000/300"
    xml = _xml("<p>Sovelletaan, mitä työsopimuslaissa (55/2001) säädetään.</p>")
    forest_keys = _forest_keys_for(xml, statute_id)
    lens_keys = lens_citation_subset_target_keys(_lens_mentions(xml, statute_id))

    diff = diff_forest_vs_lens_citation_subset(forest_keys, lens_keys)
    assert diff.is_zero_delta, (
        f"missing={sorted(diff.forest_missing)} extra={sorted(diff.forest_extra)}"
    )
    assert forest_keys == {"2001/55////"}, sorted(forest_keys)


def test_zero_delta_with_finding_b_head_separated_from_paren() -> None:
    """Finding-B: a head separated from its paren by a modifier still matches.

    ``annetun lain`` style head (the statute-name head separated from the ``(id)``
    by an intervening genitive / participle modifier) is the construction parse's
    own win class — the forest reproduces it 1:1 with the lens citation lane.
    """
    statute_id = "2015/100"
    xml = _xml(
        "<p>Poiketen siitä, mitä valvotusta koevapaudesta annetun lain "
        "(629/2013) 8 §:ssä säädetään.</p>"
    )
    forest_keys = _forest_keys_for(xml, statute_id)
    lens_keys = lens_citation_subset_target_keys(_lens_mentions(xml, statute_id))

    diff = diff_forest_vs_lens_citation_subset(forest_keys, lens_keys)
    assert diff.is_zero_delta, (
        f"missing={sorted(diff.forest_missing)} extra={sorted(diff.forest_extra)}"
    )
    assert "2013/629//8//" in forest_keys, sorted(forest_keys)


# ── residual worklist: families the forest does NOT own ──────────────────────


def test_forest_does_not_own_internal_ref_family() -> None:
    """A bare internal § cross-ref (no ``(id)`` anchor) is NOT a forest reference.

    The internal-reference family (``5 §:ssä`` with no statute id) is one the
    forest's citation leaf does not produce — it has no ``(NUMBER/YEAR)`` anchor.
    The lens DOES emit it (a different lane); the forest projects nothing for it.
    This is the residual worklist working as designed (surfaced, not silently
    claimed).
    """
    statute_id = "2003/314"
    xml = _xml("<p>Lisäksi 5 §:ssä säädetään poikkeuksesta.</p>")
    forest_keys = _forest_keys_for(xml, statute_id)
    # No inline-(id) anchor → the forest's citation projection is empty.
    assert forest_keys == set(), sorted(forest_keys)


def test_ref_wrapped_cite_is_annotation_boundary_residual() -> None:
    """A ``<ref>``-wrapped cite: forest recovers it; lens routes it to lane 1.

    The forest reads ``decode_body_text`` (itertext over ``<p>``, which crosses
    ``<ref>`` boundaries), so it recovers the inline-(id) cite even when Finlex
    wrapped it in ``<ref>`` markup — the annotation-INDEPENDENT behaviour. The
    lens's ``citation_construction`` lane scans NON-ref text, so the same cite is
    emitted under the ``ref_element`` lane instead. The forest is therefore (by
    design) a strict SUPERSET on annotated cites; this is the documented residual,
    not a miss.
    """
    statute_id = "2017/1000"
    xml = _xml(
        "<p>Sovelletaan, mitä "
        '<ref href="/akn/fi/act/statute-consolidated/2003/163#sec_1">'
        "valtioneuvoston asetuksessa (163/2003)</ref> säädetään.</p>"
    )
    forest_keys = _forest_keys_for(xml, statute_id)
    mentions = _lens_mentions(xml, statute_id)
    citation_subset = lens_citation_subset_target_keys(mentions)

    # The forest recovers the cite from the ref-wrapped surface …
    assert "2003/163////" in forest_keys, sorted(forest_keys)
    # … but the lens's citation_construction lane does NOT (it scans non-ref text),
    # so it is a forest-EXTRA vs the citation subset — the annotation boundary.
    diff = diff_forest_vs_lens_citation_subset(forest_keys, citation_subset)
    assert "2003/163////" in diff.forest_extra, sorted(diff.forest_extra)
    # The lens DOES capture the same target, via the <ref> lane (lane 1).
    assert any(
        m.phrase_lemma == "ref_element"
        and m.target_provision_ref is not None
        and m.target_provision_ref.statute_id == "2003/163"
        for m in mentions
    )


# ── projection shape sanity ──────────────────────────────────────────────────


def test_projection_is_gated_by_reference_np_leaves() -> None:
    """The projection emits facts only for segments the citation family gated.

    A pure-prose provision with no citation construction produces no
    ``reference_np`` leaf and therefore no projected reference.
    """
    statute_id = "2020/1"
    body = "Viranomaisen on tehtävä päätös viivytyksettä."
    forest = assemble_source_syntax_graph(
        subject=_SUBJECT, source_units=(), statute_id=statute_id, body=body
    )
    assert not forest.nodes_of_kind("reference_np")
    projected = project_forest_references(
        forest, body, source_statute_id=statute_id
    )
    assert projected == ()


def test_projected_reference_anchors_to_enclosing_segment() -> None:
    """Each projected reference is anchored to its enclosing structural segment."""
    statute_id = "2020/999"
    body = "Noudatetaan, mitä ympäristönsuojelulaissa (527/2014) 5 a §:ssä säädetään."
    forest = assemble_source_syntax_graph(
        subject=_SUBJECT, source_units=(), statute_id=statute_id, body=body
    )
    projected = project_forest_references(
        forest, body, source_statute_id=statute_id
    )
    assert projected, "expected one projected reference"
    p = projected[0]
    # Anchored to a real segment node whose span contains the cite.
    assert p.segment_node_id in forest.syntax_nodes
    assert body[p.char_start : p.char_end].count("(527/2014)") == 1
    assert p.mentions
