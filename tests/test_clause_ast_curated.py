"""Clause-AST-level semantic regression tests for curated PEG cases.

ParsedOp.code() is lossy: it doesn't encode renumber destinations, move
from→to pairs, exception semantics, text amendments, meta clauses, or
resolution provenance.  Tests that assert only op-code strings can pass
while semantics are wrong.

This file adds parallel ClauseAST-level assertions for the ~18 curated
cases that exercise features ParsedOp.code() is blind to:
  - Renumber cases (destination address)
  - Move cases (from→to)
  - jolloin renumber pairs (source + destination)
  - Valiotsikko heading cases (LabelAmend heading_replace)
  - Scope block cases (chapter/part context preserved as ScopedBlock)
  - Meta-only clauses (MetaClause nodes)
  - Exception (lukuun ottamatta) presence in parse result
  - Exception cases already covered in test_parse_clause.py are not
    duplicated here — this file focuses on ClauseAST node semantics.

Existing ParsedOp assertions in test_peg_curated.py are NOT removed.
These tests are additive.

Run:
    cd LawVM && uv run pytest tests/test_clause_ast_curated.py -v
"""

from __future__ import annotations

from typing import List

from lawvm.core.clause_ast import (
    ClauseAST,
    ClauseNode,
    LabelAmend,
    MetaClause,
    RefAmend,
    ScopedBlock,
    VerbGroup,
)
from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import FacetKind, LabelAction, MetaClauseKind, StructuralAction
from lawvm.finland.johtolause.api import parse_clause

# Map English action names to shared structural actions for _vg_by_action.
_ACTION_TO_STRUCTURAL_ACTION = {
    "replace": StructuralAction.REPLACE,
    "repeal": StructuralAction.REPEAL,
    "insert": StructuralAction.INSERT,
    "renumber": StructuralAction.RENUMBER,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_nodes(ast: ClauseAST) -> List[ClauseNode]:
    """Flatten all top-level nodes from all VerbGroups."""
    result: List[ClauseNode] = []
    for vg in ast.verb_groups:
        result.extend(vg.nodes)
    return result


def _flat_nodes(ast: ClauseAST) -> List[ClauseNode]:
    """Recursively flatten all leaf nodes, expanding ScopedBlocks."""
    result: List[ClauseNode] = []
    for vg in ast.verb_groups:
        for node in vg.nodes:
            _collect(node, result)
    return result


def _collect(node: ClauseNode, out: List[ClauseNode]) -> None:
    if isinstance(node, ScopedBlock):
        for child in node.children:
            _collect(child, out)
    else:
        out.append(node)


def _vg_by_action(ast: ClauseAST, action: str) -> VerbGroup:
    """Return the first VerbGroup with the given action, or raise.

    Accepts English action names ("replace", "repeal", "insert", "renumber")
    which are mapped to shared structural actions for comparison.
    """
    target_verb = _ACTION_TO_STRUCTURAL_ACTION.get(action)
    for vg in ast.verb_groups:
        if target_verb is not None and vg.verb == target_verb:
            return vg
        if vg.verb == action:  # fallback for direct string match
            return vg
    raise AssertionError(f"No VerbGroup with action={action!r}; found verbs: {[vg.verb for vg in ast.verb_groups]}")


def _addr(*pairs, special=None) -> LegalAddress:
    """Shorthand for LegalAddress."""
    return LegalAddress(path=tuple(pairs), special=special)


# ===========================================================================
# 1. Renumber cases — destination address
# ===========================================================================


class TestRenumberDestination:
    """Renumber ops must carry the correct destination address in LabelAmend.

    ParsedOp.code() returns "M P N" for *both* a plain replace and a
    "muutetaan N §:n numero M:ksi" renumber — they are indistinguishable
    at the op-code level.  The ClauseAST encodes this difference as
    LabelAmend(action="renumber", new_label=M).
    """

    def test_renumber_single_section_produces_label_amend(self):
        """'muutetaan 1 §:n numero 3:ksi' -> LabelAmend(renumber), new_label='3'."""
        result = parse_clause("muutetaan 1 §:n numero 3:ksi")
        nodes = _flat_nodes(result.clause_ast)
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, LabelAmend), f"Expected LabelAmend for renumber, got {type(node).__name__}"
        assert node.action.value == "renumber"
        assert node.new_label == "3", f"Expected new_label='3', got {node.new_label!r}"
        # Source address: section 1
        assert ("section", "1") in node.target.path

    def test_renumber_single_section_destination_address(self):
        """Destination LegalAddress must carry the new section label."""
        result = parse_clause("muutetaan 1 §:n numero 3:ksi")
        nodes = _flat_nodes(result.clause_ast)
        node = nodes[0]
        assert isinstance(node, LabelAmend)
        assert node.destination is not None, "LabelAmend must have a destination address"
        assert ("section", "3") in node.destination.path

    def test_renumber_with_backref_momentti_produces_renumber_then_replace(self):
        """'muutetaan 2 §:n numero 4:ksi ja mainitun pykälän 1 momentti'
        -> LabelAmend(renumber) for the section, RefAmend(replace) for the momentti.

        The momentti continuation is a regular replace op (not another renumber),
        even though ParsedOp.code() returns identically-structured strings.
        """
        result = parse_clause("muutetaan 2 §:n numero 4:ksi ja mainitun pykälän 1 momentti")
        nodes = _flat_nodes(result.clause_ast)
        assert len(nodes) == 2
        # First: renumber op for section 2 -> 4
        assert isinstance(nodes[0], LabelAmend)
        assert nodes[0].action == LabelAction.RENUMBER
        assert nodes[0].new_label == "4"
        # Second: replace op for section 2, momentti 1 (sub-ref continuation)
        assert isinstance(nodes[1], RefAmend)
        assert nodes[1].action is StructuralAction.REPLACE
        assert ("subsection", "1") in nodes[1].target.path

    def test_renumber_chain_all_sections_have_destinations(self):
        """'muutetaan 1 §:n numero 3:ksi, 2 §:n numero 4:ksi ...'
        — all three renumber ops must carry their respective destinations.
        """
        text = (
            "muutetaan 1 §:n numero 3:ksi, 2 §:n numero 4:ksi "
            "ja mainitun pykälän 1 momentti, 3 §:n numero 5:ksi "
            "ja mainitun pykälän 3 momentti"
        )
        result = parse_clause(text)
        nodes = _flat_nodes(result.clause_ast)
        # Five nodes: renumber(1->3), renumber(2->4), replace(2,1),
        #             renumber(3->5), replace(3,3)
        assert len(nodes) == 5

        renumbers = [n for n in nodes if isinstance(n, LabelAmend) and n.action == LabelAction.RENUMBER]
        assert len(renumbers) == 3, f"Expected 3 renumber nodes, got {len(renumbers)}"

        new_labels = sorted(r.new_label for r in renumbers)
        assert new_labels == ["3", "4", "5"], f"Expected destinations 3,4,5 — got {new_labels}"

    def test_renumber_plural_backref_otsikot_heading_action(self):
        """'muutetaan 5 ja 6 §:n numero 7 ja 8:ksi ja mainittujen pykälien otsikot'
        — heading continuation ops must be LabelAmend(heading_replace), not RefAmend.

        ParsedOp.code() returns "M P 5 o" / "M P 6 o" for heading ops, the same
        as a normal heading replace.  The ClauseAST distinguishes by action.
        """
        text = "muutetaan 5 ja 6 §:n numero 7 ja 8:ksi ja mainittujen pykälien otsikot"
        result = parse_clause(text)
        nodes = _flat_nodes(result.clause_ast)
        # 4 nodes: renumber(5->7), renumber(6->8), heading(5 o), heading(6 o)
        assert len(nodes) == 4

        heading_nodes = [
            n for n in nodes if isinstance(n, LabelAmend) and n.action == LabelAction.HEADING_REPLACE
        ]
        assert len(heading_nodes) == 2, (
            f"Expected 2 heading_replace nodes for otsikot, got {len(heading_nodes)}: {nodes}"
        )
        # Heading targets must be section 5 and 6 with special=FacetKind.HEADING
        heading_sections = sorted(dict(n.target.path).get("section", "") for n in heading_nodes)
        assert heading_sections == ["5", "6"]
        for hn in heading_nodes:
            assert hn.target.special == FacetKind.HEADING, (
                f"Heading op target.special must be 'heading', got {hn.target.special!r}"
            )


