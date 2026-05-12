"""Tests for ClauseAST construction from ParsedOp lists.

Covers:
  - Simple single-verb case
  - Multi-verb case
  - Scoped case (chapter context → ScopedBlock)
  - Renumber (verb=S → LabelAmend)
  - extract_ops_diagnostic emits clause_ast field
  - Typed enum accessors (Pro #16 Step 3)
"""

from __future__ import annotations

from dataclasses import fields
import pytest

from lawvm.core.clause_ast import (
    CLAUSE_AST_UNSUPPORTED_GENERIC_LOWERING_KIND,
    CLAUSE_AST_UNSUPPORTED_GENERIC_LOWERING_RULE_ID,
    ClauseAST,
    ItemShiftClause,
    LabelAmend,
    MetaClause,
    NamedRowClause,
    RefAmend,
    ScopedBlock,
    TextAmend,
    VerbGroup,
    clause_ast_to_legal_ops_with_diagnostics,
    clause_node_to_legal_operation,
)
from lawvm.core.ir import LegalAddress, LegalOperation, TextPatchSpec, TextSelector
from lawvm.core.semantic_types import FacetKind, LabelAction, MetaClauseKind, StructuralAction, TextPatchKindEnum
from lawvm.finland.johtolause.api import infer_move_clause_target_unit_kind
from lawvm.finland.johtolause.parsed_op_clause_ast import build_clause_ast, parsed_op_to_clause_node
from lawvm.finland.johtolause.types import ParsedOp
from lawvm.finland.johtolause.peg3 import extract_ops_diagnostic
from typing import Optional


def _op(
    verb: str,
    kind: str,
    number: str,
    chapter: str = "",
    momentti: int = 0,
    item: str = "",
    facet: Optional[FacetKind] = None,
    part: str = "",
    special: str = "",
) -> ParsedOp:
    """Construct a minimal ParsedOp for testing."""
    return ParsedOp(
        verb=verb,
        kind=kind,
        chapter=chapter,
        number=number,
        momentti=momentti,
        item=item,
        facet=facet,
        raw="",
        part=part,
        special=special,
    )


# ---------------------------------------------------------------------------
# Test 1: single-verb, single-section → one VerbGroup, one RefAmend
# ---------------------------------------------------------------------------


def test_single_verb_single_section():
    """muutetaan 6 § → ClauseAST with one VerbGroup(replace), one RefAmend."""
    ops = [_op("M", "P", "6")]
    ast = build_clause_ast(ops, "muutetaan 6 §")

    assert isinstance(ast, ClauseAST)
    assert ast.source_text == "muutetaan 6 §"
    assert len(ast.verb_groups) == 1

    vg = ast.verb_groups[0]
    assert isinstance(vg, VerbGroup)
    assert vg.verb == StructuralAction.REPLACE
    assert isinstance(vg.verb, StructuralAction)
    assert len(vg.nodes) == 1

    node = vg.nodes[0]
    assert isinstance(node, RefAmend)
    assert node.action == StructuralAction.REPLACE
    assert node.target == LegalAddress(path=(("section", "6"),))


# ---------------------------------------------------------------------------
# Test 2: multi-verb → two VerbGroups in order
# ---------------------------------------------------------------------------


def test_multi_verb():
    """kumotaan 16 §, muutetaan 6 § → two VerbGroups (repeal, replace)."""
    ops = [_op("K", "P", "16"), _op("M", "P", "6")]
    ast = build_clause_ast(ops, "kumotaan 16 §, muutetaan 6 §")

    assert len(ast.verb_groups) == 2

    vg_repeal = ast.verb_groups[0]
    assert vg_repeal.verb == StructuralAction.REPEAL
    assert len(vg_repeal.nodes) == 1
    repeal_node = vg_repeal.nodes[0]
    assert isinstance(repeal_node, RefAmend)
    assert repeal_node.action == StructuralAction.REPEAL
    assert repeal_node.target == LegalAddress(path=(("section", "16"),))

    vg_replace = ast.verb_groups[1]
    assert vg_replace.verb == StructuralAction.REPLACE
    assert len(vg_replace.nodes) == 1
    replace_node = vg_replace.nodes[0]
    assert isinstance(replace_node, RefAmend)
    assert replace_node.action == StructuralAction.REPLACE
    assert replace_node.target == LegalAddress(path=(("section", "6"),))


