"""Finland repeal-payload invariant at the op-construction boundary.

A FI op whose action is REPEAL or TEXT_REPEAL must carry ``payload=None`` OR a
repeal tombstone (``attrs["lawvm_repeal_placeholder"] == "1"``). Any other
substantive content payload on a FI repeal is an illegal state and must fail
loud at the Finland op-construction boundary (``AmendmentOp.from_lo`` /
``validate_fi_repeal_payload``).

This convention is enforced inside ``finland/`` ONLY — it is a Finland drafting
convention, not a property of the shared ``core.ir.LegalOperation`` carrier (a
near-identical check on the core type once broke Estonia and was reverted).
"""

from __future__ import annotations

import pytest

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, StructuralAction
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.ops import (
    AmendmentOp,
    FinlandRepealPayloadError,
    validate_fi_repeal_payload,
)


def _section_target() -> LegalAddress:
    return LegalAddress(path=(("section", "5"),))


def _substantive_payload() -> IRNode:
    """A real section content payload (NOT a repeal tombstone)."""
    return IRNode(
        kind=IRNodeKind.SECTION,
        label="5",
        children=(IRNode(kind=IRNodeKind.SUBSECTION, text="uutta sisältöä"),),
    )


def _tombstone_payload() -> IRNode:
    """The allowed FI repeal tombstone placeholder."""
    return IRNode(
        kind=IRNodeKind.SECTION,
        label="5",
        attrs={"lawvm_repeal_placeholder": "1"},
        children=(IRNode(kind=IRNodeKind.NUM, text="5 §"),),
    )


def _repeal_lo(payload: IRNode | None, *, action: StructuralAction = StructuralAction.REPEAL) -> LegalOperation:
    return LegalOperation(
        op_id="repeal_5",
        sequence=0,
        action=action,
        target=_section_target(),
        payload=payload,
    )


# --- validate_fi_repeal_payload (the boundary check itself) -----------------


def test_repeal_with_none_payload_is_accepted() -> None:
    validate_fi_repeal_payload(_repeal_lo(None))  # must not raise


def test_repeal_with_tombstone_payload_is_accepted() -> None:
    validate_fi_repeal_payload(_repeal_lo(_tombstone_payload()))  # must not raise


def test_text_repeal_with_none_payload_is_accepted() -> None:
    validate_fi_repeal_payload(_repeal_lo(None, action=StructuralAction.TEXT_REPEAL))


def test_repeal_with_substantive_payload_raises() -> None:
    with pytest.raises(FinlandRepealPayloadError) as exc:
        validate_fi_repeal_payload(_repeal_lo(_substantive_payload()))
    # Self-evidencing: the offending op + payload shape is embedded.
    message = str(exc.value)
    assert "repeal_5" in message
    assert "REPEAL" in message
    assert "lawvm_repeal_placeholder" in message


def test_text_repeal_with_substantive_payload_raises() -> None:
    with pytest.raises(FinlandRepealPayloadError):
        validate_fi_repeal_payload(
            _repeal_lo(_substantive_payload(), action=StructuralAction.TEXT_REPEAL)
        )


def test_non_repeal_action_with_payload_is_ignored() -> None:
    # A REPLACE legitimately carries a substantive payload; the invariant only
    # constrains the repeal actions and must not touch this op.
    replace_lo = LegalOperation(
        op_id="replace_5",
        sequence=0,
        action=StructuralAction.REPLACE,
        target=_section_target(),
        payload=_substantive_payload(),
    )
    validate_fi_repeal_payload(replace_lo)  # must not raise


# --- enforced at the AmendmentOp.from_lo construction boundary --------------


def test_from_lo_accepts_repeal_without_payload() -> None:
    ops = AmendmentOp.from_lo(_repeal_lo(None), 0)
    assert len(ops) == 1


def test_from_lo_accepts_repeal_with_tombstone() -> None:
    ops = AmendmentOp.from_lo(_repeal_lo(_tombstone_payload()), 0)
    assert len(ops) == 1


def test_from_lo_rejects_repeal_with_substantive_payload() -> None:
    with pytest.raises(FinlandRepealPayloadError):
        AmendmentOp.from_lo(_repeal_lo(_substantive_payload()), 0)