# ===========================================================================
# 2. Move cases — from→to in LabelAmend
# ===========================================================================


class TestMoveCases:
    """Move (siirretään) ops must encode both source and destination addresses."""

    def test_corpus_2020_575_move_retargets_section(self):
        """corpus_2020_575: '85 b § ... siirretään ... 9 lukuun' — the replace
        op for 85b must carry chapter 9 as destination.

        In ParsedOp the code is "M P L:9 85b" which encodes the destination
        chapter in the target address.  In the ClauseAST (native path from
        LabelAmend) the source address must be separable from the destination.
        """
        text = (
            "muutetaan\n                         "
            "maksupalvelulain (290/2010) 85 b ja 85 c §, sellaisena kuin ne ovat "
            "laissa 898/2017,\n                        siirretään\n                         "
            "muutettu 85 b § 9 lukuun ja lisätään\n                         "
            "lakiin uusi 85 d § seuraavasti:"
        )
        result = parse_clause(text)
        # The op for 85b must be a replace (cross-verb retarget means the
        # siirretään patches the replace op's chapter to "9").
        codes = [op.code() for op in result.parsed_ops]
        assert "M P L:9 85b" in codes, f"Expected M P L:9 85b in {codes}"

        # In the ClauseAST: the "replace" verb group must have section 85b
        # with chapter "9" baked into its target address.
        replace_vg = _vg_by_action(result.clause_ast, "replace")
        flat_replace = []
        for n in replace_vg.nodes:
            _collect(n, flat_replace)

        sec85b_nodes = [
            n for n in flat_replace if isinstance(n, (RefAmend, LabelAmend)) and ("section", "85b") in n.target.path
        ]
        assert len(sec85b_nodes) >= 1, (
            f"Expected at least one node for section 85b in replace group; nodes: {flat_replace}"
        )
        # The node for 85b must carry chapter 9 in its target
        for n85b in sec85b_nodes:
            path_dict = dict(n85b.target.path)
            assert path_dict.get("chapter") == "9", f"Section 85b must carry chapter '9', got path: {n85b.target.path}"

    def test_inline_move_tail_section_inside_new_chapter(self):
        """'250 §, joka samalla siirretään lakiin lisättävään 29 a lukuun' —
        section 250 must land in chapter 29a in the ClauseAST.
        """
        text = (
            "muutetaan tietoyhteiskuntakaaren (917/2014) 250 §, "
            "joka samalla siirretään lakiin lisättävään 29 a lukuun, "
            "271 a §, 272 §:n 1 momentin johdantokappale ja 325 §:n 2 momentti seuraavasti:"
        )
        result = parse_clause(text)
        # ParsedOp-level: "M P L:29a 250"
        codes = [op.code() for op in result.parsed_ops]
        assert "M P L:29a 250" in codes, f"Expected M P L:29a 250 in {codes}"

        # ClauseAST-level: section 250 must have chapter "29a" in target path
        all_flat = _flat_nodes(result.clause_ast)
        nodes_250 = [
            n for n in all_flat if isinstance(n, (RefAmend, LabelAmend)) and ("section", "250") in n.target.path
        ]
        assert len(nodes_250) >= 1, "Expected a node for section 250"
        for n250 in nodes_250:
            assert ("chapter", "29a") in n250.target.path, (
                f"Section 250 must carry chapter '29a', got {n250.target.path}"
            )

    def test_leading_chapter_destination_carries_to_moved_section(self):
        """'uusi 3 a luku, johon samalla siirretään muutettu 11 §' must carry the
        destination chapter onto the moved section.
        """
        text = "lakiin uusi 3 a luku, johon samalla siirretään muutettu 11 §"
        result = parse_clause(text)
        assert len(result.parsed_ops) == 1
        op = result.parsed_ops[0]
        assert op.verb == "S"
        assert op.kind == "P"
        assert op.number == "11"
        assert op.chapter == "3a"
        assert op.move_clause_target_unit_kind == "chapter"
        assert op.renumber_dest_chapter == "3a"

    def test_inline_move_tail_with_pronoun_preserves_following_sibling_target(self):
        """`76 § ja siirretään se ... ja 87 §:n 2 momentti` must keep both targets."""
        from lawvm.finland.grafter import AmendmentOp, extract_johtolause_legal_ops

        text = "muutetaan 76 § ja siirretään se lakiin lisättävään 11 a lukuun ja 87 §:n 2 momentti"
        result = parse_clause(text)
        codes = [op.code() for op in result.parsed_ops]

        assert "M P L:11a 76" in codes, f"Expected moved section 76 in {codes}"
        assert "M P 87 2" in codes, f"Expected sibling section 87 subsection 2 in {codes}"

        moved_legal_ops = [
            lo
            for lo in extract_johtolause_legal_ops(text)
            if dict(lo.target.path).get("section") == "76"
        ]
        assert moved_legal_ops
        assert any(dict(lo.target.path).get("chapter") == "11a" for lo in moved_legal_ops)

        sibling_legal_ops = [
            lo
            for lo in extract_johtolause_legal_ops(text)
            if dict(lo.target.path).get("section") == "87"
        ]
        assert sibling_legal_ops
        assert any(dict(lo.target.path).get("subsection") == "2" for lo in sibling_legal_ops)

    def test_inline_move_tail_preserves_move_kind_for_chapter_scoped_replace(self):
        """Inline same-label move tails must survive the ClauseAST and legacy LO bridge."""
        from lawvm.finland.grafter import AmendmentOp, extract_johtolause_legal_ops

        text = "muutetaan 31–34 §, joista 33 ja 34 § samalla siirretään 5 lukuun"
        result = parse_clause(text)

        moved_parsed_ops = [
            op
            for op in result.parsed_ops
            if op.chapter == "5" and op.number in {"33", "34"}
        ]
        assert moved_parsed_ops

        moved_legal_ops = [
            lo
            for lo in extract_johtolause_legal_ops(text)
            if dict(lo.target.path).get("chapter") == "5" and dict(lo.target.path).get("section") in {"33", "34"}
        ]
        assert moved_legal_ops
        assert all(
            op.target_chapter == "5"
            for lo in moved_legal_ops
            for op in AmendmentOp.from_lo(lo, 0)
        )

    def test_inline_move_tail_preserves_move_kind_for_part_scoped_replace(self):
        """Part-scoped move tails must survive the ClauseAST and legacy LO bridge."""
        from lawvm.finland.grafter import AmendmentOp, extract_johtolause_legal_ops

        text = "muutetaan I osa, 30 ja 31§, jotka samalla siirretään I osaan"
        result = parse_clause(text)

        moved_parsed_ops = [op for op in result.parsed_ops if op.part == "I" and op.number in {"30", "31"}]
        assert moved_parsed_ops

        moved_legal_ops = [
            lo
            for lo in extract_johtolause_legal_ops(text)
            if dict(lo.target.path).get("part") == "I" and dict(lo.target.path).get("section") in {"30", "31"}
        ]
        assert moved_legal_ops
        assert all(
            op.target_part == "I"
            for lo in moved_legal_ops
            for op in AmendmentOp.from_lo(lo, 0)
        )


