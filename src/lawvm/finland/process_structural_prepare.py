"""Pre-apply structural preparation for ``process_muutoslaki``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from lawvm.finland.chapter_seed import _op_targets_chapter
from lawvm.finland.chapter_seed_targets import (
    ChapterSeedSkipInput,
    normalize_chapter_seed_skips,
)
from lawvm.finland.ops import AmendmentOp
from lawvm.finland.restructure_plan import (
    StructuralTransformPlan,
    build_restructure_plan,
)

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
            target_unit_kind=op.target_unit_kind,
            target_section=op.target_section,
            target_chapter=op.target_chapter,
            target_part=op.target_part,
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
        ops = self._drop_seeded_chapter_ops(self.ops)
        self._preseed_restructure_plan(ops)
        return ops

    def _drop_seeded_chapter_ops(self, ops: list[AmendmentOp]) -> list[AmendmentOp]:
        # The seeded content is already in state.ir. Re-applying the same ops
        # would either fail (REPLACE on existing) or duplicate.
        if not self.chapter_seed_skip or not ops:
            return ops

        seeded_labels = {
            skip.chapter_label
            for skip in normalize_chapter_seed_skips(self.chapter_seed_skip)
            if skip.amendment_id == self.amendment_id
        }
        if not seeded_labels:
            return ops

        dropped_ops = [op for op in ops if _op_targets_chapter(op, seeded_labels)]
        if not dropped_ops:
            return ops

        kept_ops = [op for op in ops if not _op_targets_chapter(op, seeded_labels)]
        dropped_records = tuple(ChapterSeedDroppedOp.from_op(op) for op in dropped_ops)
        self.elaboration_observations.append(
            {
                "kind": "ELAB.CHAPTER_SEED_SKIP",
                "rule_id": FI_CHAPTER_SEED_SKIP_RULE_ID,
                "family": "ontology_normalization",
                "phase": "process_muutoslaki.structural_prepare",
                "source_statute": self.amendment_id,
                "strict_disposition": "inherit_chapter_seed_repair",
                "quirks_disposition": "suppress_duplicate_apply",
                "reason": (
                    "chapter body was already seeded from this amendment; "
                    "re-applying matching chapter op would duplicate or fail"
                ),
                "seeded_chapters": sorted(seeded_labels),
                "dropped_count": len(dropped_ops),
                "dropped_ops": [op.description() for op in dropped_ops],
                "dropped_op_records": [record.as_detail() for record in dropped_records],
            }
        )
        self.replay_print(
            f"  [{self.amendment_id}] SEED-SKIP: dropped {len(dropped_ops)} op(s) "
            f"targeting seeded chapter(s) {sorted(seeded_labels)}"
        )
        return kept_ops

    def _preseed_restructure_plan(self, ops: list[AmendmentOp]) -> None:
        # Pre-seed pure relabel restructure plans before the main apply loop so
        # same-act structural ownership is not split between the resolved-op
        # path and the restructure executor. Coverage-aware plans may still be
        # added later during uncovered-body analysis; exact duplicates are
        # suppressed there.
        if not ops:
            return
        early_plan = build_restructure_plan(
            self.target_statute,
            self.amendment_id,
            ops=list(ops),
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
