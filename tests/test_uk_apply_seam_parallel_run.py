"""UK parallel-run equality gate for the Wave 5 apply-seam cutover.

``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §4 Wave 5: UK is the FINAL and
hardest tree frontend to route its apply fold through the unified
``core/apply_seam.apply_op`` kernel (``replay_uk_ops``). The fold is now the seam
loop ``for op: executor.seam_apply_op(op)``; the UK materializer (``UKReplayExecutor.
_uk_materialize_one``) carries the VERBATIM executor dispatch — the
``_apply_op_with_context`` action-mixin dispatch, the warm-EID copy-on-write in
``replay_state``, the ``MutationEvent`` stream, the env-gated per-op
mutation-boundary probe, the ``lo_ops_out`` section snapshots, and the
``write_receipts_out`` sink — all UK-specific side channels that STAY in the fold
and remain the single producers of their output. The seam owns ONLY the
``applied`` derivation (``new_state is not base_state``).

WHY UK IS ITS OWN (FINAL) WAVE. UK carries more wrinkles than NO/SE/EE/EU:

  1. a STATEFUL executor (it mutates ``self.statute`` in place across calls,
     unlike the pure ``(body, op) -> body`` folds of the other four frontends);
  2. warm-EID-preserving CoW (a UK-specific relabel mechanism in
     ``replay_state``) and a ``MutationEvent`` stream;
  3. the ``lo_ops_out`` per-section snapshot channel;
  4. a TWO-STAGE rejection model (prepare-time filter + apply-time skip), so the
     §1.8 conserved partition is sourced from the seam's per-op ``applied``
     signal (``applied_op_ids_out``), not from enumerating UK's ~70 adjudication
     kinds.

THE GATES.
  (a) MATERIALIZED IR + ADJUDICATIONS + ``lo_ops_out`` + ``mutation_events``: the
      seam-based ``replay_uk_ops`` produces a deterministic, cross-run-identical
      materialized ``IRStatute`` (structural body hash + title), a deterministic
      adjudication-kind multiset, a byte-identical ``lo_ops_out`` snapshot stream,
      AND a byte-identical ``mutation_events`` stream across every representative
      op set. The seam loop is a verbatim lift, so this byte-identity IS the
      cutover proof — the existing UK replay suite cross-validates it against
      pre-cutover behaviour.
  (b) CONSERVED WRAPPER (NET-NEW, §1.8): the bare fold and
      ``replay_uk_ops_conserved`` materialize the same statute, write the same
      ``lo_ops_out``, and the conserved partition is TOTAL (accepted + rejected =
      input). Validated against the CORE conservation invariant, not an old
      artifact (UK had no conserved apply wrapper before).
  (c) RECEIPT SYNTHESIS (NET-NEW from PRODUCTION, §2.3): the seam-synthesized
      ``WriteReceipt`` lane (``uk_replay_write_receipts`` /
      ``replay_uk_ops_conserved(emit_receipts=True)``) is well-formed,
      field-stable across runs, and every receipt's bound→landed divergence is
      EXPLAINED (``WriteReceipt.divergence_explained`` — the UK RENUMBER relabel
      owned by ``uk_section_renumber_relabel``). Validated against the CORE
      receipt invariant.
  (d) WARM-EID RELABEL: a REPLACE that preserves a section's ``eId`` survives the
      warm-EID CoW through the seam and resolves by eId afterwards — the
      UK-specific relabel mechanism stays byte-identical under the seam.

The op sets exercise every UK apply action family: REPLACE / INSERT / REPEAL /
RENUMBER / TEXT_REPLACE on sections, multi-section bundles, warm-EID relabel, and
genuine skips (unresolved target, unsupported action). A future edit to the UK
materializer or the seam that perturbs the materialized IR, the adjudications,
the ``lo_ops_out`` / ``mutation_events`` streams breaks this gate loudly — the
grounding-neutral byte-identity contract (AGENTS.md §0). This completes the
tree-frontend apply-seam cascade (NO → SE → EE → EU → UK).
"""
from __future__ import annotations

