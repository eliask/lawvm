"""Pre-apply structural preparation for ``process_muutoslaki``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from lawvm.core.filter_result import FilterResult, RejectedItem
from lawvm.core.stage_result import PartitionResult, Residual
from lawvm.finland.chapter_seed import _op_targets_chapter
from lawvm.finland.chapter_seed_targets import (
    ChapterSeedSkipInput,
    normalize_chapter_seed_skips,
)
from lawvm.finland.ops import AmendmentOp, OpType
from lawvm.finland.restructure_plan import (
    StructuralTransformPlan,
    build_restructure_plan,
)
from lawvm.core.quirks_disposition import QuirksDisposition

ReplayPrint = Callable[[str], None]

FI_CHAPTER_SEED_SKIP_RULE_ID = "fi.chapter_seed.skip_seeded_chapter_op"


@dataclass(frozen=True, slots=True)
class ChapterSeedDroppedOp:
    """Operation suppressed because chapter seeding already materialized it."""

    op_id: str
    op_type: str
    target_unit_kind: str
    target_section: str
    target_chapter: str | None
    target_part: str | None
    description: str
    source_statute: str
    witness_rule_id: str | None

    @classmethod
    def from_op(cls, op: AmendmentOp) -> "ChapterSeedDroppedOp":
        return cls(
            op_id=op.op_id,
            op_type=op.op_type,
            target_unit_kind=op.target_cols.target_unit_kind,
            target_section=op.target_cols.target_section,
            target_chapter=op.target_cols.target_chapter,
            target_part=op.target_cols.target_part,
            description=op.description(),
            source_statute=op.source_statute,
            witness_rule_id=op.witness_rule_id,
        )

    def as_detail(self) -> dict[str, object]:
        return {
            "op_id": self.op_id,
            "op_type": self.op_type,
            "target_unit_kind": self.target_unit_kind,
            "target_section": self.target_section,
            "target_chapter": self.target_chapter,
            "target_part": self.target_part,
            "description": self.description,
            "source_statute": self.source_statute,
            "witness_rule_id": self.witness_rule_id,
        }


@dataclass(slots=True)
class ProcessStructuralPrepareContext:
    amendment_id: str
    target_statute: str
    ops: list[AmendmentOp]
    chapter_seed_skip: Optional[set[ChapterSeedSkipInput]]
    restructure_plans: list[StructuralTransformPlan]
    elaboration_observations: list[dict[str, object]]
    replay_print: ReplayPrint

    def prepare(self) -> list[AmendmentOp]:
        # Conservation (Audit C): the seed-skip filter returns a conserving
        # PartitionResult — accepted (kept) ops + rejected (dropped) ops carrying
        # the typed ChapterSeedDroppedOp record + a typed Residual naming the
        # seeded chapters. This method is the production consumer: it reads
        # ``.rejected`` / ``.residuals`` and surfaces them on the elaboration
        # observation ledger (the same ledger replay already drains). Nothing is
        # dropped silently — the drop is the rejected lane.
        partition = self._drop_seeded_chapter_ops(self.ops)
        self._record_chapter_seed_skip(partition)
        ops = list(partition.accepted)
        self._preseed_restructure_plan(ops)
        return ops

    def _drop_seeded_chapter_ops(
        self, ops: list[AmendmentOp]
    ) -> PartitionResult[AmendmentOp]:
        # The seeded content is already in state.ir. Re-applying the same ops
        # would either fail (REPLACE on existing) or duplicate. The matching ops
        # are not silently dropped — they are routed to the rejected lane with a
        # typed reason so the conservation account stays total.
        if not self.chapter_seed_skip or not ops:
            return PartitionResult(FilterResult(accepted_items=tuple(ops)))

        seeded_labels = {
            skip.chapter_label
            for skip in normalize_chapter_seed_skips(self.chapter_seed_skip)
            if skip.amendment_id == self.amendment_id
        }
        if not seeded_labels:
            return PartitionResult(FilterResult(accepted_items=tuple(ops)))

        dropped_ops = [op for op in ops if _op_targets_chapter(op, seeded_labels)]
        if not dropped_ops:
            return PartitionResult(FilterResult(accepted_items=tuple(ops)))

        kept_ops = [op for op in ops if not _op_targets_chapter(op, seeded_labels)]
        rejected = tuple(
            RejectedItem(
                item=op,
                reason=(
                    "chapter body was already seeded from this amendment; "
                    "re-applying matching chapter op would duplicate or fail"
                ),
                reason_code=FI_CHAPTER_SEED_SKIP_RULE_ID,
                blocking=False,
            )
            for op in dropped_ops
        )
        residuals = tuple(
            Residual(
                kind="out_of_scope",
                reason=(
                    "chapter body already seeded from this amendment; "
                    f"chapter(s) {label} matching ops suppressed at structural prepare"
                ),
                scope=f"{self.amendment_id}:chapter:{label}",
                blocking=False,
            )
            for label in sorted(seeded_labels)
        )
        return PartitionResult(
            FilterResult(accepted_items=tuple(kept_ops), rejected_items=rejected),
            residuals=residuals,
        )

    def _record_chapter_seed_skip(
        self, partition: PartitionResult[AmendmentOp]
    ) -> None:
        """Surface the rejected lane onto the elaboration-observation ledger.

        This is the production consumer of the partition's ``.rejected`` /
        ``.residuals``: the dropped ops are turned back into the same elaboration
        observation replay already drains. No-op when nothing was rejected.
        """
        if not partition.rejected:
            return

        dropped_ops = [rejected.item for rejected in partition.rejected]
        dropped_records = tuple(ChapterSeedDroppedOp.from_op(op) for op in dropped_ops)
        seeded_chapters = sorted(
            residual.scope.rsplit(":", 1)[-1] for residual in partition.residuals
        )
        self.elaboration_observations.append(
            {
                "kind": "ELAB.CHAPTER_SEED_SKIP",
                "rule_id": FI_CHAPTER_SEED_SKIP_RULE_ID,
                "family": "ontology_normalization",
                "phase": "process_muutoslaki.structural_prepare",
                "source_statute": self.amendment_id,
                "strict_disposition": "inherit_chapter_seed_repair",
                "quirks_disposition": QuirksDisposition.SUPPRESS_DUPLICATE_APPLY,
                "reason": (
                    "chapter body was already seeded from this amendment; "
                    "re-applying matching chapter op would duplicate or fail"
                ),
                "seeded_chapters": seeded_chapters,
                "dropped_count": len(dropped_ops),
                "dropped_ops": [op.description() for op in dropped_ops],
                "dropped_op_records": [record.as_detail() for record in dropped_records],
            }
        )
        self.replay_print(
            f"  [{self.amendment_id}] SEED-SKIP: dropped {len(dropped_ops)} op(s) "
            f"targeting seeded chapter(s) {seeded_chapters}"
        )

    def _preseed_restructure_plan(self, ops: list[AmendmentOp]) -> None:
        # Pre-seed pure relabel restructure plans before the main apply loop so
        # same-act structural ownership is not split between the resolved-op
        # path and the restructure executor. Coverage-aware plans may still be
        # added later during uncovered-body analysis; exact duplicates are
        # suppressed there.
        preseed_ops = [op for op in ops if op.op_type == OpType.RENUMBER]
        if not preseed_ops:
            return
        early_plan = build_restructure_plan(
            self.target_statute,
            self.amendment_id,
            ops=preseed_ops,
            uncov_ratio=0.0,
            total_units=0,
            body_unit_ids_by_chapter=None,
        )
        if early_plan is None:
            return
        duplicate = any(
            existing.amendment_id == self.amendment_id
            and existing.ops == early_plan.ops
            for existing in self.restructure_plans
        )
        if duplicate:
            return
        self.restructure_plans.append(early_plan)