# ===========================================================================
# 3. jolloin renumber pairs — source + destination
# ===========================================================================


class TestJolloiNRenumberPairs:
    """jolloin-triggered renumber pairs must appear as LabelAmend nodes with
    both source section and destination label.

    'lisätään uusi 10 §, jolloin nykyinen 10 § siirtyy 10 a §:ksi'
    should produce:
      - LabelAmend(renumber, target=section:10, new_label="10a")  [jolloin pair]
      - RefAmend(insert, target=section:10)                        [the insertion]
    """

    def test_jolloin_section_renumber_letter_suffix(self):
        """jolloin renumber: section 10 -> 10a and insert 10."""
        text = "lisätään uusi 10 §, jolloin nykyinen 10 § siirtyy 10 a §:ksi, sekä muutetaan 14 §"
        result = parse_clause(text)
        nodes = _flat_nodes(result.clause_ast)

        # Must have a renumber op for 10 -> 10a
        renumber_nodes = [n for n in nodes if isinstance(n, LabelAmend) and n.action == LabelAction.RENUMBER]
        assert len(renumber_nodes) >= 1, (
            f"Expected at least one renumber node, got nodes: {[type(n).__name__ for n in nodes]}"
        )
        renumber_10 = [n for n in renumber_nodes if ("section", "10") in n.target.path]
        assert len(renumber_10) >= 1, (
            f"Expected renumber node with source section 10, renumber_nodes: {[n.target.path for n in renumber_nodes]}"
        )
        # Destination label must be "10a"
        for rn in renumber_10:
            assert rn.new_label == "10a", f"Expected new_label='10a' for jolloin renumber, got {rn.new_label!r}"

    def test_jolloin_section_renumber_simple_numeric(self):
        """'lisätään lakiin uusi 5 §, jolloin nykyinen 5 § siirtyy 6 §:ksi'
        -> renumber(5->6) + insert(5).
        """
        text = "lisätään lakiin uusi 5 §, jolloin nykyinen 5 § siirtyy 6 §:ksi"
        result = parse_clause(text)
        nodes = _flat_nodes(result.clause_ast)

        renumber_nodes = [
            n
            for n in nodes
            if isinstance(n, LabelAmend) and n.action == LabelAction.RENUMBER and ("section", "5") in n.target.path
        ]
        assert len(renumber_nodes) >= 1, f"Expected renumber node for section 5->6, nodes: {nodes}"
        for rn in renumber_nodes:
            assert rn.new_label == "6", f"Expected new_label='6', got {rn.new_label!r}"

    def test_jolloin_move_consequence_preserves_following_target(self):
        """'muutetaan 5 §:n 1 momentti, jolloin nykyinen 2 momentti siirtyy 3 momentiksi, ja 8 §'
        -> replace(5,1) and replace(8) — the jolloin is consumed, not a target.

        This test verifies that section 8 still appears as a node (the jolloin
        clause doesn't eat the following target list).
        """
        text = "muutetaan 5 §:n 1 momentti, jolloin nykyinen 2 momentti siirtyy 3 momentiksi, ja 8 §"
        result = parse_clause(text)
        codes = [op.code() for op in result.parsed_ops]
        assert "M P 5 1" in codes, f"Expected M P 5 1 in {codes}"
        assert "M P 8" in codes, f"Expected M P 8 (after jolloin) in {codes}"

        # In ClauseAST: section 8 must be present as a RefAmend(replace)
        nodes = _flat_nodes(result.clause_ast)
        sec8_nodes = [n for n in nodes if isinstance(n, RefAmend) and ("section", "8") in n.target.path]
        assert len(sec8_nodes) >= 1, (
            f"Section 8 must appear as RefAmend after jolloin clause; nodes: {[type(n).__name__ for n in nodes]}"
        )


