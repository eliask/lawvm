"""Tests for parse_clause() — Phase 6 canonical public API.

Covers:
  - Basic smoke: parse_clause returns ClauseParseResult with non-empty ClauseAST
  - ClauseParseResult fields are populated correctly
  - ClauseAST output matches legacy extract_ops output (structural equivalence)
  - Runs on all curated cases and verifies ClauseAST is non-empty for non-xfail
  - statute_id is preserved in diagnostics when provided
  - Empty / no-op text produces a well-formed (empty) result
"""

from __future__ import annotations

import pytest

from lawvm.core.clause_ast import ClauseAST
from lawvm.core.semantic_types import FacetKind, MetaClauseKind, StructuralAction
from lawvm.finland.johtolause import extract_legal_ops
from lawvm.finland.johtolause.api import ClauseParseResult, parse_clause
from lawvm.finland.ops import lo_scope_confidence
from lawvm.finland.johtolause.surface_model import (
    ScopeKind,
    SurfaceRenumberTail,
    TargetKind,
    SurfaceTargetRef,
    SurfaceScopeBlock,
    SurfaceDescendantCoordination,
    VerbKind,
)
from tests.fixtures.fi_curated_cases import CURATED_CASES


# ---------------------------------------------------------------------------
# Basic smoke tests
# ---------------------------------------------------------------------------


def test_parse_clause_returns_clause_parse_result():
    """parse_clause() must return a ClauseParseResult."""
    result = parse_clause("muutetaan 5 §")
    assert isinstance(result, ClauseParseResult)


def test_parse_clause_clause_ast_is_clause_ast():
    """The clause_ast field must be a ClauseAST instance."""
    result = parse_clause("muutetaan 5 §")
    assert isinstance(result.clause_ast, ClauseAST)


def test_parse_clause_clause_ast_non_empty():
    """A valid johtolause must produce a non-empty ClauseAST."""
    result = parse_clause("muutetaan 5 §")
    assert result.clause_ast.verb_groups, "ClauseAST should have at least one VerbGroup for a valid johtolause"


def test_parse_clause_doc_insert_range_after_heading_residue_does_not_crash():
    """Regression for 1996/473: heading residues between insert ranges are not cursors."""
    text = (
        "lisätään 1 §:ään uusi 2 momentti, asetukseen uusi 37 a §, asetuksen "
        "7 lukuun uusi 52 a §, 53 §:n edelle uusi luvun otsikko, asetukseen "
        "uusi 53 a―53 d §, 54 §:n edelle uusi luvun otsikko, asetukseen uusi "
        "54 a―54 f § seuraavasti:"
    )
    result = parse_clause(text)
    assert isinstance(result, ClauseParseResult)


def test_parse_clause_alakohta_replace_preserves_later_chapter_scoped_targets():
    """Regression for 2019/518: alakohta precision must not truncate later targets."""
    text = (
        "muutetaan vakuutusyhtiölain ( 521/2008 ) 1 luvun 11 b §:n 6 kohdan "
        "a alakohta, 3 luvun 20 §:n 1 momentti, 4 luvun 5 §:n 3 momentti, "
        "5 luvun 2 §:n otsikko, 7 §:n 2 momentti ja 18 §, 6 luvun 1 § ja "
        "5 §:n 1 momentti, 16 luvun 6 §:n 4 momentti ja 11 §:n 2 momentti "
        "sekä 28 luvun 2 §:n 3 momentti ja 3 §:n 2 momentti, lisätään "
        "1 lukuun uusi 24 a-24 c §, 5 luvun 2 §:ään, sellaisena kuin se "
        "on laissa 587/2009, uusi 2 momentti ja lukuun uusi 6 a ja 18 a §, "
        "6 lukuun uusi 2 a ja 4 a § ja luvun 7 §:ään uusi 2 momentti sekä "
        "lukuun uusi 20 d ja 20 e § sekä niiden edelle uusi väliotsikko "
        "seuraavasti:"
    )
    result = parse_clause(text)
    assert [op.code() for op in result.parsed_ops] == [
        "M P L:1 11b 1 6 a",
        "M P L:3 20 1",
        "M P L:4 5 3",
        "M P L:5 2 o",
        "M P L:5 7 2",
        "M P L:5 18",
        "M P L:6 1",
        "M P L:6 5 1",
        "M P L:16 6 4",
        "M P L:16 11 2",
        "M P L:28 2 3",
        "M P L:28 3 2",
        "L P L:1 24a",
        "L P L:1 24b",
        "L P L:1 24c",
        "L P L:5 2 2",
        "L P L:5 6a",
        "L P L:5 18a",
        "L P L:6 2a",
        "L P L:6 4a",
        "L P L:6 7 2",
        "L P L:6 20d",
        "L P L:6 20e",
    ]


def test_parse_clause_parsed_ops_populated():
    """parsed_ops must be populated for a valid johtolause."""
    text = "muutetaan 5 §"
    result = parse_clause(text)
    assert len(result.parsed_ops) == 1
    assert result.parsed_ops[0].code() == "M P 5"


def test_parse_clause_part_renumber_keeps_roman_translative_destination():
    """Roman translative renumber destinations must survive tokenization and parsing.

    Regression for the live 2019/371 clause fragment "II A osan numero III:ksi":
    the destination used to be tokenized as WORD, which prevented the part
    renumber branch from attaching renumber_dest.
    """
    from lawvm.finland.johtolause.lexer import tokenize

    tokens = tokenize("III:ksi")
    assert len(tokens) == 1
    assert tokens[0].cat == "NUM"
    assert tokens[0].case == "TRANS"
    assert tokens[0].lemma == "III"

    result = parse_clause("muutetaan II A osan numero III:ksi")
    sc = result.surface_clause
    assert sc is not None
    part_nodes = [
        node
        for vg in sc.verb_groups
        for node in vg.nodes
        if isinstance(node, SurfaceTargetRef) and node.kind == TargetKind.PART
    ]
    assert part_nodes, "Expected a part target in the surface clause"
    assert part_nodes[0].label == "IIa"
    assert part_nodes[0].renumber_dest == "III"
    assert result.parsed_ops[0].renumber_dest == "III"


def test_parse_clause_part_backref_scoped_section_renumbers_continue_after_part_renumber():
    """Part context must survive into ``mainitun osan`` scoped section renumbers."""
    from lawvm.finland.johtolause import extract_legal_ops

    ops = extract_legal_ops(
        "muutetaan II A osan numero III:ksi, "
        "mainitun osan 1 luvun 1 §:n numero 136:ksi, "
        "2 §:n numero 137:ksi, "
        "3 §:n numero 138:ksi"
    )

    assert len(ops) == 4
    assert ops[0].target.path == (("part", "IIa"),)
    assert ops[0].destination is not None
    assert ops[0].destination.path == (("part", "III"),)

    assert ops[1].target.path == (("part", "IIa"), ("chapter", "1"), ("section", "1"))
    assert ops[1].destination is not None
    assert ops[1].destination.path == (("section", "136"),)

    assert ops[2].target.path == (("part", "IIa"), ("chapter", "1"), ("section", "2"))
    assert ops[2].destination is not None
    assert ops[2].destination.path == (("section", "137"),)

    assert ops[3].target.path == (("part", "IIa"), ("chapter", "1"), ("section", "3"))
    assert ops[3].destination is not None
    assert ops[3].destination.path == (("section", "138"),)


def test_parse_clause_chapter_backref_scoped_section_renumbers_continue_after_heading_target():
    """Chapter context must survive into ``mainitun luvun`` scoped section renumbers."""
    from lawvm.finland.johtolause import extract_legal_ops

    ops = extract_legal_ops(
        "muutetaan 2 luvun otsikko, "
        "mainitun luvun 1 §:n numero 144:ksi, "
        "2 §:n numero 145:ksi"
    )

    assert len(ops) == 3
    assert ops[0].target.path == (("chapter", "2"),)

    assert ops[1].target.path == (("chapter", "2"), ("section", "1"))
    assert ops[1].destination is not None
    assert ops[1].destination.path == (("section", "144"),)

    assert ops[2].target.path == (("chapter", "2"), ("section", "2"))
    assert ops[2].destination is not None
    assert ops[2].destination.path == (("section", "145"),)


def test_parse_clause_chapter_heading_wording_and_number_keeps_later_part_context() -> None:
    """Chapter heading wording plus ``ja numero`` must not terminate the target list."""
    result = parse_clause(
        "muutetaan VI osan 4 luvun otsikon ruotsinkielinen sanamuoto ja numero 29:ksi, "
        "233 §:n 1 momentin johdantokappale, "
        "VI osan 5 luvun otsikon ruotsinkielinen sanamuoto ja numero 30:ksi, "
        "236 §:n otsikon ja 2 momentin ruotsinkielinen sanamuoto, "
        "VII osan 1 luvun numero 31:ksi, "
        "VII osan 2 luvun numero 32:ksi"
    )

    chapter_renumbers = [
        op
        for op in result.parsed_ops
        if op.kind == "L" and op.renumber_dest in {"29", "30", "31", "32"}
    ]

    assert [(op.part, op.number, op.renumber_dest) for op in chapter_renumbers] == [
        ("VI", "4", "29"),
        ("VI", "5", "30"),
        ("VII", "1", "31"),
        ("VII", "2", "32"),
    ]


def test_parse_clause_chapter_backref_targets_continue_across_verb_groups() -> None:
    """Chapter context must survive into ``mainitun luvun`` after a prior verb group."""
    result = parse_clause(
        "kumotaan 25 luvun 5 §, "
        "muutetaan mainitun luvun 1 §, 2 §:n 1 momentti, 3 §:n 1 ja 3 momentti, 4 § ja 6-9 §"
    )

    assert [op.code() for op in result.parsed_ops] == [
        "K P L:25 5",
        "M P L:25 1",
        "M P L:25 2 1",
        "M P L:25 3 1",
        "M P L:25 3 3",
        "M P L:25 4",
        "M P L:25 6",
        "M P L:25 7",
        "M P L:25 8",
        "M P L:25 9",
    ]


def test_parse_clause_reinsert_target_survives_after_jolloin_renumber() -> None:
    """A ``kumotun N §:n tilalle uusi N §`` reinsertion after a jolloin
    renumber tail must NOT be swallowed by the renumber span.

    Shape from 2002/1091: the ``jolloin nykyinen 4 momentti siirtyy 5
    momentiksi`` consequence is followed by ``ja kumotun 91 §:n tilalle uusi
    91 §``.  The renumber span must terminate at the reinsertion so the
    trailing insert target is produced rather than dropped.
    """
    result = parse_clause(
        "lisätään lain (1/81) 87 §:ään uusi 4 momentti, "
        "jolloin nykyinen 4 momentti siirtyy 5 momentiksi, "
        "ja kumotun 91 §:n tilalle uusi 91 § seuraavasti:"
    )
    codes = [op.code() for op in result.parsed_ops]
    assert "L P 91" in codes


def test_parse_clause_provenance_reinsert_survives_after_jolloin_renumber() -> None:
    """Real 2002/1091 johtolause: the reinsertion is introduced by a
    provenance phrase (collapsed into a citation span) before the ``kumotun``
    participle — ``... siirtyy 6 ja 7 momentiksi, ja mainitulla lailla
    989/1992 kumotun 91 §:n tilalle uusi 91 §``.  The trailing insert must
    survive the renumber tail.
    """
    result = parse_clause(
        "kumotaan 3 päivänä huhtikuuta 1981 annetun tieliikennelain "
        "( 267/1981 ) 83, 83 a, 83 b ja 84 §, muutetaan 2 a §, "
        "6 luvun otsikko, 85 §, 86 §:n 2 momentti, 87 §:n 3 momentti, "
        "88 §:n 1 momentti, 89 §:n 1 momentti sekä 92, 96, 105, 107 ja 108 §, "
        "lisätään 87 §:ään, sellaisena kuin se on mainitussa laissa 989/1992, "
        "uusi 4 ja 5 momentti, jolloin nykyinen 4 ja 5 momentti siirtyy "
        "6 ja 7 momentiksi, ja mainitulla lailla 989/1992 kumotun 91 §:n "
        "tilalle uusi 91 § seuraavasti:"
    )
    codes = [op.code() for op in result.parsed_ops]
    assert "L P 91" in codes


def test_parse_clause_multi_section_reinsert_target_list() -> None:
    """A coordinated reinsertion ``kumotun N, M ja P §:n tilalle uusi N, M ja
    P §`` must yield one insert op per listed section, even when it opens the
    verb group (shape from 2003/1337 / 2004/816).
    """
    result = parse_clause(
        "lisätään lain (1/01) kumotun 11, 12 ja 12 a §:n tilalle "
        "uusi 11, 12 ja 12 a § seuraavasti:"
    )
    assert [op.code() for op in result.parsed_ops] == ["L P 11", "L P 12", "L P 12a"]


def test_parse_clause_multi_section_reinsert_mid_list_keeps_neighbours() -> None:
    """A multi-section reinsertion wedged between two ordinary insert targets
    must not drop either neighbour (shape from 2002/700):
    ``... lakiin ... kumottujen 63 ja 65 §:n tilalle uusi 63 ja 65 §, lakiin
    uusi 80 a §``.
    """
    result = parse_clause(
        "lisätään lain (1/87) 61 §:ään uusi 4 momentti, "
        "lakiin mainitulla lailla 895/1996 kumottujen 63 ja 65 §:n tilalle "
        "uusi 63 ja 65 §, lakiin uusi 80 a § seuraavasti:"
    )
    codes = [op.code() for op in result.parsed_ops]
    for expected in ("L P 63", "L P 65", "L P 80a"):
        assert expected in codes, codes


def test_parse_clause_chapter_reinsert_with_descriptive_provenance_keeps_sections() -> None:
    """``N lukuun <descriptive provenance> kumotun M §:n sijaan uusi M §``.

    Regression for 1973/390: the chapter destination is followed by the title
    of the repealed 1868 act before the reinstatement sentinel.  The provenance
    is not a target; the inserts must land in the explicit chapter.
    """
    result = parse_clause(
        "lisätään 9 lukuun määräajasta velkomisasioissa sekä julkisesta "
        "haasteesta velkojille 9 päivänä marraskuuta 1868 annetulla "
        "asetuksella kumotun 12 §:n sijaan uusi 12 § sekä uusi 13 § "
        "seuraavasti:"
    )

    assert [op.code() for op in result.parsed_ops] == ["L P L:9 12", "L P L:9 13"]


def test_parse_clause_historical_passive_preverbal_replace_keeps_section_list() -> None:
    text = (
        "Eduskunnan päätöksen mukaisesti säädetään, että 1 päivänä kesäkuuta 1922 "
        "annetun kielilain 2, 3, 5, 6, 9, 10, 12, 13, 16, 17, 18, 20 sekä 21 §, "
        "näistä 20 § sellaisena, kuin se on 28 päivänä toukokuuta 1927 annetussa "
        "laissa, on muutettava näin kuuluviksi:"
    )

    result = parse_clause(text)
    codes = [op.code() for op in result.parsed_ops]

    assert codes == [
        "M P 2",
        "M P 3",
        "M P 5",
        "M P 6",
        "M P 9",
        "M P 10",
        "M P 12",
        "M P 13",
        "M P 16",
        "M P 17",
        "M P 18",
        "M P 20",
        "M P 21",
    ]
    assert codes.count("M P 20") == 1
    assert any(
        diagnostic.rule_id == "fi.johtolause.historical_passive_preverbal_replace.v1"
        for diagnostic in result.typed_diagnostics
    )


def test_parse_clause_transport_glued_verb_numeric_target_space_keeps_replace_group() -> None:
    text = (
        "muutetaan12 §, 13 §:n 2 momentti, 16 §:n 1, 2 ja 4 momentti, "
        "16 a §:n 4 momentti, 17 §:n 2 ja 3 momentti, 18 §:n 1 momentti, "
        "23 §:n 3 momentti, 26 §:n 1, 2 ja 4 momentti sekä 28 §:n 5 momentti"
    )

    result = parse_clause(text)

    assert [op.code() for op in result.parsed_ops] == [
        "M P 12",
        "M P 13 2",
        "M P 16 1",
        "M P 16 2",
        "M P 16 4",
        "M P 16a 4",
        "M P 17 2",
        "M P 17 3",
        "M P 18 1",
        "M P 23 3",
        "M P 26 1",
        "M P 26 2",
        "M P 26 4",
        "M P 28 5",
    ]
    assert result.surface_clause is not None
    assert result.surface_clause.source_text == text
    assert any(
        diagnostic.rule_id
        == "fi.johtolause.transport_glued_verb_numeric_target_space.v1"
        for diagnostic in result.typed_diagnostics
    )


