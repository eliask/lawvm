"""Fire-drill tests for the NO conserved apply wrapper (AGENTS.md §1.8, §2.9).

Drives a synthesized statute with at least one skip through
:func:`apply_no_ops_conserved` and asserts the FilterResult is the right typed
partition of the bare :func:`apply_no_ops` behaviour, and that the returned
statute IS the bare variant's replayed statute (the conserved wrapper adds the
receipt; it does not change replay semantics).
"""
from __future__ import annotations

import pytest

from lawvm.core.filter_result import FilterResult, RejectedItem
from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.norway.grafter import apply_no_ops, apply_no_ops_conserved, NOApplyResult
from lawvm.replay_adjudication import CompileAdjudication


def _statute_with_section(label: str = "2", text: str = "Original text.") -> IRStatute:
    return IRStatute(
        statute_id="no/lov/2025-01-01-1",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label=label, text=text),),
        ),
    )


def _replace_op(*, op_id: str, sequence: int, label: str, source_id: str = "no/lovtid/2025-02-02-5") -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", label),)),
        payload=IRNode(
            kind=IRNodeKind.SECTION,
            label=label,
            children=(IRNode(kind=IRNodeKind.HEADING, text="Ny tittel"),),
        ),
        source=OperationSource(statute_id=source_id),
    )


def test_apply_no_ops_conserved_partitions_accepted_and_skipped() -> None:
    """§1.8: every input op lands in exactly one of accepted / rejected."""
    statute = _statute_with_section("2", "Original.")
    ops = [
        # op #1 — succeeds: REPLACE §2 (target found in the statute body).
        _replace_op(op_id="no-replace-ok", sequence=1, label="2"),
        # op #2 — skipped: HEADING_REPLACE on §2 (NO replay supports only
        # REPLACE / REPEAL / INSERT / RENUMBER, so HEADING_REPLACE falls through
        # to the ``replay_unsupported_action`` skip path).
        LegalOperation(
            op_id="no-skip-unsupported-heading",
            sequence=2,
            action=StructuralAction.HEADING_REPLACE,
            target=LegalAddress(path=(("section", "2"),)),
            payload=IRNode(kind=IRNodeKind.HEADING, text="Ny tittel"),
            source=OperationSource(statute_id="no/lovtid/2025-02-02-5"),
        ),
    ]

    result = apply_no_ops_conserved(statute, ops)

    assert isinstance(result, NOApplyResult)
    assert isinstance(result.filter_result, FilterResult)
    # The returned statute IS the replayed IRStatute — §2 was replaced; the
    # HEADING_REPLACE op was skipped (action not supported by NO replay).
    assert result.statute.body is not statute.body

    # Conservation contract: every input op appears in exactly one lane.
    assert len(result.applied_ops) == 1
    assert result.applied_ops[0].op_id == "no-replace-ok"

    assert len(result.skipped_items) == 1
    rejected_by_id = {item.item.op_id: item for item in result.skipped_items}
    assert "no-skip-unsupported-heading" in rejected_by_id
    skipped = rejected_by_id["no-skip-unsupported-heading"]
    assert isinstance(skipped, RejectedItem)
    assert skipped.reason  # message forwarded from the bare variant's adjudication
    assert skipped.reason_code == "replay_unsupported_action"
    assert skipped.blocking is False  # NO conserved skips are recorded, not blocking

    # Partition is total (no silent drops, no phantoms). Accepted + rejected = input.
    accepted_ids = {op.op_id for op in result.filter_result.accepted_items}
    rejected_ids = {item.item.op_id for item in result.filter_result.rejected_items}
    input_ids = {op.op_id for op in ops}
    assert accepted_ids | rejected_ids == input_ids
    assert accepted_ids & rejected_ids == set()  # disjoint


def test_apply_no_ops_conserved_does_not_treat_recovery_as_skip() -> None:
    """§1.8: recovery adjudications (``no_replay_*``) record transformations
    that WERE applied — REPLACE recovered to INSERT, etc. — and must NOT mark
    their op as rejected. The partition uses SKIP_ADJUDICATION_KINDS only
    (``replay_unsupported_action`` / ``replay_unresolved_target`` /
    ``replay_noop``); recovery adjudications record the transformation
    alongside the accepted op, not as a rejection."""
    statute = _statute_with_section("2", "Original.")
    ops = [
        # REPLACE §99 — section does not exist; NO recovers REPLACE→INSERT in
        # the inferred parent (top-level body root when the parent is None).
        # This is the documented ``no_replay_replace_recovered_by_insert``
        # recovery rule (lines ~3780-3821 of the bare variant): the op IS
        # applied, with a recovery adjudication.
        LegalOperation(
            op_id="no-replace-recovered-as-insert",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "99"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="99", text="Recovered as insert."),
            source=OperationSource(statute_id="no/lovtid/2025-02-02-5"),
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    result = apply_no_ops_conserved(statute, ops, adjudications_out=adjudications)

    # The bare variant emitted a recovery adjudication for the op...
    assert any(a.kind == "no_replay_replace_recovered_by_insert" for a in adjudications)
    # ...but the op was APPLIED (recovered to INSERT), so it is ACCEPTED, NOT
    # REJECTED. The recovery adjudication is part of the evidence ledger, not
    # the per-op skip partition.
    assert len(result.applied_ops) == 1
    assert result.applied_ops[0].op_id == "no-replace-recovered-as-insert"
    assert len(result.skipped_items) == 0


def _skip_op(*, op_id: str, sequence: int, label: str = "2", source_id: str = "no/lovtid/2025-02-02-5") -> LegalOperation:
    """Skip-path op: HEADING_REPLACE on a section target (action not in NO's
    supported set {REPLACE, REPEAL, INSERT, RENUMBER}); NO replay emits a
    ``replay_unsupported_action`` adjudication and skips the op."""
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.HEADING_REPLACE,
        target=LegalAddress(path=(("section", label),)),
        payload=IRNode(kind=IRNodeKind.HEADING, text="Ny tittel"),
        source=OperationSource(statute_id=source_id),
    )