def test_repeated_verbs_stay_in_separate_runs():
    """Interleaved repeated verbs should produce separate VerbGroups."""
    ops = [_op("M", "P", "1"), _op("K", "P", "2"), _op("M", "P", "3")]
    ast = build_clause_ast(ops, "muutetaan 1 §, kumotaan 2 §, muutetaan 3 §")

    assert [vg.verb for vg in ast.verb_groups] == [
        StructuralAction.REPLACE,
        StructuralAction.REPEAL,
        StructuralAction.REPLACE,
    ]
    assert [len(vg.nodes) for vg in ast.verb_groups] == [1, 1, 1]


# ---------------------------------------------------------------------------
# Test 3: scoped case — ops sharing a chapter context → ScopedBlock
# ---------------------------------------------------------------------------


def test_scoped_block_same_chapter():
    """Two ops in chapter 2 → ScopedBlock wrapping both children."""
    ops = [
        _op("M", "P", "3", chapter="2"),
        _op("M", "P", "4", chapter="2"),
    ]
    ast = build_clause_ast(ops, "muutetaan 2 luvun 3 ja 4 §")

    assert len(ast.verb_groups) == 1
    vg = ast.verb_groups[0]
    assert vg.verb == StructuralAction.REPLACE
    assert len(vg.nodes) == 1

    block = vg.nodes[0]
    assert isinstance(block, ScopedBlock)
    assert block.scope == LegalAddress(path=(("chapter", "2"),))
    assert len(block.children) == 2

    child0, child1 = block.children
    assert isinstance(child0, RefAmend)
    assert child0.target == LegalAddress(path=(("chapter", "2"), ("section", "3")))
    assert isinstance(child1, RefAmend)
    assert child1.target == LegalAddress(path=(("chapter", "2"), ("section", "4")))


def test_scoped_block_distinguishes_part_and_chapter() -> None:
    """Same chapter label under different parts must not collapse into one scope."""
    ops = [
        _op("M", "P", "3", chapter="2", part="1"),
        _op("M", "P", "4", chapter="2", part="2"),
    ]
    ast = build_clause_ast(ops, "muutetaan I osan 2 luvun 3 ja II osan 2 luvun 4 §")

    vg = ast.verb_groups[0]
    assert len(vg.nodes) == 2

    first, second = vg.nodes
    assert isinstance(first, ScopedBlock)
    assert isinstance(second, ScopedBlock)
    assert first.scope == LegalAddress(path=(("part", "1"), ("chapter", "2")))
    assert second.scope == LegalAddress(path=(("part", "2"), ("chapter", "2")))


def test_parsed_op_special_otsikko_routes_heading_replace():
    """`special='otsikko'` should map to LabelAmend(heading_replace)."""
    op = _op("M", "P", "5", chapter="3", special="otsikko")
    op.facet = None
    node = parsed_op_to_clause_node(op)

    assert isinstance(node, LabelAmend)
    assert node.action == LabelAction.HEADING_REPLACE
    assert node.target == LegalAddress(path=(("chapter", "3"), ("section", "5")), special=FacetKind.HEADING)


# ---------------------------------------------------------------------------
# Test 4: renumber case — verb=S → LabelAmend
# ---------------------------------------------------------------------------


def test_renumber_verb():
    """siirretään 7 § → LabelAmend(action='renumber')."""
    ops = [_op("S", "P", "7")]
    ast = build_clause_ast(ops, "siirretään 7 §")

    assert len(ast.verb_groups) == 1
    vg = ast.verb_groups[0]
    assert vg.verb == StructuralAction.RENUMBER
    assert len(vg.nodes) == 1

    node = vg.nodes[0]
    assert isinstance(node, LabelAmend)
    assert node.action == LabelAction.RENUMBER
    assert node.target == LegalAddress(path=(("section", "7"),))


