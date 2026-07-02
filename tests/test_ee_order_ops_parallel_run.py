"""EE parallel-run equality gate for the Wave 0 ordering-kernel cutover.

``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §4 Wave 0 mandates: before cutting
``apply_ee_ops`` over from the direct ``detect_cross_act_same_moment_conflicts``
call to ``order_ops``, run BOTH paths on representative EE op sets and assert
IDENTICAL (a) ordered op list and (b) same-moment findings set. This test encodes
that gate and ALSO drives the REAL ``apply_ee_ops`` fold (not just the detector
unit), proving the cutover is grounding-neutral (EE byte-identical).

The "old path" is reconstructed here verbatim — the same direct detector call
``apply_ee_ops`` used before the cutover: ``detect_cross_act_same_moment_conflicts``
on the input ops with ``finder_kind_prefix="ee"`` and EE's own
``ee_same_moment_payloads_incompatible`` predicate, findings appended to an
``adjudications_out`` list. The "new path" is the production
``order_ops(ops, ee_ordering_profile())`` the grafter now uses. Equality of the
two on representative op sets is the cutover proof.

EE diverges from SE in two load-bearing ways exercised here:

  * EE supplies its OWN incompatible-payload predicate (REPEAL+TEXT_REPLACE is
    incompatible per EE; the shared default treats it as compatible). The op
    sets include a REPEAL vs TEXT_REPLACE pair so the EE-specific predicate is
    actually driven through both paths.
  * EE's production ops are stamped with a monotonically ascending global
    sequence upstream; the kernel's stable sort by (sequence, sequence) is then
    input order. An out-of-input-order op set is included to confirm the kernel
    re-sorts to sequence order without changing the findings SET.
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
from lawvm.core.op_ordering import order_ops
from lawvm.core.semantic_types import IRNodeKind
from lawvm.estonia.grafter import apply_ee_ops
from lawvm.estonia.ordering import (
    EE_SAME_MOMENT_AMBIGUITY_RULE_ID,
    ee_ordering_profile,
    ee_same_moment_payloads_incompatible,
)
from lawvm.replay_adjudication import CompileAdjudication


# ── op / statute builders (mirror the EE production op shape) ────────────────


def _statute(section_label: str = "5", text: str = "Algne tekst") -> IRStatute:
    return IRStatute(
        statute_id="ee/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label=section_label, text=text),
            ),
        ),
    )


def _replace(op_id, sequence, label, source_id, effective, text="uus sõnastus"):
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


def _text_replace(op_id, sequence, label, source_id, effective, old="Algne"):
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.TEXT_PATCH,
        target=LegalAddress(path=(("section", label),)),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="asendatud fragment",
            attrs={"old_text": old},
        ),
        source=OperationSource(statute_id=source_id, effective=effective),
    )


# ── representative EE op sets (the "corpus" for the gate) ─────────────────────


def _op_sets() -> list[list[LegalOperation]]:
    return [
        # empty
        [],
        # single op, no conflict
        [_replace("s1", 1, "5", "ee/act-a/2025", "2026-01-01")],
        # two distinct acts, same target, same date -> incompatible REPLACE pair
        [
            _replace("a", 1, "5", "ee/act-a/2025", "2026-01-01"),
            _replace("b", 2, "5", "ee/act-b/2025", "2026-01-01"),
        ],
        # REPEAL vs REPLACE same moment -> incompatible
        [
            _replace("a", 1, "5", "ee/act-a/2025", "2026-01-01"),
            _repeal("b", 2, "5", "ee/act-b/2025", "2026-01-01"),
        ],
        # EE-SPECIFIC: REPEAL vs TEXT_REPLACE same moment -> incompatible per
        # EE's predicate (the shared default would treat it as compatible). This
        # is the divergence the EE profile's predicate must carry through both
        # paths identically.
        [
            _repeal("a", 1, "5", "ee/act-a/2025", "2026-01-01"),
            _text_replace("b", 2, "5", "ee/act-b/2025", "2026-01-01"),
        ],
        # two TEXT_REPLACEs same target, distinct acts -> NOT incompatible
        # (both fragment-level; EE predicate short-circuits the both-fragment
        # case to False).
        [
            _text_replace("a", 1, "5", "ee/act-a/2025", "2026-01-01"),
            _text_replace("b", 2, "5", "ee/act-b/2025", "2026-01-01"),
        ],
        # two REPEALs same target -> NOT incompatible (no finding)
        [
            _repeal("a", 1, "5", "ee/act-a/2025", "2026-01-01"),
            _repeal("b", 2, "5", "ee/act-b/2025", "2026-01-01"),
        ],
        # different effective dates -> no same-moment finding
        [
            _replace("a", 1, "5", "ee/act-a/2025", "2026-01-01"),
            _replace("b", 2, "5", "ee/act-b/2025", "2027-01-01"),
        ],
        # same act, two ops -> no cross-act finding
        [
            _replace("a1", 1, "5", "ee/act-a/2025", "2026-01-01"),
            _replace("a2", 2, "5", "ee/act-a/2025", "2026-01-01"),
        ],
        # multi-target, mixed: one conflicting pair on §5, an unrelated §7 op
        [
            _replace("p", 1, "5", "ee/act-a/2025", "2026-01-01"),
            _replace("q", 2, "7", "ee/act-c/2025", "2026-01-01"),
            _replace("r", 3, "5", "ee/act-b/2025", "2026-01-01"),
        ],
        # OUT-OF-INPUT-ORDER: ops supplied with descending sequence. The kernel
        # stably re-sorts to ascending sequence; the findings SET is unchanged
        # (each finding's internals are sorted, so ordering is content-stable).
        [
            _replace("r", 3, "5", "ee/act-b/2025", "2026-01-01"),
            _replace("q", 2, "7", "ee/act-c/2025", "2026-01-01"),
            _replace("p", 1, "5", "ee/act-a/2025", "2026-01-01"),
        ],
    ]


def _old_path(ops: list[LegalOperation]):
    """Reconstruct the pre-cutover direct-detector path.

    Returns ``(ordered_op_ids, findings)`` where the order is the input order
    sorted by sequence (the old apply fold's ``sorted_ops = sorted(ops, ...)``)
    and the findings are the ``CompileAdjudication``s the direct detector — with
    EE's own predicate — appended.
    """
    adjudications: list[CompileAdjudication] = []
    detect_cross_act_same_moment_conflicts(
        ops,
        finder_kind_prefix="ee",
        incompatible_payload_predicate=ee_same_moment_payloads_incompatible,
        adjudications_out=adjudications,
    )
    ordered_ids = [op.op_id for op in sorted(ops, key=lambda o: o.sequence)]
    return ordered_ids, adjudications


def _new_path(ops: list[LegalOperation]):
    """The production ``order_ops`` path the grafter now uses."""
    ordered = order_ops(ops, ee_ordering_profile())
    return [op.op_id for op in ordered.ops], list(ordered.findings)


def _finding_key(a: CompileAdjudication):
    """A comparable identity for a finding — compare the load-bearing fields
    (CompileAdjudication has no cross-construction __eq__ we can rely on over its
    frozen detail mapping)."""
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


def test_ee_old_path_equals_new_path_ordered_ops_and_findings() -> None:
    """PARALLEL-RUN GATE: for every representative EE op set, the old direct-
    detector path and the new order_ops path produce IDENTICAL (a) ordered op
    list and (b) findings set."""
    for ops in _op_sets():
        old_order, old_findings = _old_path(ops)
        new_order, new_findings = _new_path(ops)

        # (a) ordered op list identical (both are ascending-sequence order).
        assert new_order == old_order, (
            f"ordered op list diverged for op set {[o.op_id for o in ops]!r}: "
            f"old={old_order!r} new={new_order!r}"
        )
        # (b) findings set identical (same count, same finding identities).
        assert len(new_findings) == len(old_findings)
        assert sorted(_finding_key(f) for f in new_findings) == sorted(
            _finding_key(f) for f in old_findings
        ), f"findings diverged for op set {[o.op_id for o in ops]!r}"


def test_ee_apply_fold_emits_same_findings_as_old_detector_path() -> None:
    """Drive the REAL ``apply_ee_ops`` fold and assert the same-moment findings
    it emits equal the old direct-detector path's — the cutover is byte-identical
    on the production lane (not just the isolated order_ops unit)."""
    for ops in _op_sets():
        if not ops:
            continue
        statute = _statute()
        produced: list[CompileAdjudication] = []
        apply_ee_ops(statute, list(ops), adjudications_out=produced)
        produced_same_moment = [
            a for a in produced if a.kind == EE_SAME_MOMENT_AMBIGUITY_RULE_ID
        ]

        _, old_findings = _old_path(ops)

        assert len(produced_same_moment) == len(old_findings)
        assert sorted(_finding_key(f) for f in produced_same_moment) == sorted(
            _finding_key(f) for f in old_findings
        ), f"apply_ee_ops findings diverged for op set {[o.op_id for o in ops]!r}"


def test_ee_apply_fold_byte_identical_result_across_runs() -> None:
    """Determinism: applying the same ops twice yields identical materialized
    body + identical adjudications (grounding-neutral cutover)."""
    ops = _op_sets()[2]  # the conflicting-pair set
    statute = _statute()

    adj_a: list[CompileAdjudication] = []
    out_a = apply_ee_ops(statute, list(ops), adjudications_out=adj_a)
    adj_b: list[CompileAdjudication] = []
    out_b = apply_ee_ops(statute, list(ops), adjudications_out=adj_b)

    assert out_a.body == out_b.body
    assert [_finding_key(a) for a in adj_a] == [_finding_key(b) for b in adj_b]


def test_ee_specific_repeal_vs_text_replace_is_flagged_through_kernel() -> None:
    """The EE-specific divergence: REPEAL vs TEXT_REPLACE at the same moment is
    incompatible per EE's predicate and MUST surface a finding through the
    kernel path (the shared DEFAULT predicate would NOT flag it). This pins that
    the EE profile actually carries EE's own predicate into ``order_ops``."""
    ops = [
        _repeal("a", 1, "5", "ee/act-a/2025", "2026-01-01"),
        _text_replace("b", 2, "5", "ee/act-b/2025", "2026-01-01"),
    ]
    _, new_findings = _new_path(ops)
    assert len(new_findings) == 1, (
        "EE's predicate must flag REPEAL+TEXT_REPLACE through the kernel; "
        f"got {new_findings!r}"
    )
    detail = new_findings[0].detail
    assert detail["resolution"] == "sequence_order_unproven"
    assert set(detail["conflicting_affecting_acts"]) == {
        "ee/act-a/2025",
        "ee/act-b/2025",
    }
