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
        self.elaboration_observations.append(
            {
                "kind": "ELAB.CHAPTER_SEED_SKIP",
                "source_statute": self.amendment_id,
                "seeded_chapters": sorted(seeded_labels),
                "dropped_count": len(dropped_ops),
                "dropped_ops": [op.description() for op in dropped_ops],
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
