"""Fire-drill tests for the EU conserved apply wrapper (AGENTS.md §1.8, §2.9).

Drives a synthesized baseline statute with at least one skip through
:func:`apply_eu_ops_conserved` and asserts the FilterResult is the right typed
partition of the bare :func:`apply_eu_ops` behaviour, and that the returned
statute IS the bare variant's replayed statute (the conserved wrapper adds the
receipt; it does not change replay semantics).
"""
from __future__ import annotations

import pytest

from lawvm.core.filter_result import FilterResult, RejectedItem
from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource, StructuralAction
from lawvm.core.semantic_types import IRNodeKind
from lawvm.eu.pipeline import apply_eu_ops, apply_eu_ops_conserved, EUApplyResult
from lawvm.replay_adjudication import CompileAdjudication


def _baseline_statute() -> IRStatute:
    return IRStatute(
        statute_id="32000R0000",
        title="baseline",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1", text="Section 1"),
                IRNode(kind=IRNodeKind.SECTION, label="2", text="Section 2"),
            ),
        ),
    )


def _replace_op(*, op_id: str, sequence: int, section_label: str, text: str, source_id: str = "2026/1") -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", section_label),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label=section_label, text=text),
        source=OperationSource(statute_id=source_id),
    )


def test_apply_eu_ops_conserved_partitions_accepted_and_skipped() -> None:
    """§1.8: every input op lands in exactly one of accepted / rejected."""
    baseline = _baseline_statute()
    ops = [
        # op #1 — succeeds: REPLACE §1 (target found in baseline).
        _replace_op(op_id="eu-replace-ok", sequence=1, section_label="1", text="replacement"),
        # op #2 — skipped: REPLACE with payload=None (eu_replay_text_payload_missing).
        LegalOperation(
            op_id="eu-replace-no-payload",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "2"),)),
            payload=None,
            source=OperationSource(statute_id="2026/2"),
        ),
    ]

    result = apply_eu_ops_conserved(baseline, ops)

    assert isinstance(result, EUApplyResult)
    assert isinstance(result.filter_result, FilterResult)
    # The returned statute IS the replayed IRStatute — §1's children were
    # replaced; §2's op was skipped (payload missing).
    assert result.statute.body is not baseline.body
    assert result.statute.body.children[0].children == ()  # replaced in place

    # Conservation contract: every input op appears in exactly one lane.
    assert len(result.applied_ops) == 1
    assert result.applied_ops[0].op_id == "eu-replace-ok"

    assert len(result.skipped_items) == 1
    rejected_by_id = {item.item.op_id: item for item in result.skipped_items}
    assert "eu-replace-no-payload" in rejected_by_id
    skipped = rejected_by_id["eu-replace-no-payload"]
    assert isinstance(skipped, RejectedItem)
    assert skipped.reason  # message forwarded from the bare variant's adjudication
    assert skipped.reason_code == "eu_replay_text_payload_missing"
    assert skipped.blocking is False  # EU conserved skips are recorded, not blocking

    # Partition is total (no silent drops, no phantoms). Accepted + rejected = input.
    accepted_ids = {op.op_id for op in result.filter_result.accepted_items}
    rejected_ids = {item.item.op_id for item in result.filter_result.rejected_items}
    input_ids = {op.op_id for op in ops}
    assert accepted_ids | rejected_ids == input_ids
    assert accepted_ids & rejected_ids == set()  # disjoint


def test_apply_eu_ops_conserved_statute_identical_to_bare_variant() -> None:
    """The conserved wrapper mirrors the bare variant — same replay output."""
    baseline = _baseline_statute()
    ops = [
        _replace_op(op_id="eu-replace-ok", sequence=1, section_label="1", text="replacement"),
        # Skipped: REPLACE section:9 not found in the baseline.
        LegalOperation(
            op_id="eu-replace-not-found",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "9"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="9", text="replacement"),
            source=OperationSource(statute_id="2026/2"),
        ),
    ]

    bare_adjudications: list[CompileAdjudication] = []
    bare_statute = apply_eu_ops(baseline, list(ops), adjudications_out=bare_adjudications)

    conserved = apply_eu_ops_conserved(baseline, ops)

    # The two statutes are byte-identical — same replay semantics, same op order.
    assert bare_statute == conserved.statute
    assert bare_statute.body is not baseline.body  # both produced a new body
    # The conserved wrapper preserves the bare variant's adjudication ledger too.
    bare_kinds = {a.kind for a in bare_adjudications}
    assert "eu_replay_target_not_found" in bare_kinds  # one skip was emitted


def test_apply_eu_ops_conserved_forwards_adjudications_out_when_passed() -> None:
    """When the caller passes an ``adjudications_out`` list, the conserved
    wrapper surfaces the bare variant's adjudications there too."""
    baseline = _baseline_statute()
    ops = [
        _replace_op(op_id="eu-replace-ok", sequence=1, section_label="1", text="replacement"),
        LegalOperation(
            op_id="eu-replace-not-found",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "9"),)),  # skipped
            payload=IRNode(kind=IRNodeKind.SECTION, label="9", text="replacement"),
            source=OperationSource(statute_id="2026/2"),
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    result = apply_eu_ops_conserved(baseline, ops, adjudications_out=adjudications)

    assert len(adjudications) == 1
    assert adjudications[0].kind == "eu_replay_target_not_found"
    assert adjudications[0].op_id == "eu-replace-not-found"
    assert len(result.applied_ops) == 1
    assert len(result.skipped_items) == 1


def test_apply_eu_ops_conserved_does_not_silently_accept_empty_op_id_skip() -> None:
    """§1.8 conservation: a SKIPPED op with an empty op_id must NOT silently
    land in the accepted lane."""
    baseline = _baseline_statute()
    ops = [
        LegalOperation(
            op_id="",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "9"),)),  # skipped
            payload=IRNode(kind=IRNodeKind.SECTION, label="9", text="replacement"),
            source=OperationSource(statute_id="2026/2"),
        ),
    ]
    with pytest.raises(ValueError, match="non-empty op_id"):
        apply_eu_ops_conserved(baseline, ops)


def test_apply_eu_ops_conserved_rejects_duplicate_op_ids() -> None:
    """§1.8 conservation: duplicate op_ids mis-partition. The conserved wrapper
    fails loud on duplicate op_ids rather than mis-partitioning."""
    baseline = _baseline_statute()
    ops = [
        _replace_op(op_id="dup", sequence=1, section_label="1", text="replacement"),
        LegalOperation(
            op_id="dup",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "9"),)),  # skipped
            payload=IRNode(kind=IRNodeKind.SECTION, label="9", text="replacement"),
            source=OperationSource(statute_id="2026/2"),
        ),
    ]
    with pytest.raises(ValueError, match="unique"):
        apply_eu_ops_conserved(baseline, ops)