def test_parse_clause_container_provenance_bridge_keeps_first_section_target() -> None:
    """A provenance span after a chapter target must not swallow the first
    following section target.

    Real witness: 1998/1143, where ``3 luku siihen myöhemmin tehtyine
    muutoksineen, 27, 28 ja 31 §`` changes chapters 2/3 and also separately
    changes sections 27/28/31. Dropping 27 lets the body wrapper smuggle it into
    chapter 3 instead of leaving it as an explicit section target.
    """

    result = parse_clause(
        "muutetaan 2 luku, 3 luku siihen myöhemmin tehtyine muutoksineen, "
        "27, 28 ja 31 §"
    )

    assert [op.code() for op in result.parsed_ops] == [
        "M L 2",
        "M L 3",
        "M P L:3 27",
        "M P L:3 28",
        "M P L:3 31",
    ]


def test_parse_clause_surface_clause_populated():
    """surface_clause must be a non-None object (Phase 3 SurfaceClause)."""
    from lawvm.finland.johtolause.surface_model import SurfaceClause

    result = parse_clause("muutetaan 5 §")
    assert result.surface_clause is not None
    assert isinstance(result.surface_clause, SurfaceClause)


def test_parse_clause_resolved_is_populated():
    """resolved is populated via the direct authority path (Phase 11)."""
    result = parse_clause("muutetaan 5 §")
    assert result.resolved is not None


# ---------------------------------------------------------------------------
# statute_id propagation
# ---------------------------------------------------------------------------


def test_parse_clause_statute_id_in_diagnostics():
    """statute_id is reflected in the diagnostics list."""
    result = parse_clause("muutetaan 5 §", statute_id="FI-1234/2020")
    assert any("FI-1234/2020" in d for d in result.diagnostics), "statute_id should appear in diagnostics"


def test_parse_clause_no_statute_id_no_extra_diagnostic():
    """Without statute_id, no statute_id diagnostic is emitted."""
    result = parse_clause("muutetaan 5 §")
    assert not any("statute_id" in d for d in result.diagnostics)


# ---------------------------------------------------------------------------
# Multi-verb and scoped cases
# ---------------------------------------------------------------------------


def test_parse_clause_multi_verb():
    """Multi-verb johtolause produces multiple VerbGroups."""
    result = parse_clause("kumotaan 7 §, muutetaan 12 §")
    assert len(result.clause_ast.verb_groups) >= 2


def test_parse_clause_doc_ill_provenance_keeps_subsection_insert_target():
    """DOC:ILL insertions must skip comma+provenance before ``uusi N §:n M momentti``.

    Regression for 2017/571: ``asetukseen, sellaisena kuin se on asetuksessa
    543/2015 uusi 1 §:n 2 momentti`` used to fall through the DOC:ILL branch and
    degrade into a whole-section insertion.
    """
    text = (
        "lisätään asetukseen, sellaisena kuin se on asetuksessa 543/2015 "
        "uusi 1 §:n 2 momentti seuraavasti:"
    )

    result = parse_clause(text)

    assert [op.code() for op in result.parsed_ops] == ["L P 1 2"]
    assert result.parsed_ops[0].witness is not None
    assert result.parsed_ops[0].witness.rule_id == "fi.insertion_sub_target"


def test_parse_clause_nain_kuuluva_after_provenance_keeps_subsection_insert():
    """``N §:ään, <provenance>, näin kuuluva uusi M momentti`` keeps the insert.

    Regression for 1960/391 (and similar archaic acts): a ``näin kuuluva``
    lead-in sits between the §:ään target (and its skipped citation/provenance
    span) and ``uusi``.  Without skipping it, Pattern A failed to reach ``uusi``
    and the subsection insert was dropped, forcing the normalize.py regex
    fallback to recover it.
    """
    text = (
        "lisätään 7 §:ään, sellaisena kuin se on viimeksi muutettuna annetussa "
        "asetuksessa (345/59), näin kuuluva uusi 3 momentti:"
    )

    result = parse_clause(text)

    assert [op.code() for op in result.parsed_ops] == ["L P 7 3"]


def test_parse_clause_kumotaan_chapter_then_reinsert_emits_repeal_and_insert():
    """`kumotaan N luku ... lisätään kumottavan N luvun tilalle uusi N luku`.

    Regression for 518/1995 <- 1997/451: the johtolause repeals chapter 5 and
    re-inserts a new chapter 5 in its place.  The chapter-level reinstatement
    preamble ``kumottavan 5 luvun tilalle`` previously broke the DOC:ILL insert
    branch, so the ``lisätään ... uusi 5 luku`` INSERT was dropped and the
    chapter was lost (a later ``REPLACE 5 luku otsikko`` then failed with
    ``master chapter:5 not found``).  Both the REPEAL and the INSERT of
    chapter 5 must now be emitted.
    """
    text = (
        "kumotaan 7 päivänä huhtikuuta 1995 annetun sähkömarkkina-asetuksen "
        "(518/1995) 5 luku, muutetaan 2 §:n 3 kohta sekä lisätään asetukseen "
        "kumottavan 5 luvun tilalle uusi 5 luku seuraavasti:"
    )

    result = parse_clause(text)

    assert [op.code() for op in result.parsed_ops] == [
        "K L 5",
        "M P 2 1 3",
        "L L 5",
    ]


def test_parse_clause_chapter_reinstatement_preamble_keeps_insert_target():
    """Bare ``lisätään ... kumottavan N luvun tilalle uusi N luku`` keeps INSERT."""
    text = "lisätään asetukseen kumottavan 5 luvun tilalle uusi 5 luku seuraavasti:"

    result = parse_clause(text)

    assert [op.code() for op in result.parsed_ops] == ["L L 5"]


def test_parse_clause_named_row_residue_does_not_truncate_later_targets():
    """A `koodi 121` residue must not truncate later ordinary targets."""
    text = "muutetaan 5 §, 6 §:n 2 momentin koodi 121, 7 §:n 2 momentti, 10 ja 10 a § sekä 3 ja 4 luku"

    result = parse_clause(text)

    assert [op.code() for op in result.parsed_ops] == [
        "M P 5",
        "M P 6 2",
        "M P 7 2",
        "M P 10",
        "M P 10a",
        "M L 3",
        "M L 4",
    ]


def test_parse_clause_edella_oleva_otsikko_change_target_keeps_later_targets() -> None:
    """`N §:n edellä oleva otsikko` heading-change target must not drop the rest.

    The locative ``edellä oleva`` (heading-CHANGE) form is distinct from the
    allative ``edelle uusi`` (heading-INSERT) placement form. It binds the
    preceding section as a heading-amend target, and the enclosing
    kumotaan/muutetaan list must continue past it.
    """
    result = parse_clause(
        "kumotaan 12 §, 13 §:n edellä oleva otsikko, 17 § ja 59 §"
    )

    assert [op.code() for op in result.parsed_ops] == [
        "K P 12",
        "K P 13 o",
        "K P 17",
        "K P 59",
    ]


def test_parse_clause_edella_oleva_luvun_otsikko_change_target_keeps_later_targets() -> None:
    """`N §:n edellä oleva luvun otsikko` (chapter-heading variant) must continue."""
    result = parse_clause(
        "kumotaan 12 §, 13 §:n edellä oleva luvun otsikko, 17 § ja 59 §"
    )

    assert [op.code() for op in result.parsed_ops] == [
        "K P 12",
        "K P 13 o",
        "K P 17",
        "K P 59",
    ]


def test_parse_clause_edella_oleva_alaotsikko_change_target_keeps_later_targets() -> None:
    """`N §:n edellä oleva alaotsikko` sub-heading variant must continue.

    ``alaotsikko`` is an OTSIKKO synonym (1980s drafting); without it the
    reference degraded to a bare WORD and the whole muutetaan list was dropped.
    """
    result = parse_clause(
        "muutetaan 4 §:n 1 momentti, 14 §:n edellä oleva alaotsikko, 14 §:n 1 momentti, 18 §"
    )

    assert [op.code() for op in result.parsed_ops] == [
        "M P 4 1",
        "M P 14 o",
        "M P 14 1",
        "M P 18",
    ]


def test_parse_clause_edella_olevien_lukujen_otsikoiden_numerointi_keeps_later_targets() -> None:
    """`N §:n edellä olevien lukujen otsikoiden numerointi` renumbering form must continue."""
    result = parse_clause(
        "kumotaan 23, 36 ja 41 §:n edellä olevien lukujen otsikoiden numerointi ja 59 §"
    )

    codes = [op.code() for op in result.parsed_ops]
    # The heading-renumbering reference must not abort the list: the trailing
    # ``59 §`` target survives, and the preceding section labels are all present.
    assert "K P 59" in codes
    assert {"K P 23", "K P 36"}.issubset(set(codes))
    assert any(c.startswith("K P 41") for c in codes)


def test_parse_clause_sen_edella_oleva_valiotsikko_change_target_keeps_later_targets() -> None:
    """`N § ja sen edellä oleva väliotsikko` anaphoric heading ref must continue."""
    result = parse_clause(
        "muutetaan 11 § ja sen edellä oleva väliotsikko, 12 §, 23 §"
    )

    codes = [op.code() for op in result.parsed_ops]
    assert "M P 11" in codes
    assert "M P 12" in codes
    assert "M P 23" in codes


def test_parse_clause_niiden_edella_oleva_valiotsikko_change_target_keeps_later_targets() -> None:
    """`N—M § ja niiden edellä oleva väliotsikko` plural anaphor must continue."""
    result = parse_clause(
        "muutetaan 3 §:n 2 momentti, 7—9 § ja niiden edellä oleva väliotsikko, 11 §:n 7 kohta"
    )

    codes = [op.code() for op in result.parsed_ops]
    assert "M P 3 2" in codes
    assert "M P 7" in codes and "M P 9" in codes
    assert "M P 11 1 7" in codes


def test_parse_clause_glued_numeric_conjunction_keeps_both_section_targets() -> None:
    """Glued `18ja 20 §` transport noise must split into two section targets."""
    text = "muutetaan 18ja 20 §"

    result = parse_clause(text)

    assert [op.code() for op in result.parsed_ops] == [
        "M P 18",
        "M P 20",
    ]


def test_parse_clause_letter_item_coordination_keeps_all_item_targets() -> None:
    """Coordinated letter items must not collapse into a whole-section target."""
    result = parse_clause("muutetaan 18 §:n d ja h kohta")

    assert [op.code() for op in result.parsed_ops] == [
        "M P 18 1 d",
        "M P 18 1 h",
    ]


def test_parse_clause_exact_2014_174_clause_keeps_section_18_item_targets() -> None:
    """2014/174 must keep the coordinated item targets under 18 §."""
    text = (
        "muutetaan rahoitus- ja vakuutusryhmittymien valvonnasta annetun lain (699/2004) "
        "2 §:n 1 momentin 5 kohta ja 3 momentti, 13 §:n 1 momentti, 18 §:n d ja h kohta "
        "sekä 33 §:n 1 momentti, lisätään 2 §:n 1 momenttiin, sellaisena kuin se on osaksi "
        "laeissa 132/2007, 886/2008, 763/2012, 427/2013 ja 984/2013, uusi 3 a kohta ja "
        "18 §:ään, sellaisena kuin se on osaksi laeissa 132/2007, 763/2012 ja 984/2013, "
        "uusi i kohta seuraavasti:"
    )

    result = parse_clause(text)

    assert [op.code() for op in result.parsed_ops] == [
        "M P 2 1 5",
        "M P 2 3",
        "M P 13 1",
        "M P 18 1 d",
        "M P 18 1 h",
        "M P 33 1",
        "L P 2 1 3a",
        "L P 18 1 i",
    ]


def test_parse_clause_exact_2014_622_clause_keeps_tail_after_letter_item_coordination() -> None:
    """2014/622 must keep the tail targets after ``18 §:n a ja h kohta``."""
    text = (
        "kumotaan rahoitus- ja vakuutusryhmittymien valvonnasta annetun lain (699/2004) "
        "22 §, sellaisena kuin se on laissa 1362/2010, sekä muutetaan 2 §:n 1 momentin 1 kohta, "
        "3 §:n 3 momentin 1 kohta, 4 §:n 4 momentti, 17 §:n 1 momentti, 18 §:n a ja h kohta, "
        "21 §, 28 §:n 2 momentti ja 31 §:n 1 momentin 2 kohta ja 2 momentti sekä 35 §:n 1 momentti,"
    )

    result = parse_clause(text)

    assert [op.code() for op in result.parsed_ops] == [
        "K P 22",
        "M P 2 1 1",
        "M P 3 3 1",
        "M P 4 4",
        "M P 17 1",
        "M P 18 1 a",
        "M P 18 1 h",
        "M P 21",
        "M P 28 2",
        "M P 31 1 2",
        "M P 31 2",
        "M P 35 1",
    ]


def test_parse_clause_clause_ast_source_text():
    """ClauseAST.source_text must equal the input text."""
    text = "muutetaan 3 luvun 5 §"
    result = parse_clause(text)
    assert result.clause_ast.source_text == text


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_parse_clause_empty_text():
    """Empty text must not raise; produces a well-formed empty result."""
    result = parse_clause("")
    assert isinstance(result, ClauseParseResult)
    assert isinstance(result.clause_ast, ClauseAST)
    assert result.clause_ast.verb_groups == ()
    assert result.parsed_ops == []


def test_parse_clause_no_verb_text():
    """Text with no amendment verb must not raise.

    Pure meta text (no structural verb) now produces MetaClause nodes in
    the ClauseAST — meta clauses flow through the same pipeline as
    structural clauses (Phase 7 integration).
    """
    from lawvm.core.clause_ast import MetaClause

    result = parse_clause("Tämä laki tulee voimaan 1 päivänä tammikuuta 2025.")
    assert isinstance(result, ClauseParseResult)
    # No verb → no structural ops
    assert result.parsed_ops == []
    # Meta clauses are present in the ClauseAST
    all_nodes = [n for vg in result.clause_ast.verb_groups for n in vg.nodes]
    meta_nodes = [n for n in all_nodes if isinstance(n, MetaClause)]
    assert len(meta_nodes) >= 1
    assert meta_nodes[0].kind == MetaClauseKind.COMMENCEMENT


# ---------------------------------------------------------------------------
# Curated cases: ClauseAST must be non-empty for every passing case
# ---------------------------------------------------------------------------


def _curated_ids():
    return [tc["name"] for tc in CURATED_CASES]


@pytest.mark.parametrize("tc", CURATED_CASES, ids=_curated_ids())
def test_parse_clause_curated(tc):
    """parse_clause() on every curated case must return a ClauseParseResult.

    For cases with non-empty expected ops, the ClauseAST must have at least
    one VerbGroup.

    Cases with xfail=True are expected to fail with a known grammar gap.
    Cases with expected=[] legitimately produce an empty ClauseAST (the
    johtolause text contains no amendment targets, only provenance spans).
    """
    if tc.get("xfail"):
        pytest.xfail("known failure — same grammar gap as test_peg_curated")

    text = tc["text"]
    expected = tc["expected"]
    result = parse_clause(text)

    assert isinstance(result.clause_ast, ClauseAST), f"parse_clause({text!r}) returned non-ClauseAST clause_ast"

    if expected:
        # Cases with expected ops: ClauseAST must be non-empty.
        assert result.clause_ast.verb_groups, (
            f"parse_clause({text!r}) produced empty ClauseAST.verb_groups; "
            f"parsed_ops={[op.code() for op in result.parsed_ops]}; "
            f"expected={expected}"
        )
    else:
        # Cases with expected=[] (e.g., pure provenance): empty ClauseAST is correct.
        assert result.parsed_ops == [], (
            f"parse_clause({text!r}) expected empty ops but got {[op.code() for op in result.parsed_ops]}"
        )


# ---------------------------------------------------------------------------
# SurfaceScopeBlock emission (Phase 10)
# ---------------------------------------------------------------------------


def test_surface_clause_chapter_scope_emits_scope_block():
    """Explicit 'N luvun' prefix produces SurfaceScopeBlock in surface_clause."""

    result = parse_clause("muutetaan 3 luvun 12 §")
    assert result.surface_clause is not None
    assert result.surface_clause.verb_groups is not None
    vg = result.surface_clause.verb_groups[0]
    assert len(vg.nodes) == 1
    node = vg.nodes[0]
    assert isinstance(node, SurfaceScopeBlock), f"Expected SurfaceScopeBlock, got {type(node).__name__}"
    assert node.scope_kind == ScopeKind.CHAPTER
    assert node.scope_label == "3"
    assert len(node.targets) == 1
    t0 = node.targets[0]
    assert isinstance(t0, SurfaceTargetRef)
    assert t0.label == "12"
    # Chapter must NOT be baked into the target — the scope block provides it
    assert t0.chapter == ""


def test_surface_clause_chapter_scope_multi_section_emits_scope_block():
    """Multiple sections in 'N luvun' scope wrapped in a single SurfaceScopeBlock."""

    result = parse_clause("muutetaan 3 luvun 5, 7 ja 9 §")
    assert result.surface_clause is not None
    assert result.surface_clause.verb_groups is not None
    vg = result.surface_clause.verb_groups[0]
    node = vg.nodes[0]
    assert isinstance(node, SurfaceScopeBlock)
    assert node.scope_kind == ScopeKind.CHAPTER
    assert node.scope_label == "3"
    assert len(node.targets) == 3
    targets = [t for t in node.targets if isinstance(t, SurfaceTargetRef)]
    labels = [t.label for t in targets]
    assert labels == ["5", "7", "9"]
    # Targets must not have chapter baked in
    for t in targets:
        assert t.chapter == ""


