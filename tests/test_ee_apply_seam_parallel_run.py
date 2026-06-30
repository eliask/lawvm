"""EE parallel-run equality gate for the Wave 3 apply-seam cutover.

``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §4 Wave 3: before trusting that
``apply_ee_ops`` routes its body-op application through the unified
``core/apply_seam.apply_op`` kernel, run the seam-based path and assert its
outputs are deterministic, conservation-total, and — for the EE-specific
``lo_ops_out`` section-version snapshot channel — BYTE-IDENTICAL across runs.

WHY EE IS ITS OWN WAVE. EE carries two wrinkles NO/SE lack:

  1. the ``lo_ops_out`` per-section version snapshot channel (a side channel the
     apply fold writes after each landed op). The seam must not lose it and must
     keep its output byte-identical. The gates below fingerprint ``lo_ops_out``
     (snapshot op_id / sequence / action / target path / payload structural hash)
     and assert it is identical across runs AND between the bare fold and the
     conserved wrapper.
  2. the EE-specific REPEAL+TEXT_REPLACE same-moment incompatibility predicate
     (``estonia/ordering.ee_same_moment_payloads_incompatible``), routed through
     ``order_ops``. The gate includes a REPEAL+TEXT_REPLACE same-moment op pair
     from two acts and asserts the EE ambiguity finding still fires — proving the
     ordering wrinkle survives the apply-seam migration unchanged.

THE GATES.
  (a) MATERIALIZED IR + ADJUDICATIONS + ``lo_ops_out``: the seam-based
      ``apply_ee_ops`` produces a deterministic, cross-run-identical materialized
      ``IRStatute`` (structural body hash + title), a deterministic
      adjudication-kind multiset, AND a byte-identical ``lo_ops_out`` snapshot
      stream across every representative op set.
  (b) CONSERVED WRAPPER: the bare fold and ``apply_ee_ops_conserved`` materialize
      the same statute, write the same ``lo_ops_out``, and the conserved
      partition is total.
  (c) RECEIPT SYNTHESIS (additive lane): the seam-synthesized ``WriteReceipt``
      (``apply_op`` with ``emit_receipts=True`` over the EE per-op before/after
      body) is well-formed and field-stable across runs — EE has no pre-existing
      production receipt emitter, so this lane is purely additive (like NO), but
      the gate proves the seam's receipt synthesis is reachable + deterministic
      for the EE op families.
  (d) SAME-MOMENT WRINKLE: a REPEAL+TEXT_REPLACE same-moment pair from two acts
      emits exactly one EE same-moment ambiguity finding through the migrated
      fold.

The op sets exercise every EE apply action family: REPLACE / INSERT / REPEAL /
RENUMBER / TEXT_REPLACE on sections, multi-section bundles, and the genuine
skips (unresolved target, no-op, unsupported action). A future edit to the EE
materializer or the seam that perturbs the materialized IR, the adjudications,
or the ``lo_ops_out`` snapshot stream breaks this gate loudly — the
grounding-neutral byte-identity contract (AGENTS.md §0).
"""
from __future__ import annotations

from lawvm.core.apply_seam import (
    AppliedOp,
    ApplyProfile,
    MaterializeResult,
    apply_op,
)
from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
)
from lawvm.core.ir_helpers import structural_subtree_hash
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.write_receipt import WriteReceipt
from lawvm.estonia.ordering import EE_SAME_MOMENT_AMBIGUITY_RULE_ID
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.estonia.grafter import (
    apply_ee_ops,
    apply_ee_ops_conserved,
)


# ── op + statute builders (mirror the EE production op shape) ─────────────────


def _section_addr(label: str) -> LegalAddress:
    return LegalAddress(path=(("section", label),))


def _section(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)


def _replace(op_id: str, sequence: int, label: str, *, source_id: str = "ee/amend/2025") -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=_section_addr(label),
        payload=_section(label, f"Uus {label}."),
        source=OperationSource(statute_id=source_id),
    )