def test_core_carriers_do_not_store_move_tail_residue():
    """Move-tail inference stays bridge-local, not on shared core carriers."""
    from lawvm.core.clause_ast import clause_node_to_legal_operation

    op = _op("S", "P", "5", chapter="3")
    op.renumber_dest = "7"
    op.renumber_dest_chapter = "3"

    node = parsed_op_to_clause_node(op)
    assert isinstance(node, LabelAmend)
    assert not hasattr(node, "move_clause_target_unit_kind")
    assert "move_clause_target_unit_kind" not in {field.name for field in fields(type(node))}

    lo = clause_node_to_legal_operation(node)
    assert isinstance(lo, LegalOperation)
    assert not hasattr(lo, "move_clause_target_unit_kind")
    assert "move_clause_target_unit_kind" not in {field.name for field in fields(type(lo))}


# ---------------------------------------------------------------------------
# Test 5: extract_ops_diagnostic emits a ClauseAST
# ---------------------------------------------------------------------------


def test_diagnostic_clause_ast_field():
    """extract_ops_diagnostic populates clause_ast with a ClauseAST."""
    diag = extract_ops_diagnostic("muutetaan 6 §")

    assert diag.clause_ast is not None
    assert isinstance(diag.clause_ast, ClauseAST)
    assert diag.clause_ast.source_text == "muutetaan 6 §"
    # The ClauseAST should mirror the flat ops list
    assert len(diag.clause_ast.verb_groups) == len({op.verb for op in diag.ops})


# ---------------------------------------------------------------------------
# Test 6: empty ops → empty ClauseAST
# ---------------------------------------------------------------------------


def test_empty_ops():
    """Empty ops list → ClauseAST with no verb_groups."""
    ast = build_clause_ast([], "")
    assert isinstance(ast, ClauseAST)
    assert ast.verb_groups == ()


def test_scoped_block_rejects_empty_scope_path() -> None:
    with pytest.raises(ValueError, match="non-empty path"):
        ScopedBlock(scope=LegalAddress(path=()), children=(RefAmend(
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
        ),))


def test_scoped_block_rejects_nested_scoped_blocks() -> None:
    inner = ScopedBlock(
        scope=LegalAddress(path=(("chapter", "2"),)),
        children=(RefAmend(action=StructuralAction.REPLACE, target=LegalAddress(path=(("section", "3"),))),),
    )
    with pytest.raises(ValueError, match="nested ScopedBlock"):
        ScopedBlock(scope=LegalAddress(path=(("part", "I"),)), children=(inner,))


def test_ref_amend_rejects_non_insert_anchor() -> None:
    with pytest.raises(ValueError, match="only valid for insert"):
        RefAmend(
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            anchor=LegalAddress(path=(("section", "0"),)),
        )


def test_text_amend_rejects_structural_action() -> None:
    with pytest.raises(ValueError, match="text_replace/text_repeal"):
        TextAmend(
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="x"),
                replacement="y",
            ),
        )


def test_label_amend_rejects_heading_replace_without_heading_target() -> None:
    with pytest.raises(ValueError, match="heading_replace requires a heading target"):
        LabelAmend(
            action=LabelAction.HEADING_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
        )


def test_label_amend_rejects_renumber_without_destination_or_new_label() -> None:
    with pytest.raises(ValueError, match="requires destination or new_label"):
        LabelAmend(
            action=LabelAction.RENUMBER,
            target=LegalAddress(path=(("section", "1"),)),
        )


def test_label_amend_rejects_mismatched_destination_leaf_kind() -> None:
    with pytest.raises(ValueError, match="preserve target leaf kind"):
        LabelAmend(
            action=LabelAction.RENUMBER,
            target=LegalAddress(path=(("section", "1"),)),
            destination=LegalAddress(path=(("chapter", "2"),)),
        )


def test_meta_clause_rejects_empty_raw_text() -> None:
    with pytest.raises(ValueError, match="must be non-empty"):
        MetaClause(kind=MetaClauseKind.OTHER, raw_text="")


def test_item_shift_clause_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError, match="equal length"):
        ItemShiftClause(source_items=("e",), target_items=("d", "e"))


def test_named_row_clause_rejects_empty_targets() -> None:
    with pytest.raises(ValueError, match="must be non-empty"):
        NamedRowClause(action=StructuralAction.REPEAL, named_targets=())


