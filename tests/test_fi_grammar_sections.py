"""Differential tests for the slice-1 section-reference recognizer family.

Each test asserts the NEW parser (``grammar.parser.parse``) produces a
``SurfaceClause`` byte-identical to the OLD authority (``surface_parse.parse``)
via the differential harness — objective, not self-referential. The 16 worked
examples from the slice-1 briefing are the checklist; the corpus validation
script proves coverage at scale.
"""

from __future__ import annotations

import pytest

from lawvm.finland.johtolause import surface_parse
from lawvm.finland.johtolause.grammar import parser as new_parser
from lawvm.finland.johtolause.grammar.diff import (
    compare_surface_parsers,
    parse_text_with,
)
from lawvm.finland.johtolause.grammar.parser import OutOfScope
from lawvm.finland.johtolause.surface_model import (
    SurfaceDescendantCoordination,
    SurfaceNode,
    SurfaceScopeBlock,
    SurfaceTargetRef,
)


def _as_target(node: SurfaceNode) -> SurfaceTargetRef:
    assert isinstance(node, SurfaceTargetRef)
    return node


def _as_block(node: SurfaceNode) -> SurfaceScopeBlock:
    assert isinstance(node, SurfaceScopeBlock)
    return node


def _as_coordination(node: SurfaceNode) -> SurfaceDescendantCoordination:
    assert isinstance(node, SurfaceDescendantCoordination)
    return node

# Worked examples 1–15 from the briefing: each must be byte-identical to the
# old parser. (Example 16's literal string is degenerate — see the dedicated
# tests below — so the clean prefix form stands in for the prefix-witness case.)
IN_SCOPE_EXAMPLES = [
    "muutetaan 12 §",
    "muutetaan 1, 3, 7, 8 ja 10 §",
    "muutetaan 21–23 §",
    "muutetaan 5 a §",
    "muutetaan 11 a–11 d §",
    "muutetaan 3 luvun 12 §",
    "muutetaan II osan 3 luvun 12 §",
    "muutetaan 3 §:n 2 momentti",
    "muutetaan 12 §:n 2 ja 3 momentti",
    "muutetaan 5 §:n 1 momentin 2 kohta",
    "muutetaan 5 §:n 1 kohta",
    "muutetaan 5 §:n otsikko",
    "muutetaan 12 §:n 2 momentin johdantokappale",
    "muutetaan 3 §:n 1 kohta ja 2 kohdan johdantolause",
    "muutetaan 4 pykälä numero 5:ksi",
    # Example 16: the genitive-plural prefix form, in its non-degenerate shape
    # (no trailing structural noun) so the old parser's prefix arm actually
    # fires with witness fi.section_ref_pykala_prefix.
    "muutetaan pykälien 1, 9 ja 45",
]


@pytest.mark.parametrize("text", IN_SCOPE_EXAMPLES)
def test_section_ref_examples_are_zero_delta(text: str) -> None:
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


def test_label_keeps_suffix_only_when_not_a_range() -> None:
    # "5 a §" -> label "5a"; "11 a–11 d §" -> 11a,11b,11c,11d (suffix per item).
    model = parse_text_with("muutetaan 5 a §", new_parser.parse)
    (vg,) = model.verb_groups
    target = _as_target(vg.nodes[0])
    assert target.label == "5a"


def test_chapter_moves_to_scope_block_and_clears_on_target() -> None:
    model = parse_text_with("muutetaan 3 luvun 12 §", new_parser.parse)
    (vg,) = model.verb_groups
    block = _as_block(vg.nodes[0])
    assert block.scope_label == "3"
    assert _as_target(block.targets[0]).chapter == ""
    assert block.witness is not None and block.witness.rule_id == "fi.scope_block_chapter"


def test_part_is_outer_scope_chapter_stays_on_target() -> None:
    model = parse_text_with("muutetaan II osan 3 luvun 12 §", new_parser.parse)
    (vg,) = model.verb_groups
    block = _as_block(vg.nodes[0])
    assert block.scope_label == "II"
    assert _as_target(block.targets[0]).chapter == "3"  # chapter preserved on target
    assert block.witness is not None and block.witness.rule_id == "fi.scope_block_part"


def test_bare_kohta_defaults_to_momentti_one() -> None:
    model = parse_text_with("muutetaan 5 §:n 1 kohta", new_parser.parse)
    (vg,) = model.verb_groups
    target = _as_target(vg.nodes[0])
    (sub,) = target.sub_refs
    assert sub.momentti == 1 and sub.item == "1"


def test_renumber_arm_emits_renumber_dest_and_note() -> None:
    model = parse_text_with("muutetaan 4 pykälä numero 5:ksi", new_parser.parse)
    (vg,) = model.verb_groups
    target = _as_target(vg.nodes[0])
    assert target.label == "4"
    assert target.renumber_dest == "5"
    assert "renumber_clause" in target.notes
    assert target.witness is not None and target.witness.rule_id == "fi.section_renumber"


def test_trailing_facet_distributes_to_preceding_item_arms() -> None:
    # "3 §:n 1 kohta ja 2 kohdan johdantolause" -> BOTH kohta sub_refs get INTRO.
    from lawvm.core.semantic_types import FacetKind

    model = parse_text_with(
        "muutetaan 3 §:n 1 kohta ja 2 kohdan johdantolause", new_parser.parse
    )
    (vg,) = model.verb_groups
    node = _as_coordination(vg.nodes[0])
    arms = node.arms
    assert len(arms) == 2
    assert all(a.facet == FacetKind.INTRO for a in arms)


