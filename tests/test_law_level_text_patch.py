"""Tests for law-level text replacement post-processor.

Covers the global "sana X korvataan sanalla Y" amendment pattern where
text replacements apply across an entire statute, not to specific sections.
"""

from __future__ import annotations

from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    OperationSource,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.semantic_types import IRNodeKind, StructuralAction, TextPatchKindEnum
from lawvm.finland.johtolause import extract_law_level_text_patch_los
from lawvm.finland.ops import (
    AmendmentOp,
    LawLevelTextPatch,
    _apply_law_level_text_patches,
    _apply_single_text_patch,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mk_node(text: str = "", children: tuple[IRNode, ...] = (), label: str | None = None) -> IRNode:
    """Build a simple IR node for testing."""
    return IRNode(kind=IRNodeKind.CONTENT, label=label, text=text, children=children)


def _mk_section(label: str, text: str, children: tuple[IRNode, ...] = ()) -> IRNode:
    """Build a section-like IR node."""
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text, children=children)


def _mk_body(*children: IRNode) -> IRNode:
    """Build a body IR node containing sections."""
    return IRNode(kind=IRNodeKind.BODY, children=children)


def _replace_patch(match: str, replacement: str, occurrence: int = 0) -> TextPatchSpec:
    """Build a REPLACE TextPatchSpec."""
    return TextPatchSpec(
        kind=TextPatchKindEnum.REPLACE,
        selector=TextSelector(match_text=match, occurrence=occurrence),
        replacement=replacement,
    )


def _delete_patch(match: str, occurrence: int = 0) -> TextPatchSpec:
    """Build a DELETE TextPatchSpec."""
    return TextPatchSpec(
        kind=TextPatchKindEnum.DELETE,
        selector=TextSelector(match_text=match, occurrence=occurrence),
    )


def _mk_law_level_patch(
    match: str,
    replacement: str,
    occurrence: int = 0,
    op_id: str = "test_patch",
    source_amendment: str = "2025/572",
) -> LawLevelTextPatch:
    """Build a LawLevelTextPatch for testing."""
    return LawLevelTextPatch(
        op_id=op_id,
        patch=_replace_patch(match, replacement, occurrence),
        source_amendment=source_amendment,
        effective="2025-07-01",
    )


# ---------------------------------------------------------------------------
# _apply_single_text_patch tests
# ---------------------------------------------------------------------------


class TestApplySingleTextPatch:
    def test_replace_in_leaf(self):
        node = _mk_node("lupaviranomainen päättää")
        patch = _replace_patch("lupaviranomainen", "Lupa- ja valvontavirasto")
        result = _apply_single_text_patch(node, patch)
        assert result.text == "Lupa- ja valvontavirasto päättää"

    def test_no_match_preserves_identity(self):
        node = _mk_node("ei mitään muutettavaa")
        patch = _replace_patch("lupaviranomainen", "Lupa- ja valvontavirasto")
        result = _apply_single_text_patch(node, patch)
        assert result is node

    def test_replace_all_occurrences(self):
        node = _mk_node("lupaviranomainen ja lupaviranomainen")
        patch = _replace_patch("lupaviranomainen", "virasto", occurrence=0)
        result = _apply_single_text_patch(node, patch)
        assert result.text == "virasto ja virasto"

    def test_replace_first_occurrence_only(self):
        node = _mk_node("A ja A ja A")
        patch = _replace_patch("A", "B", occurrence=1)
        result = _apply_single_text_patch(node, patch)
        assert result.text == "B ja A ja A"

    def test_replace_second_occurrence_only(self):
        node = _mk_node("A ja A ja A")
        patch = _replace_patch("A", "B", occurrence=2)
        result = _apply_single_text_patch(node, patch)
        assert result.text == "A ja B ja A"

    def test_occurrence_beyond_count(self):
        """occurrence=3 but only 2 occurrences -> no change."""
        node = _mk_node("A ja A")
        patch = _replace_patch("A", "B", occurrence=3)
        result = _apply_single_text_patch(node, patch)
        assert result is node

    def test_delete_all(self):
        node = _mk_node("sana poistettava sana")
        patch = _delete_patch("poistettava ")
        result = _apply_single_text_patch(node, patch)
        assert result.text == "sana sana"

    def test_delete_first_occurrence(self):
        node = _mk_node("X ja X ja X")
        patch = _delete_patch("X ", occurrence=1)
        result = _apply_single_text_patch(node, patch)
        assert result.text == "ja X ja X"

    def test_recursive_into_children(self):
        child1 = _mk_node("lupaviranomainen tekee")
        child2 = _mk_node("ei liity")
        parent = _mk_node("lupaviranomainen päättää", children=(child1, child2))
        patch = _replace_patch("lupaviranomainen", "virasto")
        result = _apply_single_text_patch(parent, patch)
        assert result.text == "virasto päättää"
        assert result.children[0].text == "virasto tekee"
        assert result.children[1] is child2  # unchanged child preserves identity

    def test_deeply_nested(self):
        """Patches apply at all depths."""
        leaf = _mk_node("sana X tässä")
        mid = _mk_node(children=(leaf,))
        root = _mk_node(children=(mid,))
        patch = _replace_patch("X", "Y")
        result = _apply_single_text_patch(root, patch)
        assert result.children[0].children[0].text == "sana Y tässä"

    def test_empty_text_unchanged(self):
        node = _mk_node("")
        patch = _replace_patch("X", "Y")
        result = _apply_single_text_patch(node, patch)
        assert result is node

    def test_preserves_node_metadata(self):
        """Kind, label, and attrs survive the replacement."""
        node = IRNode(kind=IRNodeKind.SECTION, label="5", text="lupaviranomainen", attrs={"source": "test"})
        patch = _replace_patch("lupaviranomainen", "virasto")
        result = _apply_single_text_patch(node, patch)
        assert result.kind == IRNodeKind.SECTION
        assert result.label == "5"
        assert result.attrs["source"] == "test"
        assert result.text == "virasto"