def test_surface_clause_chapter_scope_preserves_parsed_ops():
    """SurfaceScopeBlock lowering produces correct ParsedOps with chapter."""
    result = parse_clause("muutetaan 3 luvun 5, 7 ja 9 §")
    codes = [op.code() for op in result.parsed_ops]
    assert codes == ["M P L:3 5", "M P L:3 7", "M P L:3 9"]


def test_surface_clause_chapter_scope_with_sub_ref():
    """'N luvun M §:n K momentti' produces SurfaceScopeBlock with sub-ref target."""

    result = parse_clause("muutetaan 3 luvun 12 §:n 2 momentti")
    assert result.surface_clause is not None
    assert result.surface_clause.verb_groups is not None
    vg = result.surface_clause.verb_groups[0]
    node = vg.nodes[0]
    assert isinstance(node, SurfaceScopeBlock)
    assert node.scope_kind == ScopeKind.CHAPTER
    assert node.scope_label == "3"
    t = node.targets[0]
    assert isinstance(t, SurfaceTargetRef)
    assert t.label == "12"
    assert t.chapter == ""
    assert len(t.sub_refs) == 1
    assert t.sub_refs[0].momentti == 2
    # ParsedOp output must be unchanged
    assert result.parsed_ops[0].code() == "M P L:3 12 2"


def test_parse_clause_handles_spaced_pykala_genitive_before_subsection_ref():
    """Old Finlex spacing artifacts like '1 §: n 3 momentti' must keep GEN case."""

    result = parse_clause("muutetaan 1 §: n 3 momentti")

    assert [op.code() for op in result.parsed_ops] == ["M P 1 3"]


def test_surface_clause_no_explicit_chapter_no_scope_block():
    """Without an explicit chapter prefix, no SurfaceScopeBlock is emitted."""
    from lawvm.finland.johtolause.surface_model import SurfaceTargetRef

    result = parse_clause("muutetaan 5, 7 ja 9 §")
    assert result.surface_clause is not None
    assert result.surface_clause.verb_groups is not None
    vg = result.surface_clause.verb_groups[0]
    # All nodes should be plain SurfaceTargetRef, not SurfaceScopeBlock
    for node in vg.nodes:
        assert isinstance(node, SurfaceTargetRef), f"Expected SurfaceTargetRef, got {type(node).__name__}"


def test_surface_clause_chapter_scope_propagates_across_verb_groups():
    """Chapter from SurfaceScopeBlock propagates to subsequent verb groups."""
    result = parse_clause("muutetaan 3 luvun 12 § ja lisätään lukuun uusi 13 a §")
    codes = [op.code() for op in result.parsed_ops]
    # Chapter "3" must propagate from the muutetaan group to the lisätään group
    assert codes == ["M P L:3 12", "L P L:3 13a"]


# ---------------------------------------------------------------------------
# Canonical public-API import surface
# ---------------------------------------------------------------------------


def test_parse_clause_importable_from_api():
    """parse_clause and ClauseParseResult are the canonical public API."""
    from lawvm.finland.johtolause.api import ClauseParseResult as CPR, parse_clause as pc

    r = pc("muutetaan 5 §")
    assert isinstance(r, CPR)


# ---------------------------------------------------------------------------
# Gap 1: lukuun ottamatta (exception clause)
# ---------------------------------------------------------------------------


def _all_target_refs(vg):
    """Extract all SurfaceTargetRef from a verb group, including inside ScopeBlocks."""
    from lawvm.finland.johtolause.surface_model import SurfaceTargetRef

    refs = []
    for n in vg.nodes:
        if isinstance(n, SurfaceTargetRef):
            refs.append(n)
        elif isinstance(n, SurfaceScopeBlock):
            refs.extend(n.targets)
    return refs


def test_lukuun_ottamatta_exception_places_section_in_muuttaa_group():
    """'lukuun ottamatta' excepted section appears in the MUUTTAA verb group."""
    text = "muutetaan 4-7 luku, lukuun ottamatta kuitenkaan 7 luvun 73 §:ää, joka siirretään 7 luvun 61 §:ksi,"
    result = parse_clause(text)
    sc = result.surface_clause
    assert sc is not None

    muuttaa_vg = next(vg for vg in sc.verb_groups if vg.verb.name == "MUUTTAA")
    all_refs = _all_target_refs(muuttaa_vg)
    labels = [n.label for n in all_refs]
    assert "73" in labels, f"Expected section 73 in MUUTTAA group, got labels: {labels}"


def test_lukuun_ottamatta_without_kuitenkaan():
    """'lukuun ottamatta' without optional 'kuitenkaan' is also recognized."""
    text = "muutetaan 4-7 luku, lukuun ottamatta 7 luvun 73 §:ää"
    result = parse_clause(text)
    sc = result.surface_clause
    assert sc is not None

    muuttaa_vg = next(vg for vg in sc.verb_groups if vg.verb.name == "MUUTTAA")
    all_refs = _all_target_refs(muuttaa_vg)
    sec73 = [n for n in all_refs if n.label == "73"]
    assert len(sec73) == 1


def test_lukuun_ottamatta_relabel_recovers_source_and_dest():
    """Full lukuun-ottamatta + joka-siirretaan chain produces correct relabel."""
    text = (
        "kumotaan 12 päivänä heinäkuuta 1940 annetun perintö- ja lahjaverolain (378/40) 19 §:n 1 kohta, "
        "muutetaan 16 ja 21 a § sekä 4-7 luku, lukuun ottamatta kuitenkaan 7 luvun 73 §:ää, "
        "joka siirretään 7 luvun 61 §:ksi,"
    )
    # Verify via legal_ops that the relabel still works correctly
    from lawvm.finland.johtolause import extract_legal_ops as extract_johtolause_legal_ops

    legal_ops = extract_johtolause_legal_ops(text)
    relabel = next(lo for lo in legal_ops if lo.action is StructuralAction.RENUMBER)
    assert dict(relabel.target.path) == {"chapter": "7", "section": "73"}
    assert relabel.destination is not None
    assert dict(relabel.destination.path) == {"chapter": "7", "section": "61"}


# ---------------------------------------------------------------------------
# Gap 2: Spaced suffix labels ("39 a" -> section "39a")
# ---------------------------------------------------------------------------


def test_spaced_suffix_labels_in_insertion_context():
    """'39 a, 63 a, 63 b ja 63 c §' are parsed as section inserts, not subsection items."""
    from lawvm.finland.johtolause.surface_model import SurfaceInsertion

    text = "lisätään lakiin uusi 39 a, 63 a, 63 b ja 63 c § seuraavasti:"
    result = parse_clause(text)
    sc = result.surface_clause
    assert sc is not None

    lisata_vg = next(vg for vg in sc.verb_groups if vg.verb.name == "LISATA")
    insertions = [n for n in lisata_vg.nodes if isinstance(n, SurfaceInsertion)]
    labels = sorted(n.label for n in insertions)
    assert labels == ["39a", "63a", "63b", "63c"], f"Got labels: {labels}"

    # All must be section-level insertions
    for ins in insertions:
        assert ins.kind.name == "SECTION", f"{ins.label} should be SECTION, got {ins.kind.name}"


def test_insertion_momentin_kohta_genitive_subtarget():
    """'uusi N momentin M kohta' inserts kohta M into momentti N, not momentti N.

    The genitive 'momentin' is a container qualifier for a kohta insertion,
    mirroring the REPLACE shape 'N §:n M momentin K kohta'.  Previously parsed
    as a bare momentti insertion, dropping the trailing kohta.
    """
    text = "lisätään 102 §:ään uusi 1 momentin 4 kohta seuraavasti:"
    codes = [op.code() for op in parse_clause(text).parsed_ops]
    assert codes == ["L P 102 1 4"], codes


def test_insertion_alakohta_into_existing_item_uses_compound_item_label() -> None:
    """``K kohtaan uusi c alakohta`` inserts subitem c under existing item K."""

    text = "lisätään 1 §:n 1 momentin 1 kohtaan uusi c alakohta seuraavasti:"
    ops = parse_clause(text).parsed_ops

    assert [op.code() for op in ops] == ["L P 1 1 1 c"]
    assert ops[0].witness is not None
    assert ops[0].witness.rule_id == "fi.insertion_alakohta_into_item"


def test_replace_alakohta_under_existing_item_uses_compound_item_label() -> None:
    """``K kohdan c alakohta`` replaces subitem c under existing item K."""

    text = "muutetaan 1 §:n 2 kohdan h alakohta seuraavasti:"
    codes = [op.code() for op in parse_clause(text).parsed_ops]

    assert codes == ["M P 1 1 2 h"]


def test_2002_276_replace_alakohta_then_insert_sibling_alakohta_targets_are_distinct() -> None:
    """Regression for 2000/1106 <- 2002/276: replace 2h, then insert 2i."""

    text = (
        "muutetaan 15 päivänä joulukuuta 2000 vakuutusyritysryhmän mukautetusta "
        "vakavaraisuuslaskelmasta annetun sosiaali- ja terveysministeriön asetuksen "
        "(1106/2000) 1 §:n 2 kohdan h alakohta, 3 §:n 1 momentin 1 kohdan a "
        "alakohta sekä 2 kohdan a ja d alakohta sekä lisätään 1 §:n 2 kohtaan "
        "uusi i alakohta seuraavasti:"
    )
    codes = [op.code() for op in parse_clause(text, statute_id="2000/1106").parsed_ops]

    assert codes == [
        "M P 1 1 2 h",
        "M P 3 1 1 a",
        "M P 3 1 2 a",
        "M P 3 1 2 d",
        "L P 1 1 2 i",
    ]


def test_insert_coordinated_alakohta_under_existing_item_uses_compound_item_labels() -> None:
    """``K kohtaan uusi e ja f alakohta`` inserts both subitems under item K."""

    text = "lisätään 4 §:n 1 momentin 1 kohtaan uusi e ja f alakohta seuraavasti:"
    codes = [op.code() for op in parse_clause(text).parsed_ops]

    assert codes == ["L P 4 1 1 e", "L P 4 1 1 f"]


def test_2024_539_replace_and_insert_alakohta_lists_survive_cross_verb_clause() -> None:
    """Regression for 2022/1394 <- 2024/539: insert tail must not truncate replace targets."""

    text = (
        "muutetaan valtionavustustoiminnan tietovarantoon tallennettavista "
        "vähimmäistiedoista sekä valtionavustustietojen julkaisemisen ja käytön "
        "palvelussa julkaistavasta tietoaineistosta annetun valtioneuvoston "
        "asetuksen (1394/2022) 1 §:n 1 momentin 1 kohta, 2 §:n 1 momentin "
        "1 kohta, 4 kohdan a ja b alakohta ja 5 kohdan d ja e alakohta, "
        "3 §:n johdantokappale ja 4 ja 6 kohta, 4 §:n 1 momentin 1 kohdan "
        "a alakohta, 2 kohdan c, d, f ja g alakohta sekä 8 §:n 1 momentti, "
        "lisätään asetuksen 1 §:n 1 momenttiin uusi 10 kohta, 2 §:n 1 momentin "
        "3 kohtaan uusi d alakohta, 3 §:ään uusi 7 kohta, 4 §:n 1 momentin "
        "1 kohtaan uusi e ja f alakohta ja 2 kohtaan uusi h ja i alakohta "
        "seuraavasti:"
    )
    result = parse_clause(text, statute_id="2022/1394")
    codes = [op.code() for op in result.parsed_ops]

    assert result.parser_lane == "grammar_owned"
    assert codes == [
        "M P 1 1 1",
        "M P 2 1 1",
        "M P 2 1 4 a",
        "M P 2 1 4 b",
        "M P 2 1 5 d",
        "M P 2 1 5 e",
        "M P 3 j",
        "M P 3 1 4",
        "M P 3 1 6",
        "M P 4 1 1 a",
        "M P 4 1 2 c",
        "M P 4 1 2 d",
        "M P 4 1 2 f",
        "M P 4 1 2 g",
        "M P 8 1",
        "L P 1 1 10",
        "L P 2 1 3 d",
        "L P 3 1 7",
        "L P 4 1 1 e",
        "L P 4 1 1 f",
        "L P 4 1 2 h",
        "L P 4 1 2 i",
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "lisätään 4 §:n 2 kohtaan uusi h alakohta seuraavasti:",
            "L P 4 1 2 h",
        ),
        (
            "lisätään 4 §:n 2 kohtaan uusi h-alakohta seuraavasti:",
            "L P 4 1 2 h",
        ),
        (
            "lisätään 4 §:n 2 kohtaan uusi h -alakohta seuraavasti:",
            "L P 4 1 2 h",
        ),
        (
            "lisätään 4 §:n 2 kohtaan, sellaisena kuin se on osaksi laissa 650/2014, "
            "uusi i alakohta, seuraavasti:",
            "L P 4 1 2 i",
        ),
    ],
)
def test_insertion_alakohta_into_item_defaults_omitted_momentti_to_first(
    text: str, expected: str
) -> None:
    """``§:n K kohtaan uusi c alakohta`` keeps the alakohta target."""

    ops = parse_clause(text).parsed_ops

    assert [op.code() for op in ops] == [expected]
    assert ops[0].witness is not None
    assert ops[0].witness.rule_id == "fi.insertion_alakohta_into_item"


def test_2011_359_hyphenated_item_subitem_insert_continuations_stay_structural() -> None:
    """Hyphenated ``e-alakohta`` must not drop to section-reference fallback."""

    text = (
        "muutetaan ympäristövaikutusten arviointimenettelystä annetun "
        "valtioneuvoston asetuksen (713/2006) 6 §:n 3 kohdan a-alakohta sekä "
        "lisätään 6 §:n 7 kohtaan uusi e-alakohta ja 8 kohtaan uusi e, f ja "
        "g-alakohta seuraavasti:"
    )
    result = parse_clause(text, statute_id="2006/713")

    assert result.parser_lane == "grammar_owned"
    assert [op.code() for op in result.parsed_ops] == [
        "M P 6 1 3 a",
        "L P 6 1 7 e",
        "L P 6 1 8 e",
        "L P 6 1 8 f",
        "L P 6 1 8 g",
    ]
    assert all(op.witness is not None for op in result.parsed_ops)
    witness_rule_ids = [op.witness.rule_id for op in result.parsed_ops if op.witness is not None]
    assert witness_rule_ids == [
        "fi.section_ref",
        "fi.insertion_alakohta_into_item",
        "fi.insertion_alakohta_into_item",
        "fi.insertion_alakohta_into_item",
        "fi.insertion_alakohta_into_item",
    ]


def test_2011_359_spaced_dash_subitems_stay_structural() -> None:
    """Finlex spacing ``a- alakohta`` / ``e -alakohta`` is structural trivia."""

    text = (
        "muutetaan ympäristövaikutusten arviointimenettelystä annetun asetuksen "
        "(713/2006) 6 §:n 3 kohdan a- alakohta sekä lisätään 6 §:n 7 kohtaan "
        "uusi e -alakohta ja 8 kohtaan uusi e, f ja g -alakohta seuraavasti:"
    )
    result = parse_clause(text, statute_id="2006/713")

    assert result.parser_lane == "grammar_owned"
    assert [op.code() for op in result.parsed_ops] == [
        "M P 6 1 3 a",
        "L P 6 1 7 e",
        "L P 6 1 8 e",
        "L P 6 1 8 f",
        "L P 6 1 8 g",
    ]


def test_2014_692_insertion_list_keeps_anaphoric_momentti_and_alakohta_targets() -> None:
    """The full 2014/692 preamble must not fall back to the lossy legacy parser."""

    text = (
        "muutetaan Energiavirastosta annetun lain (870/2013) 1 §:n 2 momentin 15 kohta "
        "ja 3 momentin 6 kohta, sellaisena kuin niistä on 1 §:n 3 momentin 6 kohta "
        "laissa 650/2014 sekä lisätään 1 §:n 2 momenttiin uusi 16 kohta ja "
        "3 momenttiin, sellaisena kuin se on osaksi laissa 650/2014, uusi 7 kohta "
        "sekä 4 §:n 2 kohtaan, sellaisena kuin se on osaksi laissa 650/2014, "
        "uusi i alakohta, seuraavasti:"
    )
    result = parse_clause(text)

    assert result.parser_lane == "grammar_owned"
    assert [op.code() for op in result.parsed_ops] == [
        "M P 1 2 15",
        "M P 1 3 6",
        "L P 1 2 16",
        "L P 1 3 7",
        "L P 4 1 2 i",
    ]


