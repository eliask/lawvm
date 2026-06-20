"""Tests for the apply write-receipt seam: conservation receipts by construction.

These pin the three guarantees the seam adds:

1. Totality — performing a tree write through the witnessed primitives yields a
   WriteReceipt + ObservedWriteAudit by construction; in a small
   renumber/replace/insert scenario the receipt count equals the landed-write
   count and every audit is clean.
2. Strict-mode blocking — a landed write whose conservation receipt does not
   cover the observed mutation footprint (ObservedWriteAudit.status ==
   "violation") trips a BLOCKING Finding under a strict profile, and does not
   under a permissive profile.
3. Non-optional sink — the apply sinks always carry a receipt accumulator; a
   caller cannot drive the apply path without one (the field is non-Optional and
   defaults to a fresh list).

All fixtures are self-contained (no corpus, no network).

Run:
    uv run pytest tests/test_fi_apply_write_receipt_seam.py -v
"""

from __future__ import annotations

from typing import Any, List, cast

from lawvm.core import tree_ops as _tops
from lawvm.core.compile_result import StrictProfile
from lawvm.core.ir import IRNode
from lawvm.core.mutation_accounting import observed_vs_declared_cross_check
from lawvm.core.observed_write_audit import ObservedWriteAudit
from lawvm.core.phase_result import Finding
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.tree_ops import (
    WriteOutcome,
    insert_sorted_witnessed,
    receipt_from_diff,
    remove_at_witnessed,
    replace_at_witnessed,
)
from lawvm.core.write_receipt import WriteReceipt
from lawvm.finland.apply_ops_boundary import ApplyOpsSinks
from lawvm.finland.apply_resolved_op import (
    WRITE_RECEIPT_VIOLATION_FINDING_CODE,
    ApplyResolvedOpSinks,
    _collect_op_write_receipt,
)
from lawvm.finland.ops import AmendmentOp, ResolvedOp
from lawvm.finland.statute import ReplayState


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _sec(label: str, text: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.SECTION,
        label=label,
        children=(_content(text),),
    )


def _body(*children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.BODY, children=tuple(children))


def _resolved_op(op_id: str, action: str) -> ResolvedOp:
    op = AmendmentOp(
        op_id=op_id,
        op_type=cast("Any", action),
        target_section="1",
        target_unit_kind="section",
        source_statute="2020/1",
    )
    return ResolvedOp.from_amendment_op(
        op,
        muutos_ir=None,
        cross_ir=None,
        target_unit_kind="section",
        target_norm="1",
        target_chapter=None,
    )


# ---------------------------------------------------------------------------
# 1. totality — witnessed primitives yield a receipt + clean audit
# ---------------------------------------------------------------------------


def test_replace_at_witnessed_yields_receipt_and_clean_audit() -> None:
    tree = _body(_sec("1", "old one"), _sec("2", "two"))
    outcome = replace_at_witnessed(
        tree,
        (("section", "1"),),
        _sec("1", "new one"),
        op_id="replace_1",
    )
    assert isinstance(outcome, WriteOutcome)
    # The mutated tree and the receipt are inseparable.
    assert outcome.tree is not tree
    sec1 = next(c for c in outcome.tree.children if c.label == "1")
    assert (sec1.children[0].text or "") == "new one"
    assert isinstance(outcome.receipt, WriteReceipt)
    assert outcome.receipt.op_id == "replace_1"
    assert outcome.receipt.action == "replace"
    # Audit is clean: the receipt footprint equals the observed diff.
    assert isinstance(outcome.audit, ObservedWriteAudit)
    assert outcome.audit.status == "clean"
    assert outcome.audit.undeclared_paths == ()
    assert outcome.audit.unobserved_declared_paths == ()


def test_remove_at_witnessed_yields_receipt_and_clean_audit() -> None:
    tree = _body(_sec("1", "one"), _sec("2", "two"))
    outcome = remove_at_witnessed(tree, (("section", "2"),), op_id="remove_2")
    assert [c.label for c in outcome.tree.children] == ["1"]
    assert outcome.receipt.action == "remove"
    assert outcome.audit.status == "clean"


def test_insert_sorted_witnessed_yields_receipt_and_clean_audit() -> None:
    tree = _body(_sec("1", "one"), _sec("3", "three"))
    outcome = insert_sorted_witnessed(
        tree,
        (),
        _sec("2", "two"),
        op_id="insert_2",
    )
    assert [c.label for c in outcome.tree.children] == ["1", "2", "3"]
    assert outcome.receipt.action == "insert"
    assert outcome.audit.status == "clean"