def test_apply_no_ops_conserved_statute_identical_to_bare_variant() -> None:
    """The conserved wrapper mirrors the bare variant — same replay output."""
    statute = _statute_with_section("2", "Original.")
    ops = [
        _replace_op(op_id="no-replace-ok", sequence=1, label="2"),
        _skip_op(op_id="no-skip-unsupported-heading", sequence=2),
    ]

    bare_adjudications: list[CompileAdjudication] = []
    bare_statute = apply_no_ops(statute, list(ops), adjudications_out=bare_adjudications)

    conserved = apply_no_ops_conserved(statute, ops)

    # The two statutes are byte-identical — same replay semantics, same op order.
    assert bare_statute == conserved.statute
    assert bare_statute.body is not statute.body  # both produced a new body
    # The conserved wrapper preserves the bare variant's adjudication ledger too.
    bare_kinds = {a.kind for a in bare_adjudications}
    assert "replay_unsupported_action" in bare_kinds  # one skip was emitted


def test_apply_no_ops_conserved_forwards_adjudications_out_when_passed() -> None:
    """When the caller passes an ``adjudications_out`` list, the conserved
    wrapper surfaces the bare variant's adjudications there too."""
    statute = _statute_with_section("2", "Original.")
    ops = [
        _replace_op(op_id="no-replace-ok", sequence=1, label="2"),
        _skip_op(op_id="no-skip-unsupported-heading", sequence=2),
    ]
    adjudications: list[CompileAdjudication] = []
    result = apply_no_ops_conserved(statute, ops, adjudications_out=adjudications)

    assert len(adjudications) == 1
    assert adjudications[0].kind == "replay_unsupported_action"
    assert adjudications[0].op_id == "no-skip-unsupported-heading"
    assert len(result.applied_ops) == 1
    assert len(result.skipped_items) == 1


def test_apply_no_ops_conserved_does_not_silently_accept_empty_op_id_skip() -> None:
    """§1.8 conservation: a SKIPPED op with an empty op_id must NOT silently
    land in the accepted lane."""
    statute = _statute_with_section("2", "Original.")
    ops = [
        LegalOperation(
            op_id="",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "99"),)),  # skipped
            payload=IRNode(kind=IRNodeKind.SECTION, label="99"),
            source=OperationSource(statute_id="no/lovtid/2025-02-02-5"),
        ),
    ]
    with pytest.raises(ValueError, match="non-empty op_id"):
        apply_no_ops_conserved(statute, ops)


def test_apply_no_ops_conserved_rejects_duplicate_op_ids() -> None:
    """§1.8 conservation: duplicate op_ids mis-partition. The conserved wrapper
    fails loud on duplicate op_ids rather than mis-partitioning."""
    statute = _statute_with_section("2", "Original.")
    ops = [
        _replace_op(op_id="dup", sequence=1, label="2"),
        LegalOperation(
            op_id="dup",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "99"),)),  # skipped
            payload=IRNode(kind=IRNodeKind.SECTION, label="99"),
            source=OperationSource(statute_id="no/lovtid/2025-02-02-5"),
        ),
    ]
    with pytest.raises(ValueError, match="unique"):
        apply_no_ops_conserved(statute, ops)


def test_apply_no_ops_conserved_forwarded_strict_flags_to_bare_variant() -> None:
    """The conserved wrapper forwards the bare variant's strict_* flags so
    existing strict-mode behaviour is preserved when production routes through
    the conserved path. The default values match the bare variant's defaults."""
    statute = _statute_with_section("2", "Original.")
    ops = [_replace_op(op_id="no-replace-ok", sequence=1, label="2")]

    result = apply_no_ops_conserved(
        statute,
        ops,
        strict_invariants=True,
        strict_action_family=False,
        strict_recovery=False,
    )

    assert len(result.applied_ops) == 1
    assert len(result.skipped_items) == 0
