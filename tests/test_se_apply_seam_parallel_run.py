"""SE parallel-run equality gate for the Wave 2 apply-seam cutover.

``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §4 Wave 2: before trusting that
``apply_se_ops`` routes through the unified ``core/apply_seam.apply_op`` kernel,
run the seam-based path against SE's pre-existing apply + receipt emitters and
assert IDENTICAL outputs.

WHY SE IS THE WAVE-2 GATE. Unlike NO (which had no production receipt emitter,
so its receipt lane was purely additive), SE ALREADY emits ``WriteReceipt``s via
``se_replay_write_receipts`` / ``apply_se_ops_conserved(emit_receipts=True)``. So
this migration must prove the seam's receipt synthesis is BYTE-IDENTICAL to SE's
existing production emitter — not merely additive. That equality is the Wave-2
deliverable.

THE THREE GATES.
  (a) MATERIALIZED IR + ADJUDICATIONS: the seam-based ``apply_se_ops`` produces a
      deterministic, cross-run-identical materialized ``IRStatute`` (structural
      body hash + supplements) and adjudication-kind multiset across the
      representative op set — including the appendix (``supplements``) lane and
      the metadata ``applied_op_count`` accounting.
  (b) CONSERVED WRAPPER: the bare fold and ``apply_se_ops_conserved`` materialize
      the same statute, and the conserved partition is total.
  (c) RECEIPT BYTE-IDENTITY (the KEY SE gate): the seam-synthesized
      ``WriteReceipt`` (``apply_op`` with ``emit_receipts=True``, the SE profile's
      ``receipt_helper_prefix="apply_se_ops"`` +
      ``renumber_migration_rule_ids=("se_renumber_relabel",)``) equals SE's
      pre-existing ``se_replay_write_receipts`` output FIELD-FOR-FIELD — same
      ``op_id`` / ``helper`` / ``action`` / ``bound_target_path`` /
      ``landed_primary_path`` / created/replaced/removed/renumbered footprint /
      migration rule ids / pre & post structural-subtree hashes — over every
      applied op of every op set.

The op sets exercise every SE apply action family: REPLACE / INSERT / REPEAL /
TEXT_REPLACE / RENUMBER on sections, the section-heading INSERT/REPEAL facet, the
appendix REPLACE/INSERT/REPEAL lane, and the genuine skips. A future edit to the
SE materializer or the seam that perturbs the materialized IR, the adjudications,
or the receipt shape breaks this gate loudly — the grounding-neutral contract
(AGENTS.md §0).
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
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.ir_helpers import structural_subtree_hash
from lawvm.core.semantic_types import FacetKind, IRNodeKind, StructuralAction, TextPatchKindEnum
from lawvm.core.write_receipt import WriteReceipt
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.sweden.grafter import (
    apply_se_ops,
    apply_se_ops_conserved,
    se_replay_write_receipts,
)


# ── op + statute builders (mirror the SE production op shape) ─────────────────


def _section_addr(label: str) -> LegalAddress:
    return LegalAddress(path=(("section", label),))


def _appendix_addr(label: str) -> LegalAddress:
    # The bilaga/appendix lives in the ``supplements`` compartment root (§5.3 /
    # §7 delta #6): the SE materializer selects the supplements resolution lane
    # off the address ``root``, mirroring the production mint site
    # (``_lower_se_official_effect_plan_item`` stamps ``root="supplements"``).
    return LegalAddress(path=(("appendix", label),), root="supplements")


def _replace(op_id: str, sequence: int, label: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=_section_addr(label),
        payload=IRNode(kind=IRNodeKind.SECTION, label=label, text=f"Ny {label}."),
        source=OperationSource(statute_id="2026:999"),
    )


def _insert(op_id: str, sequence: int, label: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.INSERT,
        target=_section_addr(label),
        payload=IRNode(kind=IRNodeKind.SECTION, label=label, text=f"Ny {label}."),
        source=OperationSource(statute_id="2026:999"),
    )


def _repeal(op_id: str, sequence: int, label: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPEAL,
        target=_section_addr(label),
        source=OperationSource(statute_id="2026:999"),
    )


def _renumber(op_id: str, sequence: int, frm: str, to: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.RENUMBER,
        target=_section_addr(frm),
        destination=_section_addr(to),
        source=OperationSource(statute_id="2026:999"),
    )


def _text_replace(op_id: str, sequence: int, label: str, old: str, new: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.TEXT_PATCH,
        target=_section_addr(label),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text=old),
            replacement=new,
        ),
        source=OperationSource(statute_id="2026:999"),
    )


def _heading_insert(op_id: str, sequence: int, label: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", label),), special=FacetKind.HEADING),
        payload=IRNode(kind=IRNodeKind.HEADING, text=f"Rubrik {label}"),
        source=OperationSource(statute_id="2026:999"),
    )


def _appendix_replace(op_id: str, sequence: int, label: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=_appendix_addr(label),
        payload=IRNode(kind=IRNodeKind.APPENDIX, label=label, text=f"Bilaga {label} ny"),
        source=OperationSource(statute_id="2026:999"),
    )


def _statute() -> IRStatute:
    """A small SE statute (top-level sections + one appendix) the op sets land on."""
    return IRStatute(
        statute_id="2026:999",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=tuple(
                IRNode(kind=IRNodeKind.SECTION, label=str(n), text=f"Gamla {n}.")
                for n in (1, 2, 3, 5, 7)
            ),
        ),
        supplements=(IRNode(kind=IRNodeKind.APPENDIX, label="1", text="Bilaga 1 gammal"),),
    )


def _op_sets() -> list[tuple[str, list[LegalOperation]]]:
    """Representative SE op sets exercising every apply action family."""
    return [
        ("single_replace", [_replace("r1", 1, "1")]),
        ("insert_new", [_insert("i1", 1, "4")]),
        ("repeal_one", [_repeal("rp1", 1, "2")]),
        ("renumber", [_renumber("rn1", 1, "7", "8")]),
        ("text_replace", [_text_replace("tr1", 1, "3", "Gamla 3.", "Nya 3.")]),
        ("heading_insert", [_heading_insert("h1", 1, "5")]),
        ("appendix_replace", [_appendix_replace("ap1", 1, "1")]),
        (
            "mixed_body_ops",
            [
                _replace("m1", 1, "1"),
                _insert("m2", 2, "4"),
                _repeal("m3", 3, "2"),
                _renumber("m4", 4, "7", "9"),
            ],
        ),
        ("renumber_collision_skip", [_renumber("rc", 1, "1", "2")]),
        ("unresolved_target_skip", [_replace("miss", 1, "999")]),
        ("text_replace_no_match_skip", [_text_replace("nm", 1, "3", "absent text", "x")]),
    ]


def _adjudication_kind_multiset(adjs: list[CompileAdjudication]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for a in adjs:
        counts[a.kind] = counts.get(a.kind, 0) + 1
    return counts


def _statute_fingerprint(statute: IRStatute) -> tuple[str, tuple[str, ...], int]:
    """A byte-stable fingerprint: body hash + supplement hashes + applied_op_count."""
    return (
        structural_subtree_hash(statute.body),
        tuple(structural_subtree_hash(s) for s in statute.supplements),
        int(statute.metadata.get("applied_op_count", 0)),
    )


# ── (c) the receipt byte-identity gate: a seam emitter mirroring
# ``se_replay_write_receipts`` but routing receipt synthesis through the seam. ──


def _seam_write_receipts(
    statute: IRStatute,
    ops: list[LegalOperation],
) -> tuple[IRStatute, tuple[WriteReceipt, ...]]:
    """Mirror ``se_replay_write_receipts`` but synthesize each receipt via the SEAM.

    Applies ops one at a time exactly as ``se_replay_write_receipts`` does (using
    the production ``apply_se_ops`` as the single-op primitive to get the
    before/after body), then drives ``core/apply_seam.apply_op`` with the SE
    profile to synthesize the per-op ``WriteReceipt``. The materializer here is a
    thin shim that replays the already-computed single-op apply (so the seam sees
    the identical before/after body the SE emitter saw); the receipt the SEAM
    synthesizes is what we compare to SE's emitter field-for-field.
    """
    current = statute
    receipts: list[WriteReceipt] = []
    for op in ops:
        adjudications: list[CompileAdjudication] = []
        next_statute = apply_se_ops(current, [op], adjudications_out=adjudications)

        # The seam materializer shim: return the body the production single-op
        # apply produced. ``applied`` is True iff the apply landed (no skip
        # adjudication AND the body actually changed — matching SE's emitter,
        # which emits a receipt only on a non-empty diff).
        applied_landed = not adjudications and next_statute.body is not current.body

        def _shim(
            before: IRNode, _op: LegalOperation, _after: IRNode = next_statute.body,
            _applied: bool = applied_landed,
        ) -> MaterializeResult[IRNode]:
            return MaterializeResult(new_state=_after, applied=_applied)

        profile: ApplyProfile[IRNode] = ApplyProfile(
            jurisdiction="se",
            materializer=_shim,
            boundary_mode="off",
            emit_receipts=True,
            emit_coverage=False,
            renumber_migration_rule_ids=("se_renumber_relabel",),
            receipt_helper_prefix="apply_se_ops",
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
    """Every byte-comparable field of a WriteReceipt (the equality surface)."""
    return {
        "op_id": r.op_id,
        "helper": r.helper,
        "action": r.action,
        "bound_target_path": r.bound_target_path,
        "landed_primary_path": r.landed_primary_path,
        "created_paths": r.created_paths,
        "replaced_paths": r.replaced_paths,
        "removed_paths": r.removed_paths,
        "consumed_paths": r.consumed_paths,
        "renumbered_paths": r.renumbered_paths,
        "migration_rule_ids": r.migration_rule_ids,
        "recovery_rule_ids": r.recovery_rule_ids,
        "fallback_rule_ids": r.fallback_rule_ids,
        "pre_hashes": dict(r.pre_hashes),
        "post_hashes": dict(r.post_hashes),
    }


# ── tests ─────────────────────────────────────────────────────────────────────


def test_se_apply_seam_materialized_ir_and_adjudications_are_stable() -> None:
    """GATE (a): the seam-based ``apply_se_ops`` produces a deterministic,
    cross-run-identical materialized ``IRStatute`` (body + supplements +
    ``applied_op_count``) and a deterministic adjudication-kind multiset across
    every representative op set — including the appendix lane."""
    for name, ops in _op_sets():
        adj_a: list[CompileAdjudication] = []
        out_a = apply_se_ops(_statute(), list(ops), adjudications_out=adj_a)
        adj_b: list[CompileAdjudication] = []
        out_b = apply_se_ops(_statute(), list(ops), adjudications_out=adj_b)

        assert _statute_fingerprint(out_a) == _statute_fingerprint(out_b), (
            f"{name}: materialized statute diverged across runs"
        )
        assert out_a.body == out_b.body, f"{name}: body structural mismatch"
        assert list(out_a.supplements) == list(out_b.supplements), (
            f"{name}: supplements diverged across runs"
        )
        assert _adjudication_kind_multiset(adj_a) == _adjudication_kind_multiset(adj_b), (
            f"{name}: adjudication kinds diverged across runs"
        )


def test_se_apply_seam_matches_conserved_wrapper_statute() -> None:
    """GATE (b): the seam-based bare ``apply_se_ops`` and ``apply_se_ops_conserved``
    materialize the SAME statute, and the conserved partition is total."""
    for name, ops in _op_sets():
        bare = apply_se_ops(_statute(), list(ops))
        conserved = apply_se_ops_conserved(_statute(), list(ops))
        assert _statute_fingerprint(bare) == _statute_fingerprint(conserved.statute), (
            f"{name}: bare vs conserved materialized statute diverged"
        )
        fr = conserved.filter_result
        assert len(fr.accepted_items) + len(fr.rejected_items) == len(ops), (
            f"{name}: conserved partition is not total"
        )


def test_se_apply_seam_receipts_are_byte_identical_to_production_emitter() -> None:
    """GATE (c) — THE WAVE-2 DELIVERABLE: the SEAM-synthesized ``WriteReceipt``
    equals SE's pre-existing ``se_replay_write_receipts`` output FIELD-FOR-FIELD
    over every applied op of every op set. This is the receipt byte-identity the
    Wave-2 cutover must prove (SE already emits receipts in production, so the
    seam's receipt synthesis must MATCH them, not merely add a parallel lane).

    Equality is asserted on the full receipt field surface — op_id / helper /
    action / bound & landed paths / created/replaced/removed/renumbered footprint
    / migration & recovery & fallback rule ids / pre & post structural-subtree
    hashes."""
    for name, ops in _op_sets():
        _final_prod, prod_receipts = se_replay_write_receipts(_statute(), list(ops))
        _final_seam, seam_receipts = _seam_write_receipts(_statute(), list(ops))

        assert len(prod_receipts) == len(seam_receipts), (
            f"{name}: receipt COUNT diverged — production={len(prod_receipts)} "
            f"seam={len(seam_receipts)}"
        )
        for prod_r, seam_r in zip(prod_receipts, seam_receipts, strict=True):
            assert _receipt_fields(prod_r) == _receipt_fields(seam_r), (
                f"{name}: receipt for op {prod_r.op_id} diverged between the "
                f"production emitter and the seam.\n"
                f"  production={_receipt_fields(prod_r)}\n"
                f"  seam={_receipt_fields(seam_r)}"
            )
            # The seam's helper MUST carry SE's pre-cutover prefix (not the
            # kernel-canonical ``se::apply_op``) — the byte-identity contract.
            assert seam_r.helper.startswith("apply_se_ops::"), (
                f"{name}: seam receipt helper not SE-prefixed: {seam_r.helper}"
            )


def test_se_apply_seam_receipts_satisfy_core_contract() -> None:
    """ADDITIVE OUTPUT VALIDATION: the per-op ``WriteReceipt`` lane satisfies the
    core receipt invariants — every receipt's bound→landed divergence is explained
    (``divergence_explained``), the declared footprint is non-empty, and the
    receipt count never exceeds the op count."""
    for name, ops in _op_sets():
        _final, receipts = se_replay_write_receipts(_statute(), list(ops))
        assert len(receipts) <= len(ops), f"{name}: more receipts than ops"
        for r in receipts:
            assert r.divergence_explained, (
                f"{name}: receipt for op {r.op_id} has an unexplained "
                f"bound→landed divergence (action={r.action})"
            )
            assert r.declared_footprint, (
                f"{name}: receipt for op {r.op_id} declares an empty footprint"
            )
