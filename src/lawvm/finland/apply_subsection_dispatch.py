"""Subsection dispatch helpers for Finland apply.

This module owns the shared subsection dispatcher used by both typed and
legacy section-level paths.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
import re
from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING, List, Optional

from lawvm.core.compile_result import SourcePathology
from lawvm.core.recovery_kind import RecoveryKind
from lawvm.core.ir import IRNode
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import Path, normalized_label_key
from lawvm.finland.ops import AmendmentOp, ReplayProfile, ResolvedOp, _rebind_resolved_target_address
from lawvm.core.compile_result import StrictProfile
from lawvm.finland.apply_subsection_ops import (
    _SubsectionApplyView,
    _apply_subsection_insert,
    _apply_subsection_repeal,
    _apply_subsection_replace,
    _subsection_apply_view_for_op,
)
from lawvm.finland.apply_item_ops import (
    _ItemApplyView,
    _apply_item_insert,
    _apply_item_repeal,
    _apply_item_replace,
    _apply_special_targets,
    _item_apply_view_for_op,
)
from lawvm.finland.apply_runtime_support import (
    _expired_temporary_section_merge_base,
    _expired_temporary_section_merge_base_rebase_info,
    _expired_temporary_subsection_slot_can_be_consumed,
    _legacy_target_section_for_scope,
    _with_preserved_provision_index,
)
from lawvm.finland.source_pathology import (
    build_subsection_target_rebound_pathology,
    build_temporary_section_rebase_pathology,
)
from lawvm.finland.apply_structure_ops import _normalize_subsection_target_hint_ir
from lawvm.finland.migration_ledger import migration_lower_bound_for_op
from lawvm.finland.apply_policy import same_wave_migration_follow_is_allowed

if TYPE_CHECKING:
    from lawvm.finland.migration_ledger import MigrationLedger
    from lawvm.finland.statute import ReplayState
    from lawvm.finland.payload_normalize import SubsectionSlotAssignmentResult

logger = logging.getLogger(__name__)
_ITEM_RANGE_TARGET_RE = re.compile(r"^(\d+)\s*[―–—\-]+\s*(\d+)$")


@dataclass(frozen=True)
class _SubsectionRoutingView:
    dispatch_op: AmendmentOp | ResolvedOp
    subsection_view: _SubsectionApplyView
    item_view: _ItemApplyView
    amend_sub_ir: Optional[IRNode]
    slot_assignment: "SubsectionSlotAssignmentResult | None"
    rop: ResolvedOp | None
    target_item: str | None
    target_section: str
    target_chapter: str | None
    target_part: str | None


@dataclass(frozen=True, slots=True)
class SubsectionDispatchFailureReason:
    reason: str
    reason_code: str


def _target_scope_for_failure_reason(
    dispatch_op: AmendmentOp | ResolvedOp,
) -> tuple[int | None, str | None]:
    if isinstance(dispatch_op, ResolvedOp):
        return dispatch_op.effective_target_paragraph, dispatch_op.effective_target_item_label
    return dispatch_op.target_paragraph, dispatch_op.target_item


def classify_subsection_dispatch_failure(
    dispatch_op: AmendmentOp | ResolvedOp,
    sec_node: IRNode,
) -> SubsectionDispatchFailureReason:
    """Classify a failed subsection/item dispatch using replay-time tree shape."""
    target_paragraph, target_item = _target_scope_for_failure_reason(dispatch_op)
    subsecs = [c for c in sec_node.children if c.kind == IRNodeKind.SUBSECTION]
    if target_item is not None:
        if not subsecs:
            return SubsectionDispatchFailureReason(
                reason="item target section has no subsection children",
                reason_code="item_no_subsections",
            )
        if target_paragraph is not None and target_paragraph > len(subsecs):
            return SubsectionDispatchFailureReason(
                reason=(
                    f"item target subsection {target_paragraph} out of range "
                    f"(subsections={len(subsecs)})"
                ),
                reason_code="item_subsection_out_of_range",
            )
        target_sub = subsecs[target_paragraph - 1] if target_paragraph is not None else subsecs[0]
        paras = [c for c in target_sub.children if c.kind == IRNodeKind.PARAGRAPH]
        if not paras:
            return SubsectionDispatchFailureReason(
                reason="item target subsection has no paragraph children",
                reason_code="item_no_paragraphs",
            )
        item_norm = normalized_label_key(target_item)
        if any(normalized_label_key(p.label or "") == item_norm for p in paras):
            return SubsectionDispatchFailureReason(
                reason="item target exists but no deterministic apply path matched",
                reason_code="item_target_exists_apply_failed",
            )
        item_label = str(target_item)
        try:
            want_idx = int(re.sub(r"[^\d]", "", item_label) or "0")
        except ValueError:
            want_idx = 0
        if want_idx > len(paras):
            return SubsectionDispatchFailureReason(
                reason=f"item target label {item_label} beyond paragraph count {len(paras)}",
                reason_code="item_label_gap",
            )
        return SubsectionDispatchFailureReason(
            reason=f"item target label {item_label} missing among {len(paras)} paragraphs",
            reason_code="item_label_missing",
        )

    if target_paragraph is not None:
        if not subsecs:
            return SubsectionDispatchFailureReason(
                reason="subsection target section has no subsection children",
                reason_code="subsection_no_subsections",
            )
        if target_paragraph > len(subsecs):
            return SubsectionDispatchFailureReason(
                reason=(
                    f"subsection target {target_paragraph} out of range "
                    f"(subsections={len(subsecs)})"
                ),
                reason_code="subsection_out_of_range",
            )
        return SubsectionDispatchFailureReason(
            reason="subsection target exists but no deterministic apply path matched",
            reason_code="subsection_target_exists_apply_failed",
        )

    return SubsectionDispatchFailureReason(
        reason="no deterministic path",
        reason_code="no_deterministic_path",
    )


def _prepare_subsection_routing(
    *,
    dispatch_op: AmendmentOp | ResolvedOp,
    rop: ResolvedOp | None,
    amend_sub_ir: Optional[IRNode],
    muutos_ir: Optional[IRNode],
    slot_assignment: "SubsectionSlotAssignmentResult | None",
) -> _SubsectionRoutingView:
    raw_dispatch_op = dispatch_op.op if isinstance(dispatch_op, ResolvedOp) else dispatch_op
    if rop is not None:
        resolved_dispatch_op = rop
        scope = rop.resolved_target_scope_view
        target_item = scope.target_item
        target_section = _legacy_target_section_for_scope(scope, rop.target_unit_kind)
        target_chapter = rop.resolved_target_scope_chapter_label
        target_part = rop.resolved_target_scope_part_label
        subsection_view = _subsection_apply_view_for_op(rop)
        item_view = _item_apply_view_for_op(rop)
    else:
        resolved_dispatch_op = raw_dispatch_op
        target_item = raw_dispatch_op.target_item
        target_section = raw_dispatch_op.target_section or ""
        target_chapter = raw_dispatch_op.target_chapter
        target_part = raw_dispatch_op.target_part
        subsection_view = _subsection_apply_view_for_op(raw_dispatch_op)
        item_view = _item_apply_view_for_op(raw_dispatch_op)
    if rop is not None:
        resolved_amend_sub_ir = rop.resolved_amend_sub_ir()
        resolved_slot_assignment = None
    elif slot_assignment is not None:
        resolved_amend_sub_ir = slot_assignment.resolve_apply_subsection_ir(raw_dispatch_op, amend_sub_ir)
        resolved_slot_assignment = slot_assignment
    elif muutos_ir is not None and amend_sub_ir is None:
        resolved_amend_sub_ir = None
        resolved_slot_assignment = slot_assignment
    else:
        resolved_amend_sub_ir = amend_sub_ir
        resolved_slot_assignment = slot_assignment
    return _SubsectionRoutingView(
        dispatch_op=resolved_dispatch_op,
        subsection_view=subsection_view,
        item_view=item_view,
        amend_sub_ir=resolved_amend_sub_ir,
        slot_assignment=resolved_slot_assignment,
        rop=rop,
        target_item=target_item,
        target_section=target_section,
        target_chapter=target_chapter,
        target_part=target_part,
    )


def _range_item_routing(
    *,
    dispatch_op: AmendmentOp | ResolvedOp,
    rop: ResolvedOp | None,
    item_num: int,
) -> tuple[AmendmentOp | ResolvedOp, ResolvedOp | None]:
    if rop is not None:
        single_rop = _rebind_resolved_target_address(
            rop,
            target_paragraph=rop.effective_target_paragraph,
            target_item=str(item_num),
            target_special=None,
        )
        return single_rop, single_rop
    return dc_replace(dispatch_op, target_item=str(item_num), lo=None), None


def _follow_same_wave_subsection_migration(
    rop: ResolvedOp,
    *,
    migration_ledger: "MigrationLedger | None",
) -> ResolvedOp:
    """Follow already-applied same-wave subsection renumbers for non-INSERT ops."""
    if (
        migration_ledger is None
        or rop.resolved_action_type == "INSERT"
        or not same_wave_migration_follow_is_allowed(rop)
    ):
        return rop
    address = rop.resolved_target_address
    if address is None or not any(kind in {"subsection", "item"} for kind, _label in address.path):
        return rop

    op_effective = migration_lower_bound_for_op(rop)
    migrated = migration_ledger.current_address_with_prefix_migrations(address, not_before=op_effective)
    if migrated == address:
        return rop

    migrated_labels = {kind: label for kind, label in migrated.path}
    migrated_paragraph = migrated_labels.get("subsection")
    if migrated_paragraph is None or not migrated_paragraph.isdigit():
        return rop

    rebound = _rebind_resolved_target_address(
        rop,
        target_paragraph=int(migrated_paragraph),
        target_item=migrated_labels.get("item"),
        target_special=rop.effective_target_special,
    )
    return dc_replace(
        rebound,
        target_guessing_provenance_tags=tuple(
            dict.fromkeys((*rebound.target_guessing_provenance_tags, "follow_same_wave_migration"))
        ),
    )


def _unique_subsection_label_for_item_target(
    master_subsecs: List[IRNode],
    item_label: str | None,
) -> str | None:
    if not item_label:
        return None
    item_norm = normalized_label_key(item_label)
    matches: list[str] = []
    for subsection in master_subsecs:
        if not subsection.label:
            continue
        if any(
            child.kind is IRNodeKind.PARAGRAPH
            and child.label
            and normalized_label_key(child.label) == item_norm
            for child in subsection.children
        ):
            matches.append(str(subsection.label))
    return matches[0] if len(matches) == 1 else None


def _rebound_item_only_target_to_unique_subsection(
    original: AmendmentOp | ResolvedOp,
    *,
    master_subsecs: List[IRNode],
    source_pathologies_out: Optional[List[SourcePathology]],
    strict_profile: Optional[StrictProfile],
    amend_sub_ir: Optional[IRNode],
) -> AmendmentOp | ResolvedOp:
    if isinstance(original, ResolvedOp):
        target_paragraph = original.effective_target_paragraph
        target_item = original.effective_target_item_label
        target_section = original.resolved_target_section_label or ""
        source_statute = original.resolved_source_statute
        action = original.resolved_action_type
        witness_rule_id = original.witness_rule_id
    else:
        target_paragraph = original.target_paragraph
        target_item = original.target_item
        target_section = original.target_section or ""
        source_statute = original.source_statute or ""
        action = original.op_type
        witness_rule_id = original.witness_rule_id
    if action != "REPEAL":
        return original
    if witness_rule_id != "fi.repeal_vts_voimaantulo":
        return original
    if target_paragraph is not None or not target_item:
        return original
    rebound_label = _unique_subsection_label_for_item_target(master_subsecs, target_item)
    if rebound_label is None or not rebound_label.isdigit():
        return original
    if source_pathologies_out is not None:
        source_pathologies_out.append(
            build_subsection_target_rebound_pathology(
                source_statute=source_statute,
                target_section=target_section,
                target_paragraph=int(rebound_label),
                rebound_kind=RecoveryKind.UNIQUE_ITEM_LABEL_SUBSECTION_FALLBACK,
                stale_fragment_idx=-1,
                live_has_paragraphs=True,
                amend_has_paragraphs=bool(
                    amend_sub_ir is not None
                    and any(child.kind == IRNodeKind.PARAGRAPH for child in amend_sub_ir.children)
                ),
            )
        )
    if strict_profile is not None:
        return original
    if isinstance(original, ResolvedOp):
        rebound = _rebind_resolved_target_address(
            original,
            target_paragraph=int(rebound_label),
            target_item=target_item,
            target_special=original.effective_target_special,
        )
        return dc_replace(
            rebound,
            target_guessing_provenance_tags=tuple(
                dict.fromkeys(
                    (
                        *rebound.target_guessing_provenance_tags,
                        "unique_item_label_subsection_fallback",
                    )
                )
            ),
        )
    return dc_replace(
        original,
        target_paragraph=int(rebound_label),
        target_item=target_item,
        target_guessing_provenance_tags=tuple(
            dict.fromkeys(
                (
                    *original.target_guessing_provenance_tags,
                    "unique_item_label_subsection_fallback",
                )
            )
        ),
    )


def _normalize_subsection_dispatch_inputs(
    *,
    dispatch_op: AmendmentOp | ResolvedOp,
    rop: ResolvedOp | None,
    master_subsecs: List[IRNode],
    amend_sub_ir: Optional[IRNode],
    ctx_label: str,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    strict_profile: Optional[StrictProfile] = None,
) -> tuple[AmendmentOp | ResolvedOp, ResolvedOp | None]:
    normalized = _normalize_subsection_target_hint_ir(
        rop if rop is not None else dispatch_op,
        master_subsecs,
        amend_sub_ir,
        ctx_label,
    )
    normalized = _rebound_item_only_target_to_unique_subsection(
        normalized,
        master_subsecs=master_subsecs,
        source_pathologies_out=source_pathologies_out,
        strict_profile=strict_profile,
        amend_sub_ir=amend_sub_ir,
    )
    if rop is not None:
        original_target_paragraph = rop.effective_target_paragraph
        original_target_item = rop.effective_target_item_label
    elif isinstance(dispatch_op, ResolvedOp):
        original_target_paragraph = dispatch_op.effective_target_paragraph
        original_target_item = dispatch_op.effective_target_item_label
    else:
        original_target_paragraph = dispatch_op.target_paragraph
        original_target_item = dispatch_op.target_item
    if (
        source_pathologies_out is not None
        and original_target_paragraph is not None
        and not original_target_item
        and len(master_subsecs) == 1
        and original_target_paragraph > len(master_subsecs)
    ):
        normalized_target_paragraph = (
            normalized.effective_target_paragraph if isinstance(normalized, ResolvedOp) else normalized.target_paragraph
        )
        normalized_target_item = (
            normalized.effective_target_item_label if isinstance(normalized, ResolvedOp) else normalized.target_item
        )
        if normalized_target_paragraph == 1 and normalized_target_item == str(original_target_paragraph):
            if rop is not None:
                rebound_source_statute = rop.resolved_source_statute
                rebound_target_section = rop.resolved_target_section_label or ""
            elif isinstance(dispatch_op, ResolvedOp):
                rebound_source_statute = dispatch_op.resolved_source_statute
                rebound_target_section = dispatch_op.resolved_target_section_label or ""
            else:
                rebound_source_statute = dispatch_op.source_statute or ""
                rebound_target_section = dispatch_op.target_section or ""
            source_pathologies_out.append(
                build_subsection_target_rebound_pathology(
                    source_statute=rebound_source_statute,
                    target_section=rebound_target_section,
                    target_paragraph=original_target_paragraph,
                    rebound_kind=RecoveryKind.SINGLE_SUBSECTION_ITEM_FALLBACK,
                    stale_fragment_idx=-1,
                    live_has_paragraphs=any(
                        any(child.kind == IRNodeKind.PARAGRAPH for child in sub.children) for sub in master_subsecs
                    ),
                    amend_has_paragraphs=bool(
                        amend_sub_ir is not None and any(child.kind == IRNodeKind.PARAGRAPH for child in amend_sub_ir.children)
                    ),
                )
            )
            if strict_profile is not None:
                original = rop if rop is not None else dispatch_op
                return original, rop
    if isinstance(normalized, ResolvedOp):
        return normalized, normalized
    return normalized, None


def _maybe_update_section_heading(
    result: "ReplayState",
    sec_path: Path,
    dispatch_op: AmendmentOp | ResolvedOp,
    muutos_ir: Optional[IRNode],
    cross_ir: Optional[IRNode] = None,
) -> "ReplayState":
    """If the amendment IR carries a heading that differs from the current
    section heading, update the section heading in *result* as a side-effect
    of subsection dispatch.

    Whole-section amendments that are dispatched through the subsection path
    (because the PEG over-scoped to a subsection target) still carry the new
    heading in their muutos_ir — but the subsection handlers only touch
    subsection/item children, leaving the heading stale.
    """
    def _heading_from(node: Optional[IRNode]) -> Optional[IRNode]:
        if node is None:
            return None
        if node.kind == IRNodeKind.HEADING and node.text:
            return node
        if node.kind == IRNodeKind.CROSS_HEADING and node.text:
            return IRNode(kind=IRNodeKind.HEADING, text=node.text, attrs=node.attrs)
        return next((c for c in node.children if c.kind == IRNodeKind.HEADING), None)

    amend_heading = _heading_from(muutos_ir)
    if amend_heading is None:
        amend_heading = _heading_from(cross_ir)
    if amend_heading is None:
        return result

    if isinstance(dispatch_op, ResolvedOp):
        op_type = dispatch_op.op.op_type
        target_paragraph = dispatch_op.effective_target_paragraph
        target_item = dispatch_op.effective_target_item_label
        target_special = dispatch_op.effective_target_special
    else:
        op_type = dispatch_op.op_type
        target_paragraph = dispatch_op.target_paragraph
        target_item = dispatch_op.target_item
        target_special = dispatch_op.target_special

    if target_special != "otsikko" and op_type != "REPLACE":
        return result

    # Sparse subsection/item payloads often carry a whole-section shell with the
    # live heading plus an omission wrapper around the real targeted child. That
    # shell is not ownership for rewriting the section heading when the op itself
    # only targets a descendant. A non-omission section shell is still allowed to
    # update the heading: whole-section amendments can be routed through the
    # subsection path when the parser over-scopes the target.
    if (
        target_paragraph is not None
        or target_item is not None
        or target_special is not None
    ) and (
        muutos_ir is not None
        and muutos_ir.kind == IRNodeKind.SECTION
        and any(child.kind == IRNodeKind.OMISSION for child in muutos_ir.children)
    ):
        return result

    sec = _tops.resolve(result.ir, sec_path)
    if sec is None:
        return result

    current_heading = next(
        (c for c in sec.children if c.kind == IRNodeKind.HEADING), None
    )
    if current_heading is not None and current_heading.text == amend_heading.text:
        return result

    # Build new children list: replace existing heading, or prepend new one.
    new_children: list[IRNode] = []
    heading_placed = False
    for child in sec.children:
        if child.kind == IRNodeKind.HEADING:
            new_children.append(amend_heading)
            heading_placed = True
        else:
            new_children.append(child)
    if not heading_placed:
        new_children.insert(0, amend_heading)

    new_sec = IRNode(
        kind=sec.kind,
        label=sec.label,
        text=sec.text,
        attrs=sec.attrs,
        children=tuple(new_children),
    )
    logger.debug(
        "  subsection dispatch side-effect: updated section heading %r → %r",
        current_heading.text if current_heading else "(none)",
        amend_heading.text,
    )
    return _with_preserved_provision_index(result, _tops.replace_at(result.ir, sec_path, new_sec))


def _apply_deterministic_subsection_op(
    state: "ReplayState",
    dispatch_op: AmendmentOp | ResolvedOp,
    sec_path: Path,
    muutos_ir: Optional[IRNode],
    amend_sub_ir: Optional[IRNode],
    slot_assignment: "SubsectionSlotAssignmentResult | None",
    profile: ReplayProfile,
    ctx_label: str,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    strict_profile: Optional[StrictProfile] = None,
    *,
    cross_ir: Optional[IRNode] = None,
    rop: ResolvedOp | None = None,
    replay_history_ops: Optional[List[_LegalOperation]] = None,
    base_ir: Optional[IRNode] = None,
    migration_ledger=None,
) -> Optional["ReplayState"]:
    """Apply subsection/item operation via tree_ops."""
    if rop is not None:
        rop = _follow_same_wave_subsection_migration(
            rop,
            migration_ledger=migration_ledger,
        )
        if isinstance(dispatch_op, ResolvedOp):
            dispatch_op = rop
    routing = _prepare_subsection_routing(
        dispatch_op=dispatch_op,
        rop=rop,
        amend_sub_ir=amend_sub_ir,
        muutos_ir=muutos_ir,
        slot_assignment=slot_assignment,
    )
    dispatch_shell = routing.dispatch_op
    subsection_view = routing.subsection_view
    item_view = routing.item_view
    _target_item = routing.target_item
    _target_section = routing.target_section
    _target_chapter = routing.target_chapter
    _target_part = routing.target_part

    # lawvm-regex: owning_parser N-M range parse on the already-resolved routing target (_target_item), not source text
    if _target_item and (range_m := _ITEM_RANGE_TARGET_RE.match(_target_item)):
        start, end = int(range_m.group(1)), int(range_m.group(2))
        if start < end:
            any_handled = False
            for item_num in range(start, end + 1):
                single_dispatch_op, single_rop = _range_item_routing(
                    dispatch_op=dispatch_shell,
                    rop=rop,
                    item_num=item_num,
                )
                sp = state.find_section_path(_target_section, _target_chapter, _target_part)
                if sp is None:
                    continue
                new_state = _apply_deterministic_subsection_op(
                    state,
                    single_dispatch_op,
                    sp,
                    muutos_ir,
                    amend_sub_ir,
                    slot_assignment,
                    profile,
                    ctx_label,
                    source_pathologies_out,
                    strict_profile=strict_profile,
                    rop=single_rop,
                    replay_history_ops=replay_history_ops,
                    base_ir=base_ir,
                    migration_ledger=migration_ledger,
                )
                if new_state is not None:
                    state = new_state
                    any_handled = True
            return state if any_handled else None

    sec = _tops.resolve(state.ir, sec_path)
    assert sec is not None, f"resolve failed for {sec_path}"
    rebase_sec = None
    if subsection_view.op_type != "REPEAL" and subsection_view.target_special != "otsikko":
        rebase_sec = _expired_temporary_section_merge_base(
            op=rop or dispatch_shell,
            section_path=sec_path,
            replay_history_ops=replay_history_ops,
            base_ir=base_ir,
            current_live_section=sec,
        )
    if rebase_sec is not None:
        logger.debug("  %s → subsection dispatch rebased to non-temporary section snapshot", ctx_label)
        rebase_kind, latest_snapshot_expires = _expired_temporary_section_merge_base_rebase_info(
            op=rop or dispatch_shell,
            section_path=sec_path,
            replay_history_ops=replay_history_ops,
            current_live_section=sec,
        )
        if rebase_kind is not None and source_pathologies_out is not None:
            if rop is not None:
                rebound_source_statute = rop.resolved_source_statute
            elif isinstance(dispatch_shell, ResolvedOp):
                rebound_source_statute = dispatch_shell.resolved_source_statute
            else:
                rebound_source_statute = dispatch_shell.source_statute or ""
            source_pathologies_out.append(
                build_temporary_section_rebase_pathology(
                    source_statute=rebound_source_statute,
                    target_section=_target_section,
                    target_chapter=_target_chapter or "",
                    rebase_context="subsection_dispatch",
                    rebase_kind=rebase_kind,
                    latest_snapshot_expires=latest_snapshot_expires or "",
                )
            )
        sec = rebase_sec
    subsecs = [c for c in sec.children if c.kind == IRNodeKind.SUBSECTION]

    amend_sub = routing.amend_sub_ir
    if item_view.target_item is None and item_view.target_special in {"otsikko", "johd"}:
        result = _apply_special_targets(
            state,
            item_view,
            sec_path,
            sec,
            subsecs,
            amend_sub,
            muutos_ir,
            ctx_label,
            source_pathologies_out=source_pathologies_out,
            strict_profile=strict_profile,
        )
        if result is not None:
            return _maybe_update_section_heading(result, sec_path, dispatch_op, muutos_ir, cross_ir)
    result = _apply_subsection_repeal(
        state,
        subsection_view,
        list(sec_path),
        sec,
        subsecs,
        profile,
        ctx_label,
        source_pathologies_out,
        strict_profile=strict_profile,
    )
    if result is not None:
        return result
    result = _apply_item_repeal(
        state,
        item_view,
        sec_path,
        sec,
        subsecs,
        profile,
        ctx_label,
        source_pathologies_out,
        strict_profile=strict_profile,
    )
    if result is not None:
        return result
    result = _apply_item_replace(
        state,
        item_view,
        sec_path,
        sec,
        subsecs,
        amend_sub,
        muutos_ir,
        ctx_label,
        source_pathologies_out,
        strict_profile=strict_profile,
    )
    if result is not None:
        return _maybe_update_section_heading(result, sec_path, dispatch_op, muutos_ir, cross_ir)
    result = _apply_subsection_replace(
        state,
        subsection_view,
        list(sec_path),
        sec,
        subsecs,
        amend_sub,
        muutos_ir,
        profile,
        ctx_label,
        source_pathologies_out,
        strict_profile=strict_profile,
    )
    if result is not None:
        return _maybe_update_section_heading(result, sec_path, dispatch_op, muutos_ir, cross_ir)
    result = _apply_subsection_insert(
        state,
        subsection_view,
        list(sec_path),
        sec,
        subsecs,
        amend_sub,
        ctx_label,
        source_pathologies_out,
        strict_profile=strict_profile,
        allow_expired_temporary_duplicate_label_replace=(
            subsection_view.op_type == "INSERT"
            and subsection_view.target_paragraph is not None
            and not subsection_view.target_item
            and _expired_temporary_subsection_slot_can_be_consumed(
                op=rop or dispatch_shell,
                section_path=sec_path,
                subsection_label=str(subsection_view.target_paragraph),
                replay_history_ops=replay_history_ops,
            )
        ),
    )
    if result is not None:
        return _maybe_update_section_heading(result, sec_path, dispatch_op, muutos_ir, cross_ir)
    result = _apply_item_insert(
        state,
        item_view,
        sec_path,
        sec,
        subsecs,
        amend_sub,
        muutos_ir,
        ctx_label,
        source_pathologies_out,
        strict_profile=strict_profile,
    )
    if result is not None:
        return _maybe_update_section_heading(result, sec_path, dispatch_op, muutos_ir, cross_ir)
    result = _apply_special_targets(
        state,
        item_view,
        sec_path,
        sec,
        subsecs,
        amend_sub,
        muutos_ir,
        ctx_label,
        source_pathologies_out=source_pathologies_out,
        strict_profile=strict_profile,
    )
    if result is not None:
        return _maybe_update_section_heading(result, sec_path, dispatch_op, muutos_ir, cross_ir)
    return None