# ---------------------------------------------------------------------------
# Test 7: NUMERO-based renumber carries destination through full pipeline
# ---------------------------------------------------------------------------


def test_numero_renumber_destination_end_to_end():
    """muutetaan 1 §:n numero 3:ksi → LegalOperation.destination populated.

    The NUMERO-based renumber path now lowers to a true renumber op with
    destination=LegalAddress(section:3).
    """
    from lawvm.finland.johtolause.compat import parse_clause
    from lawvm.core.clause_ast import clause_ast_to_legal_ops

    ops = clause_ast_to_legal_ops(parse_clause("muutetaan 1 §:n numero 3:ksi").clause_ast)
    assert len(ops) == 1
    lo = ops[0]
    assert lo.action == StructuralAction.RENUMBER
    assert lo.target == LegalAddress(path=(("section", "1"),))
    assert lo.destination is not None, "NUMERO renumber must populate LegalOperation.destination"
    assert lo.destination == LegalAddress(path=(("section", "3"),))


def test_numero_renumber_multi_section_destination():
    """muutetaan 5 ja 6 §:n numero 7 ja 8:ksi → both ops get destination."""
    from lawvm.finland.johtolause.compat import parse_clause
    from lawvm.core.clause_ast import clause_ast_to_legal_ops

    ops = clause_ast_to_legal_ops(parse_clause("muutetaan 5 ja 6 §:n numero 7 ja 8:ksi").clause_ast)
    assert len(ops) == 2
    assert ops[0].destination == LegalAddress(path=(("section", "7"),))
    assert ops[1].destination == LegalAddress(path=(("section", "8"),))


def test_numero_renumber_backref_subref_no_destination():
    """Sub-ref backrefs on renumbered sections should NOT get destination.

    Only whole-section ops get the renumber destination; subsection-level
    ops (from backref continuation) are regular replace ops.
    """
    from lawvm.finland.johtolause.compat import parse_clause
    from lawvm.core.clause_ast import clause_ast_to_legal_ops

    ops = clause_ast_to_legal_ops(
        parse_clause("muutetaan 2 §:n numero 4:ksi ja mainitun pykälän 1 momentti").clause_ast
    )
    assert len(ops) == 2
    # Whole section: has destination
    assert ops[0].destination == LegalAddress(path=(("section", "4"),))
    # Sub-ref backref: no destination
    assert ops[1].destination is None


def test_verb_s_renumber_destination():
    """Jolloin-based chapter renumber: verb=S with renumber_dest → destination."""
    op = _op("S", "L", "3")
    op.renumber_dest = "5"
    ast = build_clause_ast([op], "jolloin 3 luku muuttuu 5 luvuksi")

    from lawvm.core.clause_ast import clause_ast_to_legal_ops

    legal_ops = clause_ast_to_legal_ops(ast)
    assert len(legal_ops) == 1
    assert legal_ops[0].action == StructuralAction.RENUMBER
    assert legal_ops[0].destination == LegalAddress(path=(("chapter", "5"),))


def test_direct_section_relabel_preserves_full_destination_path():
    """Relative-clause relabel should keep source and full destination path."""
    from lawvm.finland.johtolause.compat import parse_clause
    from lawvm.core.clause_ast import clause_ast_to_legal_ops

    ops = clause_ast_to_legal_ops(parse_clause("muutetaan 7 luvun 73 §:ää, joka siirretään 61 §:ksi,").clause_ast)
    relabel = next(op for op in ops if op.action == StructuralAction.RENUMBER)

    assert relabel.target == LegalAddress(path=(("chapter", "7"), ("section", "73")))
    assert relabel.destination == LegalAddress(path=(("chapter", "7"), ("section", "61")))


