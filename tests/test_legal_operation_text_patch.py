from __future__ import annotations

import pytest
from types import SimpleNamespace

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, StructuralAction, TextPatchSpec, TextSelector
from lawvm.core.semantic_types import IRNodeKind, TextPatchKindEnum


def _addr() -> LegalAddress:
    return LegalAddress(path=(("section", "1"),))


def test_legal_operation_accepts_explicit_text_patch_spec() -> None:
    op = LegalOperation(
        op_id="txt-1",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=_addr(),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="old", occurrence=1),
            replacement="new",
        ),
    )

    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "old"
    assert op.text_patch.replacement == "new"


def test_text_selector_accepts_final_occurrence_sentinel() -> None:
    selector = TextSelector(match_text="and", occurrence=-1)

    assert selector.occurrence == -1


def test_text_selector_rejects_invalid_negative_occurrence() -> None:
    with pytest.raises(ValueError, match="occurrence must be >= -1"):
        TextSelector(match_text="and", occurrence=-2)


def test_missing_text_patch_leaves_patch_empty_for_non_text_action() -> None:
    op = LegalOperation(
        op_id="txt-2",
        sequence=2,
        action=StructuralAction.REPLACE,
        target=_addr(),
    )

    assert op.text_patch is None


def test_payload_rewrite_witness_is_opaque_diagnostic_payload() -> None:
    witness = SimpleNamespace(rewrite={"marker": "Lisa 1", "appendix_table_update": True})
    op = LegalOperation(
        op_id="txt-opaque",
        sequence=4,
        action=StructuralAction.TEXT_REPLACE,
        target=_addr(),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            attrs={"rewrite_witness": witness},
        ),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="old"),
            replacement="new",
        ),
    )

    assert op.payload is not None
    assert op.payload.attrs["rewrite_witness"] is witness
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "old"
    assert op.text_patch.replacement == "new"


def test_text_patch_is_rejected_on_truly_non_text_action() -> None:
    """text_patch is rejected for actions that are not text_replace, text_repeal, replace, or unknown."""
    with pytest.raises(ValueError, match="text_patch is only valid"):
        LegalOperation(
            op_id="txt-3",
            sequence=3,
            action=StructuralAction.REPEAL,
            target=_addr(),
            text_patch=TextPatchSpec(
                kind=TextPatchKindEnum.REPLACE,
                selector=TextSelector(match_text="old"),
                replacement="new",
            ),
        )


def test_text_patch_is_accepted_on_replace_action() -> None:
    """text_patch is valid for replace action (used by UK executor for word substitution)."""
    op = LegalOperation(
        op_id="txt-3",
        sequence=3,
        action=StructuralAction.REPLACE,
        target=_addr(),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="old"),
            replacement="new",
        ),
    )
    assert op.text_patch is not None


def test_text_patch_spec_replace_requires_replacement() -> None:
    with pytest.raises(ValueError, match="requires replacement"):
        TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="old"),
        )


def test_text_patch_spec_append_requires_replacement() -> None:
    with pytest.raises(ValueError, match="requires replacement"):
        TextPatchSpec(
            kind=TextPatchKindEnum.APPEND,
            selector=TextSelector(match_text="TEXT_END"),
        )


def test_explicit_text_patch_is_authoritative() -> None:
    op = LegalOperation(
        op_id="txt-4",
        sequence=5,
        action=StructuralAction.TEXT_REPLACE,
        target=_addr(),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="newer"),
            replacement="better",
        ),
    )
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "newer"
    assert op.text_patch.replacement == "better"


# ---------------------------------------------------------------------------
# First-class MOVE (§2.1 O5) + neutral move_destination carrier (#186 / §5.3).
# These replace the deleted FI-specific ``move_clause_target_unit_kind`` string
# rider: MOVE is a structural action carrying a ``destination`` address, and the
# move-scope carrier is a typed ``LegalAddress`` (``move_destination``).
# ---------------------------------------------------------------------------


def test_move_action_constructible_with_destination() -> None:
    op = LegalOperation(
        op_id="mv-1",
        sequence=1,
        action=StructuralAction.MOVE,
        target=_addr(),
        destination=LegalAddress(path=(("chapter", "5"),)),
    )
    assert op.action is StructuralAction.MOVE
    assert op.destination is not None
    assert op.destination.leaf_kind() == "chapter"


def test_move_action_requires_destination() -> None:
    with pytest.raises(ValueError, match="action=move requires a destination"):
        LegalOperation(
            op_id="mv-2",
            sequence=1,
            action=StructuralAction.MOVE,
            target=_addr(),
        )


def test_destination_rejected_for_non_move_non_renumber_action() -> None:
    with pytest.raises(ValueError, match="only valid for renumber/move"):
        LegalOperation(
            op_id="mv-3",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=_addr(),
            destination=LegalAddress(path=(("chapter", "5"),)),
        )


def test_renumber_still_accepts_destination() -> None:
    op = LegalOperation(
        op_id="mv-4",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=_addr(),
        destination=LegalAddress(path=(("section", "2"),)),
    )
    assert op.destination is not None
    assert op.destination.leaf_label() == "2"


def test_move_destination_carrier_is_typed_address() -> None:
    op = LegalOperation(
        op_id="mv-5",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("chapter", "5"), ("section", "29e"))),
        move_destination=LegalAddress(path=(("chapter", "5"),)),
    )
    assert op.move_destination is not None
    assert op.move_destination.leaf_kind() == "chapter"


def test_move_destination_rejects_bare_string() -> None:
    with pytest.raises(TypeError, match="move_destination must be a LegalAddress"):
        LegalOperation(
            op_id="mv-6",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=_addr(),
            move_destination="chapter",  # ty: ignore[invalid-argument-type]
        )