def test_witnessed_writes_receipt_count_equals_landed_write_count() -> None:
    """|receipts| == |landed writes| across a renumber/replace/insert sequence."""
    tree = _body(_sec("1", "one"), _sec("2", "two"))
    receipts: List[WriteReceipt] = []
    audits: List[ObservedWriteAudit] = []

    # write 1: replace section 1
    o1 = replace_at_witnessed(tree, (("section", "1"),), _sec("1", "ONE"), op_id="w1")
    receipts.append(o1.receipt)
    audits.append(o1.audit)
    # write 2: renumber section 2 -> 2 by remove + insert (two landed writes)
    o2 = remove_at_witnessed(o1.tree, (("section", "2"),), op_id="w2")
    receipts.append(o2.receipt)
    audits.append(o2.audit)
    # write 3: insert section 2 back, renamed text
    o3 = insert_sorted_witnessed(o2.tree, (), _sec("2", "TWO"), op_id="w3")
    receipts.append(o3.receipt)
    audits.append(o3.audit)

    # three landed writes, three receipts, three audits, all clean.
    assert len(receipts) == 3
    assert len(audits) == 3
    assert all(a.status == "clean" for a in audits)
    sec2 = next(c for c in o3.tree.children if c.label == "2")
    assert (sec2.children[0].text or "") == "TWO"


# ---------------------------------------------------------------------------
# 2. strict-mode blocking on a violation-status write
# ---------------------------------------------------------------------------


def _strict_profile() -> StrictProfile:
    # allows_target_guessing=False ⇒ the strict gate that refuses un-accounted
    # writes is active.
    return StrictProfile(name="test_strict", allows_target_guessing=False)


def _permissive_profile() -> StrictProfile:
    return StrictProfile(name="test_permissive", allows_target_guessing=True)


def _states_with_change() -> tuple[ReplayState, ReplayState]:
    before = _body(_sec("1", "one"), _sec("2", "two"))
    after = _tops.replace_at(before, (("section", "1"),), _sec("1", "ONE"))
    return ReplayState(ir=before), ReplayState(ir=after)


def test_strict_mode_blocks_undeclared_tree_touch() -> None:
    """A landed write with an undeclared-touch signal trips a blocking finding.

    This drives the production strict-mode promotion in _collect_op_write_receipt
    with a genuine undeclared-tree-touch cross-check result (the passive
    observed-vs-declared signal) — not a hand-constructed finding.
    """
    prev_state, new_state = _states_with_change()
    rop = _resolved_op("violating_op", "REPLACE")
    findings: List[Finding] = []
    sinks = ApplyResolvedOpSinks(findings_out=findings)

    # A real undeclared-tree-touch result: the op changed section:1 but declared
    # no mutation events explaining it. This is exactly what
    # observed_vs_declared_cross_check produces in production when an op's
    # observed footprint is not covered by its declared events.
    undeclared = observed_vs_declared_cross_check(
        "violating_op",
        "test_helper",
        _tops.diff_ir_paths_identity_pruned(prev_state.ir, new_state.ir),
        events=(),  # no declared events ⇒ everything observed is undeclared
    )
    assert undeclared is not None
    assert undeclared.code == "REPLAY_UNDECLARED_TREE_TOUCH"

    _collect_op_write_receipt(
        prev_state,
        new_state,
        rop=rop,
        strict_profile=_strict_profile(),
        source_statute="2020/1",
        sinks=sinks,
        undeclared_touch=undeclared,
    )

    # totality still holds: one landed write ⇒ one receipt + one audit
    assert len(sinks.write_receipts_out) == 1
    assert len(sinks.write_audits_out) == 1
    # strict mode promoted the undeclared touch to a blocking finding.
    blocking = [f for f in findings if f.blocking]
    assert blocking, "expected a blocking finding for the undeclared-tree-touch write"
    assert blocking[0].kind == WRITE_RECEIPT_VIOLATION_FINDING_CODE
    assert blocking[0].role == "violation"
    assert blocking[0].detail["undeclared_touch_code"] == "REPLAY_UNDECLARED_TREE_TOUCH"


