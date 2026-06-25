from __future__ import annotations

import pytest
from types import SimpleNamespace

from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    LegalOperation,
    LegalOperationPayloadActionError,
    StructuralAction,
    TextPatchSpec,
    TextSelector,
)
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


# ---------------------------------------------------------------------------
# payload↔action closure (coherence audit Axis-2 finding A)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("action", [StructuralAction.REPEAL, StructuralAction.TEXT_REPEAL])
def test_repeal_action_rejects_content_payload(action: StructuralAction) -> None:
    """A repeal action carrying a real content payload is unrepresentable."""
    content = IRNode(kind=IRNodeKind.SECTION, label="5", text="real content")
    with pytest.raises(
        LegalOperationPayloadActionError, match="must not carry a substantive content payload"
    ):
        LegalOperation(
            op_id="rep-content",
            sequence=1,
            action=action,
            target=_addr(),
            payload=content,
        )


@pytest.mark.parametrize("action", [StructuralAction.REPEAL, StructuralAction.TEXT_REPEAL])
def test_repeal_action_accepts_none_payload(action: StructuralAction) -> None:
    op = LegalOperation(op_id="rep-none", sequence=1, action=action, target=_addr())
    assert op.payload is None


@pytest.mark.parametrize("action", [StructuralAction.REPEAL, StructuralAction.TEXT_REPEAL])
def test_repeal_action_accepts_repeal_placeholder_tombstone(action: StructuralAction) -> None:
    """A repeal may carry the tombstone it leaves behind (lawvm_repeal_placeholder)."""
    tombstone = IRNode(
        kind=IRNodeKind.SECTION,
        label="5",
        attrs={"lawvm_repeal_placeholder": "1"},
    )
    op = LegalOperation(
        op_id="rep-tombstone",
        sequence=1,
        action=action,
        target=_addr(),
        payload=tombstone,
    )
    assert op.payload is tombstone


@pytest.mark.parametrize("action", [StructuralAction.REPEAL, StructuralAction.TEXT_REPEAL])
def test_repeal_action_accepts_empty_metadata_carrier(action: StructuralAction) -> None:
    """A repeal may carry an empty (no text, no children) CONTENT node used only
    to carry attrs — e.g. Estonia encodes a repeal RANGE via
    ``subsection_selection_meta`` in attrs. That is not substantive content, so
    the payload↔action closure permits it."""
    carrier = IRNode(
        kind=IRNodeKind.CONTENT,
        text="",
        attrs={"subsection_selection_meta": "(2,3,4)"},
    )
    op = LegalOperation(
        op_id="rep-meta-carrier",
        sequence=1,
        action=action,
        target=_addr(),
        payload=carrier,
    )
    assert op.payload is carrier


def test_replace_action_accepts_none_payload() -> None:
    """REPLACE with payload=None is a legitimate container snapshot shape (not gated)."""
    op = LegalOperation(
        op_id="rep-replace-none",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=_addr(),
        payload=None,
    )
    assert op.payload is None


def test_replace_action_accepts_content_payload() -> None:
    op = LegalOperation(
        op_id="rep-replace-content",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=_addr(),
        payload=IRNode(kind=IRNodeKind.SECTION, label="5", text="new"),
    )
    assert op.payload is not None


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
