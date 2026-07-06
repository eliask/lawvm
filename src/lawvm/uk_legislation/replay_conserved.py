"""Typed apply-result conservation carrier + per-op receipt lane for UK replay.

Wave 5 (``notes/CORE_PIPELINE_UNIFICATION_DESIGN.md`` §4) closes the two largest
accidental divergences UK carried after its apply fold moved onto the unified
``core/apply_seam.apply_op`` kernel (``replay_uk_ops``): the CORE doc §2.1 #4
conserved-wrapper hole and #5 receipt-emission-from-production hole.

This module adds, WITHOUT touching the bare ``replay_uk_ops`` callers:

  * :class:`UKApplyResult` + :func:`replay_uk_ops_conserved` — the AGENTS.md §1.8
    conserved wrapper. It mirrors :func:`replay_uk_ops` exactly (same replay
    semantics, same ``lo_ops_out`` / ``adjudications_out`` / ``mutation_events_out``
    side channels) and returns a :class:`~lawvm.core.filter_result.FilterResult`
    partitioning EVERY input op into accepted (its binding landed in the output
    statute) or rejected (prepare-time filtered OR apply-time skipped). The
    contract is monotone: every input op ends up accepted or rejected, never
    silently dropped.

  * :func:`uk_replay_write_receipts` — the seam-synthesized per-op
    :class:`~lawvm.core.write_receipt.WriteReceipt` lane (mirrors
    ``norway/grafter.py::no_replay_write_receipts`` and
    ``eu/pipeline.py::eu_replay_write_receipts``), driving
    ``core/apply_seam.apply_op`` with ``emit_receipts=True`` so the receipt is the
    OUTPUT of the universal apply step (CORE §2.1 #5). Each receipt's
    bound→landed divergence (UK RENUMBER relabel) is owned by the named rule
    ``uk_section_renumber_relabel`` so ``WriteReceipt.divergence_explained`` holds.

THE PARTITION IS SEAM-SOURCED, NOT ADJUDICATION-KEYED. UK emits ~70 distinct
``uk_replay_*`` adjudication kinds, most of which are recovery-applied
(``*_applied`` / ``*_recovered`` / ``*_resolved``), totality-probe skips
(``*_probe_skipped``), or post-apply findings (``*_tree_invariant_violation``),
NOT per-op skips. Enumerating the genuine-skip subset (the EE/EU/NO/SE approach)
would be fragile against UK's surface. Instead the partition reads the seam's
per-op ``AppliedOp.applied`` signal directly (via ``replay_uk_ops``'s
``applied_op_ids_out`` sink): a PREPARED op is accepted iff its seam apply landed
a write; a prepared op that landed no write is apply-skipped; a prepare-filtered
op is rejected at prepare time (carrying the prepare adjudication's witness).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, List, Optional

from lawvm.core.filter_result import FilterResult, RejectedItem
from lawvm.core.ir import IRStatute, LegalOperation
from lawvm.core.mutation_events import MutationEvent
from lawvm.core.totalization import Reject
from lawvm.core.write_receipt import WriteReceipt
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.replay_executor import (
    _prepare_replay_uk_ops,
    replay_uk_ops,
)
from lawvm.uk_legislation.totalization_table import UK_TOTALIZATION_TABLE
from lawvm.uk_legislation.uk_write_receipts import emit_uk_op_receipt


@dataclass(frozen=True, slots=True)
class UKApplyResult:
    """Typed apply-result conservation carrier (AGENTS.md §1.8).

    Mirrors the :class:`~lawvm.core.filter_result.FilterResult` contract shape:
    every op in the input set is either in ``applied_ops`` (its binding landed in
    the output statute) or surfaces as a
    :class:`~lawvm.core.filter_result.RejectedItem` witness in ``skipped_items``
    with a ``reason`` / ``reason_code`` and ``blocking`` disposition. The mutation
    footprint (the IRStatute returned by :func:`replay_uk_ops`) is the ``statute``
    field.

    The ``filter_result`` field is the canonical ``FilterResult`` projection of
    the same accepted/rejected partition, so callers that already consume the
    shared core type can reuse it without unpacking ``applied_ops`` /
    ``skipped_items`` separately.

    The optional ``write_receipts`` field carries per-op landed-write receipts
    (AGENTS.md §2.3) when the conserved wrapper is invoked with
    ``emit_receipts=True``. Default ``()`` so receipt-free callers pay no per-op
    snapshot overhead. Mirrors the EU/SE/NO precedents.
    """

    statute: IRStatute
    filter_result: "FilterResult[LegalOperation]"
    write_receipts: tuple["WriteReceipt", ...] = ()

    @property
    def applied_ops(self) -> tuple["LegalOperation", ...]:
        return self.filter_result.accepted_items

    @property
    def skipped_items(self) -> tuple["RejectedItem[LegalOperation]", ...]:
        return self.filter_result.rejected_items


def replay_uk_ops_conserved(
    base: IRStatute,
    ops: list[LegalOperation] | tuple["LegalOperation", ...],
    *,
    eid_map: Optional[dict[str, str]] = None,
    text_map: Optional[dict[str, str]] = None,
    allow_oracle_alignment: bool = True,
    verbose: bool = False,
    lo_ops_out: Optional[List[LegalOperation]] = None,
    adjudications_out: Optional[List[CompileAdjudication]] = None,
    mutation_events_out: Optional[list[MutationEvent]] = None,
    replay_phase_timings_out: Optional[dict[str, float]] = None,
    emit_receipts: bool = False,
) -> UKApplyResult:
    """Apply a UK op set with a typed conservation receipt (§1.8).

    Mirrors :func:`replay_uk_ops` exactly (same replay semantics, same
    ``lo_ops_out`` / ``adjudications_out`` / ``mutation_events_out`` side
    channels — when the caller passes them, both the conserved typed result AND
    the existing descriptive streams are populated). Returns a
    :class:`UKApplyResult` whose ``filter_result`` partitions every input op into
    accepted (its replay applied) or rejected (its replay was prepare-filtered or
    apply-skipped, with a witness carrying the reason). The contract is monotone:
    every input op ends up either accepted or rejected, never silently dropped.

    Identity. The ``op_id`` string is NOT a safe identity key on its own — it
    defaults to "" and is not guaranteed unique. An EMPTY ``op_id`` is rejected
    with a ``ValueError`` (it cannot be attributed to any lane). DUPLICATE
    ``op_id`` values are TOLERATED: real UK lowering emits same-op_id sibling ops
    (an effect's structural + text legs share a ``key-…`` op_id), and the bare
    ``replay_uk_ops`` fold applies ops POSITIONALLY (never by op_id), so a
    duplicate is harmless to replay output. To remain an OUTPUT-PRESERVING
    superset of that fold over the production corpus, the conserved partition is
    a MULTISET partition keyed on op_id COUNTS: for each op_id, the number of
    input ops equals the number prepare-rejected plus the number prepared, and
    among the prepared exactly ``applied_op_id_counts[op_id]`` (the seam's
    landed-write count) are accepted with the rest apply-skipped. In the common
    unique-op_id case this is byte-identical to a set-keyed partition. An op is
    rejected iff (a) it was filtered at prepare time (``replay_prepare`` moved it
    to the rejected lane with a typed adjudication) OR (b) it was a prepared op
    whose seam apply landed NO write.

    When ``emit_receipts=True``, per-op landed-write receipts are also produced
    via :func:`uk_replay_write_receipts` and surfaced on
    :attr:`UKApplyResult.write_receipts` (the §2.9 guard-liveness fix: a receipt
    lane that exists but is unreachable from production is the worst-case silent
    failure). Mirrors EU/SE/NO.
    """
    ops_list = list(ops)
    op_ids = [op.op_id for op in ops_list]
    if any(not op_id for op_id in op_ids):
        empty_positions = [i for i, op_id in enumerate(op_ids) if not op_id]
        raise ValueError(
            "replay_uk_ops_conserved requires every op to carry a non-empty op_id "
            "(the conservation partition keys on op_id and an empty op_id would be "
            f"silently dropped from the skipped lane). Empty op_id at positions {empty_positions}."
        )

    # DUPLICATE op_ids are TOLERATED, not rejected. Real UK lowering emits
    # same-op_id sibling ops (an effect whose structural + text legs share a
    # ``key-…`` op_id; ``prepare`` itself keys on op_id via set membership and so
    # the bare ``replay_uk_ops`` fold — which applies ops POSITIONALLY, never by
    # op_id — is unaffected by the collision). To stay an OUTPUT-PRESERVING
    # superset of the bare fold over the production corpus, the conserved
    # partition is therefore a MULTISET partition keyed on op_id COUNTS rather
    # than op_id identity: for each op_id, (# input ops) == (# prepare-rejected)
    # + (# prepared); among the prepared, (# landed a write) are accepted and the
    # rest are apply-skipped. Totality still holds (accepted + rejected == input)
    # and, in the common unique-op_id case, this is byte-identical to a set-keyed
    # partition. A specific duplicate leg's accepted-vs-skipped lane is inherently
    # ambiguous — the bare positional fold does not distinguish same-op_id
    # siblings either — so the multiset attribution is the faithful reflection of
    # the fold, not a lossy approximation of it.

    # Recover the prepare partition: prepare-filtered ops are rejected at
    # prepare time with a typed adjudication carrying the witness. The prepare
    # step's ``filter_result`` keeps accepted (forwarded to the apply fold) and
    # rejected (filtered) ops, each rejected one paired with its adjudication.
    prepared = _prepare_replay_uk_ops(
        ops_list,
        base_ir=base,
        verbose=verbose,
        adjudications_out=None,  # don't double-emit; the apply run below emits
    )
    # Per-op_id FIFO queue of prepare-rejection witnesses (a list, not a dict, so
    # duplicate op_ids each retain their own witness).
    prepare_rejected_by_op_id: dict[str, list[RejectedItem[LegalOperation]]] = {}
    for rejected_item in prepared.filter_result.rejected_items:
        prepare_rejected_by_op_id.setdefault(rejected_item.item.op_id, []).append(
            rejected_item
        )

    # Drive the bare apply fold, recording — per op_id — HOW MANY prepared ops
    # landed a write (a multiset, so duplicate op_ids keep their multiplicity).
    # ``adjudications_out`` (when the caller passed one) is routed directly so the
    # caller's accumulator gets the full descriptive stream AND a mid-fold raise
    # preserves the witnesses emitted before it.
    applied_op_id_counts: Counter[str] = Counter()
    applied_statute = replay_uk_ops(
        base,
        ops_list,
        eid_map=eid_map,
        text_map=text_map,
        allow_oracle_alignment=allow_oracle_alignment,
        verbose=verbose,
        lo_ops_out=lo_ops_out,
        adjudications_out=adjudications_out,
        mutation_events_out=mutation_events_out,
        replay_phase_timings_out=replay_phase_timings_out,
        applied_op_id_counts_out=applied_op_id_counts,
    )

    accepted: list[LegalOperation] = []
    rejected: list[RejectedItem[LegalOperation]] = []
    # Consume the accepted (landed-write) budget per op_id as we walk input order,
    # so exactly ``applied_op_id_counts[op_id]`` ops per op_id land in accepted and
    # the surplus fall through to the rejected lanes.
    remaining_applied: Counter[str] = Counter(applied_op_id_counts)
    for op in ops_list:
        if remaining_applied[op.op_id] > 0:
            # A seam apply landed a write for an op carrying this op_id — accept
            # this one and consume one unit of the op_id's landed-write budget.
            remaining_applied[op.op_id] -= 1
            accepted.append(op)
            continue
        prepare_rejected_queue = prepare_rejected_by_op_id.get(op.op_id)
        if prepare_rejected_queue:
            # Filtered at prepare time — carry the prepare adjudication witness.
            rejected.append(prepare_rejected_queue.pop(0))
            continue
        # Prepared but landed no write (target not found / no-op / unsupported
        # action that the dispatch skipped). The descriptive adjudication (when
        # ``adjudications_out`` was passed) carries the detail; the conserved
        # rejected lane is the per-op witness regardless.
        #
        # θ (§2.3): the disposition is the UK table's SEAM-SOURCED strict default
        # — ``UK_TOTALIZATION_TABLE.default`` — so the off-domain reason code has
        # a single source (``uk_legislation/totalization_table.py``). The UK is
        # the I1 ✓seam frontend: this cell is the DEFAULT precisely because the
        # accepted/rejected partition is derived from the seam applied-signal, not
        # enumerated per lane. Reading it through the table is byte-identical
        # (``default.code == "uk_apply_no_write"``).
        #
        # Reason-code namespace note: this is a conserved-wrapper
        # ``RejectedItem.reason_code``, NOT a ``CompileAdjudication`` kind. It is
        # deliberately NOT in the ``uk_replay_*`` namespace so it does not
        # collide with the adjudication-kind ownership registry that
        # ``tests/test_uk_source_adjudication.py`` enforces over every
        # ``uk_replay_*`` literal in the replay modules (the apply-skip's
        # descriptive adjudication, when ``adjudications_out`` is passed, carries
        # the owned ``uk_replay_target_not_found`` / ``uk_replay_*`` kind).
        no_write_disposition = UK_TOTALIZATION_TABLE.default
        assert isinstance(no_write_disposition, Reject)
        rejected.append(
            RejectedItem(
                item=op,
                reason="UK apply landed no write for this op (target not found, no-op, or unsupported action).",
                reason_code=no_write_disposition.code,
                blocking=False,
            )
        )

    write_receipts: tuple[WriteReceipt, ...] = ()
    if emit_receipts:
        _, write_receipts = uk_replay_write_receipts(
            base,
            ops_list,
            eid_map=eid_map,
            text_map=text_map,
            allow_oracle_alignment=allow_oracle_alignment,
        )

    return UKApplyResult(
        statute=applied_statute,
        filter_result=FilterResult(
            accepted_items=tuple(accepted),
            rejected_items=tuple(rejected),
        ),
        write_receipts=write_receipts,
    )


def uk_replay_write_receipts(
    base: IRStatute,
    ops: list[LegalOperation] | tuple[LegalOperation, ...],
    *,
    eid_map: Optional[dict[str, str]] = None,
    text_map: Optional[dict[str, str]] = None,
    allow_oracle_alignment: bool = True,
) -> tuple[IRStatute, tuple[WriteReceipt, ...]]:
    """Apply ops one at a time and emit per-op :class:`WriteReceipt` records (§2.3).

    Mirrors ``norway/grafter.py::no_replay_write_receipts`` and
    ``eu/pipeline.py::eu_replay_write_receipts``. For each op, applies it via
    :func:`replay_uk_ops` to a single-op list with a ``write_receipts_out`` sink,
    so UK's existing :func:`emit_uk_op_receipt` (the seam's receipt synthesis is
    byte-identical to it: the ``receipt_helper_prefix`` /
    ``renumber_migration_rule_ids`` on the UK profile match) produces the §2.3
    receipt from the actual before/after IR diff. Skipped ops (no body change)
    emit no receipt — the conserved FilterResult's rejected_items lane carries
    the witness instead.

    Per-op apply is NOT guaranteed body-equal to the full
    :func:`replay_uk_ops` fold when the replay branches on multi-op interlocks
    (e.g. renumber-before-insert ordering edges, same-source text-patch chains);
    like NO's renumber-vacate chain, that is a pre-existing property of the
    single-op receipt fold, orthogonal to the receipt CONTRACT this lane proves.
    Returns ``(final_statute, receipts_tuple)``.
    """
    current = base
    receipts: list[WriteReceipt] = []
    for op in ops:
        sink: list[WriteReceipt] = []
        current = replay_uk_ops(
            current,
            [op],
            eid_map=eid_map,
            text_map=text_map,
            allow_oracle_alignment=allow_oracle_alignment,
            write_receipts_out=sink,
        )
        receipts.extend(sink)
    return current, tuple(receipts)


# ``emit_uk_op_receipt`` is re-exported here so the receipt synthesis used by the
# single-op fold above is importable alongside the conserved wrapper (the §2.3
# producer-side record). Kept as a module attribute reference (not a star-export)
# so static analysis sees the binding.
_emit_uk_op_receipt: Any = emit_uk_op_receipt