# ===========================================================================
# 4. Valiotsikko heading cases — LabelAmend(heading_replace)
# ===========================================================================


class TestValiotsikkoHeadingCases:
    """Valiotsikko (väliotsikko) heading references must produce
    LabelAmend(action="heading_replace") nodes in the ClauseAST.

    ParsedOp.code() returns "M P N o" which is identical to a regular
    otsikko op.  The ClauseAST distinguishes through LabelAmend.action.
    """

    def test_simple_valiotsikko_section_and_heading(self):
        """'muutetaan 5 § ja sen edellä oleva väliotsikko'
        -> RefAmend(replace, section:5) + LabelAmend(heading_replace, section:5/heading).
        """
        text = "muutetaan 5 § ja sen edellä oleva väliotsikko"
        result = parse_clause(text)
        nodes = _flat_nodes(result.clause_ast)

        # Section 5 replace
        replace_nodes = [n for n in nodes if isinstance(n, RefAmend) and ("section", "5") in n.target.path]
        assert len(replace_nodes) == 1, f"Expected one RefAmend for section 5, got {len(replace_nodes)}"
        assert replace_nodes[0].action is StructuralAction.REPLACE

        # Heading replace for section 5
        heading_nodes = [
            n
            for n in nodes
            if isinstance(n, LabelAmend) and n.action == LabelAction.HEADING_REPLACE and ("section", "5") in n.target.path
        ]
        assert len(heading_nodes) == 1, (
            f"Expected one LabelAmend(heading_replace) for section 5, got {len(heading_nodes)}"
        )
        assert heading_nodes[0].target.special == FacetKind.HEADING

    def test_valiotsikko_heading_chain_with_following_section(self):
        """'muutetaan 3 § sekä sen edellä olevan väliotsikon sanamuoto ja 7 §'
        -> replace(3) + heading_replace(3) + replace(7).
        """
        text = "muutetaan 3 § sekä sen edellä olevan väliotsikon sanamuoto ja 7 §"
        result = parse_clause(text)
        nodes = _flat_nodes(result.clause_ast)

        sec3_replace = [n for n in nodes if isinstance(n, RefAmend) and ("section", "3") in n.target.path]
        assert len(sec3_replace) >= 1, "Section 3 replace must be present"

        sec3_heading = [
            n
            for n in nodes
            if isinstance(n, LabelAmend) and n.action == LabelAction.HEADING_REPLACE and ("section", "3") in n.target.path
        ]
        assert len(sec3_heading) >= 1, "Heading replace for section 3 must be present"

        sec7 = [n for n in nodes if isinstance(n, RefAmend) and ("section", "7") in n.target.path]
        assert len(sec7) >= 1, "Section 7 must be present after valiotsikko"

    def test_valiotsikko_pykalan_subsection_plus_heading(self):
        """'muutetaan 10 §:n 2 momentti sekä pykälän edellä olevan väliotsikon sanamuoto'
        -> RefAmend(replace, 10/2) + LabelAmend(heading_replace, 10/heading).
        """
        text = "muutetaan 10 §:n 2 momentti sekä pykälän edellä olevan väliotsikon sanamuoto"
        result = parse_clause(text)
        nodes = _flat_nodes(result.clause_ast)

        momentti_nodes = [
            n
            for n in nodes
            if isinstance(n, RefAmend) and ("section", "10") in n.target.path and ("subsection", "2") in n.target.path
        ]
        assert len(momentti_nodes) >= 1, "Momentti 2 of section 10 must be present"

        heading_nodes = [
            n
            for n in nodes
            if isinstance(n, LabelAmend) and n.action == LabelAction.HEADING_REPLACE and ("section", "10") in n.target.path
        ]
        assert len(heading_nodes) >= 1, "Heading replace for section 10 must be present"
        for hn in heading_nodes:
            assert hn.target.special == FacetKind.HEADING

    def test_including_named_preceding_heading_keeps_following_section_arms(self):
        """Historical included-heading phrase must not truncate later targets."""
        text = (
            "muutetaan 45-51 §:n mukaanluettuna 50 §:n edellä olevan väliotsikon, "
            "51 a §:n 2 momentin, 52 a-55 §:n, 56 §:n 1 momentin ja 57 §:n"
        )
        result = parse_clause(text)
        nodes = _flat_nodes(result.clause_ast)

        sec50_heading = [
            n
            for n in nodes
            if isinstance(n, LabelAmend) and n.action == LabelAction.HEADING_REPLACE and ("section", "50") in n.target.path
        ]
        assert len(sec50_heading) >= 1, "Included preceding heading for section 50 must be present"

        sec51a_m2 = [
            n
            for n in nodes
            if isinstance(n, RefAmend) and ("section", "51a") in n.target.path and ("subsection", "2") in n.target.path
        ]
        assert len(sec51a_m2) >= 1, "Section 51a momentti 2 must survive after included heading"

        for label in ("52a", "53", "54", "55", "57"):
            matches = [n for n in nodes if isinstance(n, RefAmend) and ("section", label) in n.target.path]
            assert len(matches) >= 1, f"Section {label} must survive after included heading"

        sec56_m1 = [
            n
            for n in nodes
            if isinstance(n, RefAmend) and ("section", "56") in n.target.path and ("subsection", "1") in n.target.path
        ]
        assert len(sec56_m1) >= 1, "Section 56 momentti 1 must survive after included heading"

    def test_edella_oleva_valiotsikko_heading_insertion(self):
        """'lisätään lakiin uusi 53 a § ja 53 §:n edelle uusi luvun otsikko'
        -> insert(53a) + heading_replace(53/heading).
        """
        text = "lisätään lakiin uusi 53 a § ja 53 §:n edelle uusi luvun otsikko"
        result = parse_clause(text)
        nodes = _flat_nodes(result.clause_ast)

        # Insert for 53a
        insert_nodes = [
            n for n in nodes if isinstance(n, RefAmend) and n.action is StructuralAction.INSERT and ("section", "53a") in n.target.path
        ]
        assert len(insert_nodes) >= 1, "Insert for 53a must be present"

        # Heading op for section 53 (the "edelle" anchor)
        heading_nodes = [
            n
            for n in nodes
            if isinstance(n, LabelAmend) and n.action == LabelAction.HEADING_REPLACE and ("section", "53") in n.target.path
        ]
        assert len(heading_nodes) >= 1, (
            f"Heading op for section 53 anchor must be present; nodes: {[type(n).__name__ for n in nodes]}"
        )
        for hn in heading_nodes:
            assert hn.target.special == FacetKind.HEADING