from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
)
from lawvm.core.ir_helpers import structural_subtree_hash
from lawvm.core.mutation_events import MutationEvent
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.replay_conserved import (
    replay_uk_ops_conserved,
    uk_replay_write_receipts,
)
from lawvm.uk_legislation.replay_executor import replay_uk_ops


# ── op + statute builders (mirror the UK production op shape) ─────────────────


def _source() -> OperationSource:
    return OperationSource(statute_id="ukpga/2026/99", title="Amending Act")


def _section_addr(label: str) -> LegalAddress:
    return LegalAddress(path=(("section", label),))


def _section(label: str, text: str, *, eid: str | None = None) -> IRNode:
    attrs = {"eId": eid} if eid else {}
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text, attrs=attrs)


def _replace(op_id: str, sequence: int, label: str, *, eid: str | None = None) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=_section_addr(label),
        payload=_section(label, f"Replaced section {label}.", eid=eid),
        source=_source(),
    )


def _insert(op_id: str, sequence: int, label: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.INSERT,
        target=_section_addr(label),
        payload=_section(label, f"Inserted section {label}."),
        source=_source(),
    )


def _repeal(op_id: str, sequence: int, label: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPEAL,
        target=_section_addr(label),
        source=_source(),
    )


def _renumber(op_id: str, sequence: int, frm: str, to: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.RENUMBER,
        target=_section_addr(frm),
        destination=_section_addr(to),
        source=_source(),
    )


def _text_replace(op_id: str, sequence: int, label: str, old: str, new: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.TEXT_PATCH,
        target=_section_addr(label),
        payload=IRNode(kind=IRNodeKind.CONTENT, text=new, attrs={"old_text": old}),
        source=_source(),
    )


def _statute() -> IRStatute:
    """A small UK statute (top-level sections 1-7, each with an eId)."""
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=tuple(
                _section(str(n), f"Section {n} original text.", eid=f"section-{n}")
                for n in range(1, 8)
            ),
        ),
        supplements=(),
    )


def _op_sets() -> list[tuple[str, list[LegalOperation]]]:
    """Representative UK op sets exercising every apply action family + skips."""
    return [
        ("single_replace", [_replace("r1", 1, "5")]),
        ("insert_new", [_insert("i1", 1, "8")]),
        ("repeal_one", [_repeal("rp1", 1, "7")]),
        ("renumber", [_renumber("rn1", 1, "6", "9")]),
        ("text_replace", [_text_replace("tr1", 1, "3", "Section 3 original text.", "Section 3 new text.")]),
        (
            "warm_eid_relabel",
            [_replace("we1", 1, "4", eid="section-4")],
        ),
        (
            "multi_section",
            [
                _replace("m1", 1, "5"),
                _insert("m2", 2, "8"),
                _repeal("m3", 3, "7"),
                _text_replace("m4", 4, "3", "Section 3 original text.", "Section 3 amended."),
            ],
        ),
        # A REPLACE on a missing leaf is RECOVERED as an insert in UK (applied),
        # so it exercises the missing-leaf recovery lane (not a skip).
        ("replace_missing_leaf_recovered_as_insert", [_replace("miss", 1, "999")]),
        # A REPEAL on a missing target genuinely skips (no recovery) → the
        # apply-time skip lane of the conserved partition.
        ("repeal_unresolved_target_skip", [_repeal("rpmiss", 1, "999")]),
        ("text_replace_no_match_skip", [_text_replace("nm", 1, "3", "absent preimage", "x")]),
    ]


