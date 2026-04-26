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
from lawvm.finland.johtolause.compat import ClauseParseResult, parse_clause
from lawvm.finland.ops import lo_scope_confidence
from lawvm.finland.johtolause.surface_model import (
    ScopeKind,
    TargetKind,
    SurfaceTargetRef,
    SurfaceScopeBlock,
    SurfaceDescendantCoordination,
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
# Import from peg3 facade
# ---------------------------------------------------------------------------


def test_parse_clause_importable_from_peg3():
    """parse_clause and ClauseParseResult must be importable from peg3."""
    from lawvm.finland.johtolause.peg3 import ClauseParseResult as CPR, parse_clause as pc

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
    from lawvm.finland.grafter import extract_johtolause_legal_ops

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


def test_parse_clause_stripped_alakohta_tail_does_not_block_later_section_targets() -> None:
    """Qualifier stripping must not leave ``ja sekä`` residue that truncates the list.

    Regression for 2017/444 <- 2023/444: after stripping ``i alakohta`` from
    ``11 kohdan johdantokappale ja i alakohta sekä 19 kohta``, the parser used
    to stop at ``11 kohdan johdantokappale`` and drop the later ``19 kohta``,
    ``3 luvun 10 §:n 1 momentti`` and ``13 §:n 3 ja 4 momentti`` targets.
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