def test_direct_section_relabel_preserves_part_scoped_destination_path():
    """Part-scoped relative-clause relabel should keep source and destination part."""
    from lawvm.finland.johtolause.compat import parse_clause
    from lawvm.core.clause_ast import clause_ast_to_legal_ops

    ops = clause_ast_to_legal_ops(
        parse_clause("muutetaan II osan 7 luvun 73 §:ää, joka siirretään 61 §:ksi,").clause_ast
    )
    relabel = next(op for op in ops if op.action == StructuralAction.RENUMBER)

    assert relabel.target == LegalAddress(path=(("part", "II"), ("chapter", "7"), ("section", "73")))
    assert relabel.destination == LegalAddress(path=(("part", "II"), ("chapter", "7"), ("section", "61")))


def test_old_move_destination_part_preserves_full_destination_path():
    """Old move continuations should lower destination-part moves natively."""
    from lawvm.finland.johtolause.compat import parse_clause
    from lawvm.core.clause_ast import clause_ast_to_legal_ops

    ops = clause_ast_to_legal_ops(parse_clause("siirretään I osaan, II osan 4 luvun otsikko sekä 38-40 §").clause_ast)
    renumbers = [op for op in ops if op.action == StructuralAction.RENUMBER]

    assert len(renumbers) == 4
    chapter_heading, sec38, sec39, sec40 = renumbers
    assert chapter_heading.target == LegalAddress(path=(("part", "II"), ("chapter", "4")), special=FacetKind.HEADING)
    assert chapter_heading.destination == LegalAddress(path=(("part", "I"), ("chapter", "4")))
    assert sec38.target == LegalAddress(path=(("part", "II"), ("chapter", "4"), ("section", "38")))
    assert sec38.destination == LegalAddress(path=(("part", "I"), ("section", "38")))
    assert sec39.destination == LegalAddress(path=(("part", "I"), ("section", "39")))
    assert sec40.destination == LegalAddress(path=(("part", "I"), ("section", "40")))


def test_relative_move_to_part_tail_retargets_prior_section_refs():
    """Relative-clause move tails to a part should keep the retargeted sections alive."""
    from lawvm.finland.johtolause.compat import parse_clause
    from lawvm.core.clause_ast import clause_ast_to_legal_ops

    ops = clause_ast_to_legal_ops(
        parse_clause("muutetaan I osa, 30 ja 31§, jotka samalla siirretään I osaan").clause_ast
    )
    replaces = [
        op
        for op in ops
        if op.action == StructuralAction.REPLACE and op.target and op.target.leaf_kind() == "section"
    ]

    assert len(replaces) == 2
    sec30, sec31 = replaces
    assert sec30.target == LegalAddress(path=(("part", "I"), ("section", "30")))
    assert sec31.target == LegalAddress(path=(("part", "I"), ("section", "31")))


# ---------------------------------------------------------------------------
# Move semantics: ParsedOp bridge preserves from→to addresses
# ---------------------------------------------------------------------------


def test_move_parsed_op_infers_source_and_destination_kind():
    """verb=S with renumber_dest → LabelAmend preserves target=source, destination=dest.

    This verifies the Finland ParsedOp bridge path (parsed_op_to_clause_node) preserves
    explicit from→to move semantics, while the core node stays field-free.
    """
    op = _op("S", "P", "5", chapter="3")
    op.renumber_dest = "7"
    op.renumber_dest_chapter = "3"

    node = parsed_op_to_clause_node(op)
    assert isinstance(node, LabelAmend)
    assert node.action == LabelAction.RENUMBER
    # Source address (where it currently lives)
    assert node.target == LegalAddress(path=(("chapter", "3"), ("section", "5")))
    # Destination address (where it moves to)
    assert node.destination == LegalAddress(path=(("chapter", "3"), ("section", "7")))
    assert node.new_label == "7"
    assert infer_move_clause_target_unit_kind(node.destination) == "chapter"


def test_cross_part_move_parsed_op():
    """Cross-part move via ParsedOp: source part in target, dest part in destination."""
    op = _op("S", "P", "38", part="II")
    op.renumber_dest = "38"
    op.renumber_dest_part = "I"

    node = parsed_op_to_clause_node(op)
    assert isinstance(node, LabelAmend)
    # Source: part II, section 38
    assert node.target == LegalAddress(path=(("part", "II"), ("section", "38")))
    # Destination: part I, section 38
    assert node.destination is not None
    assert node.destination == LegalAddress(path=(("part", "I"), ("section", "38")))
    assert infer_move_clause_target_unit_kind(node.destination) == "part"