def test_insertion_anaphoric_momentti_continuation_without_uusi():
    """'uusi N momentin M kohta ja P momentti' shares 'uusi' across the conjunction.

    The bare 'ja P momentti' continuation inserts momentti P into the same
    section without repeating 'uusi'.
    """
    text = "lisätään 102 §:ään uusi 1 momentin 4 kohta ja 4 momentti seuraavasti:"
    codes = [op.code() for op in parse_clause(text).parsed_ops]
    assert codes == ["L P 102 1 4", "L P 102 4"], codes


def test_insertion_trailing_lakiin_uusi_section_after_subtarget_list():
    """A trailing 'ja lakiin uusi N §' coordinated after sub-target inserts is parsed.

    Real shape (arvonlisäverolaki amendment 1994/1483): the law-level section
    insert 'lakiin uusi 102 b §' used to be dropped because the preceding
    'N §:ään uusi 1 momentin 4 kohta ja 4 momentti' arm truncated the list.
    """
    text = (
        "lisätään 1 §:ään uusi 5 momentti, 102 §:ään uusi 1 momentin 4 kohta "
        "ja 4 momentti ja lakiin uusi 102 b § sekä 141 §:ään uusi 5 kohta seuraavasti:"
    )
    codes = [op.code() for op in parse_clause(text).parsed_ops]
    assert codes == [
        "L P 1 5",
        "L P 102 1 4",
        "L P 102 4",
        "L P 102b",
        "L P 141 1 5",
    ], codes


# ---------------------------------------------------------------------------
# Gap 3: Anaphoric lookup must handle SurfaceScopeBlock and
#         SurfaceDescendantCoordination as predecessors (Pro audit #3)
# ---------------------------------------------------------------------------


def test_anaphoric_pykala_ill_after_insertion_predecessor():
    """Anaphoric 'pykälään uusi N momentti' finds section from preceding SurfaceInsertion."""
    text = "lisätään 49 §:n 1 momenttiin uusi 7 kohta ja pykälään uusi 2 momentti"
    result = parse_clause(text)
    codes = [op.code() for op in result.parsed_ops]
    assert "L P 49 1 7" in codes
    assert "L P 49 2" in codes, f"Anaphoric 'pykälään' should resolve to section 49, got: {codes}"


def test_cross_verb_anaphoric_after_scope_block():
    """Cross-verb anaphoric 'momenttiin uusi N kohta' resolves section from SurfaceScopeBlock."""

    text = "muutetaan 3 luvun 5 ja 7 § sekä lisätään momenttiin uusi 4 kohta"
    result = parse_clause(text)
    sc = result.surface_clause
    assert sc is not None

    # First verb group must have a SurfaceScopeBlock
    muuttaa_vg = next(vg for vg in sc.verb_groups if vg.verb.name == "MUUTTAA")
    assert any(isinstance(n, SurfaceScopeBlock) for n in muuttaa_vg.nodes), (
        "MUUTTAA group should contain a SurfaceScopeBlock"
    )

    codes = [op.code() for op in result.parsed_ops]
    # Anaphoric resolution should pick section 7 (last in scope block) with chapter 3
    assert "L P L:3 7 1 4" in codes, (
        f"Cross-verb anaphoric should resolve section 7 from SurfaceScopeBlock, got: {codes}"
    )


def test_cross_verb_anaphoric_after_descendant_coordination():
    """Cross-verb anaphoric 'momenttiin uusi N kohta' resolves section from SurfaceDescendantCoordination."""

    text = "muutetaan 5 §:n 2 ja 3 momentti sekä lisätään momenttiin uusi 4 kohta"
    result = parse_clause(text)
    sc = result.surface_clause
    assert sc is not None

    # First verb group must have a SurfaceDescendantCoordination
    muuttaa_vg = next(vg for vg in sc.verb_groups if vg.verb.name == "MUUTTAA")
    assert any(isinstance(n, SurfaceDescendantCoordination) for n in muuttaa_vg.nodes), (
        "MUUTTAA group should contain a SurfaceDescendantCoordination"
    )

    codes = [op.code() for op in result.parsed_ops]
    # Anaphoric resolution should pick section 5, momentti 2 from DescendantCoordination
    assert "L P 5 2 4" in codes, (
        f"Cross-verb anaphoric should resolve section 5 from SurfaceDescendantCoordination, got: {codes}"
    )


def test_chapter_propagation_from_scope_block_to_next_verb_group():
    """Chapter context from SurfaceScopeBlock propagates to the next verb group."""
    text = "muutetaan 3 luvun 12 § ja lisätään lukuun uusi 13 a §"
    result = parse_clause(text)
    codes = [op.code() for op in result.parsed_ops]
    # Chapter 3 must propagate from SurfaceScopeBlock to LISATA group
    assert codes == ["M P L:3 12", "L P L:3 13a"], f"Chapter should propagate from SurfaceScopeBlock, got: {codes}"


def test_section_context_extraction_from_scope_block():
    """_extract_section_context_from_nodes correctly reads last section from SurfaceScopeBlock."""

    text = "muutetaan 3 luvun 5 ja 7 § sekä lisätään lukuun uusi 8 §"
    result = parse_clause(text)
    sc = result.surface_clause
    assert sc is not None

    # Verify the SurfaceScopeBlock is present
    muuttaa_vg = next(vg for vg in sc.verb_groups if vg.verb.name == "MUUTTAA")
    scope_block = next(n for n in muuttaa_vg.nodes if isinstance(n, SurfaceScopeBlock))
    assert scope_block.scope_label == "3"
    targets = [target for target in scope_block.targets if isinstance(target, SurfaceTargetRef)]
    assert len(targets) == len(scope_block.targets)
    assert [target.label for target in targets] == ["5", "7"]

    # The LISATA group should have inherited chapter 3
    codes = [op.code() for op in result.parsed_ops]
    assert "L P L:3 8" in codes, f"Chapter 3 should propagate to LISATA group, got: {codes}"


def test_anaphoric_luvun_continuation_keeps_chapter_across_insert_chain():
    """A bare `luvun` continuation must keep the inherited chapter inside one insert chain."""
    text = (
        "lisätään 7 lukuun uusi 7 b § ja luvun 17 §:ään uusi 2 momentti, "
        "8 luvun 1 §:ään uusi 5 momentti ja lukuun uusi 1 a, 5 a ja 10 §"
    )
    result = parse_clause(text)
    codes = [op.code() for op in result.parsed_ops]
    assert codes == [
        "L P L:7 7b",
        "L P L:7 17 2",
        "L P L:8 1 5",
        "L P L:8 1a",
        "L P L:8 5a",
        "L P L:8 10",
    ], f"Anaphoric `luvun` continuation should keep chapter scope, got: {codes}"


def test_anaphoric_luvun_descendant_continuation_does_not_become_chapter_ref():
    """Bare genitive ``luvun`` before ``9 §:ään uusi ...`` must stay a section arm.

    Regression witness: 2004/1224 <- 2016/1100.  Without the guard, the parser
    misread ``luvun 9 §:ään uusi 6 momentti`` as a reversed chapter target
    ``L L 9`` and truncated the rest of the insert chain.
    """
    text = (
        "lisätään 5 lukuun uusi 7 a §, luvun 9 §:ään uusi 6 momentti sekä "
        "6 luvun 4 §:ään uusi 3 momentti, lukuun väliaikaisesti uusi 6 a §, "
        "7 §:ään uusi 3 momentti, lukuun uusi 7 b ja 16 a §, 18 §:ään uusi "
        "3 momentti, lukuun uusi 18 a ja 22 b §, 23 §:ään uusi 5 momentti "
        "sekä 24 §:ään uusi 3 momentti"
    )
    result = parse_clause(text)
    codes = [op.code() for op in result.parsed_ops]
    assert codes == [
        "L P L:5 7a",
        "L P L:5 9 6",
        "L P L:6 4 3",
        "L P L:6 6a",
        "L P L:6 7 3",
        "L P L:6 7b",
        "L P L:6 16a",
        "L P L:6 18 3",
        "L P L:6 18a",
        "L P L:6 22b",
        "L P L:6 23 5",
        "L P L:6 24 3",
    ], f"Bare `luvun` descendant continuation should not collapse to chapter ref, got: {codes}"


def test_second_verb_group_explicit_chapter_insert_survives_prior_chapter_context():
    """A new verb group must not inherit chapter context over explicit chapter starts.

    Regression witness: 2004/1224 <- 2016/1100.  After the preceding MUUTTAA
    group leaves chapter context at ``6``, the following LISATA group starts
    with explicit ``5 lukuun uusi 7 a §`` and then crosses back into chapter 6.
    Without the fix, the second verb group failed entirely.
    """
    text = (
        "muutetaan 6 luvun 23 §:n 2 momentti, lisätään 5 lukuun uusi 7 a §, "
        "luvun 9 §:ään uusi 6 momentti sekä 6 luvun 4 §:ään uusi 3 momentti, "
        "lukuun väliaikaisesti uusi 6 a §, 7 §:ään uusi 3 momentti, lukuun "
        "uusi 7 b ja 16 a §, 18 §:ään uusi 3 momentti, lukuun uusi 18 a ja "
        "22 b §, 23 §:ään uusi 5 momentti sekä 24 §:ään uusi 3 momentti"
    )
    result = parse_clause(text)
    codes = [op.code() for op in result.parsed_ops]
    assert codes == [
        "M P L:6 23 2",
        "L P L:5 7a",
        "L P L:5 9 6",
        "L P L:6 4 3",
        "L P L:6 6a",
        "L P L:6 7 3",
        "L P L:6 7b",
        "L P L:6 16a",
        "L P L:6 18 3",
        "L P L:6 18a",
        "L P L:6 22b",
        "L P L:6 23 5",
        "L P L:6 24 3",
    ], f"Explicit chapter start in second verb group should survive prior chapter context, got: {codes}"


def test_parse_clause_citation_span_inside_insert_chain_does_not_trigger_nojalla_skip():
    """Citation spans inside real insert targets must not trip the authority skip.

    Regression witness: 2004/1224 <- 2016/1100. The exact source clause carries
    repeated ``sellaisena kuin se on laissa ...`` provenance spans inside the
    LISATA arm. `_target()` used to treat the first later CITATION_SPAN as if an
    authority-by-``nojalla`` lead-in had been seen, which dropped the entire
    second verb group.
    """
    text = (
        "muutetaan sairausvakuutuslain (1224/2004) 5 luvun 1 §:n 2 momentti, "
        "5 §:n 3 momentti, 6 §, 7 §:n 2 momentti, 9 §:n 1, 2 ja 5 momentti "
        "sekä 9 a § sekä 6 luvun 13 §, 16 §:n 1 momentti, 18 §:n 1 momentti, "
        "19 §:n 2 momentti, 20 §:n 1 momentin 2 kohta, 22 a § ja 23 §:n 2 momentti, "
        "lisätään 5 lukuun uusi 7 a §, luvun 9 §:ään, sellaisena kuin se on laeissa "
        "802/2008, 974/2013 ja 252/2015, uusi 6 momentti sekä 6 luvun 4 §:ään, "
        "sellaisena kuin se on laeissa 802/2008 ja 252/2015, uusi 3 momentti, "
        "jolloin nykyinen 3 ja 4 momentti siirtyvät 4 ja 5 momentiksi, lukuun "
        "väliaikaisesti uusi 6 a §, 7 §:ään, sellaisena kuin se on laissa 802/2008, "
        "uusi 3 momentti, lukuun uusi 7 b ja 16 a §, 18 §:ään, sellaisena kuin "
        "se on laissa 802/2008, uusi 3 momentti, lukuun uusi 18 a ja 22 b §, "
        "23 §:ään, sellaisena kuin se on laeissa 802/2008 ja 252/2015, uusi "
        "5 momentti sekä 24 §:ään, sellaisena kuin se on laissa 802/2008, uusi "
        "3 momentti seuraavasti:"
    )

    result = parse_clause(text)
    codes = [op.code() for op in result.parsed_ops]
    assert codes == [
        "S P L:6 4 3",
        "S P L:6 4 4",
        "M P L:5 1 2",
        "M P L:5 5 3",
        "M P L:5 6",
        "M P L:5 7 2",
        "M P L:5 9 1",
        "M P L:5 9 2",
        "M P L:5 9 5",
        "M P L:5 9a",
        "M P L:6 13",
        "M P L:6 16 1",
        "M P L:6 18 1",
        "M P L:6 19 2",
        "M P L:6 20 1 2",
        "M P L:6 22a",
        "M P L:6 23 2",
        "L P L:5 7a",
        "L P L:5 9 6",
        "L P L:6 4 3",
        "L P L:6 6a",
        "L P L:6 7 3",
        "L P L:6 7b",
        "L P L:6 16a",
        "L P L:6 18 3",
        "L P L:6 18a",
        "L P L:6 22b",
        "L P L:6 23 5",
        "L P L:6 24 3",
    ], f"Real provenance-heavy insert chain should survive authority-skip guard, got: {codes}"


def test_parse_clause_accepts_reversed_chapter_reference() -> None:
    result = parse_clause("kumotaan luku 6a ja 18a§")
    codes = [op.code() for op in result.parsed_ops]

    assert codes == ["K L 6a", "K P 18a"]
    sc = result.surface_clause
    assert sc is not None
    chapter_nodes = [
        node
        for vg in sc.verb_groups
        for node in vg.nodes
        if isinstance(node, SurfaceTargetRef) and node.kind == TargetKind.CHAPTER
    ]
    assert chapter_nodes
    assert chapter_nodes[0].label == "6a"
    assert chapter_nodes[0].witness is not None
    assert chapter_nodes[0].witness.rule_id == "fi.chapter_ref_reversed"


def test_parse_clause_preserves_target_version_bindings_sidecar() -> None:
    text = (
        "muutetaan 23 §, 24 c §:n 3 momenttia, 30 b §:n 2 momenttia ja "
        "34 a §:n 2 momenttia, sellaisina kuin ne ovat, 23 § laissa 195/2015 "
        "sekä 24 c, 30 b ja 34 a § laissa 575/2018, seuraavasti:"
    )

    result = parse_clause(text)

    assert [op.code() for op in result.parsed_ops] == [
        "M P 23",
        "M P 24c 3",
        "M P 30b 2",
        "M P 34a 2",
    ]
    assert [
        (binding.target_labels, binding.cited_statute_id)
        for binding in result.target_version_bindings
    ] == [
        (("23",), "2015/195"),
        (("24c", "30b", "34a"), "2018/575"),
    ]
    assert result.surface_clause is not None
    assert result.surface_clause.target_version_bindings == result.target_version_bindings
    assert result.resolved is not None
    assert result.resolved.target_version_bindings == result.target_version_bindings


def test_parse_clause_preserves_target_version_bindings_for_2000_755_2018_945() -> None:
    text = (
        "muutetaan aluevalvontalain (755/2000) 23, 24 c, 30 b ja 34 a §, "
        "sellaisina kuin ne ovat, 23 § laissa 195/2015 sekä 24 c, 30 b ja "
        "34 a § laissa 575/2018, seuraavasti:"
    )

    result = parse_clause(text)

    assert [op.code() for op in result.parsed_ops] == [
        "M P 23",
        "M P 24c",
        "M P 30b",
        "M P 34a",
    ]
    assert [
        (binding.target_labels, binding.cited_statute_id)
        for binding in result.target_version_bindings
    ] == [
        (("23",), "2015/195"),
        (("24c", "30b", "34a"), "2018/575"),
    ]


def test_parse_clause_preserves_anaphoric_asetus_target_version_binding_range() -> None:
    text = (
        "muutetaan varotoimenpiteistä lintuinfluenssan leviämisen ehkäisemiseksi "
        "luonnonvaraisten lintujen ja siipikarjan välillä annetun maa- ja "
        "metsätalousministeriön asetuksen (386/2006) 4 a-4 c §, sellaisena "
        "kuin ne ovat asetuksessa 81/2011, seuraavasti:"
    )

    result = parse_clause(text)

    assert [op.code() for op in result.parsed_ops] == ["M P 4a", "M P 4b", "M P 4c"]
    assert [
        (binding.target_labels, binding.cited_statute_id)
        for binding in result.target_version_bindings
    ] == [(("4a", "4b", "4c"), "2011/81")]