# ---------------------------------------------------------------------------
# _apply_law_level_text_patches tests
# ---------------------------------------------------------------------------


class TestApplyLawLevelTextPatches:
    def test_multiple_patches_in_sequence(self):
        ir = _mk_body(
            _mk_section("1", "lupaviranomainen päättää"),
            _mk_section("2", "lupaviranomaisen kanssa"),
        )
        patches = [
            _mk_law_level_patch("lupaviranomainen", "Lupa- ja valvontavirasto"),
            _mk_law_level_patch("lupaviranomaisen", "Lupa- ja valvontaviraston"),
        ]
        result = _apply_law_level_text_patches(ir, patches)
        assert result.children[0].text == "Lupa- ja valvontavirasto päättää"
        assert result.children[1].text == "Lupa- ja valvontaviraston kanssa"

    def test_empty_patches_returns_same_node(self):
        ir = _mk_body(_mk_section("1", "text"))
        result = _apply_law_level_text_patches(ir, [])
        assert result is ir

    def test_no_match_preserves_identity(self):
        ir = _mk_body(_mk_section("1", "nothing matches"))
        patches = [_mk_law_level_patch("nonexistent", "replacement")]
        result = _apply_law_level_text_patches(ir, patches)
        assert result is ir

    def test_applies_across_all_sections(self):
        ir = _mk_body(
            _mk_section("1", "sana tässä"),
            _mk_section("2", "sana tuossa"),
            _mk_section("3", "ei mitään"),
        )
        patches = [_mk_law_level_patch("sana", "termi")]
        result = _apply_law_level_text_patches(ir, patches)
        assert result.children[0].text == "termi tässä"
        assert result.children[1].text == "termi tuossa"
        assert result.children[2] is ir.children[2]  # unchanged


# ---------------------------------------------------------------------------
# AmendmentOp.extract_law_level_text_patches tests
# ---------------------------------------------------------------------------


