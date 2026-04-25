"""Tests for the typed LegalOperation action contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.semantic_types import FacetKind, StructuralAction
from lawvm.core.canonical_intent import FacetTarget, NodeTarget


def _make_op(action: StructuralAction) -> LegalOperation:
    """Minimal LegalOperation factory for adapter testing."""
    addr = LegalAddress(path=(("section", "1"),))
    return LegalOperation(
        op_id="test-op",
        sequence=1,
        action=action,
        target=addr,
    )


class TestLegalOperationTypedAction:
    @pytest.mark.parametrize(
        ("action", "expected"),
        [
            (StructuralAction.REPLACE, StructuralAction.REPLACE),
            (StructuralAction.REPEAL, StructuralAction.REPEAL),
            (StructuralAction.INSERT, StructuralAction.INSERT),
            (StructuralAction.RENUMBER, StructuralAction.RENUMBER),
        ],
    )
    def test_structural_actions_stay_enums(
        self, action: StructuralAction, expected: StructuralAction
    ) -> None:
        op = _make_op(action)
        assert op.action is expected
        assert op.action == action

    def test_action_field_is_enum(self) -> None:
        op = _make_op(StructuralAction.REPEAL)
        assert op.action == StructuralAction.REPEAL
        assert isinstance(op.action, StructuralAction)

    def test_renumber_with_destination(self) -> None:
        src = LegalAddress(path=(("section", "3"),))
        dst = LegalAddress(path=(("section", "4"),))
        op = LegalOperation(
            op_id="rn-1",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=src,
            destination=dst,
        )
        assert op.action is StructuralAction.RENUMBER

    def test_raw_string_action_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="must be StructuralAction"):
            LegalOperation(
                op_id="bad-1",
                sequence=1,
                action=cast(StructuralAction, "replace"),
                target=_make_op(StructuralAction.REPLACE).target,
            )

    def test_legal_operation_is_frozen(self) -> None:
        op = _make_op(StructuralAction.REPLACE)
        with pytest.raises(FrozenInstanceError):
            setattr(op, "notes", ["mutated"])


class TestCanonicalIntentEnumNormalization:
    def test_node_target_uses_address_leaf_kind(self) -> None:
        addr = LegalAddress(path=(("section", "1"),))
        target = NodeTarget(address=addr)
        assert target.address is addr
        assert target.address.leaf_kind() == "section"

    def test_facet_target_accepts_facet_kind_enum(self) -> None:
        addr = LegalAddress(path=(("section", "1"),))
        target = FacetTarget(host=addr, facet=FacetKind.HEADING)
        assert target.facet is FacetKind.HEADING
