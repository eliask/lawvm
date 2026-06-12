"""Proof-directed Finland timeline-export planning.

This module is a passive sidecar for now.  It classifies the proof boundary
between source-owned descendant operations and parent snapshots without changing
the existing replay-product exporter.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Sequence

from lawvm.core.payload_surface import TargetUnitKind

if TYPE_CHECKING:
    from lawvm.finland.ops import ResolvedOp


class TimelineExportMode(str, Enum):
    """How a group of resolved Finland ops should be exported to timelines."""

    EXACT_DESCENDANT_OPS = "exact_descendant_ops"
    PARENT_SNAPSHOT = "parent_snapshot"
    PARENT_SNAPSHOT_WITH_CHILD_SNAPSHOTS = "parent_snapshot_with_child_snapshots"
    TEMPORARY_OVERLAY_COMPAT = "temporary_overlay_compat"
    COMPAT_PARENT_SNAPSHOT = "compat_parent_snapshot"
    FAILURE_NO_EXPORT = "failure_no_export"


class ParentSnapshotProof(str, Enum):
    """Named proof family authorizing parent-level snapshot ownership."""

    COMPLETE_WHOLE_UNIT_SOURCE_PAYLOAD = "complete_whole_unit_source_payload"
    EXACT_SPARSE_REBASED_SURFACE = "exact_sparse_rebased_surface"
    TEMPORARY_OVERLAY_PARENT = "temporary_overlay_parent"
    STRUCTURAL_SCAFFOLD = "structural_scaffold"
    SOURCE_OWNED_DELETIONS = "source_owned_deletions"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class TimelineExportPlan:
    """Typed proof summary for replay-product timeline export."""

    mode: TimelineExportMode
    parent_snapshot_proof: ParentSnapshotProof = ParentSnapshotProof.NONE
    reasons: tuple[str, ...] = ()
    exact_descendant_targets: tuple[str, ...] = ()

    @property
    def authorizes_parent_snapshot(self) -> bool:
        return self.parent_snapshot_proof is not ParentSnapshotProof.NONE

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "parent_snapshot_proof": self.parent_snapshot_proof.value,
            "reasons": list(self.reasons),
            "exact_descendant_targets": list(self.exact_descendant_targets),
            "authorizes_parent_snapshot": self.authorizes_parent_snapshot,
        }


def classify_timeline_export_plan(
    group_rops: Sequence["ResolvedOp"],
    *,
    target_unit_kind: TargetUnitKind,
) -> TimelineExportPlan:
    """Classify the strongest export plan justified by typed op witnesses.

    The classifier is intentionally conservative:

    - fragmentary / preserve-tail descendant operations are not parent-snapshot
      proof;
    - complete whole-unit replacement is parent-snapshot proof;
    - temporary groups remain in the existing compatibility lane until expiry
      inheritance has its own proof object.
    """
    rops = tuple(group_rops)
    if not rops:
        return TimelineExportPlan(
            TimelineExportMode.FAILURE_NO_EXPORT,
            reasons=("empty_group",),
        )

    if any(rop.is_temporary for rop in rops):
        return TimelineExportPlan(
            TimelineExportMode.TEMPORARY_OVERLAY_COMPAT,
            parent_snapshot_proof=ParentSnapshotProof.TEMPORARY_OVERLAY_PARENT,
            reasons=("temporary_operation_group",),
        )

    if _has_complete_whole_unit_parent_proof(rops, target_unit_kind):
        return TimelineExportPlan(
            TimelineExportMode.PARENT_SNAPSHOT_WITH_CHILD_SNAPSHOTS,
            parent_snapshot_proof=ParentSnapshotProof.COMPLETE_WHOLE_UNIT_SOURCE_PAYLOAD,
            reasons=("complete_whole_unit_source_payload",),
        )

    descendant_targets = tuple(_exact_descendant_target_id(rop) for rop in rops)
    if (
        target_unit_kind == "section"
        and all(descendant_targets)
        and all(_is_supported_descendant_action(rop) for rop in rops)
        and _has_fragmentary_preserve_tail_witness(rops)
    ):
        return TimelineExportPlan(
            TimelineExportMode.EXACT_DESCENDANT_OPS,
            reasons=("fragmentary_payload_preserves_unstated_tail",),
            exact_descendant_targets=descendant_targets,
        )

    return TimelineExportPlan(
        TimelineExportMode.COMPAT_PARENT_SNAPSHOT,
        reasons=("legacy_export_requires_existing_snapshot_path",),
    )


def _has_complete_whole_unit_parent_proof(
    rops: tuple["ResolvedOp", ...],
    target_unit_kind: TargetUnitKind,
) -> bool:
    if target_unit_kind not in {"section", "chapter", "part"}:
        return False
    for rop in rops:
        if not rop.is_replace_action or not rop.targets_whole_unit(target_unit_kind):
            continue
        witness = rop.payload_completeness
        if witness is None:
            continue
        if (
            witness.kind == "complete"
            and witness.tail_policy == "replace_if_target_scope_requires"
        ):
            return True
    return False


def _has_fragmentary_preserve_tail_witness(rops: tuple["ResolvedOp", ...]) -> bool:
    for rop in rops:
        witness = rop.payload_completeness
        if witness is None:
            continue
        if witness.tail_policy == "preserve_unstated_tail":
            return True
        if witness.kind == "fragmentary":
            return True
    return False


def _is_supported_descendant_action(rop: "ResolvedOp") -> bool:
    return rop.resolved_action_type in {"REPLACE", "INSERT", "REPEAL"}


def _exact_descendant_target_id(rop: "ResolvedOp") -> str:
    address = rop.resolved_target_address
    if address is None or not address.path or address.special is not None:
        return ""
    if len(address.path) < 2:
        return ""
    final_kind, _final_label = address.path[-1]
    if final_kind not in {"subsection", "item"}:
        return ""
    return str(address)
