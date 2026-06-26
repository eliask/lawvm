from __future__ import annotations

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.observed_write_audit import (
    ObservedWriteAudit,
    build_observed_write_audit,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.write_receipt import WriteReceipt


def _section(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)


def _body(*children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=children)


def test_observed_write_audit_clean_when_observed_and_receipt_paths_match() -> None:
    before = _body(_section("1", "old"))
    after = _body(_section("1", "new"))
    receipt = WriteReceipt(
        op_id="replace_1",
        helper="test",
        action="replace",
        bound_target_path=(("section", "1"),),
        landed_primary_path=(("section", "1"),),
        replaced_paths=((("section", "1"),),),
    )

    audit = build_observed_write_audit(before, after, receipt)

    assert audit.audit_status == "clean"
    assert audit.observed_changed_paths == ((("section", "1"),),)
    assert audit.receipt_declared_paths == ((("section", "1"),),)
    assert audit.undeclared_paths == ()
    assert audit.unobserved_declared_paths == ()
    assert audit.matched_rule_ids == ()


def test_observed_write_audit_qualified_for_named_relabel_parent_child_granularity() -> None:
    before = _body(_section("1", "same"))
    after = _body(_section("2", "same"))
    receipt = WriteReceipt(
        op_id="renumber_1_to_2",
        helper="test",
        action="renumber",
        bound_target_path=(("section", "1"),),
        landed_primary_path=(("section", "2"),),
        renumbered_paths=(((("section", "1"),), (("section", "2"),)),),
        migration_rule_ids=("section_relabel_renumber",),
    )

    audit = build_observed_write_audit(before, after, receipt)

    assert audit.audit_status == "qualified"
    assert audit.observed_changed_paths == ((),)
    assert audit.receipt_declared_paths == ((("section", "1"),), (("section", "2"),))
    assert audit.undeclared_paths == ()
    assert audit.unobserved_declared_paths == ()
    assert audit.matched_rule_ids == ("section_relabel_renumber",)


def test_observed_write_audit_flags_declared_write_with_no_observed_change() -> None:
    before = _body(_section("1", "same"))
    after = before
    receipt = WriteReceipt(
        op_id="false_replace",
        helper="test",
        action="replace",
        bound_target_path=(("section", "1"),),
        landed_primary_path=(("section", "1"),),
        replaced_paths=((("section", "1"),),),
    )

    audit = build_observed_write_audit(before, after, receipt)

    assert audit.audit_status == "violation"
    assert audit.observed_changed_paths == ()
    assert audit.receipt_declared_paths == ((("section", "1"),),)
    assert audit.undeclared_paths == ()
    assert audit.unobserved_declared_paths == ((("section", "1"),),)


def test_observed_write_audit_flags_observed_write_outside_receipt() -> None:
    before = _body(_section("1", "old"), _section("2", "old"))
    after = _body(_section("1", "old"), _section("2", "new"))
    receipt = WriteReceipt(
        op_id="misdeclared_replace",
        helper="test",
        action="replace",
        bound_target_path=(("section", "1"),),
        landed_primary_path=(("section", "1"),),
        replaced_paths=((("section", "1"),),),
    )

    audit = build_observed_write_audit(before, after, receipt)

    assert audit.audit_status == "violation"
    assert audit.observed_changed_paths == ((("section", "2"),),)
    assert audit.undeclared_paths == ((("section", "2"),),)
    assert audit.unobserved_declared_paths == ((("section", "1"),),)


def test_observed_write_audit_validates_qualified_rule_ids() -> None:
    with pytest.raises(ValueError, match="qualified requires matched_rule_ids"):
        ObservedWriteAudit(
            op_id="op",
            observed_changed_paths=(),
            receipt_declared_paths=(),
            undeclared_paths=(),
            unobserved_declared_paths=(),
            audit_status="qualified",
        )