def test_provenance_heavy_relative_move_to_part_tail_retargets_prior_section_refs():
    """Provenance after a part ref should still preserve later moved section refs."""
    from lawvm.finland.johtolause.compat import parse_clause
    from lawvm.core.clause_ast import clause_ast_to_legal_ops

    ops = clause_ast_to_legal_ops(
        parse_clause(
            "muutetaan I osa, sellaisena kuin se on siihen myöhemmin tehtyine muutoksineen, "
            "30 ja 31§, jotka samalla siirretään I osaan"
        ).clause_ast
    )
    replaces = [
        op
        for op in ops
        if op.action == StructuralAction.REPLACE and op.target and op.target.leaf_kind() == "section"
    ]

    assert len(replaces) == 2
    sec30, sec31 = replaces
    assert sec30.target == LegalAddress(path=(("part", "I"), ("section", "30")))
    assert sec31.target == LegalAddress(path=(("part", "I"), ("section", "31")))


# ============================================================================
# Typed enum accessor tests (Pro #16 Step 3)
# ============================================================================


class TestTypedFacetHelper:
    """LegalAddress.special is already typed; direct access should stay typed."""

    def test_none_special_returns_none_facet(self):
        addr = LegalAddress(path=(("section", "1"),))
        assert addr.special is None

    def test_heading_special(self):
        addr = LegalAddress(path=(("section", "1"),), special=FacetKind.HEADING)
        assert addr.special is FacetKind.HEADING

    def test_intro_special(self):
        addr = LegalAddress(path=(("section", "1"),), special=FacetKind.INTRO)
        assert addr.special is FacetKind.INTRO

    def test_unknown_special_returns_none_facet(self):
        """Unrecognised special values fall back to None."""
        addr = LegalAddress(path=(("section", "1"),), special=None)
        assert addr.special is None


def test_clause_node_to_legal_operation_renumber_new_label_preserves_parent_path():
    """Renumber fallback destination should preserve the full parent path."""
    from lawvm.core.clause_ast import clause_node_to_legal_operation

    node = LabelAmend(
        action=LabelAction.RENUMBER,
        target=LegalAddress(path=(("part", "I"), ("chapter", "7"), ("section", "73"))),
        new_label="61",
    )
    lo = clause_node_to_legal_operation(node)

    assert lo is not None
    assert lo.destination == LegalAddress(path=(("part", "I"), ("chapter", "7"), ("section", "61")))


def test_parsed_op_bridge_rejects_unknown_kind():
    """Finland ParsedOp bridge rejects unsupported ParsedOp kinds explicitly."""
    op = _op("M", "Z", "1")
    with pytest.raises(ValueError, match="Unsupported ParsedOp kind"):
        parsed_op_to_clause_node(op)


def test_parsed_op_bridge_rejects_unknown_verb():
    """Finland ParsedOp bridge rejects unsupported ParsedOp verbs explicitly."""
    op = _op("X", "P", "1")
    with pytest.raises(ValueError, match="Unsupported ParsedOp verb"):
        parsed_op_to_clause_node(op)


# ---------------------------------------------------------------------------
# ItemShiftClause and NamedRowClause lowering: explicit skip (returns None)
# ---------------------------------------------------------------------------


class TestItemShiftClauseLowering:
    """ItemShiftClause remains an explicit non-generic bridge node."""

    def test_item_shift_clause_returns_none(self) -> None:
        node = ItemShiftClause(
            source_items=("e", "f", "g", "h"),
            target_items=("d", "e", "f", "g"),
            target_paragraph=1,
            target_section="2",
        )
        result = clause_node_to_legal_operation(node)
        assert result is None

    def test_item_shift_clause_skipped_in_clause_ast_to_legal_ops(self) -> None:
        from lawvm.core.clause_ast import (
            ClauseAST,
            ItemShiftClause,
            VerbGroup,
            clause_ast_to_legal_ops,
        )

        shift = ItemShiftClause(
            source_items=("e", "f"),
            target_items=("d", "e"),
            target_paragraph=1,
            target_section="5",
        )
        replace = RefAmend(
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "5"),)),
        )
        ast = ClauseAST(
            source_text="test",
            verb_groups=(
                VerbGroup(verb=StructuralAction.REPLACE, nodes=(replace, shift)),
            ),
        )
        ops = clause_ast_to_legal_ops(ast)
        # Only the RefAmend should produce a LegalOperation; ItemShiftClause is skipped
        assert len(ops) == 1
        assert ops[0].action == StructuralAction.REPLACE


