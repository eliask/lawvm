"""Fire-drill tests for the SE conserved apply wrapper (AGENTS.md §1.8, §2.9).

Drives a synthesized statute with at least one skip through
:func:`apply_se_ops_conserved` and asserts the FilterResult is the right typed
partition of the bare :func:`apply_se_ops` behaviour, and that the returned
statute IS the bare variant's replayed statute (the conserved wrapper adds the
receipt; it does not change replay semantics). Mirrors
``tests/test_ee_apply_conserved.py``.

The forward-proofing test (``test_se_skip_kind_filter_excludes_future_recovery_kinds``)
asserts the ``_SE_SKIP_ADJUDICATION_KINDS`` frozenset is the partition gate: a
non-skip-kind adjudication carrying an op_id (the shape a future SE recovery
adjudication would take) does NOT mark its op as rejected. Today every per-op
adjudication SE emits IS a genuine skip, so the filter is a no-op on current
behaviour; this test pins the gate so a future recovery adjudication cannot
silently mis-bucket an applied op.
"""
from __future__ import annotations

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
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.sweden.grafter import (
    _SE_SKIP_ADJUDICATION_KINDS,
    apply_se_ops,
    apply_se_ops_conserved,
    SEApplyResult,
)


def _statute_with_section(label: str = "5", text: str = "Original text") -> IRStatute:
    return IRStatute(
        statute_id="se/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label=label, text=text),),
        ),
    )


def _replace_op(*, op_id: str, sequence: int, label: str, text: str, source_id: str = "se/amend") -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", label),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label=label, text=text),
        source=OperationSource(statute_id=source_id),
    )


def test_apply_se_ops_conserved_partitions_accepted_and_skipped() -> None:
    """§1.8: every input op lands in exactly one of accepted / rejected."""
    statute = _statute_with_section("5", "Original.")
    ops = [
        # op #1 — succeeds: REPLACE §5 with a valid payload.
        _replace_op(op_id="se-replace-ok", sequence=1, label="5", text="Ny lydelse."),
        # op #2 — skipped: REPLACE §99 (target not found in the body) emits
        # ``se_replay_target_not_found`` and ``continue``s (the op is NOT applied).
        _replace_op(op_id="se-replace-missing", sequence=2, label="99", text="Nytt."),
    ]

    result = apply_se_ops_conserved(statute, ops)

    assert isinstance(result, SEApplyResult)
    assert isinstance(result.filter_result, FilterResult)
    # The returned statute IS the replayed IRStatute — §5's text was replaced,
    # but §99's failed op did not change anything.
    assert result.statute.body is not statute.body
    assert result.statute.body.children[0].text == "Ny lydelse."

    # Conservation contract: every input op appears in exactly one lane.
    assert len(result.applied_ops) == 1
    assert result.applied_ops[0].op_id == "se-replace-ok"

    assert len(result.skipped_items) == 1
    rejected_by_id = {item.item.op_id: item for item in result.skipped_items}
    assert "se-replace-missing" in rejected_by_id
    skipped = rejected_by_id["se-replace-missing"]
    assert isinstance(skipped, RejectedItem)
    assert skipped.reason  # message forwarded from the bare variant's adjudication
    assert skipped.reason_code == "se_replay_target_not_found"
    assert skipped.blocking is False  # SE conserved skips are recorded, not blocking


def test_apply_se_ops_conserved_statute_matches_bare_apply() -> None:
    """The conserved wrapper must not change replay semantics (§1.8 receipt-only)."""
    statute = _statute_with_section("5", "Original.")
    ops = [_replace_op(op_id="se-replace-ok", sequence=1, label="5", text="Ny lydelse.")]

    bare_statute = apply_se_ops(statute, list(ops))
    conserved = apply_se_ops_conserved(statute, ops)

    # Body text must match byte-for-byte (conserved wraps, does not reinterpret).
    assert bare_statute.body.children[0].text == conserved.statute.body.children[0].text


def test_se_skip_kind_filter_excludes_future_recovery_kinds() -> None:
    """``_SE_SKIP_ADJUDICATION_KINDS`` is the partition gate, not the bare op_id set.

    Forward-proofing: if a future SE recovery adjudication carries an op_id
    (mirroring EE/NO/EU recovery rules like ``ee_text_replace_unique_descendant_*``
    that fire when an op IS applied via a named recovery), the kind filter
    must keep it OUT of the rejected lane. Today no such adjudication exists;
    this test stubs one via the ``adjudications_out`` channel that
    :func:`apply_se_ops_conserved` reads back after bare apply.
    """
    statute = _statute_with_section("5", "Original.")
    # Op succeeds (would land in accepted lane) AND a synthetic future recovery
    # adjudication is injected via the caller-provided ``adjudications_out``.
    ops = [_replace_op(op_id="se-replace-ok", sequence=1, label="5", text="Ny lydelse.")]
    # Pre-populate the caller's adjudications_out with a hypothetical recovery
    # adjudication whose kind is NOT in ``_SE_SKIP_ADJUDICATION_KINDS``.
    future_recovery_kind = "se_replay_future_recovery_hypothetical"
    assert future_recovery_kind not in _SE_SKIP_ADJUDICATION_KINDS, (
        "test fixture must use a kind outside the skip set; if this fires, the "
        "frozenset gained a kind the test did not intend to inject"
    )
    adjudications: list[CompileAdjudication] = [
        CompileAdjudication(
            kind=future_recovery_kind,
            message="hypothetical future SE recovery adjudication (op WAS applied).",
            source_statute="se/amend",
            op_id="se-replace-ok",
            blocking=False,
            phase="replay",
        )
    ]
    result = apply_se_ops_conserved(statute, ops, adjudications_out=adjudications)

    # The recovery-kind adjudication must NOT have mis-bucketed the applied op
    # into the rejected lane — the kind filter excludes it from ``skipped_op_ids``.
    assert len(result.applied_ops) == 1
    assert result.applied_ops[0].op_id == "se-replace-ok"
    assert len(result.skipped_items) == 0