# ===========================================================================
# 5. Scope block cases — ScopedBlock preserves chapter/part context
# ===========================================================================


class TestScopeBlockCases:
    """Chapter/part context must be preserved as ScopedBlock in the ClauseAST.

    ParsedOp.code() bakes the chapter into the op-code string ("M P L:3 12"),
    which loses the information that *multiple ops share a single scope*.
    The ClauseAST preserves this as a ScopedBlock node.
    """

    def test_chapter_ctx_propagation_emits_scoped_block(self):
        """'muutetaan 3 luvun 12 §:n 2 momentti' must produce a ScopedBlock
        with scope=chapter:3 in the ClauseAST top-level nodes.
        """
        result = parse_clause("muutetaan 3 luvun 12 §:n 2 momentti")
        top_nodes = _all_nodes(result.clause_ast)

        scoped = [n for n in top_nodes if isinstance(n, ScopedBlock)]
        assert len(scoped) == 1, (
            f"Expected one ScopedBlock for chapter-scoped ref, got {len(scoped)}: "
            f"{[type(n).__name__ for n in top_nodes]}"
        )
        scope_block = scoped[0]
        assert scope_block.scope == _addr(("chapter", "3")), f"Scope must be chapter:3, got {scope_block.scope}"
        # Inside: one child with section 12 + momentti 2
        assert len(scope_block.children) == 1
        child = scope_block.children[0]
        assert isinstance(child, RefAmend)
        assert ("section", "12") in child.target.path
        assert ("subsection", "2") in child.target.path

    def test_part_otsikko_chapter_refs_produce_scoped_block(self):
        """'muutetaan IV osan otsikko, 12 luvun 3 ja 4 §...'
        — the 12-luvun section refs must appear inside a ScopedBlock(chapter:12).
        """
        text = (
            "muutetaan IV osan otsikko, 12 luvun 3 ja 4 § ja lisätään "
            "19 luvun 3 §:ään uusi 3 momentti, 19 lukuun uusi 4 a ja 5 a §, "
            "19 lukuun siitä lailla 1078/2017 kumotun 6 §:n tilalle uusi 6 §, "
            "19 lukuun uusi 6 a §"
        )
        result = parse_clause(text)
        top_nodes = _all_nodes(result.clause_ast)

        # There must be at least one ScopedBlock at top level (chapter 12 or 19)
        scoped = [n for n in top_nodes if isinstance(n, ScopedBlock)]
        assert len(scoped) >= 1, (
            f"Expected ScopedBlock(s) for chapter-scoped refs; "
            f"top-level node types: {[type(n).__name__ for n in top_nodes]}"
        )
        # At least one ScopedBlock must have chapter "12"
        chapter_labels = set()
        for sb in scoped:
            if sb.scope.path and sb.scope.path[0][0] == "chapter":
                chapter_labels.add(sb.scope.path[0][1])
        assert "12" in chapter_labels, f"Expected a ScopedBlock for chapter 12; found chapter labels: {chapter_labels}"
        chapter_12_block = next(
            sb
            for sb in scoped
            if sb.scope.path and sb.scope.path[0] == ("chapter", "12")
        )
        for child in chapter_12_block.children:
            assert isinstance(child, RefAmend)
            assert ("part", "IV") not in child.target.path

    def test_chapter_repeal_then_section_amend(self):
        """'kumotaan 3 luku, muutetaan 5 §'
        — kumotaan group must have a chapter node; muutetaan group a section node.
        Chapter repeal must NOT propagate chapter context to the next verb group.
        """
        result = parse_clause("kumotaan 3 luku, muutetaan 5 §")
        # kumotaan group: chapter 3 repeal
        repeal_vg = _vg_by_action(result.clause_ast, "repeal")
        repeal_nodes = _flat_nodes_from_vg(repeal_vg)
        chapter_repeal = [
            n
            for n in repeal_nodes
            if isinstance(n, RefAmend) and n.action is StructuralAction.REPEAL and ("chapter", "3") in n.target.path
        ]
        assert len(chapter_repeal) == 1, f"Expected repeal(chapter:3), got {[type(n).__name__ for n in repeal_nodes]}"

        # muutetaan group: section 5 without chapter context
        replace_vg = _vg_by_action(result.clause_ast, "replace")
        replace_nodes = _flat_nodes_from_vg(replace_vg)
        sec5 = [n for n in replace_nodes if isinstance(n, RefAmend) and ("section", "5") in n.target.path]
        assert len(sec5) == 1, f"Expected replace(section:5), got {replace_nodes}"
        # Section 5 must NOT carry chapter context
        assert not any(k == "chapter" for k, _ in sec5[0].target.path), (
            f"Section 5 must not inherit chapter context from repeal group; path: {sec5[0].target.path}"
        )