def test_parse_clause_preserves_parenthesized_asetus_target_version_bindings_for_2009_1815() -> None:
    text = (
        "muutetaan 22 päivänä joulukuuta 1993 annetun jäteasetuksen "
        "(1390/1993) 3 a §:n 2 momentin johdantokappale ja 3 momentti, "
        "sellaisina kuin ne ovat 20 päivänä kesäkuuta 1996 annetussa "
        "asetuksessa (472/1996), 12 § sellaisena kuin se on 24 päivänä "
        "tammikuuta 1995 annetussa asetuksessa (64/1995), 14 § ja 14 b §, "
        "sellaisina kuin ne ovat 18 päivänä helmikuuta 2000 annetussa "
        "asetuksessa (171/2000), 17 §:n 2 momentin johdantokappale, "
        "sellaisena kuin se on 24 päivänä tammikuuta 1995 annetussa "
        "asetuksessa (64/1995) sekä 21 §:n 1 momentin 2 kohta sellaisena "
        "kuin se on 18 päivänä helmikuuta 2000 annetussa asetuksessa "
        "(171/2000), seuraavasti:"
    )

    result = parse_clause(text)

    assert [op.code() for op in result.parsed_ops] == [
        "M P 3a 2 j",
        "M P 3a 3",
        "M P 12",
        "M P 14",
        "M P 14b",
        "M P 17 2 j",
        "M P 21 1 2",
    ]
    assert [
        (binding.target_labels, binding.cited_statute_id)
        for binding in result.target_version_bindings
    ] == [
        (("3a",), "1996/472"),
        (("12",), "1995/64"),
        (("14", "14b"), "2000/171"),
        (("17",), "1995/64"),
        (("21",), "2000/171"),
    ]


# ---------------------------------------------------------------------------
# Anaphoric provenance must not over-consume the resuming target list
# ---------------------------------------------------------------------------
#
# An anaphoric provenance ("sellaisena kuin se on edellä mainitussa ...
# annetussa asetuksessa") refers to a statute named earlier in the clause and
# therefore carries no closing "(NNN/YY)" citation.  The provenance-span skip
# (_skip_prov_span) relies on a closing citation to know the appositive ended;
# without one it used to keep its internal-verb guard active and swallow the
# real targets that followed the appositive until the next unrelated citation.
# These cases pin the boundary: the targets after the anaphoric appositive
# survive, while the two surviving-verb-enumeration shapes (which genuinely list
# provenance section-refs that must stay swallowed) are NOT disturbed.


def test_anaphoric_provenance_no_closing_citation_preserves_following_targets() -> None:
    """"33 §, sellaisena kuin se on edellä mainitussa ... asetuksessa, 34 §, 36 §"

    The appositive names an earlier statute (no closing citation).  The "34 §"
    and "36 §" after it are real targets resuming the list, not provenance.
    """
    text = (
        "muutetaan 33 §, sellaisena kuin se on edellä mainitussa "
        "9 päivänä maaliskuuta 1979 annetussa asetuksessa, "
        "34 §:n 2 momentti, 36 §, seuraavasti:"
    )
    nums = [op.number for op in parse_clause(text).parsed_ops]
    assert nums == ["33", "34", "36"], (
        "anaphoric provenance (no closing citation) must terminate at the "
        f"resuming '34 §' target, not swallow it; got {nums}"
    )


def test_anaphoric_provenance_longer_list_survives() -> None:
    """The whole post-appositive list resumes, not just the first item."""
    text = (
        "muutetaan 24 §:n 1 momentin 5 ja 6 kohta, sellaisina kuin mainitut "
        "kohdat ovat 20 päivänä elokuuta 1993 annetussa asetuksessa, "
        "26 §:n 1 momentin 2 ja 3 kohta, 27 §:n 3 momentti, 28 §, 32 §, "
        "33 § ja 36 §:n 2 momentti, seuraavasti:"
    )
    nums = [op.number for op in parse_clause(text).parsed_ops]
    # "24 §:n 1 momentin 5 ja 6 kohta" / "26 §:n 1 momentin 2 ja 3 kohta" each
    # enumerate two kohta sub-refs, so "24" and "26" each appear twice; the
    # load-bearing assertion is that every section AFTER the appositive
    # (26..36) survives instead of being swallowed.
    assert nums == ["24", "24", "26", "26", "27", "28", "32", "33", "36"], (
        "every target after the anaphoric appositive must resume the list; "
        f"got {nums}"
    )


def test_closing_citation_provenance_still_separates_targets() -> None:
    """Control: a provenance WITH a closing citation keeps the targets too.

    This is the shape the anaphoric fix must leave unchanged — the appositive
    collapses into a CITATION_SPAN and the surrounding commas already separate
    "33 §" from "34 §" / "36 §".
    """
    text = (
        "muutetaan 33 §, sellaisena kuin se on 9 päivänä maaliskuuta 1979 "
        "annetussa asetuksessa (269/79), 34 §:n 2 momentti, 36 §, seuraavasti:"
    )
    nums = [op.number for op in parse_clause(text).parsed_ops]
    assert nums == ["33", "34", "36"], nums


def test_surviving_verb_per_arm_citation_enumeration_stays_swallowed() -> None:
    """"sellaisina kuin ne ovat, 16 §:n ... laissa X ja 34 §:n ... laissa Y"

    Here the section-refs inside the appositive are per-arm version bindings
    (each "NUM § laissa <cite>"), NOT resuming targets.  There is no date phrase
    in the appositive, so the anaphoric exit must NOT fire — only the two real
    targets ("16 §", "34 §") before the appositive are emitted; the appositive's
    "16 §" / "34 §" stay provenance.
    """
    text = (
        "muutetaan 16 §:n 2 momentti ja 34 §:n 1 momentin 5 kohta, "
        "sellaisina kuin ne ovat, 16 §:n 2 momentti laissa 385/2007 ja "
        "34 §:n 1 momentin 5 kohta laissa 495/2005, seuraavasti:"
    )
    nums = [op.number for op in parse_clause(text).parsed_ops]
    assert nums == ["16", "34"], (
        "surviving-verb per-arm-citation provenance enumeration must stay "
        f"swallowed; got {nums}"
    )


def test_surviving_verb_section_enumeration_trailing_citation_stays_swallowed() -> None:
    """"sellaisina kuin niistä ovat 4 §:n 6 kohta, 21 ja 26 § asetuksessa X"

    The appositive enumerates its own section-refs closed by a single trailing
    citation.  Those "21" / "26 §" are provenance, not resuming targets — with no
    date phrase in the appositive the anaphoric exit must NOT fire.
    """
    text = (
        "muutetaan 4 §, 21 ja 26 §, sellaisina kuin niistä ovat 4 §:n 6 kohta, "
        "21 ja 26 § asetuksessa 1040/2008, seuraavasti:"
    )
    nums = [op.number for op in parse_clause(text).parsed_ops]
    assert nums == ["4", "21", "26"], (
        "surviving-verb section enumeration with a trailing citation must stay "
        f"swallowed; got {nums}"
    )


def test_surviving_verb_bare_number_coordination_stays_swallowed() -> None:
    """"sellaisina kuin niistä ovat, 4, 11, 12 ja 16 §, 18 §:n ... laissa X"

    The appositive opens with a bare-number coordination ("4, 11, 12 ja 16 §")
    that shares one trailing "§", then continues with further provenance items
    closed by a citation.  None of these are resuming targets.  The comma between
    the bare numbers must NOT be mistaken for a list separator: with no date
    phrase in the appositive the anaphoric exit must NOT fire (regression guard
    for the "niistä ovat, <bare numbers> §" shape).
    """
    text = (
        "muutetaan 339, 344 ja 345 §, sellaisina kuin niistä ovat, "
        "4, 11, 12 ja 16 §, 18 §:n 5 momentti laissa 1003/2018, seuraavasti:"
    )
    nums = [op.number for op in parse_clause(text).parsed_ops]
    assert nums == ["339", "344", "345"], (
        "surviving-verb bare-number coordination must stay swallowed; "
        f"got {nums}"
    )


# ---------------------------------------------------------------------------
# Pro audit #10: Explicit scope modeling must be representation-independent
# ---------------------------------------------------------------------------


def test_part_and_chapter_both_explicit_emits_scope_block():
    """'II osan 1 luvun 3 §' (both part and chapter explicit) must emit a
    SurfaceScopeBlock — same as a chapter-only or part-only reference.

    Previously _section_ref cleared both scope_ch and scope_pt when both were
    explicit, so no scope block was emitted.
    """

    result = parse_clause("muutetaan II osan 1 luvun 3 §")
    assert result.surface_clause is not None
    assert result.surface_clause.verb_groups is not None
    vg = result.surface_clause.verb_groups[0]
    assert len(vg.nodes) == 1
    node = vg.nodes[0]
    assert isinstance(node, SurfaceScopeBlock), (
        f"Expected SurfaceScopeBlock, got {type(node).__name__}: both part and chapter "
        "should produce a scope block, not a bare SurfaceTargetRef"
    )
    # Outer scope is part (the higher-level container)
    assert node.scope_kind == ScopeKind.PART
    assert node.scope_label == "II"
    # Chapter context preserved on the individual target
    assert len(node.targets) == 1
    t = node.targets[0]
    assert isinstance(t, SurfaceTargetRef)
    assert t.label == "3"
    assert t.chapter == "1", "Chapter must be preserved on the target when part is the outer scope block"
    # Part must NOT be baked into the target — scope block provides it
    assert t.part == ""


def test_part_and_chapter_both_explicit_parsed_ops():
    """ParsedOps for 'II osan 1 luvun 3 §' must include both part and chapter."""
    result = parse_clause("muutetaan II osan 1 luvun 3 §")
    codes = [op.code() for op in result.parsed_ops]
    # Should produce an op that includes both part and chapter
    assert len(codes) == 1
    # The op must encode chapter 1 and section 3; part context flows through ScopedBlock
    assert "3" in codes[0], f"Section 3 must appear in op, got: {codes}"
    assert "1" in codes[0], f"Chapter 1 must appear in op, got: {codes}"


def test_explicit_scope_with_descendant_coordination_emits_scope_block():
    """'3 luvun 5 §:n 1 ja 2 momentti' (explicit chapter + >=2 sub-refs) must
    emit a SurfaceScopeBlock, not suppress it.

    Previously _section_ref cleared scope when >=2 sub-refs, so no scope block
    was emitted even though the chapter was explicit.  This made the
    representation dependent on the number of sub-refs.
    """

    result = parse_clause("muutetaan 3 luvun 5 §:n 1 ja 2 momentti")
    assert result.surface_clause is not None
    vg = result.surface_clause.verb_groups[0]
    node = vg.nodes[0]
    assert isinstance(node, SurfaceScopeBlock), (
        f"Expected SurfaceScopeBlock, got {type(node).__name__}: explicit chapter should "
        "produce a scope block even when >=2 sub-refs are present"
    )
    assert node.scope_kind == ScopeKind.CHAPTER
    assert node.scope_label == "3"
    # Target must have the sub-refs and no chapter (scope block provides it)
    assert len(node.targets) == 1
    t = node.targets[0]
    assert isinstance(t, SurfaceTargetRef)
    assert t.label == "5"
    assert t.chapter == ""
    assert len(t.sub_refs) == 2


def test_explicit_scope_with_descendant_coordination_parsed_ops():
    """ParsedOps for 'N luvun M §:n 1 ja 2 momentti' identical whether 1 or 2 sub-refs."""
    result1 = parse_clause("muutetaan 3 luvun 5 §:n 1 momentti")
    result2 = parse_clause("muutetaan 3 luvun 5 §:n 1 ja 2 momentti")
    codes1 = [op.code() for op in result1.parsed_ops]
    codes2 = [op.code() for op in result2.parsed_ops]
    # Single sub-ref: one op with chapter and momentti
    assert codes1 == ["M P L:3 5 1"], f"Single sub-ref: {codes1}"
    # Two sub-refs: two ops, both with chapter
    assert codes2 == ["M P L:3 5 1", "M P L:3 5 2"], f"Two sub-refs: {codes2}"


def test_extract_legal_ops_preserves_explicit_chapter_scope_confidence() -> None:
    ops = extract_legal_ops("muutetaan 3 luvun 5 §:n 1 ja 2 momentti")

    assert len(ops) == 2
    witnesses = [lo_scope_confidence(op) for op in ops]
    assert all(witness is not None for witness in witnesses)
    assert [witness.source for witness in witnesses if witness is not None] == ["explicit_chunk", "explicit_chunk"]
    assert [witness.confidence for witness in witnesses if witness is not None] == ["explicit", "explicit"]
    assert [witness.resolved_chapter for witness in witnesses if witness is not None] == ["3", "3"]


def test_extract_legal_ops_preserves_explicit_part_and_chapter_scope_confidence() -> None:
    ops = extract_legal_ops("muutetaan II osan 1 luvun 3 §")

    assert len(ops) == 1
    witness = lo_scope_confidence(ops[0])
    assert witness is not None
    assert witness.source == "explicit_chunk"
    assert witness.confidence == "explicit"
    assert witness.resolved_chapter == "1"


def test_no_explicit_scope_two_sub_refs_still_descendant_coordination():
    """Without explicit scope, >=2 sub-refs still emit SurfaceDescendantCoordination
    (not SurfaceTargetRef with sub_refs).  This path is unchanged by the fix.
    """

    result = parse_clause("muutetaan 5 §:n 1 ja 2 momentti")
    assert result.surface_clause is not None
    vg = result.surface_clause.verb_groups[0]
    assert len(vg.nodes) == 1
    node = vg.nodes[0]
    assert isinstance(node, SurfaceDescendantCoordination), (
        f"Without explicit scope, >=2 sub-refs should still emit "
        f"SurfaceDescendantCoordination, got {type(node).__name__}"
    )


# ---------------------------------------------------------------------------
# Jolloin renumber — native surface parser emission (e-#1/#2 Pro audit fix)
# ---------------------------------------------------------------------------


def test_jolloin_chapter_renumber_emits_native_siirtaa_vg():
    """Jolloin chapter renumber emits SIIRTAA verb group natively from the parser.

    'lisätään uusi 4 luku, jolloin nykyinen 4 luku siirtyy 5 luvuksi'
    → surface_clause.verb_groups[0].verb == SIIRTAA  (from native parse)
    → surface_clause.verb_groups[0].nodes has SurfaceTargetRef("4") + SurfaceRenumberTail("5")
    """
    from lawvm.finland.johtolause.surface_model import (
        SurfaceRenumberTail,
        SurfaceTargetRef,
        TargetKind,
        VerbKind,
    )

    text = "lisätään uusi 4 luku, jolloin nykyinen 4 luku siirtyy 5 luvuksi"
    result = parse_clause(text)
    sc = result.surface_clause
    assert sc is not None

    # First verb group must be SIIRTAA (prepended natively by parser)
    assert sc.verb_groups, "Expected at least one verb group"
    first_vg = sc.verb_groups[0]
    assert first_vg.verb == VerbKind.SIIRTAA, (
        f"Expected first VerbGroup to be SIIRTAA (jolloin renumber), got {first_vg.verb!r}"
    )

    # Must contain target + renumber tail pair
    nodes = first_vg.nodes
    assert len(nodes) == 2, f"Expected 2 nodes (target + tail), got {len(nodes)}: {nodes}"
    target, tail = nodes
    assert isinstance(target, SurfaceTargetRef), f"Expected SurfaceTargetRef, got {type(target).__name__}"
    assert target.kind == TargetKind.CHAPTER
    assert target.label == "4"
    assert isinstance(tail, SurfaceRenumberTail), f"Expected SurfaceRenumberTail, got {type(tail).__name__}"
    assert tail.new_label == "5"


def test_jolloin_section_renumber_emits_native_siirtaa_vg():
    """Jolloin section renumber emits SIIRTAA verb group natively from the parser.

    'lisätään lakiin uusi 5 §, jolloin nykyinen 5 § siirtyy 6 §:ksi'
    → surface_clause.verb_groups[0].verb == SIIRTAA
    → target is SECTION kind with label "5", tail has new_label "6"
    """
    from lawvm.finland.johtolause.surface_model import (
        SurfaceRenumberTail,
        SurfaceTargetRef,
        TargetKind,
        VerbKind,
    )

    text = "lisätään lakiin uusi 5 §, jolloin nykyinen 5 § siirtyy 6 §:ksi"
    result = parse_clause(text)
    sc = result.surface_clause
    assert sc is not None

    first_vg = sc.verb_groups[0]
    assert first_vg.verb == VerbKind.SIIRTAA, (
        f"Expected first VerbGroup to be SIIRTAA, got {first_vg.verb!r}"
    )
    nodes = first_vg.nodes
    assert len(nodes) == 2
    target, tail = nodes
    assert isinstance(target, SurfaceTargetRef)
    assert target.kind == TargetKind.SECTION
    assert target.label == "5"
    assert isinstance(tail, SurfaceRenumberTail)
    assert tail.new_label == "6"


def test_siirtaa_current_section_renumber_tail_keeps_explicit_pairs() -> None:
    text = (
        "siirretään 8 luvun otsikko uuden 67 §:n edelle sekä nykyinen 63 § "
        "uudeksi 69 §:ksi ja nykyinen 64 § uudeksi 70 §:ksi"
    )
    result = parse_clause(text)
    sc = result.surface_clause
    assert sc is not None

    assert sc.verb_groups[0].verb == VerbKind.SIIRTAA
    pairs: list[tuple[str, str, str]] = []
    nodes = sc.verb_groups[0].nodes
    for idx, node in enumerate(nodes[:-1]):
        tail = nodes[idx + 1]
        if isinstance(node, SurfaceTargetRef) and isinstance(tail, SurfaceRenumberTail):
            pairs.append((node.chapter, node.label, tail.new_label))

    assert pairs == [("8", "63", "69"), ("8", "64", "70")]
    assert [op.code() for op in result.parsed_ops if op.renumber_dest] == [
        "S L 8 o",
        "S P L:8 63",
        "S P L:8 64",
    ]
    assert [op.renumber_dest for op in result.parsed_ops if op.renumber_dest] == [
        "8",
        "69",
        "70",
    ]


