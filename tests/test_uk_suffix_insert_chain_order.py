"""Out-of-order suffix-section insert chains materialize by label ordering.

A UK amending Act can insert a chain of suffix-numbered sections — a base
section ``98`` gaining ``98A``, then ``98B``, then ``98C`` — and the compiled
INSERT ops are not guaranteed to arrive in label order. Each suffix insert is a
single-segment body target with no positional anchor (``anchor=None``); its
placement is resolved by :func:`uk_find_body_predecessor_parent`, which walks the
body for the nearest existing same-kind predecessor under label sort order.

These tests pin that label-ordering placement so the whole chain lands in the
correct sequence regardless of feed order, and so a suffix section still lands
when an intermediate sibling is genuinely absent (e.g. ``98C`` inserted while
``98B`` was never created). This is the behaviour that keeps transitively
suffix-numbered inserts from depending on sibling-arrival order.
"""
from __future__ import annotations

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.uk_legislation.uk_amendment_replay import replay_uk_ops


def _base_98_99() -> IRStatute:
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="98", text="Section 98."),
                IRNode(kind=IRNodeKind.SECTION, label="99", text="Section 99."),
            ),
        ),
        supplements=(),
    )


def _insert_section(label: str, sequence: int) -> LegalOperation:
    payload = IRNode(kind=IRNodeKind.SECTION, label=label, text=f"Section {label}.")
    return LegalOperation(
        op_id=f"ins-{label}",
        sequence=sequence,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", label.lower()),)),
        payload=payload,
    )


def _body_section_labels(statute: IRStatute) -> list[str]:
    out: list[str] = []

    def _walk(node: IRNode) -> None:
        for child in node.children:
            if str(getattr(child.kind, "value", child.kind)).lower() == "section" and child.label:
                out.append(str(child.label))
            _walk(child)

    _walk(statute.body)
    return out


def test_out_of_order_suffix_chain_materializes_in_label_order() -> None:
    # Feed the chain reversed: 98C (suffix-of-suffix) first, then 98B, then 98A.
    ops = [
        _insert_section("98C", 0),
        _insert_section("98B", 1),
        _insert_section("98A", 2),
    ]
    replayed = replay_uk_ops(_base_98_99(), ops)
    assert _body_section_labels(replayed) == ["98", "98A", "98B", "98C", "99"]


def test_in_order_suffix_chain_materializes_in_label_order() -> None:
    ops = [
        _insert_section("98A", 0),
        _insert_section("98B", 1),
        _insert_section("98C", 2),
    ]
    replayed = replay_uk_ops(_base_98_99(), ops)
    assert _body_section_labels(replayed) == ["98", "98A", "98B", "98C", "99"]


def test_suffix_section_lands_when_intermediate_sibling_absent() -> None:
    # Only 98C is inserted; 98A and 98B are never created. 98C must still land in
    # its label-sorted position between base sections 98 and 99 rather than being
    # dropped for lack of a sibling anchor.
    replayed = replay_uk_ops(_base_98_99(), [_insert_section("98C", 0)])
    assert _body_section_labels(replayed) == ["98", "98C", "99"]