def test_permissive_profile_does_not_block_undeclared_tree_touch() -> None:
    """The same undeclared touch under a permissive profile does NOT block."""
    prev_state, new_state = _states_with_change()
    rop = _resolved_op("perm_op", "REPLACE")
    findings: List[Finding] = []
    sinks = ApplyResolvedOpSinks(findings_out=findings)
    undeclared = observed_vs_declared_cross_check(
        "perm_op",
        "test_helper",
        _tops.diff_ir_paths_identity_pruned(prev_state.ir, new_state.ir),
        events=(),
    )
    assert undeclared is not None
    _collect_op_write_receipt(
        prev_state,
        new_state,
        rop=rop,
        strict_profile=_permissive_profile(),
        source_statute="2020/1",
        sinks=sinks,
        undeclared_touch=undeclared,
    )
    assert len(sinks.write_receipts_out) == 1
    assert not any(f.blocking for f in findings)


def test_collect_op_write_receipt_strict_clean_write_no_finding() -> None:
    """A clean landed write produces a receipt + audit but no blocking finding."""
    prev_state, new_state = _states_with_change()
    rop = _resolved_op("clean_op", "REPLACE")
    findings: List[Finding] = []
    sinks = ApplyResolvedOpSinks(findings_out=findings)

    _collect_op_write_receipt(
        prev_state,
        new_state,
        rop=rop,
        strict_profile=_strict_profile(),
        source_statute="2020/1",
        sinks=sinks,
    )

    # one landed write ⇒ one receipt + one audit.
    assert len(sinks.write_receipts_out) == 1
    assert len(sinks.write_audits_out) == 1
    # the op-level receipt records the observed footprint, so the audit is clean
    # and no blocking finding fires.
    assert sinks.write_audits_out[0].status == "clean"
    assert not any(f.blocking for f in findings)


def test_collect_op_write_receipt_noop_yields_no_receipt() -> None:
    """No tree change ⇒ no landed write ⇒ no receipt (totality holds)."""
    state = ReplayState(ir=_body(_sec("1", "one")))
    rop = _resolved_op("noop", "REPLACE")
    findings: List[Finding] = []
    sinks = ApplyResolvedOpSinks(findings_out=findings)

    _collect_op_write_receipt(
        state,
        state,  # identical state: no write landed
        rop=rop,
        strict_profile=_strict_profile(),
        source_statute="2020/1",
        sinks=sinks,
    )
    assert sinks.write_receipts_out == []
    assert sinks.write_audits_out == []


# ---------------------------------------------------------------------------
# 3. non-optional receipt sink
# ---------------------------------------------------------------------------


def test_apply_resolved_op_sinks_receipt_accumulator_is_non_optional() -> None:
    """The receipt accumulator is always present; you cannot get None."""
    sinks = ApplyResolvedOpSinks()
    assert sinks.write_receipts_out == []
    assert sinks.write_audits_out == []
    # distinct lists per instance (no shared mutable default)
    other = ApplyResolvedOpSinks()
    sinks.write_receipts_out.append(
        WriteReceipt(op_id="x", helper="h", action="a", bound_target_path=None, landed_primary_path=None)
    )
    assert other.write_receipts_out == []


def test_apply_ops_sinks_receipt_accumulator_is_non_optional() -> None:
    sinks = ApplyOpsSinks()
    assert sinks.write_receipts_out == []
    assert sinks.write_audits_out == []
    other = ApplyOpsSinks()
    assert other.write_receipts_out is not sinks.write_receipts_out


def test_receipt_from_diff_records_landed_reality() -> None:
    """receipt_from_diff derives footprint + hashes from the actual diff."""
    before = _body(_sec("1", "one"))
    after = _tops.replace_at(before, (("section", "1"),), _sec("1", "ONE"))
    receipt = receipt_from_diff(
        before,
        after,
        op_id="r",
        helper="h",
        action="replace",
        bound_target_path=(("section", "1"),),
    )
    # The pre/post hashes are keyed on the actual observed diff paths (landed
    # reality), and the changed path's hashes differ.
    assert receipt.pre_hashes  # non-empty: the tree moved
    assert set(receipt.pre_hashes) == set(receipt.post_hashes)
    changed = [
        addr for addr in receipt.pre_hashes if receipt.pre_hashes[addr] != receipt.post_hashes[addr]
    ]
    assert changed, "expected at least one changed-hash path"
    # declared footprint covers the observed change ⇒ a clean audit.
    audit = _tops.build_observed_write_audit(before, after, receipt)
    assert audit.status == "clean"