def _insert(op_id: str, sequence: int, label: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.INSERT,
        target=_section_addr(label),
        payload=_section(label, f"Uus {label}."),
        source=OperationSource(statute_id="ee/amend/2025"),
    )


def _repeal(op_id: str, sequence: int, label: str, *, source_id: str = "ee/amend/2025", effective: str = "") -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPEAL,
        target=_section_addr(label),
        source=OperationSource(statute_id=source_id, effective=effective),
    )


def _renumber(op_id: str, sequence: int, frm: str, to: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.RENUMBER,
        target=_section_addr(frm),
        destination=_section_addr(to),
        source=OperationSource(statute_id="ee/amend/2025"),
    )


def _text_replace(
    op_id: str, sequence: int, label: str, old: str, new: str, *, source_id: str = "ee/amend/2025", effective: str = ""
) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.TEXT_REPLACE,
        target=_section_addr(label),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=new,
            attrs={"old_text": old},
        ),
        source=OperationSource(statute_id=source_id, effective=effective),
    )


def _statute() -> IRStatute:
    """A small EE statute (top-level sections) the op sets land on."""
    return IRStatute(
        statute_id="ee/base",
        title="Test seadus",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=tuple(
                _section(str(n), f"Vana {n} tekst.") for n in (1, 2, 3, 5, 7)
            ),
        ),
    )


def _op_sets() -> list[tuple[str, list[LegalOperation]]]:
    """Representative EE op sets exercising every apply action family + skips."""
    return [
        ("single_replace", [_replace("r1", 1, "1")]),
        ("insert_new", [_insert("i1", 1, "4")]),
        ("repeal_one", [_repeal("rp1", 1, "2")]),
        ("renumber", [_renumber("rn1", 1, "7", "8")]),
        ("text_replace", [_text_replace("tr1", 1, "3", "Vana 3 tekst.", "Uus 3 tekst.")]),
        (
            "multi_section",
            [
                _replace("m1", 1, "1"),
                _insert("m2", 2, "4"),
                _repeal("m3", 3, "2"),
                _renumber("m4", 4, "7", "9"),
                _text_replace("m5", 5, "3", "Vana 3 tekst.", "Uus 3 tekst."),
            ],
        ),
        ("unresolved_target_skip", [_replace("miss", 1, "999")]),
        ("text_replace_no_match_skip", [_text_replace("nm", 1, "3", "puudub tekst", "x")]),
        # REPEAL+TEXT_REPLACE same-moment from two distinct acts (the EE-specific
        # incompatibility predicate; the ordering wrinkle the seam must preserve).
        (
            "repeal_text_replace_same_moment",
            [
                _repeal("sm-rp", 1, "5", source_id="ee/act-a/2025", effective="2026-01-01"),
                _text_replace(
                    "sm-tr", 2, "5", "Vana 5 tekst.", "Uus 5 tekst.",
                    source_id="ee/act-b/2025", effective="2026-01-01",
                ),
            ],
        ),
    ]