def _adjudication_kind_multiset(adjs: list[CompileAdjudication]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for a in adjs:
        counts[a.kind] = counts.get(a.kind, 0) + 1
    return counts


def _statute_fingerprint(statute: IRStatute) -> tuple[str, str]:
    return (structural_subtree_hash(statute.body), statute.title)


def _lo_ops_fingerprint(lo_ops: list[LegalOperation]) -> tuple[tuple[object, ...], ...]:
    out: list[tuple[object, ...]] = []
    for snap in lo_ops:
        action = snap.action.value if hasattr(snap.action, "value") else snap.action
        payload_hash = structural_subtree_hash(snap.payload) if snap.payload is not None else None
        out.append((snap.op_id, snap.sequence, action, tuple(snap.target.path), payload_hash))
    return tuple(out)


def _mutation_events_fingerprint(events: list[MutationEvent]) -> tuple[tuple[object, ...], ...]:
    out: list[tuple[object, ...]] = []
    for ev in events:
        out.append(
            (
                ev.op_id,
                ev.source_statute,
                ev.action,
                ev.helper,
                ev.outcome,
                tuple(ev.resolved_target_path or ()),
                tuple(ev.parent_path or ()),
            )
        )
    return tuple(out)


# ── tests ─────────────────────────────────────────────────────────────────────


def test_uk_apply_seam_materialized_ir_adjudications_lo_ops_and_events_are_stable() -> None:
    """GATE (a): the seam-based ``replay_uk_ops`` produces a deterministic,
    cross-run-identical materialized ``IRStatute``, adjudication-kind multiset,
    ``lo_ops_out`` snapshot stream, AND ``mutation_events`` stream across every
    representative op set. The seam loop is a verbatim lift of UK's executor
    dispatch, so this byte-identity IS the cutover proof."""
    for name, ops in _op_sets():
        adj_a: list[CompileAdjudication] = []
        lo_a: list[LegalOperation] = []
        ev_a: list[MutationEvent] = []
        out_a = replay_uk_ops(
            _statute(), list(ops), lo_ops_out=lo_a, adjudications_out=adj_a, mutation_events_out=ev_a
        )
        adj_b: list[CompileAdjudication] = []
        lo_b: list[LegalOperation] = []
        ev_b: list[MutationEvent] = []
        out_b = replay_uk_ops(
            _statute(), list(ops), lo_ops_out=lo_b, adjudications_out=adj_b, mutation_events_out=ev_b
        )

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
        assert _mutation_events_fingerprint(ev_a) == _mutation_events_fingerprint(ev_b), (
            f"{name}: mutation_events stream diverged across runs"
        )


def test_uk_apply_seam_matches_conserved_wrapper_statute_and_lo_ops() -> None:
    """GATE (b), NET-NEW §1.8: the seam-based bare ``replay_uk_ops`` and
    ``replay_uk_ops_conserved`` materialize the SAME statute, write the SAME
    ``lo_ops_out``, and the conserved partition is TOTAL (accepted + rejected =
    input). Validated against the CORE conservation invariant (UK had no
    conserved apply wrapper before)."""
    for name, ops in _op_sets():
        lo_bare: list[LegalOperation] = []
        bare = replay_uk_ops(_statute(), list(ops), lo_ops_out=lo_bare)
        lo_cons: list[LegalOperation] = []
        conserved = replay_uk_ops_conserved(_statute(), list(ops), lo_ops_out=lo_cons)

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
        # Every input op appears exactly once across the two lanes (no dup/drop).
        partition_ids = [op.op_id for op in fr.accepted_items] + [
            r.item.op_id for r in fr.rejected_items
        ]
        assert sorted(partition_ids) == sorted(op.op_id for op in ops), (
            f"{name}: conserved partition lost or duplicated an op"
        )


def test_uk_apply_seam_conserved_rejects_unresolved_target() -> None:
    """GATE (b) sharper: an op whose seam apply lands no write surfaces in the
    conserved REJECTED lane with a typed witness, never silently in accepted. A
    REPEAL on a missing target genuinely skips in UK (a REPLACE on a missing leaf
    is RECOVERED as an insert, so it is NOT a skip)."""
    ops = [_repeal("rpmiss", 1, "999")]
    conserved = replay_uk_ops_conserved(_statute(), ops)
    fr = conserved.filter_result
    assert len(fr.accepted_items) == 0, "an unresolved-target REPEAL landed in accepted"
    assert len(fr.rejected_items) == 1
    rej = fr.rejected_items[0]
    assert rej.item.op_id == "rpmiss"
    assert rej.reason_code == "uk_apply_no_write"
    assert rej.reason  # non-empty witness


def test_uk_apply_seam_receipts_are_well_formed_explained_and_stable() -> None:
    """GATE (c), NET-NEW §2.3 from production: the seam-synthesized
    ``WriteReceipt`` lane is reachable, deterministic across runs, every
    receipt's bound→landed divergence is EXPLAINED (the UK RENUMBER relabel owned
    by ``uk_section_renumber_relabel``), and the footprint is non-empty.
    Validated against the CORE receipt invariant."""
    for name, ops in _op_sets():
        _final_a, receipts_a = uk_replay_write_receipts(_statute(), list(ops))
        _final_b, receipts_b = uk_replay_write_receipts(_statute(), list(ops))

        assert len(receipts_a) == len(receipts_b), f"{name}: receipt COUNT diverged across runs"
        assert len(receipts_a) <= len(ops), f"{name}: more receipts than ops"
        for ra, rb in zip(receipts_a, receipts_b, strict=True):
            assert ra.op_id == rb.op_id and ra.action == rb.action, (
                f"{name}: seam receipt for op {ra.op_id} diverged across runs"
            )
            assert ra.helper == rb.helper
            assert ra.bound_target_path == rb.bound_target_path
            assert ra.landed_primary_path == rb.landed_primary_path
            assert dict(ra.pre_hashes) == dict(rb.pre_hashes)
            assert dict(ra.post_hashes) == dict(rb.post_hashes)
            assert ra.divergence_explained, (
                f"{name}: receipt for op {ra.op_id} has an unexplained "
                f"bound→landed divergence (action={ra.action})"
            )
            assert ra.declared_footprint, (
                f"{name}: receipt for op {ra.op_id} declares an empty footprint"
            )
            assert ra.helper.startswith("UKReplayExecutor.apply_op::"), (
                f"{name}: seam receipt helper not UK-prefixed: {ra.helper}"
            )


def test_uk_apply_seam_conserved_emit_receipts_surfaces_explained_receipts() -> None:
    """GATE (c) production reachability: ``replay_uk_ops_conserved(emit_receipts=
    True)`` surfaces per-op receipts on the conserved result, every one explained
    — the §2.9 guard-liveness fix (the receipt lane reachable from the conserved
    production wrapper, not just a standalone helper)."""
    ops = [
        _replace("a", 1, "5"),
        _renumber("b", 2, "6", "9"),
        _insert("c", 3, "8"),
    ]
    conserved = replay_uk_ops_conserved(_statute(), ops, emit_receipts=True)
    assert conserved.write_receipts, "conserved wrapper produced no receipts under emit_receipts"
    for r in conserved.write_receipts:
        assert r.divergence_explained, f"receipt {r.op_id} has unexplained divergence"
        assert r.declared_footprint, f"receipt {r.op_id} has empty footprint"
    # The RENUMBER receipt carries the named migration rule owning its relabel.
    renumber_receipts = [r for r in conserved.write_receipts if r.action == "renumber"]
    for r in renumber_receipts:
        assert "uk_section_renumber_relabel" in r.migration_rule_ids, (
            f"RENUMBER receipt {r.op_id} missing the named relabel rule"
        )


def test_uk_missing_leaf_replace_recovery_declared_to_boundary() -> None:
    """task #108-UK: the missing-leaf REPLACE→INSERT recovery retarget is now
    DECLARED on the per-op carrier and the ``MaterializeResult``, so the seam's
    always-on LS-01 mutation-boundary observer no longer reads the authorized
    body-root write as an escape — while the recovered section still lands
    (production materialization byte-identical).

    Three facts, proven on the exact ``replace_missing_leaf_recovered_as_insert``
    op the boundary measurement drives:

    1. ``MaterializeResult.declared_recovery_prefixes`` carries the resolved
       write-parent path (the body root ``()``) for the recovered REPLACE→INSERT.
    2. The seam emits NO ``APPLY.MUTATION_BOUNDARY_FINDING_AT_OP`` observation for
       that op (the declared recovery covers the body-root change).
    3. The recovered section 999 is still inserted — the declaration changes no
       write.
    """
    from lawvm.core.mutation_boundary_proof import MUTATION_BOUNDARY_FINDING_AT_OP_CODE
    from lawvm.uk_legislation.replay_executor import UKReplayExecutor

    op = _replace("miss", 1, "999")

    # (1) the materializer surfaces the recovery retarget on the MaterializeResult.
    ex = UKReplayExecutor(_statute())
    result = ex._uk_materialize_one(ex.statute.body, op)
    assert result.declared_recovery_prefixes == ((),), (
        "missing-leaf REPLACE→INSERT recovery did not declare the body-root "
        "write parent on the MaterializeResult"
    )

    # (2) the seam emits no boundary-escape observation for that op.
    seam_ex = UKReplayExecutor(_statute())
    applied = seam_ex.seam_apply_op(op)
    boundary_obs = [
        f for f in applied.observations if f.kind == MUTATION_BOUNDARY_FINDING_AT_OP_CODE
    ]
    assert boundary_obs == [], (
        "seam still surfaced a mutation-boundary escape for the recovered "
        "REPLACE→INSERT after the recovery retarget was declared"
    )
    assert applied.applied, "recovered REPLACE→INSERT did not land a write"

    # (3) the recovered section is still inserted (write unchanged).
    replayed = replay_uk_ops(_statute(), [op])
    labels = [c.label for c in replayed.body.children]
    assert "999" in labels, "recovered section 999 missing after the declared recovery"


def test_uk_recovery_declaration_does_not_leak_across_ops() -> None:
    """The per-op recovery carrier is reset each op: a recovery retarget from one
    op must not widen a later op's declared boundary. After the recovering "miss"
    op, a plain in-boundary REPLACE declares NO recovery prefix."""
    from lawvm.uk_legislation.replay_executor import UKReplayExecutor

    ex = UKReplayExecutor(_statute())
    # Recovering op populates the carrier.
    rec = ex._uk_materialize_one(ex.statute.body, _replace("miss", 1, "999"))
    assert rec.declared_recovery_prefixes == ((),)
    # A subsequent ordinary REPLACE on an existing leaf declares nothing.
    plain = ex._uk_materialize_one(ex.statute.body, _replace("r5", 2, "5"))
    assert plain.declared_recovery_prefixes == ()


def test_uk_apply_seam_preserves_warm_eid_relabel() -> None:
    """GATE (d): a REPLACE that preserves a section's ``eId`` survives the
    warm-EID CoW through the seam — the replacement node carries the preserved
    eId and the section text is the replacement's. The UK-specific warm-EID
    relabel mechanism stays byte-identical under the seam loop."""
    base = _statute()
    op = _replace("we", 1, "4", eid="section-4")
    replayed = replay_uk_ops(base, [op])

    # Locate section 4 in the replayed body; its text is the replacement's and
    # its eId is preserved (the warm-EID CoW relabel mechanism).
    sec4 = next(
        (c for c in replayed.body.children if c.kind == IRNodeKind.SECTION and c.label == "4"),
        None,
    )
    assert sec4 is not None, "section 4 missing after warm-EID replace"
    assert sec4.text == "Replaced section 4.", "warm-EID replace did not land the replacement text"
    assert sec4.attrs.get("eId") == "section-4", "warm-EID replace did not preserve the eId"
