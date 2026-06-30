"""NO parallel-run equality gate for the Wave 0 ordering-kernel cutover.

``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §4 Wave 0 mandates: before cutting
``apply_no_ops`` over from its bespoke group-sort + ``_ordered_renumber_group`` +
direct ``detect_cross_act_same_moment_conflicts`` cluster to ``order_ops``, run
BOTH paths on representative NO op sets (especially renumber-vacate cases and
same-moment cases) and assert IDENTICAL (a) ordered op list and (b) findings set.
This test encodes that gate AND drives the REAL ``apply_no_ops`` fold, proving the
cutover is grounding-neutral (NO byte-identical).

The "old path" is reconstructed here verbatim — the exact group-sort +
REPEAL-first / topological-RENUMBER / rest-by-sequence fold and direct-detector
pre-pass ``apply_no_ops`` used at base ``52923487`` (before the
``order_ops`` cutover). The "new path" is the production
``order_ops(ops, no_ordering_profile())`` the grafter now uses. Equality of the
two on representative op sets — including the renumber-vacate topological cases
(the highest-risk reorder) — is the cutover proof.
"""
from __future__ import annotations

import itertools

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
from lawvm.norway.grafter import (
    _no_sort_key,
    apply_no_ops,
    no_ordering_profile,
    order_ops,
)

NO_SAME_MOMENT_KIND = "no_same_moment_cross_act_incompatible_payload_ambiguous"


# ── op builders (mirror the NO production op shape) ──────────────────────────


def _addr(label: str) -> LegalAddress:
    return LegalAddress(path=(("section", label),))


def _replace(op_id, sequence, label, source_id, effective, enacted="2025-01-01"):
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=_addr(label),
        payload=IRNode(kind=IRNodeKind.SECTION, label=label, text=f"ny {label}"),
        source=OperationSource(statute_id=source_id, effective=effective, enacted=enacted),
    )


def _repeal(op_id, sequence, label, source_id, effective, enacted="2025-01-01"):
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPEAL,
        target=_addr(label),
        source=OperationSource(statute_id=source_id, effective=effective, enacted=enacted),
    )


def _renumber(op_id, sequence, frm, to, source_id, effective, enacted="2025-01-01"):
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.RENUMBER,
        target=_addr(frm),
        destination=_addr(to),
        source=OperationSource(statute_id=source_id, effective=effective, enacted=enacted),
    )


def _insert(op_id, sequence, label, source_id, effective, enacted="2025-01-01"):
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.INSERT,
        target=_addr(label),
        payload=IRNode(kind=IRNodeKind.SECTION, label=label, text=f"ny {label}"),
        source=OperationSource(statute_id=source_id, effective=effective, enacted=enacted),
    )


# ── representative NO op sets (the "corpus" for the gate) ─────────────────────


