"""SE parallel-run equality gate for the Wave 0 ordering-kernel cutover.

``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §4 Wave 0 mandates: before cutting
``apply_se_ops`` over from the direct ``detect_cross_act_same_moment_conflicts``
call to ``order_ops``, run BOTH paths on the SE corpus and assert IDENTICAL
(a) ordered op list and (b) findings set. This test encodes that gate and drives
the REAL ``apply_se_ops`` fold (not just the detector unit), proving the cutover
is grounding-neutral (SE byte-identical).

The "old path" is reconstructed here verbatim — the same direct detector call
``apply_se_ops`` used at base ``c20986cf`` (ops in input order; findings appended
to an ``adjudications_out`` list). The "new path" is the production
``order_ops(ops, se_ordering_profile())`` the grafter now uses. Equality of the
two on representative op sets is the cutover proof.
"""
from __future__ import annotations

from lawvm.core.cross_act_same_moment import (
    detect_cross_act_same_moment_conflicts,
)
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
from lawvm.sweden.grafter import apply_se_ops, order_ops, se_ordering_profile

SE_SAME_MOMENT_KIND = "se_same_moment_cross_act_incompatible_payload_ambiguous"


# ── op / statute builders (mirror the SE production op shape) ────────────────


def _statute(section_label: str = "5", text: str = "Ursprunglig text") -> IRStatute:
    return IRStatute(
        statute_id="se/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label=section_label, text=text),
            ),
        ),
    )


def _replace(op_id, sequence, label, source_id, effective, text="ny lydelse"):
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", label),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label=label, text=text),
        source=OperationSource(statute_id=source_id, effective=effective),
    )


def _repeal(op_id, sequence, label, source_id, effective):
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", label),)),
        source=OperationSource(statute_id=source_id, effective=effective),
    )


# ── representative SE op sets (the "corpus" for the gate) ─────────────────────


def _op_sets() -> list[list[LegalOperation]]:
    return [
        # empty
        [],
        # single op, no conflict
        [_replace("s1", 1, "5", "se/act-a/2025", "2026-01-01")],
        # two distinct acts, same target, same date -> incompatible REPLACE pair
        [
            _replace("a", 1, "5", "se/act-a/2025", "2026-01-01"),
            _replace("b", 2, "5", "se/act-b/2025", "2026-01-01"),
        ],
        # REPEAL vs REPLACE same moment -> incompatible
        [
            _replace("a", 1, "5", "se/act-a/2025", "2026-01-01"),
            _repeal("b", 2, "5", "se/act-b/2025", "2026-01-01"),
        ],
        # two REPEALs same target -> NOT incompatible (no finding)
        [
            _repeal("a", 1, "5", "se/act-a/2025", "2026-01-01"),
            _repeal("b", 2, "5", "se/act-b/2025", "2026-01-01"),
        ],
        # different effective dates -> no same-moment finding
        [
            _replace("a", 1, "5", "se/act-a/2025", "2026-01-01"),
            _replace("b", 2, "5", "se/act-b/2025", "2027-01-01"),
        ],
        # same act, two ops -> no cross-act finding
        [
            _replace("a1", 1, "5", "se/act-a/2025", "2026-01-01"),
            _replace("a2", 2, "5", "se/act-a/2025", "2026-01-01"),
        ],
        # multi-target, mixed: one conflicting pair on §5, an unrelated §7 op
        [
            _replace("p", 1, "5", "se/act-a/2025", "2026-01-01"),
            _replace("q", 2, "7", "se/act-c/2025", "2026-01-01"),
            _replace("r", 3, "5", "se/act-b/2025", "2026-01-01"),
        ],
    ]


def _old_path(ops: list[LegalOperation]):
    """Reconstruct the pre-cutover direct-detector path (base c20986cf).

    Returns ``(ordered_op_ids, findings)`` where the order is input order (the
    old apply fold iterated ``ops`` directly) and the findings are the
    ``CompileAdjudication``s the direct detector appended.
    """
    adjudications: list[CompileAdjudication] = []
    detect_cross_act_same_moment_conflicts(
        ops,
        finder_kind_prefix="se",
        adjudications_out=adjudications,
    )
    return [op.op_id for op in ops], adjudications


def _new_path(ops: list[LegalOperation]):
    """The production ``order_ops`` path the grafter now uses."""
    ordered = order_ops(ops, se_ordering_profile())
    return [op.op_id for op in ordered.ops], list(ordered.findings)


def _finding_key(a: CompileAdjudication):
    """A comparable identity for a finding (CompileAdjudication has no __eq__
    over its frozen detail mapping that we can rely on cross-construction, so
    compare the load-bearing fields)."""
    return (
        a.kind,
        a.message,
        a.source_statute,
        a.op_id,
        a.blocking,
        a.phase,
        a.detail.get("resolution"),
        tuple(sorted(a.detail.get("conflicting_affecting_acts", ()))),
        tuple(
            sorted(op["op_id"] for op in a.detail.get("conflicting_ops", ()))
        ),
    )


def test_se_old_path_equals_new_path_ordered_ops_and_findings() -> None:
    """PARALLEL-RUN GATE: for every representative SE op set, the old direct-
    detector path and the new order_ops path produce IDENTICAL (a) ordered op
    list and (b) findings set."""
    for ops in _op_sets():
        old_order, old_findings = _old_path(ops)
        new_order, new_findings = _new_path(ops)

        # (a) ordered op list identical.
        assert new_order == old_order, (
            f"ordered op list diverged for op set {[o.op_id for o in ops]!r}: "
            f"old={old_order!r} new={new_order!r}"
        )
        # (b) findings set identical (same count, same finding identities).
        assert len(new_findings) == len(old_findings)
        assert sorted(_finding_key(f) for f in new_findings) == sorted(
            _finding_key(f) for f in old_findings
        ), f"findings diverged for op set {[o.op_id for o in ops]!r}"


def test_se_apply_fold_emits_same_findings_as_old_detector_path() -> None:
    """Drive the REAL ``apply_se_ops`` fold and assert the same-moment findings
    it emits equal the old direct-detector path's — the cutover is byte-identical
    on the production lane (not just the isolated order_ops unit)."""
    for ops in _op_sets():
        if not ops:
            continue
        statute = _statute()
        produced: list[CompileAdjudication] = []
        apply_se_ops(statute, list(ops), adjudications_out=produced)
        produced_same_moment = [
            a for a in produced if a.kind == SE_SAME_MOMENT_KIND
        ]

        _, old_findings = _old_path(ops)

        assert len(produced_same_moment) == len(old_findings)
        assert sorted(_finding_key(f) for f in produced_same_moment) == sorted(
            _finding_key(f) for f in old_findings
        ), f"apply_se_ops findings diverged for op set {[o.op_id for o in ops]!r}"


def test_se_apply_fold_byte_identical_result_across_runs() -> None:
    """Determinism: applying the same ops twice yields identical materialized
    body + identical adjudications (grounding-neutral cutover)."""
    ops = _op_sets()[2]  # the conflicting-pair set
    statute = _statute()

    adj_a: list[CompileAdjudication] = []
    out_a = apply_se_ops(statute, list(ops), adjudications_out=adj_a)
    adj_b: list[CompileAdjudication] = []
    out_b = apply_se_ops(statute, list(ops), adjudications_out=adj_b)

    assert out_a.body == out_b.body
    assert [_finding_key(a) for a in adj_a] == [_finding_key(b) for b in adj_b]
