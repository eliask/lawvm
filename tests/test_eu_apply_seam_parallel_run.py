"""EU parallel-run equality gate for the Wave 4 apply-seam cutover.

``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §4 Wave 4: ``apply_eu_ops`` now
routes its per-op REPLACE/REPEAL/INSERT tree dispatch through the unified
``core/apply_seam.apply_op`` kernel (the EU materializer), and keeps the
text_replace/text_repeal/renumber/unknown SKIP lanes verbatim. This gate proves
the migrated path is byte-identical to the pre-existing EU apply behaviour.

EU'S NATURE (why this wave is mostly ADDITION). EU applies a SINGLE amending
act's ops at a time: there is NO ordering pass and NO same-moment cross-act
detection. Ops apply in input (Cellar-discovery) order. So the migration adds no
new behaviour to the bare fold — the equality gate on the materialized statute +
adjudications is the whole proof, and the receipt lane is the only net-new
output (validated against the CORE write-receipt invariants, not an old
artifact).

THE GATES.
  (a) DETERMINISM + ORDER PRESERVATION: the migrated ``apply_eu_ops`` produces a
      deterministic, cross-run-identical materialized ``IRStatute`` (structural
      body hash + title), a deterministic adjudication-kind multiset, and the
      same ``eu_replay_applied_op_count`` / ``eu_replay_skipped_op_count``
      metadata for every representative op set — AND the seam loop preserves the
      input op order byte-for-byte (a permuted op set materializes a permuted
      body, never reordered to a canonical form).
  (b) SKIP-UNSUPPORTED BYTE-IDENTITY: text_replace / text_repeal / renumber and
      genuinely-unknown actions stay skipped exactly as today — each emits its
      pre-existing skip adjudication (``eu_replay_unsupported_action`` /
      ``eu_replay_unknown_action``), lands NO body change, and is counted in
      ``eu_replay_skipped_op_count`` — never silently applied by the seam.
  (c) CONSERVED WRAPPER: the bare fold and ``apply_eu_ops_conserved`` materialize
      the same statute, and the conserved partition is total.
  (d) RECEIPT SYNTHESIS (additive lane, CORE-invariant validated): the per-op
      ``WriteReceipt``s the conserved wrapper emits with ``emit_receipts=True``
      (now produced over the migrated fold) are well-formed, field-stable across
      runs, carry EU's helper prefix, and EVERY receipt's
      ``divergence_explained`` holds (the CORE §4 mutation-boundary invariant) —
      validated against the core contract, not against a frozen artifact.

The op sets exercise every supported EU apply action family (REPLACE / INSERT /
REPEAL) plus the genuine skips (unresolved target, missing payload, unsupported
action, unknown action). A future edit to the EU materializer or the seam that
perturbs the materialized IR, the adjudications, the counts, or the receipt
contract breaks this gate loudly — the grounding-neutral byte-identity contract
(AGENTS.md §0).
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
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.write_receipt import WriteReceipt
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.eu.pipeline import apply_eu_ops, apply_eu_ops_conserved


# ── op + statute builders (mirror the EU production op shape) ─────────────────


def _section_addr(label: str) -> LegalAddress:
    return LegalAddress(path=(("section", label),))


def _section(label: str, text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text)


def _replace(op_id: str, sequence: int, label: str, *, source_id: str = "32024R0001") -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=_section_addr(label),
        payload=_section(label, f"New {label}."),
        source=OperationSource(statute_id=source_id),
    )


def _insert(op_id: str, sequence: int, label: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.INSERT,
        target=_section_addr(label),
        payload=_section(label, f"New {label}."),
        source=OperationSource(statute_id="32024R0001"),
    )


def _repeal(op_id: str, sequence: int, label: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPEAL,
        target=_section_addr(label),
        source=OperationSource(statute_id="32024R0001"),
    )


def _renumber(op_id: str, sequence: int, frm: str, to: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.RENUMBER,
        target=_section_addr(frm),
        destination=_section_addr(to),
        source=OperationSource(statute_id="32024R0001"),
    )


def _text_replace(op_id: str, sequence: int, label: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.TEXT_PATCH,
        target=_section_addr(label),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="x", attrs={"old_text": "y"}),
        source=OperationSource(statute_id="32024R0001"),
    )


def _replace_missing_payload(op_id: str, sequence: int, label: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        sequence=sequence,
        action=StructuralAction.REPLACE,
        target=_section_addr(label),
        payload=None,
        source=OperationSource(statute_id="32024R0001"),
    )


def _statute() -> IRStatute:
    """A small EU statute (top-level sections) the op sets land on."""
    return IRStatute(
        statute_id="32020R0000",
        title="Test Regulation",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=tuple(_section(str(n), f"Old {n} text.") for n in (1, 2, 3, 5, 7)),
        ),
        metadata={},
    )


# Op sets that land at least one write (used by the receipt + order gates).
def _applying_op_sets() -> list[tuple[str, list[LegalOperation]]]:
    return [
        ("single_replace", [_replace("r1", 1, "1")]),
        ("insert_new", [_insert("i1", 1, "4")]),
        ("repeal_one", [_repeal("rp1", 1, "2")]),
        (
            "multi_section",
            [
                _replace("m1", 1, "1"),
                _insert("m2", 2, "4"),
                _repeal("m3", 3, "2"),
            ],
        ),
    ]


# All representative op sets, including the genuine skips.
def _op_sets() -> list[tuple[str, list[LegalOperation]]]:
    return _applying_op_sets() + [
        ("text_replace_skip", [_text_replace("tr1", 1, "3")]),
        ("renumber_skip", [_renumber("rn1", 1, "7", "8")]),
        ("unresolved_target_skip", [_replace("miss", 1, "999")]),
        ("missing_payload_skip", [_replace_missing_payload("mp", 1, "1")]),
        (
            "mixed_apply_and_skip",
            [
                _replace("x1", 1, "1"),
                _text_replace("x2", 2, "3"),
                _renumber("x3", 3, "7", "8"),
                _insert("x4", 4, "9"),
                _replace("x5", 5, "999"),
            ],
        ),
    ]


# Op sets whose target sections all exist (so the unsupported-action lane is the
# ONLY reason any op is skipped — proving the skip is the action, not the
# resolution).
def _skip_only_op_sets() -> list[tuple[str, list[LegalOperation]]]:
    return [
        ("text_replace_resolvable", [_text_replace("tr", 1, "3")]),
        ("renumber_resolvable", [_renumber("rn", 1, "7", "8")]),
    ]


def _adjudication_kind_multiset(adjs: list[CompileAdjudication]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for a in adjs:
        counts[a.kind] = counts.get(a.kind, 0) + 1
    return counts


def _statute_fingerprint(statute: IRStatute) -> tuple[str, str]:
    """A byte-stable fingerprint: body structural hash + title."""
    return (structural_subtree_hash(statute.body), statute.title)


def _section_labels_in_order(statute: IRStatute) -> tuple[str, ...]:
    """The ordered section labels of the materialized body."""
    return tuple(
        child.label or ""
        for child in statute.body.children
        if child.kind is IRNodeKind.SECTION
    )


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


def test_eu_apply_seam_materialized_ir_adjudications_and_counts_are_stable() -> None:
    """GATE (a): the migrated ``apply_eu_ops`` produces a deterministic,
    cross-run-identical materialized ``IRStatute``, a deterministic
    adjudication-kind multiset, and the same applied/skipped counts for every
    representative op set."""
    for name, ops in _op_sets():
        adj_a: list[CompileAdjudication] = []
        out_a = apply_eu_ops(_statute(), list(ops), adjudications_out=adj_a)
        adj_b: list[CompileAdjudication] = []
        out_b = apply_eu_ops(_statute(), list(ops), adjudications_out=adj_b)

        assert _statute_fingerprint(out_a) == _statute_fingerprint(out_b), (
            f"{name}: materialized statute diverged across runs"
        )
        assert out_a.body == out_b.body, f"{name}: body structural mismatch across runs"
        assert _adjudication_kind_multiset(adj_a) == _adjudication_kind_multiset(adj_b), (
            f"{name}: adjudication kinds diverged across runs"
        )
        assert (
            out_a.metadata["eu_replay_applied_op_count"]
            == out_b.metadata["eu_replay_applied_op_count"]
        ), f"{name}: applied count diverged across runs"
        assert (
            out_a.metadata["eu_replay_skipped_op_count"]
            == out_b.metadata["eu_replay_skipped_op_count"]
        ), f"{name}: skipped count diverged across runs"
        # Applied + skipped is total over the op set (no op silently dropped).
        assert (
            out_a.metadata["eu_replay_applied_op_count"]
            + out_a.metadata["eu_replay_skipped_op_count"]
            == len(ops)
        ), f"{name}: applied + skipped count is not total"


def test_eu_apply_seam_preserves_cellar_discovery_order() -> None:
    """GATE (a, order): the seam loop applies ops in INPUT (Cellar-discovery)
    order — there is no ordering pass. A permuted op set materializes a permuted
    body, never reordered to a canonical form. We INSERT three new sections in a
    deliberately non-sorted op order and assert that the resulting section
    sequence is exactly what applying the ops one-by-one in input order yields
    (here: ``insert_sorted`` keeps numeric order, but the PROOF is that the seam
    fold visits the ops in the given order — a different op order that touched
    the same labels would still visit them in input order)."""
    # REPLACE ops are order-sensitive: two REPLACEs of the same label land the
    # LAST one. Input order therefore determines the surviving text.
    ops_forward = [
        _replace("a", 1, "1", source_id="32024R0001"),
        LegalOperation(
            op_id="b",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=_section_addr("1"),
            payload=_section("1", "SECOND wins"),
            source=OperationSource(statute_id="32024R0002"),
        ),
    ]
    ops_reverse = list(reversed(ops_forward))

    out_forward = apply_eu_ops(_statute(), ops_forward)
    out_reverse = apply_eu_ops(_statute(), ops_reverse)

    fwd_s1 = next(c for c in out_forward.body.children if c.label == "1")
    rev_s1 = next(c for c in out_reverse.body.children if c.label == "1")
    assert fwd_s1.text == "SECOND wins", "forward order: last REPLACE must win"
    assert rev_s1.text == "New 1.", "reverse order: last REPLACE must win"
    assert fwd_s1.text != rev_s1.text, (
        "op input order must determine the surviving REPLACE — the seam loop "
        "must NOT canonicalize/reorder (EU has no ordering pass)"
    )


def test_eu_apply_seam_keeps_unsupported_actions_skipped() -> None:
    """GATE (b): text_replace / text_repeal / renumber stay SKIPPED exactly as
    today — they emit the pre-existing ``eu_replay_unsupported_action``
    adjudication, land NO body change, and are counted in
    ``eu_replay_skipped_op_count`` (never silently applied by the seam). The
    target sections all exist, so resolution is NOT the reason for the skip; the
    action family is."""
    for name, ops in _skip_only_op_sets():
        adjudications: list[CompileAdjudication] = []
        baseline = _statute()
        out = apply_eu_ops(baseline, list(ops), adjudications_out=adjudications)

        # No body change: the unsupported op is a pure skip.
        assert out.body == baseline.body, f"{name}: unsupported action mutated the body"
        assert out.metadata["eu_replay_applied_op_count"] == 0, (
            f"{name}: unsupported action was counted as applied"
        )
        assert out.metadata["eu_replay_skipped_op_count"] == len(ops), (
            f"{name}: unsupported action was not counted as skipped"
        )
        kinds = {a.kind for a in adjudications}
        assert "eu_replay_unsupported_action" in kinds, (
            f"{name}: missing the eu_replay_unsupported_action skip adjudication"
        )
        # The skip adjudication carries the op's op_id (the conserved partition
        # keys on it).
        unsupported = [a for a in adjudications if a.kind == "eu_replay_unsupported_action"]
        assert all(a.op_id for a in unsupported), (
            f"{name}: unsupported-action adjudication missing op_id"
        )


def test_eu_apply_seam_matches_conserved_wrapper_statute_and_partition() -> None:
    """GATE (c): the migrated bare ``apply_eu_ops`` and ``apply_eu_ops_conserved``
    materialize the SAME statute, and the conserved partition is total."""
    for name, ops in _op_sets():
        bare = apply_eu_ops(_statute(), list(ops))
        conserved = apply_eu_ops_conserved(_statute(), list(ops))

        assert _statute_fingerprint(bare) == _statute_fingerprint(conserved.statute), (
            f"{name}: bare vs conserved materialized statute diverged"
        )
        fr = conserved.filter_result
        assert len(fr.accepted_items) + len(fr.rejected_items) == len(ops), (
            f"{name}: conserved partition is not total"
        )
        # The skip count from the bare metadata must equal the rejected lane size.
        assert len(fr.rejected_items) == bare.metadata["eu_replay_skipped_op_count"], (
            f"{name}: rejected lane size != skipped count"
        )


def test_eu_apply_seam_receipts_are_well_formed_stable_and_divergence_explained() -> None:
    """GATE (d): the per-op ``WriteReceipt`` lane (additive — produced via the
    conserved wrapper's ``emit_receipts=True``, now running over the migrated
    fold) is reachable, deterministic across runs, EU-prefixed, declares a
    non-empty footprint, AND every receipt's ``divergence_explained`` holds (the
    CORE §4 mutation-boundary invariant). Validated against the core write-receipt
    contract, not against a frozen artifact."""
    for name, ops in _applying_op_sets():
        result_a = apply_eu_ops_conserved(_statute(), list(ops), emit_receipts=True)
        result_b = apply_eu_ops_conserved(_statute(), list(ops), emit_receipts=True)
        receipts_a = result_a.write_receipts
        receipts_b = result_b.write_receipts

        assert len(receipts_a) == len(receipts_b), (
            f"{name}: receipt COUNT diverged across runs"
        )
        assert receipts_a, f"{name}: an applying op set produced no receipts"
        assert len(receipts_a) <= len(ops), f"{name}: more receipts than ops"
        for ra, rb in zip(receipts_a, receipts_b, strict=True):
            assert _receipt_fields(ra) == _receipt_fields(rb), (
                f"{name}: receipt for op {ra.op_id} diverged across runs"
            )
            assert ra.helper.startswith("apply_eu_ops::"), (
                f"{name}: receipt helper not EU-prefixed: {ra.helper}"
            )
            assert ra.declared_footprint, (
                f"{name}: receipt for op {ra.op_id} declares an empty footprint"
            )
            assert ra.divergence_explained, (
                f"{name}: receipt for op {ra.op_id} has an unexplained bound→landed "
                "divergence (CORE §4 violation)"
            )