def _op_sets() -> list[list[LegalOperation]]:
    return [
        # empty
        [],
        # single op, no conflict
        [_replace("s1", 1, "5", "no/act-a/2025", "2026-01-01")],
        # renumber-vacate chain: 5->6, 6->7. The 6->7 op must vacate §6 BEFORE
        # 5->6 occupies it. Topological order: (6->7) then (5->6).
        [
            _renumber("r1", 1, "5", "6", "no/act-a/2025", "2026-01-01"),
            _renumber("r2", 2, "6", "7", "no/act-a/2025", "2026-01-01"),
        ],
        # same chain but presented in already-vacating order (idempotent topo).
        [
            _renumber("r2", 1, "6", "7", "no/act-a/2025", "2026-01-01"),
            _renumber("r1", 2, "5", "6", "no/act-a/2025", "2026-01-01"),
        ],
        # longer chain 3->4, 4->5, 5->6 (must vacate deepest-occupied first).
        [
            _renumber("a", 1, "3", "4", "no/act-a/2025", "2026-01-01"),
            _renumber("b", 2, "4", "5", "no/act-a/2025", "2026-01-01"),
            _renumber("c", 3, "5", "6", "no/act-a/2025", "2026-01-01"),
        ],
        # mixed group: a REPEAL, two renumbers (chain), and a replace — must come
        # out REPEAL-first, then topological renumbers, then the replace.
        [
            _replace("rep", 4, "10", "no/act-a/2025", "2026-01-01"),
            _renumber("rn1", 2, "5", "6", "no/act-a/2025", "2026-01-01"),
            _repeal("rpl", 1, "1", "no/act-a/2025", "2026-01-01"),
            _renumber("rn2", 3, "6", "7", "no/act-a/2025", "2026-01-01"),
        ],
        # two distinct acts, same target, same date -> incompatible REPLACE pair.
        [
            _replace("a", 1, "5", "no/act-a/2025", "2026-01-01"),
            _replace("b", 2, "5", "no/act-b/2025", "2026-01-01"),
        ],
        # REPEAL vs REPLACE same moment -> incompatible.
        [
            _replace("a", 1, "5", "no/act-a/2025", "2026-01-01"),
            _repeal("b", 2, "5", "no/act-b/2025", "2026-01-01"),
        ],
        # two REPEALs same target -> NOT incompatible (no finding).
        [
            _repeal("a", 1, "5", "no/act-a/2025", "2026-01-01"),
            _repeal("b", 2, "5", "no/act-b/2025", "2026-01-01"),
        ],
        # two distinct conflict groups (different targets) -> finding LIST order
        # follows detector input order (the byte-sensitive multi-finding case).
        [
            _replace("p", 1, "5", "no/act-a/2025", "2026-01-01"),
            _replace("q", 2, "5", "no/act-b/2025", "2026-01-01"),
            _replace("r", 3, "7", "no/act-c/2025", "2026-01-01"),
            _replace("s", 4, "7", "no/act-d/2025", "2026-01-01"),
        ],
        # distinct effective dates -> separate groups, no same-moment finding,
        # ordering split into two temporal groups.
        [
            _replace("a", 1, "5", "no/act-a/2025", "2026-01-01"),
            _renumber("b", 2, "5", "6", "no/act-b/2025", "2027-01-01"),
            _insert("c", 3, "9", "no/act-a/2025", "2026-01-01"),
        ],
        # multi-group renumber: act-a chain at one date, act-b chain at another.
        [
            _renumber("a1", 1, "5", "6", "no/act-a/2025", "2026-01-01"),
            _renumber("a2", 2, "6", "7", "no/act-a/2025", "2026-01-01"),
            _renumber("b1", 3, "2", "3", "no/act-b/2025", "2027-01-01"),
            _renumber("b2", 4, "3", "4", "no/act-b/2025", "2027-01-01"),
        ],
    ]


# ── OLD path: the verbatim pre-cutover NO ordering cluster (base 52923487) ────


def _old_group_sort_key(op: LegalOperation) -> tuple[str, str, str, int]:
    effective = op.source.effective if op.source and op.source.effective else ""
    enacted = op.source.enacted if op.source and op.source.enacted else ""
    source_id = op.source.statute_id if op.source and op.source.statute_id else ""
    return (effective, enacted, source_id, op.sequence)


def _old_group_identity(op: LegalOperation) -> tuple[str, str, str]:
    effective = op.source.effective if op.source and op.source.effective else ""
    enacted = op.source.enacted if op.source and op.source.enacted else ""
    source_id = op.source.statute_id if op.source and op.source.statute_id else ""
    return (effective, enacted, source_id)


def _old_renumber_sort_key(op: LegalOperation):
    return (
        len(op.target.path),
        tuple(_no_sort_key(label) for _kind, label in op.target.path),
        op.sequence,
    )


def _old_ordered_renumber_group(group: list[LegalOperation]) -> list[LegalOperation]:
    renumbers = [
        op
        for op in group
        if op.action is StructuralAction.RENUMBER and op.destination is not None
    ]
    by_target = {op.target.path: op for op in renumbers}
    ordered: list[LegalOperation] = []
    visiting: set = set()
    visited: set = set()

    def _visit(op: LegalOperation) -> None:
        key = op.target.path
        if key in visited:
            return
        if key in visiting:
            return
        visiting.add(key)
        dep = by_target.get(op.destination.path if op.destination is not None else ())
        if dep is not None:
            _visit(dep)
        visiting.remove(key)
        visited.add(key)
        ordered.append(op)

    for op in sorted(renumbers, key=_old_renumber_sort_key, reverse=True):
        _visit(op)
    return ordered


def _old_path(ops: list[LegalOperation]):
    """Reconstruct the pre-cutover NO ordering + direct-detector path.

    Returns ``(ordered_op_ids, findings)`` where the order is the old group-sort
    + REPEAL-first / topological-RENUMBER / rest-by-sequence fold and the
    findings are the ``CompileAdjudication``s the direct detector appended
    (over the raw input ``ops``).
    """
    adjudications: list[CompileAdjudication] = []
    detect_cross_act_same_moment_conflicts(
        ops,
        finder_kind_prefix="no",
        adjudications_out=adjudications,
    )

    ordered: list[LegalOperation] = []
    for _key, group_iter in itertools.groupby(
        sorted(ops, key=_old_group_sort_key), key=_old_group_identity
    ):
        group = list(group_iter)
        ordered.extend(
            sorted(
                (op for op in group if op.action is StructuralAction.REPEAL),
                key=lambda op: op.sequence,
            )
        )
        ordered.extend(_old_ordered_renumber_group(group))
        ordered.extend(
            sorted(
                (
                    op
                    for op in group
                    if op.action
                    not in {StructuralAction.REPEAL, StructuralAction.RENUMBER}
                ),
                key=lambda op: op.sequence,
            )
        )
    return [op.op_id for op in ordered], adjudications