def test_jolloin_renumber_followed_by_main_verb_group():
    """Jolloin renumber prepended SIIRTAA vg is followed by the main amendment vg.

    'lisätään uusi 10 §, jolloin nykyinen 10 § siirtyy 10 a §:ksi, sekä muutetaan 14 §'
    → surface_clause.verb_groups[0] = SIIRTAA (jolloin renumber)
    → surface_clause.verb_groups[1] = LISATA (uusi 10 §)
    → surface_clause.verb_groups[2] = MUUTTAA (14 §)
    """
    from lawvm.finland.johtolause.surface_model import VerbKind

    text = "lisätään uusi 10 §, jolloin nykyinen 10 § siirtyy 10 a §:ksi, sekä muutetaan 14 §"
    result = parse_clause(text)
    sc = result.surface_clause
    assert sc is not None

    verbs = [vg.verb for vg in sc.verb_groups]
    assert VerbKind.SIIRTAA in verbs, f"Expected SIIRTAA verb group from jolloin, got verbs: {verbs}"
    siirtaa_idx = verbs.index(VerbKind.SIIRTAA)
    assert siirtaa_idx == 0, f"SIIRTAA (jolloin) must be first verb group, got index {siirtaa_idx}"


def test_jolloin_section_suffix_range_renumber_emits_each_pair() -> None:
    """A final-label range like ``32 e-32 h`` owns each section relabel."""
    text = (
        "muutetaan 7 päivänä kesäkuuta 1978 annetun merimieslain (423/1978) "
        "32 ja 32 b §, 32 c §:n 1 momentti, 32 e §, 32 f §:n 1 momentti "
        "sekä 40 §:n 1 ja 4 momentti, lisätään lakiin uusi 32 c ja 32 d §, "
        "jolloin osaksi muutettu 32 c §, nykyinen 32 d §, muutettu 32 e § "
        "ja osaksi muutettu 32 f § siirtyy 32 e-32 h §:ksi, seuraavasti:"
    )
    result = parse_clause(text)
    assert result.surface_clause is not None

    renumber_ops = [op for op in result.parsed_ops if op.verb == "S" and op.kind == "P"]
    assert [(op.number, op.renumber_dest) for op in renumber_ops] == [
        ("32c", "32e"),
        ("32d", "32f"),
        ("32e", "32g"),
        ("32f", "32h"),
    ]
    assert result.surface_clause.verb_groups[0].verb.name == "SIIRTAA"


def test_jolloin_renumber_not_enriched_in_api_phase1b():
    """Jolloin renumber must NOT appear in enriched_surface_clause (Phase 1b is a no-op).

    The Pro audit fix (e-#1/#2) moves jolloin renumber from Phase 1b post-hoc
    enrichment into the parser.  The result.surface_clause already contains the
    SIIRTAA verb group from the parser.  enriched_surface_clause should not exist
    (it may still exist due to Phase 1c/1d but NOT due to Phase 1b jolloin injection).

    Key invariant: surface_clause is the canonical output; if enriched_surface_clause
    exists, it must NOT contain a SIIRTAA verb group that is absent from surface_clause.
    """
    from lawvm.finland.johtolause.surface_model import VerbKind

    text = "lisätään uusi 4 luku, jolloin nykyinen 4 luku siirtyy 5 luvuksi"
    result = parse_clause(text)

    # surface_clause must have the SIIRTAA vg (emitted by parser)
    sc = result.surface_clause
    assert sc is not None
    sc_verbs = [vg.verb for vg in sc.verb_groups]
    assert VerbKind.SIIRTAA in sc_verbs, f"SIIRTAA must be in surface_clause verbs: {sc_verbs}"

    # If enriched_surface_clause exists, it must not add an EXTRA SIIRTAA vg
    # that was absent from surface_clause (which would indicate Phase 1b still running).
    esc = result.enriched_surface_clause
    if esc is not None:
        esc_verbs = [vg.verb for vg in esc.verb_groups]
        # Count SIIRTAA groups: enriched must not have more than surface
        assert esc_verbs.count(VerbKind.SIIRTAA) == sc_verbs.count(VerbKind.SIIRTAA), (
            f"enriched_surface_clause has more SIIRTAA vgs than surface_clause — "
            f"Phase 1b is still running when it should be a no-op. "
            f"sc_verbs={sc_verbs}, esc_verbs={esc_verbs}"
        )


def test_no_comma_trailing_bare_insert_after_jolloin_is_preserved():
    """No-comma ``ja uusi N momentti`` after jolloin must still emit relabel + insert."""
    ops = parse_clause(
        "lisätään 11 §:ään uusi 4 momentti, jolloin nykyinen 4 momentti "
        "siirtyy 5 momentiksi ja uusi 6 momentti"
    ).parsed_ops

    assert [(op.code(), op.renumber_dest) for op in ops] == [
        ("S P 11 4", "5"),
        ("L P 11 4", ""),
        ("L P 11 6", ""),
    ]


def test_no_comma_structural_insert_after_jolloin_is_preserved() -> None:
    """A trailing explicit structural target after jolloin must remain outside the span."""
    ops = parse_clause(
        "lisätään 20 j §:ään uusi 3 ja 5 momentti, jolloin muutettu 3 momentti "
        "siirtyy 4 momentiksi sekä 24 §:n 2 momenttiin uusi 10–12 kohta"
    ).parsed_ops

    assert [(op.code(), op.renumber_dest) for op in ops] == [
        ("S P 20j 3", "4"),
        ("L P 20j 3", ""),
        ("L P 20j 5", ""),
        ("L P 24 2 10", ""),
        ("L P 24 2 11", ""),
        ("L P 24 2 12", ""),
    ]


def test_jolloin_section_renumber_stops_before_following_structural_insert_clause():
    """A trailing section renumber must not swallow the next outer insert target."""
    ops = parse_clause(
        "lisätään lakiin uusi 5 b §, jolloin nykyinen 5 b § siirtyy 5 c §:ksi, "
        "7 §:ään uusi 5 momentti"
    ).parsed_ops

    assert [(op.code(), op.renumber_dest) for op in ops] == [
        ("S P 5b", "5c"),
        ("L P 5b", ""),
        ("L P 7 5", ""),
    ]


def test_jolloin_moment_renumber_stops_before_following_doc_insert_clause():
    """A moment renumber must keep both the next insert and a DOC-scoped insert visible."""
    ops = parse_clause(
        "lisätään 32 §:ään uusi 1 momentti, jolloin muutettu 1 momentti ja nykyinen "
        "2 momentti siirtyvät 2 ja 3 momentiksi, 118 §:ään uusi 4 momentti, jolloin "
        "nykyinen 4 momentti siirtyy 5 momentiksi sekä lakiin uusi 127 a §"
    ).parsed_ops

    assert [(op.code(), op.renumber_dest) for op in ops] == [
        ("S P 32 1", "2"),
        ("S P 32 2", "3"),
        ("S P 118 4", "5"),
        ("L P 32 1", ""),
        ("L P 118 4", ""),
        ("L P 127a", ""),
    ]


def test_jolloin_reinstatement_insert_keeps_later_chapter_insert_clause() -> None:
    """2007/923 shape: a scoped reinstatement insert after ``jolloin`` must not
    truncate the following law-level chapter insert and scoped section inserts.
    """
    ops = parse_clause(
        "lisätään 1 luvun 1 §:ään uusi 11 momentti, lukuun uusi 3 a §, "
        "mainitulla lailla 581/1996 kumotun 4 §:n 3 momentin tilalle uusi "
        "3 momentti, 4 §:ään uusi 5 momentti, jolloin nykyinen 5-10 momentti "
        "siirtyvät 6-11 momentiksi, lukuun uusi 4 b ja 4 c § ja niiden edelle "
        "uudet väliotsikot, lakiin uusi 3 a luku, 5 lukuun uusi 5 a § ja "
        "7 lukuun uusi 2 a § seuraavasti:"
    ).parsed_ops

    assert [(op.code(), op.renumber_dest) for op in ops] == [
        ("S P L:1 4 5", "6"),
        ("S P L:1 4 6", "7"),
        ("S P L:1 4 7", "8"),
        ("S P L:1 4 8", "9"),
        ("S P L:1 4 9", "10"),
        ("S P L:1 4 10", "11"),
        ("L P L:1 1 11", ""),
        ("L P L:1 3a", ""),
        ("L P L:1 4 3", ""),
        ("L P L:1 4 5", ""),
        ("L P L:1 4b", ""),
        ("L P L:1 4c", ""),
        ("L L 3a", ""),
        ("L P L:5 5a", ""),
        ("L P L:7 2a", ""),
    ]


def test_genitive_moment_insert_item_arm_is_not_truncated() -> None:
    """`§:n N momentin uusi K kohta` must parse as one insert arm.

    Regression from 2018/1330: the parser used to stop at the genitive
    moment target and drop the trailing inserted item plus all later arms in
    the same `lisätään ...` chain.
    """
    ops = parse_clause("lisätään 7 luvun 27 §:n 2 momentin uusi 12 a kohta").parsed_ops

    assert [op.code() for op in ops] == ["L P L:7 27 2 12a"]


def test_long_insert_chain_survives_genitive_moment_item_arm() -> None:
    """A genitive moment-item arm must not truncate later insert targets."""
    text = (
        "lisätään 2 lukuun uusi 1 a §, 2 luvun 5 §:n 2 momenttiin uusi 5 a kohta, "
        "7 luvun 27 §:n 2 momentin uusi 12 a kohta, 13 luvun 13 §:ään uusi 5 momentti, "
        "19 luvun 14 §:ään uusi 3 ja 4 momentti sekä 20 luvun 14 §:ään uusi 2 ja 3 momentti"
    )

    ops = parse_clause(text).parsed_ops

    assert [op.code() for op in ops] == [
        "L P L:2 1a",
        "L P L:2 5 2 5a",
        "L P L:7 27 2 12a",
        "L P L:13 13 5",
        "L P L:19 14 3",
        "L P L:19 14 4",
        "L P L:20 14 2",
        "L P L:20 14 3",
    ]


def test_jolloin_multi_section_renumber_keeps_full_source_and_destination_lists():
    from lawvm.finland.johtolause.surface_model import SurfaceRenumberTail, SurfaceTargetRef, VerbKind

    text = (
        "lisätään 6 §:ään uusi 2 momentti sekä asetukseen uusi 9 §, jolloin nykyiset 9, "
        "10 ja 11 § siirtyvät 10, 11 ja 12 §:ksi, seuraavasti:"
    )

    result = parse_clause(text)
    sc = result.surface_clause
    assert sc is not None

    assert [vg.verb for vg in sc.verb_groups[:2]] == [VerbKind.SIIRTAA, VerbKind.LISATA]
    move_nodes = sc.verb_groups[0].nodes
    assert len(move_nodes) == 6

    labels: list[tuple[str, str]] = []
    for idx in range(0, len(move_nodes), 2):
        target = move_nodes[idx]
        tail = move_nodes[idx + 1]
        assert isinstance(target, SurfaceTargetRef)
        assert isinstance(tail, SurfaceRenumberTail)
        labels.append((target.label, tail.new_label))

    assert labels == [("9", "10"), ("10", "11"), ("11", "12")]

    move_ops = result.parsed_ops[:3]
    assert [op.code() for op in move_ops] == ["S P 9", "S P 10", "S P 11"]
    assert [op.renumber_dest for op in move_ops] == ["10", "11", "12"]
    assert [op.code() for op in result.parsed_ops[3:]] == ["L P 6 2", "L P 9"]


def test_parse_clause_keeps_anaphoric_pykala_insert_after_provenance_reinstatement_span():
    text = (
        "lisätään lakiin uusi 2 b § ja 6 §:ään, siitä mainitulla 7 päivänä "
        "tammikuuta 1977 annetulla lailla kumotun 4 momentin tilalle, uusi 4 momentti "
        "seuraavasti:"
    )

    ops = parse_clause(text).parsed_ops

    assert [op.code() for op in ops] == ["L P 2b", "L P 6 4"]


def test_parse_clause_skips_nojalla_authority_reference_before_real_targets():
    from lawvm.finland.johtolause.surface_model import SurfaceTargetRef, TargetKind, VerbKind

    text = (
        "muutetaan valtion virkamiehiltä vaadittavasta kielitaidosta 1 päivänä kesäkuuta 1922 "
        "annetun lain 6 §:n nojalla sanotun lain täytäntöönpanosta 29 päivänä joulukuuta 1922 "
        "annetun asetuksen 1, 3, 7, 8 ja 10 §, niistä 3 ja 8 § sellaisina kuin ne ovat muutettuina "
        "edellinen 15 päivänä marraskuuta 1924 ja jälkimmäinen 28 päivänä marraskuuta 1930 "
        "annetussa asetuksessa, näin kuuluviksi:"
    )

    result = parse_clause(text)
    sc = result.surface_clause
    assert sc is not None
    assert len(sc.verb_groups) == 1
    vg = sc.verb_groups[0]
    assert vg.verb == VerbKind.MUUTTAA

    targets = [node for node in vg.nodes if isinstance(node, SurfaceTargetRef)]
    got = []
    for target in targets:
        pair = (target.kind, target.label)
        if pair not in got:
            got.append(pair)
    assert got[:5] == [
        (TargetKind.SECTION, "1"),
        (TargetKind.SECTION, "3"),
        (TargetKind.SECTION, "7"),
        (TargetKind.SECTION, "8"),
        (TargetKind.SECTION, "10"),
    ]


def test_parse_clause_skips_nojalla_authority_chain_before_real_uusi_insert_target():
    text = (
        "lisätään 5 päivänä kesäkuuta 2002 annetun tonnistoverolain (476/2002) "
        "34 §:n 2 momentin ja 35 §:n 1 momentin nojalla, ilmoittamisvelvollisuudesta "
        "28 päivänä joulukuuta 1995 annettuun valtiovarainministeriön päätökseen "
        "(1760/1995) uusi 8 b seuraavasti:"
    )

    ops = parse_clause(text).parsed_ops

    assert [op.code() for op in ops] == ["L P 8b"]


# ---------------------------------------------------------------------------
# Bare-statute-name skip + leading-nojalla authority recovery (grammar-owned).
#
# A leading ``N §:n nojalla`` authority basis mis-reads the enabling-statute
# section as the first target; the real targets sit behind a BARE statute name
# (no parenthetical id). The grammar now SKIPS the authority lead-in to the real
# target list and OWNS the clause (grammar_owned lane), instead of declining to a
# legacy fallback that grabbed the authority section and dropped the targets.
# ---------------------------------------------------------------------------


def test_parse_clause_recovers_bare_name_targets_behind_leading_nojalla():
    """1957/230: ``kansaneläkelain (347/56) 99 §:n nojalla … annetun
    kansaneläkeasetuksen 80 ja 81 §`` — the ``99 §`` authority is dropped and the
    bare-name targets ``80`` / ``81`` recovered. Previously legacy grabbed ``99``."""
    text = (
        "muutetaan 8 päivänä kesäkuuta 1956 annetun kansaneläkelain ( 347/56 ) "
        "99 §:n nojalla 7 päivänä joulukuuta 1956 annetun kansaneläkeasetuksen "
        "80 ja 81 §, sellaisina kuin ne ovat 29 päivänä maaliskuuta 1957 annetussa "
        "asetuksessa (138/57), näin kuuluviksi:"
    )
    result = parse_clause(text)
    assert result.parser_lane == "grammar_owned"
    assert [op.code() for op in result.parsed_ops] == ["M P 80", "M P 81"]


def test_parse_clause_recovers_bare_name_doc_targets_behind_leading_nojalla():
    """1935/418: ``kielilain 25 §:n nojalla, mainitun lain täytäntöönpanosta …
    annetun asetuksen 1, 2, 5, 8 ja 9 §`` — the ``25 §`` authority is dropped and
    the bare-name section list recovered, grammar-owned."""
    text = (
        "muutetaan täten, 1 päivänä kesäkuuta 1922 annetun kielilain 25 §:n nojalla, "
        "mainitun lain täytäntöönpanosta 29 päivänä joulukuuta 1922 annetun asetuksen "
        "1, 2, 5, 8 ja 9 § näin kuuluviksi:."
    )
    result = parse_clause(text)
    assert result.parser_lane == "grammar_owned"
    assert [op.code() for op in result.parsed_ops] == [
        "M P 1",
        "M P 2",
        "M P 5",
        "M P 8",
        "M P 9",
    ]