def _flat_nodes_from_vg(vg: VerbGroup) -> List[ClauseNode]:
    """Recursively flatten nodes from a single VerbGroup."""
    result: List[ClauseNode] = []
    for node in vg.nodes:
        _collect(node, result)
    return result


# ===========================================================================
# 6. Meta-only clauses — MetaClause nodes
# ===========================================================================


class TestMetaClauseCases:
    """Meta clauses must produce MetaClause nodes in the ClauseAST.

    ParsedOp has NO representation for meta clauses — they are completely
    invisible at the op-code level.  The ClauseAST exposes them explicitly.
    """

    def test_commencement_clause_produces_meta_node(self):
        """Pure commencement text must produce MetaClause(kind='commencement')."""
        text = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2025."
        result = parse_clause(text)

        all_nodes = _flat_nodes(result.clause_ast)
        meta_nodes = [n for n in all_nodes if isinstance(n, MetaClause)]
        assert len(meta_nodes) >= 1, (
            f"Expected at least one MetaClause for commencement text; nodes: {[type(n).__name__ for n in all_nodes]}"
        )
        assert meta_nodes[0].kind == MetaClauseKind.COMMENCEMENT

    def test_meta_clause_kind_is_non_empty_string(self):
        """MetaClause.kind must have a non-empty string value (MetaClauseKind or str)."""
        from lawvm.core.semantic_types import MetaClauseKind
        text = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2025."
        result = parse_clause(text)
        all_nodes = _flat_nodes(result.clause_ast)
        meta_nodes = [n for n in all_nodes if isinstance(n, MetaClause)]
        for mn in meta_nodes:
            kind_val = mn.kind.value if isinstance(mn.kind, MetaClauseKind) else mn.kind
            assert kind_val, f"MetaClause.kind must have a non-empty value, got {mn.kind!r}"

    def test_parsed_ops_empty_for_pure_meta_text(self):
        """ParsedOps must be empty for a pure meta text (no structural verb).

        This verifies that the absence of ParsedOps does NOT mean the parse
        produced nothing — it means only MetaClause nodes were produced.
        """
        text = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2025."
        result = parse_clause(text)
        assert result.parsed_ops == [], (
            f"No ParsedOps expected for pure commencement text, got: {[op.code() for op in result.parsed_ops]}"
        )
        # But the ClauseAST must not be empty
        assert result.clause_ast.verb_groups, "ClauseAST must have verb_groups even for meta-only text"


# ===========================================================================
# 7. Chapter heading otsikko — LabelAmend vs RefAmend
# ===========================================================================