class TestNamedRowClauseLowering:
    """NamedRowClause remains an explicit non-generic bridge node."""

    def test_named_row_clause_returns_none(self) -> None:
        node = NamedRowClause(
            action=StructuralAction.REPEAL,
            named_targets=("iitin", "juvan"),
            target_section="1",
        )
        result = clause_node_to_legal_operation(node)
        assert result is None

    def test_named_row_clause_skipped_in_clause_ast_to_legal_ops(self) -> None:
        from lawvm.core.clause_ast import (
            ClauseAST,
            NamedRowClause,
            VerbGroup,
            clause_ast_to_legal_ops,
        )

        named_row = NamedRowClause(
            action=StructuralAction.REPEAL,
            named_targets=("kouvolan",),
            target_section="1",
        )
        repeal = RefAmend(
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "3"),)),
        )
        ast = ClauseAST(
            source_text="test",
            verb_groups=(
                VerbGroup(verb=StructuralAction.REPEAL, nodes=(repeal, named_row)),
            ),
        )
        ops = clause_ast_to_legal_ops(ast)
        # Only the RefAmend should produce a LegalOperation; NamedRowClause is skipped
        assert len(ops) == 1
        assert ops[0].action == StructuralAction.REPEAL


def test_clause_ast_to_legal_ops_with_diagnostics_records_unsupported_generic_nodes() -> None:
    replace = RefAmend(
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "5"),)),
    )
    shift = ItemShiftClause(
        source_items=("e", "f"),
        target_items=("d", "e"),
        target_paragraph=1,
        target_section="5",
    )
    named_row = NamedRowClause(
        action=StructuralAction.REPEAL,
        named_targets=("kouvolan",),
        target_section="1",
    )
    meta = MetaClause(kind=MetaClauseKind.OTHER, raw_text="voimaantulo erikseen")
    ast = ClauseAST(
        source_text="test",
        verb_groups=(
            VerbGroup(
                verb=StructuralAction.REPLACE,
                nodes=(
                    ScopedBlock(
                        scope=LegalAddress(path=(("chapter", "2"),)),
                        children=(replace, shift),
                    ),
                    named_row,
                    meta,
                ),
            ),
        ),
    )

    ops, diagnostics = clause_ast_to_legal_ops_with_diagnostics(ast)

    assert [op.target for op in ops] == [
        LegalAddress(path=(("chapter", "2"), ("section", "5"))),
    ]
    assert [diagnostic.node_kind for diagnostic in diagnostics] == [
        "ItemShiftClause",
        "NamedRowClause",
        "MetaClause",
    ]
    assert {diagnostic.kind for diagnostic in diagnostics} == {
        CLAUSE_AST_UNSUPPORTED_GENERIC_LOWERING_KIND,
    }
    assert {diagnostic.rule_id for diagnostic in diagnostics} == {
        CLAUSE_AST_UNSUPPORTED_GENERIC_LOWERING_RULE_ID,
    }
    assert all(diagnostic.phase == "lowering" for diagnostic in diagnostics)
    assert all(diagnostic.family == "lowering_filter" for diagnostic in diagnostics)
    assert all(diagnostic.blocking for diagnostic in diagnostics)
    assert all(diagnostic.strict_disposition == "block" for diagnostic in diagnostics)
    assert all(diagnostic.quirks_disposition == "record" for diagnostic in diagnostics)
    assert diagnostics[0].scope == LegalAddress(path=(("chapter", "2"),))
    assert diagnostics[1].scope is None
    assert diagnostics[2].scope is None
    assert "source_items" in (diagnostics[0].detail or "")
    assert "named_targets" in (diagnostics[1].detail or "")
    assert diagnostics[2].detail == "meta_kind=other"