def test_pykala_prefix_witness() -> None:
    model = parse_text_with("muutetaan pykälien 1, 9 ja 45", new_parser.parse)
    (vg,) = model.verb_groups
    targets = [_as_target(n) for n in vg.nodes]
    assert [t.label for t in targets] == ["1", "9", "45"]
    assert all(
        t.witness is not None and t.witness.rule_id == "fi.section_ref_pykala_prefix"
        for t in targets
    )


def test_degenerate_pykala_prefix_with_trailing_section_is_out_of_scope() -> None:
    # The literal briefing example 16 "pykälien 1, 9, 45 §": the trailing § makes
    # the old parser's prefix arm back out, yielding EMPTY verb_groups. The new
    # parser declares this out of scope (it is not a clean section-ref clause).
    text = "muutetaan pykälien 1, 9, 45 §"
    old = parse_text_with(text, surface_parse.parse)
    assert old.verb_groups == ()  # degenerate in the old parser too
    with pytest.raises(OutOfScope):
        new_parser.parse(*_tokens_for(text))


def test_out_of_scope_shapes_raise() -> None:
    # NB: heading-placement inserts ("§:n edelle uusi väliotsikko") are NO LONGER
    # out of scope — target-first heading inserts now parse to a
    # SurfaceHeadingPlacement (rule fi.heading_edelle_otsikko_target_list); see
    # test_parse_clause_target_first_valiotsake_then_subsection_insert and the
    # SurfaceHeadingPlacement coverage in test_fi_grammar_insertions.py.
    # Meta-only clause (no amendment verb) is out of scope.
    with pytest.raises(OutOfScope):
        new_parser.parse(*_tokens_for("Tämä laki tulee voimaan 1 päivänä tammikuuta 2020."))


HEADING_CHANGE_EXAMPLES = [
    # Single section heading-change ("edellä oleva otsikko").
    "muutetaan 1 §:n edellä oleva otsikko",
    # Heading-change after a momentti qualifier (both parsers drop the heading
    # past the momentti — still byte-identical).
    "muutetaan 5 §:n 2 momentin edellä oleva otsikko",
    # Continuation: a plain section then a heading-change section.
    "muutetaan 1 §, 2 §:n edellä oleva otsikko",
    # Optional "luvun" before otsikko ("edellä oleva luvun otsikko").
    "muutetaan 1 §:n edellä oleva luvun otsikko",
    # The (väli)otsikko spelling also binds a section-level HEADING facet.
    "muutetaan 16 luvun 3 §:n edellä oleva väliotsikko",
    # Plural participle heading-renumbering, distributed over the section list.
    "muutetaan 3 ja 5 §:n edellä olevien lukujen otsikoiden numerointi",
]


@pytest.mark.parametrize("text", HEADING_CHANGE_EXAMPLES)
def test_heading_change_sub_ref_is_zero_delta(text: str) -> None:
    # The "edellä oleva/olevien otsikko" heading-CHANGE arm is now owned by the
    # section recognizer (a section-level HEADING facet), byte-identical to the
    # old parser rather than declining to the surface fallback.
    report = compare_surface_parsers(text, surface_parse.parse, new_parser.parse)
    assert report.equal, f"delta on {text!r}:\n{report.summary()}"


def test_heading_change_emits_section_heading_facet() -> None:
    from lawvm.core.semantic_types import FacetKind

    model = parse_text_with("muutetaan 1 §:n edellä oleva otsikko", new_parser.parse)
    (vg,) = model.verb_groups
    target = _as_target(vg.nodes[0])
    assert target.label == "1"
    assert len(target.sub_refs) == 1
    assert target.sub_refs[0].facet == FacetKind.HEADING
    assert target.witness is not None and target.witness.rule_id == "fi.section_ref"


def test_heading_change_before_backref_continuation_declines() -> None:
    # "N §:n edellä oleva väliotsikko ja mainitun pykälän …": the old parser
    # folds the leading separator into the backref span, which the integrated
    # driver does not reproduce — the recognizer declines rather than miscompile.
    text = "muutetaan 16 luvun 3 §:n edellä oleva väliotsikko ja mainitun pykälän 2 momentin 14 kohta"
    with pytest.raises(OutOfScope):
        new_parser.parse(*_tokens_for(text))


def test_heading_change_with_multi_section_heading_insert_declines() -> None:
    # A heading-CHANGE coexisting with a multi-section heading-INSERT elsewhere
    # in the clause is declined: the insertion family does not yet expand the
    # multi-section insert identically to the old parser, so recovering the
    # heading-change would surface that divergence as a miscompile.
    text = (
        "muutetaan 6 luvun otsikko, 48 §:n edellä oleva väliotsikko, "
        "lisätään 41 c ja 54 a §:n edelle uusi väliotsikko seuraavasti:"
    )
    with pytest.raises(OutOfScope):
        new_parser.parse(*_tokens_for(text))


def _tokens_for(text: str):
    """Build the (tokens, jolloin) the contract entry point expects."""
    from lawvm.finland.johtolause.lexer import tokenize
    from lawvm.finland.johtolause.scan import apply_annotations_with_jolloin_pairs

    raw = tokenize(text)
    tokens, jolloin = apply_annotations_with_jolloin_pairs(raw)
    return tokens, (jolloin if jolloin else None)