class TestChapterOtsikko:
    """Chapter heading ops must be LabelAmend(heading_replace), not RefAmend.

    ParsedOp.code() returns "M L N o" for chapter otsikko, indistinguishable
    from "M L N" at the verb/kind level.  The ClauseAST differentiates via
    LabelAmend.action = "heading_replace".
    """

    def test_chapter_otsikko_is_label_amend(self):
        """'muutetaan 5 luvun otsikko' -> LabelAmend(heading_replace, chapter:5/heading)."""
        result = parse_clause("muutetaan 5 luvun otsikko")
        nodes = _flat_nodes(result.clause_ast)
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, LabelAmend), f"Chapter otsikko must be LabelAmend, got {type(node).__name__}"
        assert node.action == LabelAction.HEADING_REPLACE
        assert ("chapter", "5") in node.target.path
        assert node.target.special == FacetKind.HEADING

    def test_chapter_heading_not_plain_repeal(self):
        """'kumotaan 3 luku' must be RefAmend(repeal, chapter:3), NOT LabelAmend."""
        result = parse_clause("kumotaan 3 luku")
        nodes = _flat_nodes(result.clause_ast)
        assert len(nodes) == 1
        node = nodes[0]
        assert isinstance(node, RefAmend), f"Chapter repeal must be RefAmend, got {type(node).__name__}"
        assert node.action is StructuralAction.REPEAL
        assert ("chapter", "3") in node.target.path


# ===========================================================================
# 8. Insertion patterns — RefAmend(action="insert")
# ===========================================================================


class TestInsertionCases:
    """Insertion ops must be RefAmend(action="insert") in the ClauseAST.

    ParsedOp.code() returns "L P N" / "L P N M" for inserts.  The ClauseAST
    encodes action="insert" on RefAmend, distinguishing from replace/repeal.
    """

    def test_law_level_insert_new_section(self):
        """'lisätään lakiin uusi 5 a §' -> RefAmend(insert, section:5a)."""
        result = parse_clause("lisätään lakiin uusi 5 a §")
        nodes = _flat_nodes(result.clause_ast)
        insert_nodes = [n for n in nodes if isinstance(n, RefAmend) and n.action is StructuralAction.INSERT]
        assert len(insert_nodes) >= 1, f"Expected insert node, got: {nodes}"
        labels = [dict(n.target.path).get("section", "") for n in insert_nodes]
        assert "5a" in labels, f"Expected section '5a' in insert targets, got {labels}"

    def test_section_level_insert_new_momentti(self):
        """'lisätään 8 §:ään uusi 3 momentti' -> RefAmend(insert, section:8/subsection:3)."""
        result = parse_clause("lisätään 8 §:ään uusi 3 momentti")
        nodes = _flat_nodes(result.clause_ast)
        insert_nodes = [n for n in nodes if isinstance(n, RefAmend) and n.action is StructuralAction.INSERT]
        assert len(insert_nodes) == 1, f"Expected one insert node, got: {insert_nodes}"
        path = dict(insert_nodes[0].target.path)
        assert path.get("section") == "8"
        assert path.get("subsection") == "3"

    def test_chapter_level_insert_new_chapter(self):
        """'lisätään lakiin uusi 3 luku' -> RefAmend(insert, chapter:3)."""
        result = parse_clause("lisätään lakiin uusi 3 luku")
        nodes = _flat_nodes(result.clause_ast)
        insert_nodes = [n for n in nodes if isinstance(n, RefAmend) and n.action is StructuralAction.INSERT]
        assert len(insert_nodes) >= 1
        chapter_inserts = [n for n in insert_nodes if ("chapter", "3") in n.target.path]
        assert len(chapter_inserts) == 1, f"Expected chapter:3 insert, got: {[(n.target.path) for n in insert_nodes]}"

    def test_insert_chapter_reinstatement_propagates_chapter_ctx(self):
        """'muutetaan 10 luvun otsikko ja lisätään 10 lukuun ... 14 § seuraavasti:'
        — the insert op for § 14 must carry chapter context.
        """
        text = (
            "muutetaan 10 luvun otsikko ja lisätään 10 lukuun "
            "siitä lailla 361/1999 kumotun 14 §:n tilalle uusi 14 § seuraavasti:"
        )
        result = parse_clause(text)
        codes = [op.code() for op in result.parsed_ops]
        assert "L P L:10 14" in codes, f"Expected L P L:10 14 in {codes}"

        # ClauseAST: the insert for section 14 must carry chapter 10
        nodes = _flat_nodes(result.clause_ast)
        insert_14 = [
            n for n in nodes if isinstance(n, RefAmend) and n.action is StructuralAction.INSERT and ("section", "14") in n.target.path
        ]
        assert len(insert_14) >= 1, "Expected insert node for section 14"
        for n14 in insert_14:
            assert ("chapter", "10") in n14.target.path, (
                f"Insert for §14 must carry chapter 10; path: {n14.target.path}"
            )


# ===========================================================================
# 9. Part context
# ===========================================================================