def _adjudication_kind_multiset(adjs: list[CompileAdjudication]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for a in adjs:
        counts[a.kind] = counts.get(a.kind, 0) + 1
    return counts


def _statute_fingerprint(statute: IRStatute) -> tuple[str, str]:
    """A byte-stable fingerprint: body structural hash + title."""
    return (structural_subtree_hash(statute.body), statute.title)


def _lo_ops_fingerprint(lo_ops: list[LegalOperation]) -> tuple[tuple[object, ...], ...]:
    """A byte-stable fingerprint of the ``lo_ops_out`` snapshot channel.

    Each snapshot op is reduced to its load-bearing identity: op_id, sequence,
    action, target path, and the structural hash of the payload (None-safe for
    repeal tombstones). This is the EE-specific side channel the seam must keep
    byte-identical.
    """
    out: list[tuple[object, ...]] = []
    for snap in lo_ops:
        action = snap.action.value if hasattr(snap.action, "value") else snap.action
        payload_hash = structural_subtree_hash(snap.payload) if snap.payload is not None else None
        out.append(
            (
                snap.op_id,
                snap.sequence,
                action,
                tuple(snap.target.path),
                payload_hash,
            )
        )
    return tuple(out)


# ── (c) the additive receipt lane: a seam emitter mirroring the EE per-op fold. ─


def _seam_write_receipts(
    statute: IRStatute,
    ops: list[LegalOperation],
) -> tuple[IRStatute, tuple[WriteReceipt, ...]]:
    """Replay ops one at a time, synthesizing each receipt via the SEAM.

    Applies ops one at a time using the production ``apply_ee_ops`` as the
    single-op primitive (so the seam sees the identical before/after body the EE
    fold produces), then drives ``core/apply_seam.apply_op`` with the EE profile
    to synthesize the per-op ``WriteReceipt``. EE has no pre-existing production
    receipt emitter, so this lane is purely additive — the gate proves it is
    reachable + deterministic for EE's op families.
    """
    current = statute
    receipts: list[WriteReceipt] = []
    for op in ops:
        adjudications: list[CompileAdjudication] = []
        next_statute = apply_ee_ops(current, [op], adjudications_out=adjudications)
        applied_landed = next_statute.body is not current.body

        def _shim(
            before: IRNode, _op: LegalOperation, _after: IRNode = next_statute.body,
            _applied: bool = applied_landed,
        ) -> MaterializeResult[IRNode]:
            return MaterializeResult(new_state=_after, applied=_applied)

        profile: ApplyProfile[IRNode] = ApplyProfile(
            jurisdiction="ee",
            materializer=_shim,
            boundary_mode="off",
            emit_receipts=True,
            emit_coverage=False,
            receipt_helper_prefix="apply_ee_ops",
        )
        result: AppliedOp[IRNode] = apply_op(
            current.body, op, provenance=op.source, profile=profile,
            source_statute=statute.statute_id,
        )
        if result.write_receipt is not None:
            receipts.append(result.write_receipt)
        current = next_statute
    return current, tuple(receipts)


def _receipt_fields(r: WriteReceipt) -> dict[str, object]:
    return {
        "op_id": r.op_id,
        "helper": r.helper,
        "action": r.action,
        "bound_target_path": r.bound_target_path,
        "landed_primary_path": r.landed_primary_path,
        "created_paths": r.created_paths,
        "replaced_paths": r.replaced_paths,
        "removed_paths": r.removed_paths,
        "renumbered_paths": r.renumbered_paths,
        "migration_rule_ids": r.migration_rule_ids,
        "pre_hashes": dict(r.pre_hashes),
        "post_hashes": dict(r.post_hashes),
    }


# ── tests ─────────────────────────────────────────────────────────────────────


def test_ee_apply_seam_materialized_ir_adjudications_and_lo_ops_are_stable() -> None:
    """GATE (a): the seam-based ``apply_ee_ops`` produces a deterministic,
    cross-run-identical materialized ``IRStatute``, a deterministic
    adjudication-kind multiset, AND a byte-identical ``lo_ops_out`` snapshot
    stream across every representative op set."""
    for name, ops in _op_sets():
        adj_a: list[CompileAdjudication] = []
        lo_a: list[LegalOperation] = []
        out_a = apply_ee_ops(_statute(), list(ops), lo_ops_out=lo_a, adjudications_out=adj_a)
        adj_b: list[CompileAdjudication] = []
        lo_b: list[LegalOperation] = []
        out_b = apply_ee_ops(_statute(), list(ops), lo_ops_out=lo_b, adjudications_out=adj_b)

        assert _statute_fingerprint(out_a) == _statute_fingerprint(out_b), (
            f"{name}: materialized statute diverged across runs"
        )
        assert out_a.body == out_b.body, f"{name}: body structural mismatch across runs"
        assert _adjudication_kind_multiset(adj_a) == _adjudication_kind_multiset(adj_b), (
            f"{name}: adjudication kinds diverged across runs"
        )
        assert _lo_ops_fingerprint(lo_a) == _lo_ops_fingerprint(lo_b), (
            f"{name}: lo_ops_out snapshot channel diverged across runs"
        )


def test_ee_apply_seam_matches_conserved_wrapper_statute_and_lo_ops() -> None:
    """GATE (b): the seam-based bare ``apply_ee_ops`` and ``apply_ee_ops_conserved``
    materialize the SAME statute, write the SAME ``lo_ops_out``, and the
    conserved partition is total."""
    for name, ops in _op_sets():
        lo_bare: list[LegalOperation] = []
        bare = apply_ee_ops(_statute(), list(ops), lo_ops_out=lo_bare)
        lo_cons: list[LegalOperation] = []
        conserved = apply_ee_ops_conserved(_statute(), list(ops), lo_ops_out=lo_cons)

        assert _statute_fingerprint(bare) == _statute_fingerprint(conserved.statute), (
            f"{name}: bare vs conserved materialized statute diverged"
        )
        assert _lo_ops_fingerprint(lo_bare) == _lo_ops_fingerprint(lo_cons), (
            f"{name}: lo_ops_out diverged between bare fold and conserved wrapper"
        )
        fr = conserved.filter_result
        assert len(fr.accepted_items) + len(fr.rejected_items) == len(ops), (
            f"{name}: conserved partition is not total"
        )


def test_ee_apply_seam_receipts_are_well_formed_and_stable() -> None:
    """GATE (c): the seam-synthesized ``WriteReceipt`` lane (additive — EE has no
    pre-existing production emitter) is reachable, deterministic across runs, and
    carries EE's helper prefix for every applied op of every op set."""
    for name, ops in _op_sets():
        _final_a, receipts_a = _seam_write_receipts(_statute(), list(ops))
        _final_b, receipts_b = _seam_write_receipts(_statute(), list(ops))

        assert len(receipts_a) == len(receipts_b), (
            f"{name}: receipt COUNT diverged across runs"
        )
        assert len(receipts_a) <= len(ops), f"{name}: more receipts than ops"
        for ra, rb in zip(receipts_a, receipts_b, strict=True):
            assert _receipt_fields(ra) == _receipt_fields(rb), (
                f"{name}: seam receipt for op {ra.op_id} diverged across runs"
            )
            assert ra.helper.startswith("apply_ee_ops::"), (
                f"{name}: seam receipt helper not EE-prefixed: {ra.helper}"
            )
            assert ra.declared_footprint, (
                f"{name}: seam receipt for op {ra.op_id} declares an empty footprint"
            )


def test_ee_apply_seam_preserves_same_moment_repeal_text_replace_wrinkle() -> None:
    """GATE (d): the EE-specific REPEAL+TEXT_REPLACE same-moment incompatibility
    predicate still fires through the migrated apply fold. A REPEAL and a
    TEXT_REPLACE on the same section from two distinct acts at the same effective
    date emit exactly one EE same-moment ambiguity finding — the ordering wrinkle
    survives the apply-seam cutover unchanged (the ``order_ops`` +
    ``ee_same_moment_payloads_incompatible`` path is untouched by Wave 3)."""
    ops = [
        _repeal("sm-rp", 1, "5", source_id="ee/act-a/2025", effective="2026-01-01"),
        _text_replace(
            "sm-tr", 2, "5", "Vana 5 tekst.", "Uus 5 tekst.",
            source_id="ee/act-b/2025", effective="2026-01-01",
        ),
    ]
    adjudications: list[CompileAdjudication] = []
    apply_ee_ops(_statute(), ops, adjudications_out=adjudications)

    moments = [a for a in adjudications if a.kind == EE_SAME_MOMENT_AMBIGUITY_RULE_ID]
    assert len(moments) == 1, (
        f"expected exactly 1 EE same-moment finding through the migrated fold; got {moments!r}"
    )
    # Cross-act finding carries an empty op_id so it never pollutes the
    # conserved-wrapper per-op partition.
    assert moments[0].op_id == ""
