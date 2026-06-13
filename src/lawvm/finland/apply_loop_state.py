"""Typed state + audit for the Finland resolved-op apply fold.

``apply_ops_to_tree`` folds a list of resolved ops over a ``ReplayState``,
accumulating ops into *groups* (same target) and emitting one timeline snapshot
per group at each group boundary. The fold's mutable bookkeeping — the current
group key, the ops accumulated so far, the live path hint, and whether any apply
in the group failed — used to live as four bare locals mutated inline.

Reconstruction note (hostile-source / missing-spec compilation): without a source
spec, the apply loop's group-boundary semantics are part of what we must keep
auditable. Consolidating the bookkeeping into one typed object with explicit
transition methods (``advance_to``, ``record_apply_failed``, ``record_applied``)
makes the group lifecycle a named state machine rather than scattered
assignments, and the per-op :class:`ApplyOpAudit` gives the fold a per-decision
trail. The actual tree mutation (``apply_op``) and snapshot emission stay in the
caller — they close over the live ``state`` and many sinks — so this object owns
*only* the group bookkeeping, which is the part that is pure and self-contained.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from lawvm.finland.ops import ResolvedGroupKeyView, ResolvedOp

PathHint = Tuple[Tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ApplyOpAudit:
    """One typed audit record per resolved op processed by the apply fold.

    The observable trail of the apply loop: every op that the fold considers
    produces exactly one of these, naming whether it was applied, whether the
    apply failed, or whether it was skipped (no apply pass required). Pure data,
    reconstructable from the op alone.
    """

    op_id: str
    description: str
    disposition: str  # "APPLIED" | "APPLY_FAILED" | "NO_APPLY_PASS"

    def __post_init__(self) -> None:
        if self.disposition not in ("APPLIED", "APPLY_FAILED", "NO_APPLY_PASS"):
            raise ValueError(f"unknown apply disposition {self.disposition!r}")


@dataclass(slots=True)
class ApplyGroupState:
    """Group-boundary bookkeeping for the resolved-op apply fold.

    Threads the four mutable values the fold carried as bare locals:

    - ``prev_group_key`` — the group key of the previous op, so a change marks a
      group boundary (at which the caller emits the previous group's snapshot).
    - ``group_rops`` — the ops accumulated in the current group.
    - ``group_path_hint`` — the live resolved path of the current group, refreshed
      after each apply so later ops in the group resolve against the moved tree.
    - ``group_had_failed_apply`` — a failed apply is a replay barrier: the group's
      payload must not be promoted into the materialized timeline by the snapshot
      lane.

    Mutation is confined to the explicit transition methods so the group
    lifecycle is one auditable state machine.
    """

    prev_group_key: Optional[ResolvedGroupKeyView] = None
    group_rops: List[ResolvedOp] = field(default_factory=list)
    group_path_hint: Optional[PathHint] = None
    group_had_failed_apply: bool = False
    audits: List[ApplyOpAudit] = field(default_factory=list)

    def is_group_boundary(self, group_key: ResolvedGroupKeyView) -> bool:
        """Whether ``group_key`` opens a new group versus the current one."""
        return group_key != self.prev_group_key

    def start_group(self, group_key: ResolvedGroupKeyView) -> None:
        """Reset per-group accumulators for a freshly opened group."""
        self.group_rops = []
        self.prev_group_key = group_key
        self.group_path_hint = None
        self.group_had_failed_apply = False

    def mark_failed_apply(self) -> None:
        """Record that an apply in the current group failed (a replay barrier)."""
        self.group_had_failed_apply = True

    def set_path_hint(self, path_hint: Optional[PathHint]) -> None:
        """Update the current group's live resolved-path hint."""
        self.group_path_hint = path_hint

    def append_rop(self, rop: ResolvedOp, *, disposition: str) -> None:
        """Append a processed op to the current group and record its audit."""
        self.group_rops.append(rop)
        self.audits.append(
            ApplyOpAudit(
                op_id=rop.op_id or "",
                description=rop.description(),
                disposition=disposition,
            )
        )
