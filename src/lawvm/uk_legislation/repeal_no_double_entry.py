"""No-double-entry filtering for UK repeal Schedule duplicates.

OPC drafting guidance says a repeal belongs either in the body text or in a
repeal Schedule, not both. The legislation.gov.uk effects feed can nevertheless
emit two rows for the same target: one for the body provision carrying the repeal
Schedule context and one for the repeal Schedule row alone. This module removes
only exact duplicate structural repeal operations from those diagnosed groups and
records the rejected operation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.ir import LegalOperation
from lawvm.core.semantic_types import StructuralAction
from lawvm.uk_legislation.addressing import _action_name
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.repeal_semantics_witnesses import (
    is_repeal_semantics_effect,
    normalize_repeal_semantics_text,
)


@dataclass(frozen=True)
class UKRepealNoDoubleEntryGroup:
    """One same-target body+schedule duplicate group from the effect feed."""

    group_id: str
    affected_provisions: str
    affecting_act_id: str
    related_effect_ids: tuple[str, ...]
    related_affecting_provisions: tuple[str, ...]


def collect_repeal_no_double_entry_groups(
    effects: Sequence[UKEffectRecord],
) -> tuple[UKRepealNoDoubleEntryGroup, ...]:
    """Return effect-id groups covered by the no-double-entry repeal rule."""
    groups: dict[tuple[str, str, str], list[UKEffectRecord]] = defaultdict(list)
    for effect in effects:
        if not is_repeal_semantics_effect(effect):
            continue
        affected_key = normalize_repeal_semantics_text(effect.affected_provisions)
        if not affected_key:
            continue
        key = (
            str(effect.affecting_act_id or ""),
            affected_key,
            _repeal_effect_family_key(effect.effect_type),
        )
        groups[key].append(effect)

    out: list[UKRepealNoDoubleEntryGroup] = []
    for index, group in enumerate(groups.values()):
        affecting_refs = tuple(
            sorted(
                {
                    str(effect.affecting_provisions or "")
                    for effect in group
                    if effect.affecting_provisions
                }
            )
        )
        if len(group) < 2 or len(affecting_refs) < 2:
            continue
        participating_refs = _body_schedule_double_entry_refs(affecting_refs)
        if not participating_refs:
            continue
        related_effects = tuple(
            effect
            for effect in group
            if _normalize_source_ref(effect.affecting_provisions) in participating_refs
        )
        related_effect_ids = tuple(str(effect.effect_id or "") for effect in related_effects)
        if not all(related_effect_ids):
            continue
        first = related_effects[0]
        out.append(
            UKRepealNoDoubleEntryGroup(
                group_id=f"uk_repeal_no_double_entry:{index}",
                affected_provisions=str(first.affected_provisions or ""),
                affecting_act_id=str(first.affecting_act_id or ""),
                related_effect_ids=related_effect_ids,
                related_affecting_provisions=tuple(
                    ref
                    for ref in affecting_refs
                    if _normalize_source_ref(ref) in participating_refs
                ),
            )
        )
    return tuple(out)


def filter_repeal_no_double_entry_ops(
    ops: Sequence[LegalOperation],
    groups: Sequence[UKRepealNoDoubleEntryGroup],
    *,
    diagnostics_out: Optional[list[dict[str, Any]]] = None,
) -> list[LegalOperation]:
    """Drop exact duplicate structural repeals within body+schedule groups."""
    group_by_effect_id: dict[str, UKRepealNoDoubleEntryGroup] = {}
    for group in groups:
        for effect_id in group.related_effect_ids:
            group_by_effect_id[effect_id] = group
    if not group_by_effect_id:
        return list(ops)

    accepted: list[LegalOperation] = []
    seen: dict[tuple[str, str, tuple[tuple[str, str], ...], str], LegalOperation] = {}
    for op in ops:
        group = group_by_effect_id.get(str(op.op_id or ""))
        if group is None or not _is_plain_structural_repeal(op):
            accepted.append(op)
            continue
        key = (
            group.group_id,
            _source_effective_key(op),
            op.target.path,
            op.witness_rule_id or "",
        )
        previous = seen.get(key)
        if previous is None:
            seen[key] = op
            accepted.append(op)
            continue
        _append_no_double_entry_rejection(
            diagnostics_out,
            rejected=op,
            kept=previous,
            group=group,
        )
    return accepted


def _is_plain_structural_repeal(op: LegalOperation) -> bool:
    return (
        op.action is StructuralAction.REPEAL
        and op.payload is None
        and op.text_patch is None
    )


def _source_effective_key(op: LegalOperation) -> str:
    if op.source is None:
        return ""
    return f"{op.source.statute_id}|{op.source.effective}"


def _append_no_double_entry_rejection(
    diagnostics_out: Optional[list[dict[str, Any]]],
    *,
    rejected: LegalOperation,
    kept: LegalOperation,
    group: UKRepealNoDoubleEntryGroup,
) -> None:
    if diagnostics_out is None:
        return
    diagnostics_out.append(
        diagnostic_detail(
            rule_id="uk_effect_repeal_no_double_entry_duplicate_rejected",
            family="repeal_no_double_entry",
            phase="lowering",
            blocking=False,
            reason=(
                "same repeal target was emitted by both a body provision and "
                "the referenced repeal Schedule; keeping the first operation"
            ),
            effect_id=str(rejected.op_id or ""),
            kept_effect_id=str(kept.op_id or ""),
            action=_action_name(rejected.action),
            target=str(rejected.target),
            affecting_act_id=group.affecting_act_id,
            affected_provisions=group.affected_provisions,
            related_effect_ids=list(group.related_effect_ids),
            related_affecting_provisions=list(group.related_affecting_provisions),
            witness_rule_id=str(rejected.witness_rule_id or ""),
        )
    )


def _body_schedule_double_entry_refs(affecting_refs: tuple[str, ...]) -> frozenset[str]:
    normalized = tuple(_normalize_source_ref(ref) for ref in affecting_refs)
    participating: set[str] = set()
    for shorter in normalized:
        if not shorter.startswith("sch."):
            continue
        for longer in normalized:
            if longer == shorter:
                continue
            if longer.endswith(shorter):
                participating.add(shorter)
                participating.add(longer)
    return frozenset(participating)


def _repeal_effect_family_key(effect_type: str) -> str:
    normalized = normalize_repeal_semantics_text(effect_type)
    if "revoke" in normalized:
        return "revocation"
    if "omit" in normalized:
        return "omission"
    if "cease" in normalized:
        return "cease_effect"
    return "repeal"


def _normalize_source_ref(text: str) -> str:
    return normalize_repeal_semantics_text(text).strip(" .,;")


def group_rows(groups: Sequence[UKRepealNoDoubleEntryGroup]) -> tuple[Mapping[str, Any], ...]:
    """JSON-safe projection for tests and future diagnostic surfaces."""
    return tuple(
        {
            "group_id": group.group_id,
            "affected_provisions": group.affected_provisions,
            "affecting_act_id": group.affecting_act_id,
            "related_effect_ids": group.related_effect_ids,
            "related_affecting_provisions": group.related_affecting_provisions,
        }
        for group in groups
    )