def _new_path(ops: list[LegalOperation]):
    """The production ``order_ops`` path the grafter now uses."""
    ordered = order_ops(ops, no_ordering_profile())
    return [op.op_id for op in ordered.ops], list(ordered.findings)


def _finding_key(a: CompileAdjudication):
    return (
        a.kind,
        a.message,
        a.source_statute,
        a.op_id,
        a.blocking,
        a.phase,
        a.detail.get("resolution"),
        tuple(sorted(a.detail.get("conflicting_affecting_acts", ()))),
        tuple(sorted(op["op_id"] for op in a.detail.get("conflicting_ops", ()))),
    )


def test_no_old_path_equals_new_path_ordered_ops_and_findings() -> None:
    """PARALLEL-RUN GATE: for every representative NO op set (renumber-vacate +
    same-moment + multi-group), the old group-sort/topological/direct-detector
    path and the new order_ops path produce IDENTICAL (a) ordered op list and
    (b) findings set."""
    for ops in _op_sets():
        old_order, old_findings = _old_path(ops)
        new_order, new_findings = _new_path(ops)

        # (a) ordered op list identical — INCLUDING list order (renumber-vacate
        # topological order is the highest-risk reorder).
        assert new_order == old_order, (
            f"ordered op list diverged for op set {[o.op_id for o in ops]!r}: "
            f"old={old_order!r} new={new_order!r}"
        )
        # (b) findings set identical (same count, same finding identities) AND
        # same list order (the multi-group case is order-sensitive).
        assert len(new_findings) == len(old_findings)
        assert [_finding_key(f) for f in new_findings] == [
            _finding_key(f) for f in old_findings
        ], f"findings diverged for op set {[o.op_id for o in ops]!r}"


def test_no_new_path_renumber_vacate_is_topologically_correct() -> None:
    """Explicit assertion that the renumber-vacate stage vacates destinations
    before occupying them (5->6 requires §6 free, so 6->7 sorts first)."""
    ops = [
        _renumber("r1", 1, "5", "6", "no/act-a/2025", "2026-01-01"),
        _renumber("r2", 2, "6", "7", "no/act-a/2025", "2026-01-01"),
    ]
    new_order, _ = _new_path(ops)
    assert new_order == ["r2", "r1"], new_order


def _statute() -> IRStatute:
    return IRStatute(
        statute_id="no/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="5", text="Original"),
            ),
        ),
    )


def test_no_apply_fold_emits_same_findings_as_old_detector_path() -> None:
    """Drive the REAL ``apply_no_ops`` fold and assert the same-moment findings
    it emits equal the old direct-detector path's — the cutover is byte-identical
    on the production lane (not just the isolated order_ops unit)."""
    for ops in _op_sets():
        if not ops:
            continue
        produced: list[CompileAdjudication] = []
        apply_no_ops(
            _statute(),
            list(ops),
            adjudications_out=produced,
            strict_invariants=False,
        )
        produced_same_moment = [
            a for a in produced if a.kind == NO_SAME_MOMENT_KIND
        ]
        _, old_findings = _old_path(ops)

        assert len(produced_same_moment) == len(old_findings)
        assert [_finding_key(f) for f in produced_same_moment] == [
            _finding_key(f) for f in old_findings
        ], f"apply_no_ops findings diverged for op set {[o.op_id for o in ops]!r}"


def test_no_apply_fold_byte_identical_result_across_runs() -> None:
    """Determinism: applying the same ops twice yields identical materialized
    body + identical adjudications (grounding-neutral cutover)."""
    ops = _op_sets()[6]  # the conflicting-pair set
    adj_a: list[CompileAdjudication] = []
    out_a = apply_no_ops(_statute(), list(ops), adjudications_out=adj_a, strict_invariants=False)
    adj_b: list[CompileAdjudication] = []
    out_b = apply_no_ops(_statute(), list(ops), adjudications_out=adj_b, strict_invariants=False)

    assert out_a.body == out_b.body
    assert [_finding_key(a) for a in adj_a] == [_finding_key(b) for b in adj_b]