def test_parse_clause_recovers_uusi_insert_behind_leading_nojalla():
    """1987/1046: ``annetun lain (380/87) 14§:n 2 momentin nojalla uusi 4 a§`` —
    the ``14 §`` authority is dropped and the real insertion ``uusi 4 a §``
    recovered (the authority skip lands on the ``UUSI`` anchor)."""
    text = (
        "lisätään asetukseen vammaisuuden perusteella järjestettävistä palveluista "
        "ja tukitoimista 3 päivänä huhtikuuta 1987 annetun lain (380/87) "
        "14§:n 2 momentin nojalla uusi 4 a§, seuraavasti:"
    )
    result = parse_clause(text)
    assert result.parser_lane == "grammar_owned"
    assert [op.code() for op in result.parsed_ops] == ["L P 4a"]


def test_parse_clause_ordinary_bare_name_section_not_authority_skipped():
    """Negative guard: an ordinary ``… annetun lain N §`` reference WITHOUT a
    ``nojalla`` authority basis must parse its sections as targets unchanged — the
    leading-authority skip must NOT fire on an ordinary bare statute name."""
    text = "muutetaan kansaneläkeasetuksen 80 ja 81 § näin kuuluviksi:"
    result = parse_clause(text)
    assert [op.code() for op in result.parsed_ops] == ["M P 80", "M P 81"]


def test_parse_clause_authority_skip_does_not_grab_citation_year_as_section():
    """Negative guard: the ``_num_begins_operative_target`` test requires a PYKALA
    to close the number-list run, so a date / citation numeral after ``nojalla``
    (``… nojalla 7 päivänä …``) is never mistaken for a section target — the skip
    advances past it to the real bare-name ``§`` list (1957/230 lands on ``80``,
    not the date ``7``)."""
    text = (
        "muutetaan 8 päivänä kesäkuuta 1956 annetun kansaneläkelain ( 347/56 ) "
        "99 §:n nojalla 7 päivänä joulukuuta 1956 annetun kansaneläkeasetuksen "
        "80 ja 81 §, näin kuuluviksi:"
    )
    result = parse_clause(text)
    # The date numeral ``7`` and citation year ``1956`` are skipped; only the real
    # bare-name targets 80/81 are produced (never 7 / 99 / 1956).
    assert [op.code() for op in result.parsed_ops] == ["M P 80", "M P 81"]


# ---------------------------------------------------------------------------
# Lexer normalization: §-suffix apostrophe (Finlex XML artifact)
# ---------------------------------------------------------------------------


def test_parse_clause_pykala_apostrophe_normalization():
    """§:'ään (with apostrophe) must be tokenized as §:ään (PYKALA ILL).

    Regression for Finlex XML artifact in 1974/911: the johtolause contains
    "11 §:'ään uusi 2 momentti" where the apostrophe before 'ään' breaks PYKALA
    ILL tokenization, causing the insertion to fall back to a section replace.
    """
    from lawvm.finland.johtolause.lexer import tokenize

    tokens = tokenize("lisätään 11 §:'ään uusi 2 momentti")
    pykala_toks = [t for t in tokens if t.cat == "PYKALA"]
    assert len(pykala_toks) == 1, f"Expected 1 PYKALA token, got {pykala_toks}"
    assert pykala_toks[0].case == "ILL", (
        f"§:'ään must tokenize as PYKALA ILL, got case={pykala_toks[0].case!r}"
    )

    # Full parse: lisätään 11 §:ään uusi 2 momentti → 1 INSERT op with momentti=2
    ops = parse_clause("lisätään 11 §:'ään uusi 2 momentti").parsed_ops
    assert len(ops) == 1, f"Expected 1 parsed op, got {ops}"
    assert ops[0].verb == "L"
    assert ops[0].number == "11"
    assert ops[0].momentti == 2
    assert ops[0].witness is not None
    assert ops[0].witness.rule_id == "fi.insertion_sub_target"


def test_parse_clause_pykala_short_illative_typo_normalization():
    """§:än must tokenize as illative §:ään for source-typo amendment clauses.

    Regression for `1993/81 <- 1994/495`: the johtolause has "2 §:än ...
    uuden 5 momentin", omitting one `ä` from the standard illative suffix.
    Treating it as WORD dropped the only operation from the amendment.
    """
    from lawvm.finland.johtolause.lexer import tokenize

    tokens = tokenize("lisätään 2 §:än uusi 5 momentti")
    pykala_toks = [t for t in tokens if t.cat == "PYKALA"]
    assert len(pykala_toks) == 1
    assert pykala_toks[0].text == "§:än"
    assert pykala_toks[0].case == "ILL"

    ops = parse_clause("lisätään 2 §:än uusi 5 momentti seuraavasti:").parsed_ops
    assert [op.code() for op in ops] == ["L P 2 5"]
    assert ops[0].witness is not None
    assert ops[0].witness.rule_id == "fi.insertion_sub_target"


def test_parse_clause_skips_glued_nainkuuluva_before_subsection_insert_targets() -> None:
    """Glued ``näinkuuluva`` must not collapse a subsection insert into a chapter insert.

    Regression for 1979/373: ``4 luvun 2 §:ään uusi näinkuuluva 2 ja 3 momentti``
    previously parsed only as ``INSERT 4 luku`` because the archaic
    ``näin kuuluva`` lead-in appeared as one glued token.
    """
    ops = parse_clause("lisätään 4 luvun 2 §:ään uusi näinkuuluva 2 ja 3 momentti").parsed_ops

    assert len(ops) == 2
    assert [(op.verb, op.chapter, op.number, op.momentti) for op in ops] == [
        ("L", "4", "2", 2),
        ("L", "4", "2", 3),
    ]
    for op in ops:
        assert op.witness is not None
        assert op.witness.rule_id == "fi.insertion_sub_target"


def test_parse_clause_accepts_mathematical_minus_in_subsection_range() -> None:
    """U+2212 minus is a source dash glyph, not an opaque word separator."""
    ops = parse_clause(
        "lisätään 3 §:ään, sellaisena kuin se on osaksi asetuksessa 225/2015, "
        "uusi 8−10 momentti ja asetukseen uusi 5 a § seuraavasti:"
    ).parsed_ops

    assert [op.code() for op in ops] == ["L P 3 8", "L P 3 9", "L P 3 10", "L P 5a"]
    for op in ops[:3]:
        assert op.witness is not None
        assert op.witness.rule_id == "fi.insertion_sub_target"


def test_parse_clause_keeps_inherited_part_scope_for_chapter_insert_continuation() -> None:
    """Inherited ``osaan uusi`` continuation must keep the current part scope.

    Regression for 2018/301: after ``II osan ... 3 lukuun uusi 3-15 §``, the
    continuation ``ja osaan uusi 4-13 luku`` used to stop parsing entirely.
    """
    ops = parse_clause(
        "lisätään II osan 3 lukuun uusi 3-15 § ja osaan uusi 4-13 luku"
    ).parsed_ops

    chapter_inserts = [
        (op.part, op.chapter, op.number)
        for op in ops
        if op.verb == "L" and op.kind == "L"
    ]
    assert chapter_inserts == [
        ("II", "", "4"),
        ("II", "", "5"),
        ("II", "", "6"),
        ("II", "", "7"),
        ("II", "", "8"),
        ("II", "", "9"),
        ("II", "", "10"),
        ("II", "", "11"),
        ("II", "", "12"),
        ("II", "", "13"),
    ]


def test_parse_clause_keeps_explicit_illative_part_scope_for_chapter_inserts() -> None:
    """Explicit ``V osaan uusi 2 ja 3 luku`` must emit chapter inserts, not a part insert."""
    ops = parse_clause(
        "lisätään IV osaan uusi 3 ja 4 luku, V osaan uusi 2 ja 3 luku"
    ).parsed_ops

    chapter_inserts = [
        (op.part, op.chapter, op.number)
        for op in ops
        if op.verb == "L" and op.kind == "L"
    ]
    assert chapter_inserts == [
        ("IV", "", "3"),
        ("IV", "", "4"),
        ("V", "", "2"),
        ("V", "", "3"),
    ]


# ---------------------------------------------------------------------------
# Parser: combined heading + subsection insertion
# ---------------------------------------------------------------------------


def test_parse_clause_uusi_otsikko_ja_momentti():
    """lisätään N §:ään uusi otsikko ja M momentti → 2 INSERT ops.

    Regression for 1962/420 §7: amendment 2024/247 johtolause says
    "lisätään 7 §:ään uusi otsikko ja 2 momentti".  Previously only the
    heading op was emitted; the "ja 2 momentti" continuation was dropped.
    """
    ops = parse_clause("lisätään 7 §:ään uusi otsikko ja 2 momentti").parsed_ops
    assert len(ops) == 2, f"Expected 2 ops (heading + subsection), got {ops}"

    heading_ops = [op for op in ops if op.facet is not None]
    subsection_ops = [op for op in ops if op.momentti == 2]
    assert len(heading_ops) == 1, "Expected 1 heading INSERT op"
    assert len(subsection_ops) == 1, "Expected 1 subsection (momentti=2) INSERT op"

    h = heading_ops[0]
    assert h.verb == "L"
    assert h.number == "7"
    assert h.witness is not None
    assert h.witness.rule_id == "fi.insertion_heading"

    m = subsection_ops[0]
    assert m.verb == "L"
    assert m.number == "7"
    assert m.momentti == 2
    assert m.witness is not None
    assert m.witness.rule_id == "fi.insertion_sub_target"


def test_parse_clause_uusi_otsikko_ja_uusi_momentti():
    """Repeated ``uusi`` in the continuation keeps both heading and subsection."""
    ops = parse_clause("lisätään 8 §:ään uusi otsikko ja uusi 4 momentti").parsed_ops
    assert len(ops) == 2

    heading_ops = [op for op in ops if op.facet is FacetKind.HEADING]
    subsection_ops = [op for op in ops if op.momentti == 4]
    assert len(heading_ops) == 1
    assert len(subsection_ops) == 1
    assert heading_ops[0].number == "8"
    assert subsection_ops[0].number == "8"


def test_parse_clause_lisata_otsikko_without_uusi_after_target():
    """``lisätään N §:ään otsikko`` is an explicit heading insertion."""
    ops = parse_clause("lisätään 8 §:ään otsikko ja uusi 4 momentti").parsed_ops
    assert len(ops) == 2

    heading_ops = [op for op in ops if op.facet is FacetKind.HEADING]
    subsection_ops = [op for op in ops if op.momentti == 4]
    assert len(heading_ops) == 1
    assert len(subsection_ops) == 1
    assert heading_ops[0].verb == "L"
    assert heading_ops[0].number == "8"


def test_parse_clause_target_first_valiotsake_then_subsection_insert() -> None:
    """``N §:n edelle uusi väliotsake`` must not drop the next insert arm."""
    text = (
        "lisätään asetuksen 19 §:n edelle uusi väliotsake ja 19 §:ään, "
        "sellaisen kuin se on muutettuna 21 päivänä huhtikuuta 1978 annetussa "
        "asetuksessa (282/78), uusi 2 momentti, jolloin nykyiset 2 ja 3 momentti "
        "siirtyvät 3 ja 4 momenteiksi seuraavasti:"
    )
    ops = parse_clause(text, statute_id="1973/692").parsed_ops

    assert [op.code() for op in ops] == ["S P 19 2", "S P 19 3", "L P 19 o", "L P 19 2"]
    assert ops[0].renumber_dest == "3"
    assert ops[1].renumber_dest == "4"
    assert ops[2].witness is not None
    assert ops[2].witness.rule_id == "fi.heading_edelle_otsikko_target_list"
    assert ops[3].witness is not None
    assert ops[3].witness.rule_id == "fi.insertion_sub_target"


def test_parse_clause_skips_temporal_modifier_before_insert_targets() -> None:
    """Leading ``väliaikaisesti`` must not swallow the real insert targets.

    Regression for 1973/36 <- 2003/156: the scanner used to collapse
    ``väliaikaisesti 2 §:ään ...`` into a fake statute-name span, after which
    cross-verb fallback invented ``31 § 3 mom`` and lost both the real
    ``2 § 3 mom`` and ``27 §`` inserts.
    """
    text = (
        "muutetaan väliaikaisesti lasten päivähoidosta 19 päivänä tammikuuta 1973 annetun lain "
        "(36/1973) 11 §:n 3 momentti, 28 ja 29 § sekä 31 §:n 1 momentti, "
        "sellaisena kuin niistä ovat 11 §:n 3 momentti laissa 875/1981 ja 31 §:n 1 momentti "
        "laissa 1497/1994, sekä lisätään väliaikaisesti 2 §:ään, sellaisena kuin se on osaksi "
        "laeissa 698/1982 ja 304/1983, mainitulla lailla 698/1982 kumotun 3 momentin tilalle "
        "uusi 3 momentti ja lakiin siitä lailla 389/1979 kumotun 27 §:n tilalle uusi 27 § "
        "seuraavasti:"
    )

    ops = parse_clause(text).parsed_ops
    codes = [op.code() for op in ops]

    assert "L P 2 3" in codes
    assert "L P 27" in codes
    assert "L P 31 3" not in codes


def test_parse_clause_chapter_heading_insert_can_continue_to_section_range() -> None:
    """``uusi N luvun otsikko ja M—P §`` must emit both heading and sections.

    Regression for 1973/36 <- 2012/909: PEG stopped at ``uusi 3 luvun
    otsikko`` and dropped the following ``15—18 §`` inserts.
    """
    text = (
        "lisätään lakiin siitä lailla 698/1982 kumotun 3 luvun otsikon ja 15—18 §:n tilalle "
        "uusi 3 luvun otsikko ja 15—18 §"
    )

    ops = parse_clause(text).parsed_ops
    codes = [op.code() for op in ops]
    heading_ops = [op for op in ops if op.facet is FacetKind.HEADING]

    assert len(heading_ops) == 1
    assert heading_ops[0].verb == "L"
    assert heading_ops[0].kind == "L"
    assert heading_ops[0].number == "3"
    assert heading_ops[0].witness is not None
    assert heading_ops[0].witness.rule_id == "fi.insertion_heading"
    assert codes.count("L P L:3 15") == 1
    assert codes.count("L P L:3 16") == 1
    assert codes.count("L P L:3 17") == 1
    assert codes.count("L P L:3 18") == 1


def test_double_hyphen_section_range_does_not_truncate_the_list() -> None:
    """``21--23 §`` (doubled ASCII hyphen) must tokenize as a range, not a WORD.

    Regression for 1978/612: the Finlex source wrote the en-dash range
    ``21--23 §`` with two ASCII hyphens.  The lexer collapsed ``21--23`` into a
    single opaque WORD (the single-dash split's lookahead failed on the second
    hyphen), so the target-list continuation broke and every target after it —
    ``24 §:n 1 ja 2 momentti, 25 ja 27 §, ...`` — was silently dropped.  A run
    of dash characters now splits as one DASH delimiter.
    """
    codes = [op.code() for op in parse_clause("muutetaan 19, 21--23 §, 24 §").parsed_ops]
    assert codes == ["M P 19", "M P 21", "M P 22", "M P 23", "M P 24"], codes


def test_parse_clause_multi_target_heading_arm_does_not_truncate_enumeration() -> None:
    """``<list/range> §:n edelle uusi väliotsikko`` must not abort the parse.

    Regression for 2009/886 <- 1501/1993 (arvonlisäverolaki): the DOC:ILL
    insert enumeration parsed ``lakiin uusi 69 a § ja 69 b–69 i §`` then hit
    ``sekä 69 b–69 e ja 69 g–69 i §:n edelle uusi väliotsikko`` — a heading
    placement whose target is a list/range — and silently dropped every
    following clause (69 j, 69 k, and the trailing ``138 §`` reinstatement).
    The arm is now parsed and the enumeration continues.
    """
    text = (
        "lisätään lakiin uusi 69 a § ja 69 b–69 i § "
        "sekä 69 b–69 e ja 69 g–69 i §:n edelle uusi väliotsikko, "
        "lakiin uusi 69 j ja 69 k § "
        "sekä lakiin siitä lailla 1218/1994 kumotun 138 §:n tilalle uusi 138 §"
    )

    ops = parse_clause(text).parsed_ops
    numbers = [op.number for op in ops]

    # The whole-section inserts before AND after the heading arm survive.
    for sec in ("69a", "69b", "69i", "69j", "69k", "138"):
        assert sec in numbers, f"{sec} dropped: {numbers}"

    # The heading arm fired with the dedicated target-list witness rule.
    heading_rule = "fi.heading_edelle_otsikko_target_list"
    assert any(
        op.witness is not None and op.witness.rule_id == heading_rule for op in ops
    ), [op.witness.rule_id if op.witness else None for op in ops]