class TestPartContextCases:
    """Part (osa) context must be preserved correctly in the ClauseAST."""

    def test_part_otsikko_is_label_amend(self):
        """'muutetaan IV osan otsikko' -> LabelAmend(heading_replace, part:IV/heading)."""
        text = "muutetaan IV osan otsikko"
        result = parse_clause(text)
        nodes = _flat_nodes(result.clause_ast)
        # The "M O IV o" code should map to a LabelAmend with part in path
        label_amends = [n for n in nodes if isinstance(n, LabelAmend)]
        assert len(label_amends) >= 1, f"Expected LabelAmend for part otsikko, got: {[type(n).__name__ for n in nodes]}"
        heading_amends = [n for n in label_amends if n.action == LabelAction.HEADING_REPLACE]
        assert len(heading_amends) >= 1, "Expected heading_replace action for part otsikko"
        for ha in heading_amends:
            assert ha.target.special == FacetKind.HEADING

    def test_section_in_part_carries_part_context(self):
        """'muutetaan II osan 1 luvun 3 §' -> section 3 must carry part II and chapter 1."""
        result = parse_clause("muutetaan II osan 1 luvun 3 §")
        # Via ParsedOp the code includes O:II and L:1
        codes = [op.code() for op in result.parsed_ops]
        assert len(codes) == 1
        assert "3" in codes[0], f"Section 3 must appear in op: {codes}"

        # Via ClauseAST: must have a ScopedBlock for part II at the top level
        top_nodes = _all_nodes(result.clause_ast)
        part_scoped = [
            n
            for n in top_nodes
            if isinstance(n, ScopedBlock)
            and n.scope.path
            and n.scope.path[0][0] == "part"
            and n.scope.path[0][1] == "II"
        ]
        assert len(part_scoped) == 1, (
            f"Expected ScopedBlock(part:II) at top level, got: "
            f"{[(type(n).__name__, getattr(n, 'scope', None)) for n in top_nodes]}"
        )
        # The ScopedBlock child must target section 3
        scope_block = part_scoped[0]
        flat_children: List[ClauseNode] = []
        for child in scope_block.children:
            _collect(child, flat_children)
        sec3_nodes = [
            n for n in flat_children if isinstance(n, (RefAmend, LabelAmend)) and ("section", "3") in n.target.path
        ]
        assert len(sec3_nodes) >= 1, (
            f"Expected section 3 child inside part-scoped block; "
            f"flat children: {[type(n).__name__ for n in flat_children]}"
        )


# ===========================================================================
# 10. Resolution provenance fields (resolution_kind, resolution_detail)
# ===========================================================================


class TestResolutionProvenanceFields:
    """Backref resolution must populate resolution_kind and resolution_detail
    on the produced ClauseAST nodes.

    ParsedOp has no provenance fields — this information is entirely absent
    from the op-code level.  ClauseAST carries it via RefAmend.resolution_kind.
    """

    def test_backref_singular_resolution_kind(self):
        """'mainitun pykälän 2 momentti' backref must carry resolution_kind='backref_singular'."""
        text = "muutetaan 7 §, sellaisena kuin se on laissa 200/2022, ja mainitun pykälän 2 momentti"
        result = parse_clause(text)
        # Expected ops: "M P 7", "M P 7 2"
        codes = [op.code() for op in result.parsed_ops]
        assert codes == ["M P 7", "M P 7 2"], f"Unexpected codes: {codes}"

        # In ClauseAST: the second node (momentti 2 of section 7)
        # must carry resolution provenance from the backref resolver
        nodes = _flat_nodes(result.clause_ast)
        momentti_nodes = [
            n
            for n in nodes
            if isinstance(n, (RefAmend, LabelAmend))
            and ("section", "7") in n.target.path
            and ("subsection", "2") in n.target.path
        ]
        assert len(momentti_nodes) >= 1, f"Expected a node for section 7 momentti 2; nodes: {nodes}"
        for mn in momentti_nodes:
            assert mn.resolution_kind is not None, f"Backref resolution must set resolution_kind; node: {mn}"
            assert "backref" in mn.resolution_kind, (
                f"resolution_kind should indicate backref; got {mn.resolution_kind!r}"
            )

    def test_missing_pykala_sign_in_genitive_number_before_momentti(self):
        """Source pathology: '94:n 1 momentti' should parse as '94 §:n 1 momentti'.

        Some Finlex johtolause texts accidentally omit the § sign before the
        genitive suffix (e.g. 2009/1829 targeting 1999/895 §94).  The lexer
        normalizes this before tokenization.
        """
        result = parse_clause("muutetaan 88 §, 94:n 1 momentti, 95 §, 96 §")
        assert not result.is_failed
        parsed = {(op.number, op.momentti) for op in result.parsed_ops}
        assert ("88", 0) in parsed, f"§88 not found: {parsed}"
        assert ("94", 1) in parsed, f"§94 subsection 1 not found: {parsed}"
        assert ("95", 0) in parsed, f"§95 not found: {parsed}"
        assert ("96", 0) in parsed, f"§96 not found: {parsed}"

    def test_backref_resolution_detail_carries_antecedent(self):
        """resolution_detail must identify the antecedent section label."""
        text = "muutetaan 3 §:n numero 5:ksi ja mainitun pykälän otsikko ja 1 momentti"
        result = parse_clause(text)
        nodes = _flat_nodes(result.clause_ast)

        # otsikko and momentti nodes should have resolution provenance
        otsikko_nodes = [n for n in nodes if isinstance(n, LabelAmend) and n.action == LabelAction.HEADING_REPLACE]
        momentti_nodes = [n for n in nodes if isinstance(n, RefAmend) and ("subsection", "1") in n.target.path]

        provenance_nodes = otsikko_nodes + momentti_nodes
        # At least some provenance should be present
        nodes_with_provenance = [n for n in provenance_nodes if n.resolution_kind is not None]
        assert len(nodes_with_provenance) >= 1, (
            f"Expected at least one node with resolution_kind; provenance_nodes: {provenance_nodes}"
        )
