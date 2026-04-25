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