def test_parse_clause_alakohta_continuation_does_not_block_later_section_targets() -> None:
    """Same-item ``i alakohta`` continuation must not truncate the target list.

    Regression for 2017/444 <- 2023/444: ``11 kohdan johdantokappale ja
    i alakohta sekä 19 kohta`` must parse ``i alakohta`` under the same
    11 kohta and still keep the later ``19 kohta``, ``3 luvun 10 §:n
    1 momentti`` and ``13 §:n 3 ja 4 momentti`` targets.
    """
    text = (
        "muutetaan 1 luvun 2 §:n 1 momentin 11 kohta, "
        "4 §:n 1 momentin 10 kohdan e alakohta, 11 kohdan johdantokappale "
        "ja i alakohta sekä 19 kohta, 2 luvun otsikko, 2 §:n 4 momentti "
        "ja 3 §:n otsikko, 3 luvun 2 §:n 1 momentin 3 kohta, 3 §:n 2 momentin 2 kohta, "
        "3 §:n 5 momentti, 4 §:n 3 momentti, 8 §:n 1 momentti, "
        "10 §:n 1 momentti sekä 13 §:n 3 ja 4 momentti"
    )

    codes = [op.code() for op in parse_clause(text).parsed_ops]

    assert "M P L:1 4 1 11 i" in codes
    assert "M P L:1 4 1 19" in codes
    assert "M P L:3 10 1" in codes
    assert "M P L:3 13 3" in codes
    assert "M P L:3 13 4" in codes


def test_parse_clause_compound_replace_then_insert_item_via_seka_lisataan() -> None:
    """Compound johtolause 'muutetaan X seuraavasti sekä lisätään Y uusi N kohta' must
    yield both the REPLACE and the INSERT op.

    Regression for 2006/308 <- 2017/198: the annotate_end_sentinels filter was
    spanning the END_SENTINEL_SPAN all the way to the end of the token stream,
    eating the second verb group 'lisätään 9 §:ään uusi 5 kohta seuraavasti'.
    The fix stops the sentinel at the next VERB so the second clause is visible.
    """
    text = (
        "muutetaan majoitus- ja ravitsemustoiminnasta annetun lain ( 308/2006 ) "
        "9 §:n 4 kohta seuraavasti sekä lisätään 9 §:ään uusi 5 kohta seuraavasti:"
    )

    result = parse_clause(text, statute_id="2006/308")
    codes = [op.code() for op in result.parsed_ops]

    # Both the replace (M) and the insert (L) must be present
    assert "M P 9 1 4" in codes, f"Expected 'M P 9 1 4' in {codes}"
    assert "L P 9 1 5" in codes, f"Expected 'L P 9 1 5' in {codes}"
    assert not result.is_failed


def test_parse_clause_compound_replace_then_infinitive_insert_item() -> None:
    """Coordinated infinitive ``lisätä`` is a real amendment verb.

    Regression for 1993/1495 <- 1994/931: the lexer classified ``lisätä`` as a
    WORD, so the grammar-owned parser declined and the legacy fallback dropped
    the ``1 §:ään uuden 7 kohdan`` insert.
    """
    text = (
        "muuttaa maa- ja metsätalousministeriön suoritteista perittävistä "
        "maksuista 23 päivänä joulukuuta 1993 antamansa päätöksen (1495/93) "
        "2 §:n 2 momentin 1 kohdan ja liitteenä olevan maksutaulukon sekä "
        "lisätä 1 §:ään uuden 7 kohdan jolloin nykyiset 7 ja 8 kohta siirtyvät "
        "8 ja 9 kohdaksi seuraavasti:"
    )

    result = parse_clause(text, statute_id="1993/1495")
    codes = [op.code() for op in result.parsed_ops]

    assert result.parser_lane == "grammar_owned"
    assert result.grammar_decline_reason is None
    assert "M P 2 2 1" in codes
    assert "L P 1 1 7" in codes


def test_tokenize_restores_ocr_lost_dash_in_section_range() -> None:
    """Two bare adjacent section numbers before a single § are a coalesced range.

    Regression for the live 1987/320 <- 1994/1375 clause "21 23 §": the source
    scan dropped the en-dash from "21–23 §", leaving NUM NUM PYKALA.  Without
    repair the range parse fails and every target after it (here the trailing
    "32 §:n 1 momentin 3, 4, 6 ja 8 kohta") is dropped on the floor.  The lexer
    reinserts the implied DASH only for the unambiguous ascending bare pair.
    """
    from lawvm.finland.johtolause.lexer import tokenize

    tokens = tokenize("21 23 §")
    cats = [t.cat for t in tokens]
    assert cats == ["NUM", "DASH", "NUM", "PYKALA"], cats
    assert [t.text for t in tokens][:3] == ["21", "–", "23"]

    # Full live clause: the range and the trailing kohta list must all compile.
    text = (
        "muutetaan 2 §:n 2 momentti, 21 23 § ja "
        "32 §:n 1 momentin 3, 4, 6 ja 8 kohta seuraavasti:"
    )
    result = parse_clause(text, statute_id="1987/320")
    codes = [op.code() for op in result.parsed_ops]
    for expected in ("M P 21", "M P 22", "M P 23", "M P 32 1 3", "M P 32 1 8"):
        assert expected in codes, f"Expected {expected!r} in {codes}"


def test_tokenize_does_not_fabricate_range_for_separated_or_descending_numbers() -> None:
    """The lost-dash repair must NOT coalesce legitimately separate targets.

    A genuine list always carries an explicit separator (",", "ja", "sekä"),
    and a descending or equal bare pair is not a valid range — none of these
    may gain an implied DASH.
    """
    from lawvm.finland.johtolause.lexer import tokenize

    # Comma- and conjunction-separated pairs keep their separator, no DASH.
    assert [t.cat for t in tokenize("21, 23 §")] == ["NUM", "COMMA", "NUM", "PYKALA"]
    assert [t.cat for t in tokenize("21 ja 23 §")] == ["NUM", "CONJ", "NUM", "PYKALA"]
    # Descending and equal bare pairs are left untouched (no fabricated range).
    assert [t.cat for t in tokenize("23 21 §")] == ["NUM", "NUM", "PYKALA"]
    assert [t.cat for t in tokenize("21 21 §")] == ["NUM", "NUM", "PYKALA"]


def test_tokenize_splits_item_letter_range_into_letter_dash_letter() -> None:
    """A dashed item-letter range must split so the range grammar can expand it.

    Regression for the live 1972/484 <- 1989/820 clause fragment
    "1 §:n c ja j-l kohta": the "j-l" range used to tokenize as a single WORD,
    which the enumeration grammar could not consume — it poisoned the parse and
    dropped every target after it (the whole "3 §, 4 §:n 1 ja 3 momentti, ..."
    list).  Splitting into LETTER DASH LETTER lets _letter_list expand j-l to
    j, k, l and the rest of the enumeration survives.
    """
    from lawvm.finland.johtolause.lexer import tokenize

    assert [t.cat for t in tokenize("j-l")] == ["LETTER", "DASH", "LETTER"]
    assert [t.text for t in tokenize("j-l")] == ["j", "–", "l"]

    text = (
        "muutetaan 1 §:n c ja j-l kohta sekä 3 §, 4 §:n 1 ja 3 momentti, "
        "10 §, 13 §:n 1 momentti seuraavasti:"
    )
    result = parse_clause(text, statute_id="1972/484")
    codes = [op.code() for op in result.parsed_ops]
    # The j-l range expands to items j, k, l on section 1.
    for expected in ("M P 1 1 j", "M P 1 1 k", "M P 1 1 l"):
        assert expected in codes, f"Expected {expected!r} in {codes}"
    # And the downstream section targets are no longer dropped.
    for expected in ("M P 3", "M P 4 1", "M P 4 3", "M P 10", "M P 13 1"):
        assert expected in codes, f"Expected {expected!r} in {codes}"


def test_tokenize_does_not_split_letter_dash_number_as_letter_range() -> None:
    """The letter-range split must not steal letter-dash-number (a–1) forms."""
    from lawvm.finland.johtolause.lexer import tokenize

    assert [t.cat for t in tokenize("a-1")] == ["LETTER", "DASH", "NUM"]
    # Plain compound and single section letter stay intact.
    assert [t.cat for t in tokenize("14a")] == ["NUM", "LETTER"]


def test_parse_clause_anaphoric_sanottu_pykala_keeps_downstream_arms() -> None:
    """1982/106: ``sanottuun pykälään`` insert arm must not abort the target list.

    The clause inserts items into ``8 §:n 3 momentti``, then ``sanottuun
    pykälään uusi 5 ja 6 momentti`` (anaphoric backref to §8), then ``sekä lakiin
    uusi 11 a, 15 a ja 15 b §``.  The anaphoric determiner ``sanottuun`` lexes as
    a bare WORD; previously the continuation loop aborted at it and dropped both
    the §8 subsection inserts and the downstream law-level section inserts.
    """
    text = (
        "muutetaan rintamasotilaseläkelain (119/77) 3 §:n 1 momentti, 6 §, "
        "8 §:n 2 ja 4 momentti, 12 §, 13 §:n 1 momentti ja 17 §, "
        "lisätään 8 §:n 3 momenttiin uusi 10 ja 11 kohta ja "
        "sanottuun pykälään uusi 5 ja 6 momentti sekä lakiin uusi "
        "11 a, 15 a ja 15 b § seuraavasti:"
    )

    result = parse_clause(text, statute_id="1982/106")
    codes = [op.code() for op in result.parsed_ops]

    assert result.parse_error is None
    # 8 §:n 3 momentti item inserts.
    assert "L P 8 3 10" in codes
    assert "L P 8 3 11" in codes
    # sanottuun pykälään -> §8 subsection inserts (previously dropped).
    assert "L P 8 5" in codes
    assert "L P 8 6" in codes
    # Downstream law-level section inserts (previously dropped with the arm).
    assert "L P 11a" in codes
    assert "L P 15a" in codes
    assert "L P 15b" in codes


def test_parse_clause_transport_dropped_pykala_and_ocr_lisataan_keeps_replace_list() -> None:
    """1994/1265 has OCR damage in the section-list boundary before ``lisätään``."""
    text = (
        "muutetaan 1 päivänä joulukuuta 1989 annetun säätiöasetuksen "
        "( 1045/89 ) 2, 3, 5, 7 ja 9 ) sekä 1isätään uusi 9 a § seuraavasti:"
    )

    result = parse_clause(text, statute_id="1989/1045")
    codes = [op.code() for op in result.parsed_ops]

    assert result.parse_error is None
    assert codes == ["M P 2", "M P 3", "M P 5", "M P 7", "M P 9", "L P 9a"]
    assert (
        "parser_normalization=fi.johtolause.transport_dropped_pykala_before_boundary.v1"
        in result.diagnostics
    )
    assert "parser_normalization=fi.johtolause.transport_ocr_glued_lisataan.v1" in (
        result.diagnostics
    )


def test_parse_clause_anaphoric_saman_pykala_momentti_resolves_section() -> None:
    """``saman pykälän M momenttiin uusi K kohta`` resolves to the last section."""
    text = (
        "lisätään 5 §:n 2 momenttiin uusi 3 kohta ja "
        "saman pykälän 2 momenttiin uusi 4 kohta seuraavasti:"
    )

    result = parse_clause(text)
    codes = [op.code() for op in result.parsed_ops]

    assert result.parse_error is None
    assert codes == ["L P 5 2 3", "L P 5 2 4"]


def test_parse_clause_anaphoric_mainittu_momentti_resolves_momentti() -> None:
    """``mainittuun momenttiin uusi K kohta`` resolves to the last momentti."""
    text = (
        "lisätään 5 §:ään uusi 2 momentti ja "
        "mainittuun momenttiin uusi 1 kohta seuraavasti:"
    )

    result = parse_clause(text)
    codes = [op.code() for op in result.parsed_ops]

    assert result.parse_error is None
    assert codes == ["L P 5 2", "L P 5 2 1"]


def test_parse_clause_anaphoric_sanottu_lakiin_resolves_root_section_insert() -> None:
    """``sanottuun lakiin uusi N §`` continues a law-level section insert list."""
    text = "lisätään lakiin uusi 5 § ja sanottuun lakiin uusi 6 § seuraavasti:"

    result = parse_clause(text)
    codes = [op.code() for op in result.parsed_ops]

    assert result.parse_error is None
    assert codes == ["L P 5", "L P 6"]


def test_parse_clause_anaphoric_mainittu_lukuun_carries_chapter() -> None:
    """``mainittuun lukuun uusi N §`` inserts into the last-mentioned chapter."""
    text = (
        "lisätään 5 lukuun uusi 10 § ja "
        "mainittuun lukuun uusi 11 § seuraavasti:"
    )

    result = parse_clause(text)
    codes = [op.code() for op in result.parsed_ops]

    assert result.parse_error is None
    assert codes == ["L P L:5 10", "L P L:5 11"]


def test_parse_clause_doc_ill_prefix_chapter_keeps_section_insert() -> None:
    """``lisätään lakiin N lukuun uusi M §`` must insert section M into chapter N.

    The chapter named between the document and ``uusi`` is the destination scope
    for the new section.  Previously this mis-routed to a whole-chapter insert
    (``L L 1``) and the trailing ``uusi 20 a §`` was dropped.
    """
    result = parse_clause("lisätään lakiin 1 lukuun uusi 20 a § seuraavasti:")

    assert result.parse_error is None
    assert [op.code() for op in result.parsed_ops] == ["L P L:1 20a"]

    # Plain whole-chapter insert still routes to a chapter insertion.
    chapter_result = parse_clause("lisätään lakiin uusi 3 luku seuraavasti:")
    assert [op.code() for op in chapter_result.parsed_ops] == ["L L 3"]


def test_parse_clause_doc_ill_spaced_citation_bare_section_insert() -> None:
    """A target-statute citation after DOC:ILL is not the inserted section label.

    Real witness: 2016/1540 inserts 1 a § into 2011/1546, but its johtolause
    spells the parent citation as ``(1546 /2011)`` and omits the ``§`` after
    ``uusi 1 a``. The citation residue is source identity evidence; the only
    owned insertion target is the immediately closed bare section label.
    """
    result = parse_clause(
        "lisätään julkisesti tuetuista vienti- ja alusluotoista sekä "
        "korontasauksesta annetun valtioneuvoston asetukseen (1546 /2011) "
        "uusi 1 a seuraavasti:",
        statute_id="2011/1546",
    )

    assert result.parse_error is None
    assert result.surface_clause is not None
    node = result.surface_clause.verb_groups[0].nodes[0]
    assert node.witness is not None
    assert node.witness.rule_id == "fi.insertion_law_level_bare_section"


def test_parse_clause_provenance_then_ja_momentti_continuation_not_dropped() -> None:
    """A provenance/citation span between a target and a following ``ja N
    momentti`` continuation must not drop the continuation or the rest of the
    list.

    Regression for the live 1987/1176 ``muutetaan`` run::

        16 §:n 4 momentti, sellaisena kuin se on ... (249/76), ja 6 momentti,
        ..., 22 §:n ...

    The ``sellaisena kuin se on ... (NNN/YY)`` phrase collapses to a citation
    span tagged between the comma and the ``ja``.  The separator must skip that
    span and resume the same-section sub-ref continuation; previously it landed
    on the span, failed to parse the bare ``6 momentti``, and silently dropped
    everything after ``16 §``.
    """
    result = parse_clause(
        "muutetaan 16 §, sellaisena kuin se on annetussa asetuksessa (249/76), "
        "ja 6 momentti, 34 § seuraavasti:"
    )
    assert result.parse_error is None
    assert [op.code() for op in result.parsed_ops] == [
        "M P 16",
        "M P 16 6",
        "M P 34",
    ]


def test_parse_clause_provenance_then_bare_momentti_continuation_not_dropped() -> None:
    """Same as above but the continuation omits the ``ja`` conjunction:
    ``..., (249/76), 6 momentti, 34 §``.  The comma-led bare momentti must still
    resume against the prior section and the trailing ``34 §`` must survive.
    """
    result = parse_clause(
        "muutetaan 16 §, sellaisena kuin se on annetussa asetuksessa (249/76), "
        "6 momentti, 34 § seuraavasti:"
    )
    assert result.parse_error is None
    assert [op.code() for op in result.parsed_ops] == [
        "M P 16",
        "M P 16 6",
        "M P 34",
    ]


def test_parse_clause_comma_flanked_citation_span_keeps_following_section() -> None:
    """A citation span flanked by commas (``N momentti, [CITE], M §``) is a single
    logical separator: the second, trailing comma must be absorbed so the
    following section is not dropped.

    Regression for the live 1987/1176 ``kumotaan`` run, where the first
    sub-target's provenance citation is written ``..., sellaisena kuin se on ...
    (269/79), 37 §:n ...`` with commas on both sides of the collapsed span.
    """
    result = parse_clause(
        "kumotaan 16 §:n 5 ja 7 momentti, sellaisena kuin se on annetussa "
        "asetuksessa (269/79), 37 §:n 2 momentti, 45 § seuraavasti:"
    )
    assert result.parse_error is None
    assert [op.code() for op in result.parsed_ops] == [
        "K P 16 5",
        "K P 16 7",
        "K P 37 2",
        "K P 45",
    ]