class TestExtractLawLevelTextPatches:
    def test_extracts_from_empty_path_with_text_patch(self):
        lo = LegalOperation(
            op_id="text_replace_0",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=()),
            text_patch=_replace_patch("lupaviranomainen", "virasto"),
            source=OperationSource(
                statute_id="2025/572",
                effective="2025-07-01",
                enacted="2025-06-01",
            ),
        )
        patches = AmendmentOp.extract_law_level_text_patches([lo])
        assert len(patches) == 1
        assert patches[0].op_id == "text_replace_0"
        assert patches[0].patch.selector.match_text == "lupaviranomainen"
        assert patches[0].patch.replacement == "virasto"
        assert patches[0].source_amendment == "2025/572"
        assert patches[0].effective == "2025-07-01"

    def test_ignores_section_level_ops(self):
        lo = LegalOperation(
            op_id="section_replace_0",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "5"),)),
            text_patch=_replace_patch("X", "Y"),
        )
        patches = AmendmentOp.extract_law_level_text_patches([lo])
        assert len(patches) == 0

    def test_ignores_empty_path_without_text_patch(self):
        lo = LegalOperation(
            op_id="structural_0",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=()),
        )
        patches = AmendmentOp.extract_law_level_text_patches([lo])
        assert len(patches) == 0

    def test_extracts_multiple(self):
        lo1 = LegalOperation(
            op_id="tp_0",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=()),
            text_patch=_replace_patch("A", "B"),
            source=OperationSource(statute_id="2025/1", effective="2025-01-01"),
        )
        lo2 = LegalOperation(
            op_id="tp_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=()),
            text_patch=_replace_patch("C", "D"),
            source=OperationSource(statute_id="2025/1", effective="2025-01-01"),
        )
        lo_section = LegalOperation(
            op_id="sec_0",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
        )
        patches = AmendmentOp.extract_law_level_text_patches([lo1, lo2, lo_section])
        assert len(patches) == 2
        assert patches[0].patch.selector.match_text == "A"
        assert patches[1].patch.selector.match_text == "C"

    def test_missing_source_defaults_empty(self):
        lo = LegalOperation(
            op_id="tp_0",
            sequence=0,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=()),
            text_patch=_replace_patch("X", "Y"),
        )
        patches = AmendmentOp.extract_law_level_text_patches([lo])
        assert len(patches) == 1
        assert patches[0].source_amendment == ""
        assert patches[0].effective == ""

    def test_text_patch_is_extracted(self):
        """LOs with explicit text_patch should be extracted."""
        lo = LegalOperation(
            op_id="legacy_tp",
            sequence=0,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=()),
            text_patch=_replace_patch("old_word", "new_word"),
            source=OperationSource(statute_id="2024/100", effective="2024-06-01"),
        )
        patches = AmendmentOp.extract_law_level_text_patches([lo])
        assert len(patches) == 1
        assert patches[0].patch.selector.match_text == "old_word"
        assert patches[0].patch.replacement == "new_word"


# ---------------------------------------------------------------------------
# extract_law_level_text_patch_los tests
# ---------------------------------------------------------------------------


class TestExtractLawLevelTextPatchLos:
    def test_unscoped_single_word_replace(self):
        """Pure unscoped rename produces one law-level LO."""
        johto = 'sana "lupaviranomainen" korvataan sanalla "Lupa- ja valvontavirasto"'
        ops = extract_law_level_text_patch_los(johto, amendment_id="2025/572", effective="2025-07-01")
        assert len(ops) == 1
        lo = ops[0]
        assert lo.target.path == ()
        assert lo.text_patch is not None
        assert lo.text_patch.selector.match_text == "lupaviranomainen"
        assert lo.text_patch.replacement == "Lupa- ja valvontavirasto"
        assert lo.source is not None
        assert lo.source.statute_id == "2025/572"
        assert lo.source.effective == "2025-07-01"

    def test_section_scoped_excluded(self):
        """Section-scoped text amend is NOT emitted as a law-level patch."""
        johto = '5 §:ssä sana "lääninhallitus" korvataan sanalla "aluehallintovirasto"'
        ops = extract_law_level_text_patch_los(johto)
        assert len(ops) == 0

    def test_unscoped_multi_word_replace(self):
        """Unscoped multi-word sanat/sanoilla pattern produces one law-level LO."""
        johto = 'sanat "kauppa- ja teollisuusministeriö" korvataan sanoilla "työ- ja elinkeinoministeriö"'
        ops = extract_law_level_text_patch_los(johto)
        assert len(ops) == 1
        tp = ops[0].text_patch
        assert tp is not None
        assert tp.selector.match_text == "kauppa- ja teollisuusministeriö"
        assert tp.replacement == "työ- ja elinkeinoministeriö"

    def test_no_text_amend(self):
        """Johtolause with no text amend produces empty list."""
        johto = "muutetaan 3 § siten, että momentti lisätään"
        ops = extract_law_level_text_patch_los(johto)
        assert len(ops) == 0

    def test_empty_johto(self):
        """Empty johtolause produces empty list."""
        ops = extract_law_level_text_patch_los("")
        assert len(ops) == 0

    def test_no_amendment_id_no_source(self):
        """Without amendment_id, source is None."""
        johto = 'sana "X" korvataan sanalla "Y"'
        ops = extract_law_level_text_patch_los(johto)
        assert len(ops) == 1
        assert ops[0].source is None

    def test_roundtrip_through_extract_law_level_text_patches(self):
        """LOs produced here are correctly extracted by AmendmentOp.extract_law_level_text_patches."""
        johto = 'sana "lupaviranomainen" korvataan sanalla "Lupa- ja valvontavirasto"'
        los = extract_law_level_text_patch_los(johto, amendment_id="2025/572", effective="2025-07-01")
        patches = AmendmentOp.extract_law_level_text_patches(los)
        assert len(patches) == 1
        assert patches[0].patch.selector.match_text == "lupaviranomainen"
        assert patches[0].patch.replacement == "Lupa- ja valvontavirasto"
        assert patches[0].source_amendment == "2025/572"
        assert patches[0].effective == "2025-07-01"
