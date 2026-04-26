"""Container and whole-section execution helpers for Finland replay/apply."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
import logging
import re
from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING, FrozenSet, List, Optional, cast

from lawvm.core.compile_result import SourcePathology
from lawvm.core.elaboration_context import TargetUnitKind
from lawvm.core.ir import IRNode
from lawvm.core.ir import LegalAddress
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import Path

from lawvm.finland.ops import (
    AmendmentOp,
    ReplayProfile,
    ResolvedOp,
    _lo_with_path_update,
    _rebind_resolved_target_address,
    runtime_scope_confidence_for_op,
)
from lawvm.finland.helpers import _norm_num_token, _roman_label_to_arabic
from lawvm.finland.replay_notices import replay_print
from lawvm.finland.source_pathology import (
    build_container_replace_target_absent_pathology,
    build_destructive_shape_loss_risk_pathology,
    build_partial_whole_section_payload_pathology,
    build_sparse_merge_invariant_skip_pathology,
    build_temporary_section_rebase_pathology,
    build_unique_payload_insert_under_live_duplicates_pathology,
)
from lawvm.finland.apply_ir_ops import (
    _build_repeal_placeholder_ir,
    _build_repeal_placeholder_from_label_ir,
    _relabel_section_ir,
)
from lawvm.finland.apply_runtime_support import (
    _expired_temporary_section_merge_base,
    _expired_temporary_section_merge_base_rebase_info,
    _find_insert_parent_path,
    _find_chapter_insert_parent_path,
    _legacy_target_section_for_scope,
    _legacy_target_special_for_scope,
    _parent_direct_child_path_with_same_label,
    _same_norm_label,
    _with_preserved_provision_index,
)

if TYPE_CHECKING:
    from lawvm.finland.payload_normalize import PayloadCompletenessWitness
    from lawvm.finland.statute import ReplayState
from lawvm.finland.merge import (
    _has_section_omissions_ir,
    _heading_intro_replace_preserve_items_ir,
    _mixed_sparse_intro_replace_preserve_first_subsection_items_ir,
    _merge_same_numbered_container_insert_ir,
    _merge_section_with_omission_ir,
    _multi_subsection_sparse_item_section_replace_merge_ir,
    _sparse_item_section_replace_merge_ir,
    _is_suspicious_partial_section_replace_ir,
)

logger = logging.getLogger(__name__)

def _absorb_trailing_wrapper_sections_into_letter_suffix_chapter(
    state,
    *,
    chapter_path: Path,
    merged_chapter: IRNode,
) -> tuple[IRNode, IRNode, tuple[tuple[tuple[str, str], ...], ...]]:
    """Move loose same-parent sections into a newly realized letter chapter.

    Historical Finland replay can carry sections that semantically belong to a
    pseudo-marker subchapter as direct siblings of that chapter shell inside the
    surrounding wrapper container. When a later amendment introduces the real
    letter-suffix chapter and inserts a section into it, absorb the contiguous
    loose sections that still trail that chapter before the next chapter/part.
    """
    chapter_label = str(merged_chapter.label or "")
    if re.fullmatch(r".*[a-z]+", chapter_label, re.I) is None:
        return state.ir, merged_chapter, ()

    actual_chapter_path = _tops.find_family(state.ir, "chapter", chapter_label)
    if actual_chapter_path is not None:
        chapter_path = actual_chapter_path

    parent_path = tuple(chapter_path[:-1])
    parent_node = _tops.resolve(state.ir, parent_path) if parent_path else state.ir
    if parent_node is None:
        return state.ir, merged_chapter, ()

    chapter_index = None
    for idx, child in enumerate(parent_node.children):
        if child.kind is IRNodeKind.CHAPTER and _same_norm_label(child.label or "", chapter_label):
            chapter_index = idx
            break
    if chapter_index is None:
        return state.ir, merged_chapter, ()

    adopted_sections: list[IRNode] = []
    adopted_paths: list[tuple[tuple[str, str], ...]] = []
    scan_index = chapter_index + 1
    while scan_index < len(parent_node.children):
        candidate = parent_node.children[scan_index]
        if candidate.kind in (IRNodeKind.CHAPTER, IRNodeKind.PART):
            break
        if candidate.kind is not IRNodeKind.SECTION or not candidate.label:
            break
        adopted_sections.append(candidate)
        adopted_paths.append(parent_path + (("section", candidate.label),))
        scan_index += 1

    if not adopted_sections:
        terminal_sections: list[IRNode] = []
        terminal_paths: list[tuple[tuple[str, str], ...]] = []
        for reverse_index in range(len(parent_node.children) - 1, -1, -1):
            candidate = parent_node.children[reverse_index]
            if candidate.kind is not IRNodeKind.SECTION or not candidate.label:
                break
            terminal_sections.append(candidate)
            terminal_paths.append(parent_path + (("section", candidate.label),))
        if terminal_sections:
            adopted_sections = list(reversed(terminal_sections))
            adopted_paths = list(reversed(terminal_paths))
        else:
            return state.ir, merged_chapter, ()

    absorbed_chapter = IRNode(
        kind=merged_chapter.kind,
        label=merged_chapter.label,
        text=merged_chapter.text,
        attrs=dict(merged_chapter.attrs),
        children=tuple(list(merged_chapter.children) + adopted_sections),
    )
    absorbed_chapter = _tops.resort_children(absorbed_chapter)

    new_parent_children = list(parent_node.children)
    new_parent_children[chapter_index] = absorbed_chapter
    if parent_node.children[chapter_index + 1 : chapter_index + 1 + len(adopted_sections)] == tuple(adopted_sections):
        del new_parent_children[chapter_index + 1 : chapter_index + 1 + len(adopted_sections)]
    else:
        adopted_keys = {(child.kind, child.label) for child in adopted_sections}
        new_parent_children = [
            child
            for idx, child in enumerate(new_parent_children)
            if idx == chapter_index or (child.kind, child.label) not in adopted_keys
        ]
    new_parent = IRNode(
        kind=parent_node.kind,
        label=parent_node.label,
        text=parent_node.text,
        attrs=dict(parent_node.attrs),
        children=tuple(new_parent_children),
    )
    if parent_path:
        return _tops.replace_at(state.ir, parent_path, new_parent), absorbed_chapter, tuple(adopted_paths)
    return new_parent, absorbed_chapter, tuple(adopted_paths)


def _apply_section_tail_policy_marker(
    node: IRNode,
    *,
    rop: ResolvedOp | None,
    view: _StructureApplyView | None = None,
) -> IRNode:
    """Stamp section-root replace coverage onto replay content for PIT masking."""
    if rop is not None:
        payload_completeness = rop.payload_completeness
    elif view is not None:
        payload_completeness = view.payload_completeness
    else:
        payload_completeness = None
    if node.kind is not IRNodeKind.SECTION or payload_completeness is None:
        return node
    tail_policy = str(payload_completeness.tail_policy or "").strip()
    if not tail_policy:
        return node
    attrs = dict(node.attrs)
    if attrs.get("lawvm_tail_policy") == tail_policy:
        return node
    attrs["lawvm_tail_policy"] = tail_policy
    attrs["lawvm_payload_completeness_kind"] = str(payload_completeness.kind or "")
    return IRNode(
        kind=node.kind,
        label=node.label,
        text=node.text,
        attrs=attrs,
        children=node.children,
    )


def _preserve_unstated_live_subsection_tail(
    candidate: IRNode,
    *,
    live_sec: IRNode,
    rop: ResolvedOp | None,
    view: _StructureApplyView | None = None,
) -> IRNode:
    """Preserve live trailing subsection tail for fragmentary whole-section shells.

    Some Finland section-root payloads carry only the changed leading subsection
    while same-group scoped ops describe later descendant changes separately.
    When the payload completeness witness says to preserve the unstated tail,
    keep any unmatched trailing live subsections in place instead of collapsing
    the section down to the sparse shell.
    """
    if rop is not None:
        payload_completeness = rop.payload_completeness
    elif view is not None:
        payload_completeness = view.payload_completeness
    else:
        payload_completeness = None
    if candidate.kind is not IRNodeKind.SECTION or payload_completeness is None:
        return candidate
    if str(payload_completeness.tail_policy or "").strip() != "preserve_unstated_tail":
        return candidate

    live_subsections = [child for child in live_sec.children if child.kind is IRNodeKind.SUBSECTION]
    candidate_subsections = [child for child in candidate.children if child.kind is IRNodeKind.SUBSECTION]
    if not candidate_subsections or len(candidate_subsections) >= len(live_subsections):
        return candidate

    candidate_label_map: dict[str, IRNode] = {}
    for subsection in candidate_subsections:
        norm = _tops._norm(subsection.label or "")
        if not norm or norm in candidate_label_map:
            candidate_label_map = {}
            break
        candidate_label_map[norm] = subsection
    live_label_seq = [_tops._norm(subsection.label or "") for subsection in live_subsections]
    if candidate_label_map and all(live_label_seq):
        merged_subsections: list[IRNode] = []
        used_labels: set[str] = set()
        for live_subsection in live_subsections:
            norm = _tops._norm(live_subsection.label or "")
            replacement = candidate_label_map.get(norm)
            if replacement is not None:
                merged_subsections.append(replacement)
                used_labels.add(norm)
            else:
                merged_subsections.append(live_subsection)
        for subsection in candidate_subsections:
            norm = _tops._norm(subsection.label or "")
            if norm not in used_labels:
                merged_subsections.append(subsection)
        candidate_children = [child for child in candidate.children if child.kind is not IRNodeKind.SUBSECTION]
        candidate_children.extend(merged_subsections)
        return IRNode(
            kind=candidate.kind,
            label=candidate.label,
            text=candidate.text,
            attrs=candidate.attrs,
            children=tuple(candidate_children),
        )

    candidate_children = [child for child in candidate.children if child.kind is not IRNodeKind.SUBSECTION]
    candidate_children.extend(candidate_subsections)
    candidate_children.extend(live_subsections[len(candidate_subsections):])
    return IRNode(
        kind=candidate.kind,
        label=candidate.label,
        text=candidate.text,
        attrs=candidate.attrs,
        children=tuple(candidate_children),
    )


def _align_section_payload_subsection_labels_from_slot_assignment(
    payload: IRNode,
    *,
    rop: ResolvedOp | None,
    view: _StructureApplyView | None = None,
) -> IRNode:
    """Rewrite fragmentary section-shell subsection labels from legal slot targets."""
    if rop is not None:
        payload_completeness = rop.payload_completeness
    elif view is not None:
        payload_completeness = view.payload_completeness
    else:
        payload_completeness = None
    slot_assignment = rop.slot_assignment if rop is not None else None
    if (
        payload.kind is not IRNodeKind.SECTION
        or payload_completeness is None
        or str(payload_completeness.tail_policy or "").strip() != "preserve_unstated_tail"
        or slot_assignment is None
    ):
        return payload

    slot_target_labels: dict[str, str] = {}
    for binding in slot_assignment.sparse_slot_bindings:
        if binding.target_paragraph is None or binding.target_item or binding.target_special:
            continue
        slot_label = _tops._norm(str(binding.payload_slot_label or ""))
        if not slot_label:
            continue
        target_label = str(binding.target_paragraph)
        existing = slot_target_labels.get(slot_label)
        if existing is None:
            slot_target_labels[slot_label] = target_label
        elif existing != target_label:
            return payload

    if not slot_target_labels:
        return payload

    changed = False
    new_children: list[IRNode] = []
    for child in payload.children:
        if child.kind is IRNodeKind.SUBSECTION:
            slot_label = _tops._norm(child.label or "")
            target_label = slot_target_labels.get(slot_label)
            if target_label and target_label != (child.label or ""):
                child = IRNode(
                    kind=child.kind,
                    label=target_label,
                    text=child.text,
                    attrs=child.attrs,
                    children=child.children,
                )
                changed = True
        new_children.append(child)

    if not changed:
        return payload
    return IRNode(
        kind=payload.kind,
        label=payload.label,
        text=payload.text,
        attrs=payload.attrs,
        children=tuple(new_children),
    )


def _preserve_live_container_on_merge_duplicate(
    *,
    source_pathologies_out: list[SourcePathology] | None,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_label: str,
    recovery_kind: str,
    live_node: IRNode,
    payload_node: IRNode,
) -> IRNode:
    if source_pathologies_out is not None:
        source_pathologies_out.append(
            build_sparse_merge_invariant_skip_pathology(
                source_statute=source_statute,
                target_unit_kind=target_unit_kind,
                target_label=target_label,
                recovery_kind=recovery_kind,
                live_sibling_count=len([c for c in live_node.children if c.kind is IRNodeKind.SECTION]),
                payload_sibling_count=len([c for c in payload_node.children if c.kind is IRNodeKind.SECTION]),
            )
        )
    return live_node


def _merge_unique_payload_sections_after_live_duplicate_skip(
    live_node: IRNode,
    payload_node: IRNode,
) -> IRNode | None:
    """Admit unique payload sections even when the live container has duplicates."""
    live_labels = {
        child.label
        for child in live_node.children
        if child.kind is IRNodeKind.SECTION and child.label
    }
    payload_sections = [
        child
        for child in payload_node.children
        if child.kind is IRNodeKind.SECTION and child.label and child.label not in live_labels
    ]
    if not payload_sections:
        return None
    return _tops.resort_children(
        _tops._with_children(live_node, (*live_node.children, *payload_sections))
    )


def _prepare_section_root_payload_for_replay(
    payload: IRNode,
    *,
    live_sec: IRNode,
    rop: ResolvedOp | None,
    view: _StructureApplyView | None = None,
) -> IRNode:
    """Prepare a whole-section payload before it replaces an occupied live section."""
    payload = _align_section_payload_subsection_labels_from_slot_assignment(
        payload,
        rop=rop,
        view=view,
    )
    has_heading = any(c.kind == IRNodeKind.HEADING for c in payload.children)
    if has_heading:
        prepared = payload
    else:
        live_heading = next((c for c in live_sec.children if c.kind == IRNodeKind.HEADING), None)
        if live_heading is None:
            prepared = payload
        else:
            new_children = list(payload.children)
            insert_at = 1 if new_children and new_children[0].kind == IRNodeKind.NUM else 0
            new_children.insert(insert_at, live_heading)
            prepared = IRNode(
                kind=payload.kind,
                label=payload.label,
                text=payload.text,
                attrs=payload.attrs,
                children=tuple(new_children),
            )
    prepared = _preserve_unstated_live_subsection_tail(
        prepared,
        live_sec=live_sec,
        rop=rop,
        view=view,
    )
    return _apply_section_tail_policy_marker(prepared, rop=rop, view=view)


def _create_part_and_move_siblings(
    ir: IRNode,
    part_label: str,
    sibling_chapter_labels: tuple[str, ...],
    *,
    migration_ledger=None,
    effective: str = "",
    source_statute: str = "",
) -> IRNode:
    """Create a new PART, move the named sibling chapters into it, return updated IR.

    Used when a chapter INSERT carries a ``lawvm_amendment_part_hint`` for a part
    that does not yet exist in the statute.  The new part is inserted immediately
    before the part currently containing the first (sorted) sibling chapter.
    """
    provisions_parent_path = _tops.find_provisions_parent(ir) or ()

    # Locate sibling chapters in the current IR
    chapters_to_move: list[tuple[Path, IRNode]] = []
    for ch_label in sibling_chapter_labels:
        ch_path = _tops.find(ir, "chapter", ch_label)
        if ch_path is not None:
            ch_node = _tops.resolve(ir, ch_path)
            if ch_node is not None:
                chapters_to_move.append((ch_path, ch_node))

    # Determine insertion point: before the part that contains the first sibling
    insert_before_part_label: str | None = None
    if chapters_to_move:
        chapters_to_move.sort(key=lambda x: _tops._default_sort_key(x[1].label))
        for ch_path, _ in chapters_to_move:
            for step_kind, step_label in ch_path:
                if step_kind == "part":
                    insert_before_part_label = step_label
                    break
            if insert_before_part_label is not None:
                break

    # Remove sibling chapters from their current positions
    for ch_path, _ in reversed(chapters_to_move):
        ir = _tops.remove_at(ir, ch_path)

    # Build the new part with the moved chapters (already sorted above)
    new_part = IRNode(
        kind=IRNodeKind.PART,
        label=part_label,
        children=tuple(ch_node for _, ch_node in chapters_to_move),
    )

    # Insert the new part at the determined position
    if insert_before_part_label is not None:
        parent_node = _tops.resolve(ir, provisions_parent_path) if provisions_parent_path else ir
        if parent_node is None:
            parent_node = ir
        new_children: list[IRNode] = []
        inserted = False
        for child in parent_node.children:
            if not inserted and child.kind is IRNodeKind.PART and child.label == insert_before_part_label:
                new_children.append(new_part)
                inserted = True
            new_children.append(child)
        if not inserted:
            new_children.append(new_part)
        new_parent = _tops._with_children(parent_node, new_children)
        if provisions_parent_path:
            ir = _tops.replace_at(ir, provisions_parent_path, new_parent)
        else:
            ir = new_parent
    else:
        ir = _tops.insert_sorted(ir, provisions_parent_path, new_part)

    if migration_ledger is not None:
        new_part_path = provisions_parent_path + (("part", part_label),)
        for ch_path, ch_node in chapters_to_move:
            chapter_label = ch_node.label or ""
            if not chapter_label:
                continue
            from_path = tuple(
                step for step in ch_path if step[0] in {"part", "chapter"}
            )
            to_path = new_part_path + (("chapter", chapter_label),)
            migration_ledger.record_move(
                LegalAddress(path=from_path),
                LegalAddress(path=to_path),
                effective=effective,
                source_statute=source_statute,
            )

    return ir


def _insert_or_replace_same_labeled_child(
    tree: IRNode,
    parent_path: Path,
    child: IRNode,
) -> tuple[IRNode, bool]:
    """Insert a child, or replace a same-labeled direct child if one already exists."""
    same_path = _parent_direct_child_path_with_same_label(
        tree,
        parent_path,
        kind=child.kind,
        label=child.label or "",
    )
    if same_path is not None:
        return _tops.replace_at(tree, same_path, child), True
    return _tops.insert_sorted(tree, parent_path, child), False


def _move_section_payload_to_target_chapter(
    tree: IRNode,
    existing_path: Path,
    target_chapter: str,
    section_ir: IRNode,
    *,
    migration_ledger=None,
    effective: str = "",
    source_statute: str = "",
    source_pathologies_out: Optional[List[SourcePathology]] = None,
) -> IRNode:
    """Move a unique section payload into the target chapter and replace it there."""
    moved_ir = _tops.remove_at(tree, existing_path)
    parent_path = _tops._as_path(_find_insert_parent_path(moved_ir, target_chapter))
    moved_ir, replaced = _insert_or_replace_same_labeled_child(moved_ir, parent_path, section_ir)
    if replaced and source_pathologies_out is not None:
        parent_node = _tops.resolve(moved_ir, parent_path)
        source_pathologies_out.append(
            build_destructive_shape_loss_risk_pathology(
                source_statute=source_statute,
                target_unit_kind="section",
                target_label=f"{target_chapter} luku {section_ir.label or ''} §",
                recovery_kind="section_move_destination_same_label_replace",
                live_sibling_count=len(
                    [c for c in (parent_node.children if parent_node is not None else ()) if c.kind is IRNodeKind.SECTION]
                ),
                payload_sibling_count=1,
            )
        )
    if migration_ledger is not None:
        migration_ledger.record_move(
            LegalAddress(path=existing_path),
            LegalAddress(path=parent_path + (("section", section_ir.label or ""),)),
            effective=effective,
            source_statute=source_statute,
    )
    return moved_ir


def _path_matches_target_part(path: Path, target_part: str | None) -> bool:
    """Require exact part scope when a section fallback is part-scoped."""
    if not target_part:
        return True
    parts = [label for kind, label in path if kind == "part" and label]
    if not parts:
        return False
    return _norm_num_token(parts[-1]) == _norm_num_token(target_part)


def _find_scoped_section_insert_parent_path(
    state: "ReplayState",
    *,
    target_chapter: str | None,
    target_part: str | None,
) -> Path | None:
    """Resolve a section insert parent without dropping explicit part scope."""
    if target_part:
        part_path = _find_direct_body_part_path(state.ir, target_part) or state.find("part", target_part)
        if part_path is None:
            return None
        if target_chapter:
            part_node = _tops.resolve(state.ir, part_path)
            if part_node is None:
                return None
            chapter_path = _tops.find(part_node, "chapter", target_chapter)
            if chapter_path is None:
                return None
            return _tops._as_path(part_path) + _tops._as_path(chapter_path)
        return _tops._as_path(part_path)
    return _tops._as_path(
        _find_insert_parent_path(
            state.ir,
            target_chapter,
            label_index=state.provision_index,
        )
    )


def _find_direct_body_part_path(ir: IRNode, target_part: str | None) -> Path | None:
    if not target_part:
        return None
    return _parent_direct_child_path_with_same_label(
        ir,
        (),
        kind=IRNodeKind.PART,
        label=target_part,
    )


@dataclass(frozen=True)
class _StructureApplyView:
    target_unit_kind: TargetUnitKind
    target_section: str
    op_type: str
    uncovered_body_recovery: bool
    target_paragraph: int | None
    target_item: str | None
    target_special: str | None
    target_chapter: str | None
    target_part: str | None
    source_statute: str | None
    source_issue_date: dt.date | None
    source_title: str
    target_address: LegalAddress | None = None
    source_effective: str = ""
    payload_completeness: "PayloadCompletenessWitness | None" = None


def _coerce_structure_apply_view(op: "_StructureApplyView | AmendmentOp | ResolvedOp") -> _StructureApplyView:
    if isinstance(op, _StructureApplyView):
        return op
    return _structure_apply_view_for_op(op)


def _structure_apply_view_for_op(op: AmendmentOp | ResolvedOp) -> _StructureApplyView:
    if isinstance(op, ResolvedOp):
        scope = op.resolved_target_scope_view
        source_statute = op.resolved_source_statute
        source_issue_date = op.resolved_source_issue_date
        source_title = op.resolved_source_title
        op_type = op.resolved_action_type
        op_lo = getattr(op, "lo", None)
        target_address = op.resolved_target_address or (op_lo.target if op_lo is not None else None)
        source_effective = op_lo.source.effective if op_lo is not None and op_lo.source is not None else ""
        target_section = _legacy_target_section_for_scope(scope, op.target_unit_kind)
        target_item = scope.target_item
        target_special = _legacy_target_special_for_scope(scope, op.effective_target_special)
        target_chapter = op.resolved_target_scope_chapter_label
        target_part = op.resolved_target_scope_part_label
    else:
        source_statute = op.source_statute
        source_issue_date = op.source_issue_date
        source_title = op.source_title or ""
        op_type = op.op_type
        target_address = op.lo.target if op.lo is not None else None
        source_effective = op.lo.source.effective if op.lo is not None and op.lo.source is not None else ""
        target_section = op.target_section or ""
        target_item = op.target_item
        target_special = op.target_special
        target_chapter = op.target_chapter
        target_part = op.target_part
    return _StructureApplyView(
        target_unit_kind=op.target_unit_kind,
        target_section=target_section,
        op_type=op_type,
        uncovered_body_recovery=op.uses_uncovered_body_recovery if isinstance(op, ResolvedOp) else op.uncovered_body_recovery,
        target_paragraph=scope.target_paragraph if isinstance(op, ResolvedOp) else op.target_paragraph,
        target_item=target_item,
        target_special=target_special,
        target_chapter=target_chapter,
        target_part=target_part,
        source_statute=source_statute,
        source_issue_date=source_issue_date,
        source_title=source_title,
        target_address=target_address,
        source_effective=source_effective,
        payload_completeness=op.payload_completeness if isinstance(op, ResolvedOp) else None,
    )


def _find_container_path_with_part_scope(
    ir: IRNode,
    *,
    kind: str,
    label: str,
    target_part: str | None,
) -> Path | None:
    """Resolve chapter lookup against an explicit part before falling back globally."""
    if kind == "chapter" and target_part:
        part_lookup = str(target_part)
        arabic = _roman_label_to_arabic(_norm_num_token(part_lookup).lower())
        if arabic is not None:
            part_lookup = str(arabic)
        part_path = _tops.find(ir, "part", part_lookup)
        if part_path is not None:
            direct = _parent_direct_child_path_with_same_label(
                ir,
                _tops._as_path(part_path),
                kind=IRNodeKind.CHAPTER,
                label=label,
            )
            return direct
        return None
    found = _tops.find(ir, kind, label)
    return _tops._as_path(found) if found is not None else None


def _apply_container_op(
    state,
    op: "_StructureApplyView | AmendmentOp | ResolvedOp",
    muutos_ir: Optional[IRNode],
    profile: ReplayProfile,
    ctx_label: str,
    base_ir: Optional[IRNode] = None,
    standalone_section_targets: Optional[FrozenSet] = None,
    mixed_sparse_insert: bool = False,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    migration_ledger=None,
):
    """Apply container (chapter/part) operation via tree_ops."""
    def _normalized_standalone_targets() -> list[tuple[str | None, str | None, str]]:
        normalized: list[tuple[str | None, str | None, str]] = []
        for raw_target in standalone_section_targets or frozenset():
            if not isinstance(raw_target, tuple):
                continue
            if len(raw_target) == 2:
                raw_part = None
                raw_chapter, raw_label = raw_target
            elif len(raw_target) == 3:
                raw_part, raw_chapter, raw_label = raw_target
            else:
                continue
            if raw_label is None:
                continue
            normalized.append(
                (
                    _norm_num_token(str(raw_part)) if raw_part not in (None, "") else None,
                    _norm_num_token(str(raw_chapter)) if raw_chapter not in (None, "") else None,
                    _norm_num_token(str(raw_label)),
                )
            )
        return normalized

    view = _coerce_structure_apply_view(op)
    _target_unit_kind = view.target_unit_kind
    _target_section = view.target_section
    _op_type = view.op_type
    _target_paragraph = view.target_paragraph
    _target_item = view.target_item
    _target_special = view.target_special
    _target_part = view.target_part
    _op_lo = getattr(op, "lo", None)
    _op_source_effective = (
        _op_lo.source.effective
        if _op_lo is not None and _op_lo.source is not None
        else view.source_effective
    )

    if _target_unit_kind == "section":
        return None

    if _target_unit_kind == "chapter":
        kind = "chapter"
    else:
        kind = "part"
    section_label = _target_section
    if kind == "part":
        arabic = _roman_label_to_arabic(section_label.lower())
        if arabic is not None:
            section_label = arabic
    path = _find_container_path_with_part_scope(
        state.ir,
        kind=kind,
        label=section_label,
        target_part=_target_part,
    )

    if path is None and _op_type not in ("INSERT", "REPLACE"):
        replay_print(f"  {ctx_label} → FAILED (master {kind}:{section_label} not found)")
        return state
    if path is None and _op_type == "REPLACE" and muutos_ir is not None:
        if source_pathologies_out is not None:
            source_pathologies_out.append(
                build_container_replace_target_absent_pathology(
                    source_statute=view.source_statute or "",
                    target_unit_kind=_target_unit_kind,
                    target_section=_target_section or "",
                    target_chapter=view.target_chapter or "",
                    target_paragraph=_target_paragraph or "",
                    target_item=_target_item or "",
                    target_special=_target_special or "",
                    has_payload=True,
                )
            )
        replay_print(f"  {ctx_label} → FAILED (master {kind}:{section_label} not found)")
        return state

    if _target_special == "otsikko":
        if path is None:
            return state
        node = _tops.resolve(state.ir, path)
        assert node is not None, f"resolve failed for {path}"
        if _op_type == "REPLACE" and muutos_ir is not None:
            amend_heading = next((c for c in muutos_ir.children if c.kind == IRNodeKind.HEADING), None)
            if amend_heading is not None:
                new_children = [amend_heading if c.kind == IRNodeKind.HEADING else c for c in node.children]
                logger.debug("  %s → container otsikko replace", ctx_label)
                return _with_preserved_provision_index(
                    state,
                    _tops.replace_at(state.ir, path, _tops._with_children(node, new_children)),
                )
            logger.debug("  %s → container otsikko replace (no heading in amendment body — no-op)", ctx_label)
            return state
        if _op_type == "REPEAL":
            new_children = [c for c in node.children if c.kind is not IRNodeKind.HEADING]
            logger.debug("  %s → container otsikko repeal", ctx_label)
            return _with_preserved_provision_index(
                state,
                _tops.replace_at(state.ir, path, _tops._with_children(node, new_children)),
            )
        logger.debug("  %s → container otsikko %s (no-op)", ctx_label, _op_type)
        return state

    if _target_unit_kind in {"chapter", "part"} and _target_paragraph is not None and path is not None:
        node = _tops.resolve(state.ir, path)
        assert node is not None, f"resolve failed for {path}"
        child_label = str(_target_paragraph)
        child_idx = next(
            (i for i, c in enumerate(node.children) if c.kind is IRNodeKind.SECTION and c.label == child_label),
            None,
        )
        if _target_item is None and muutos_ir is not None:
            child_ir = (
                next((c for c in muutos_ir.children if c.kind is IRNodeKind.SECTION and c.label == child_label), None)
                or muutos_ir
            )
            if _op_type == "REPLACE" and child_idx is not None:
                new_children = list(node.children)
                new_children[child_idx] = child_ir
                logger.debug("  %s → container child-section replace", ctx_label)
                new_ir = _tops.replace_at(state.ir, path, _tops._with_children(node, new_children))
                if child_ir.kind is IRNodeKind.SECTION and _same_norm_label(child_ir.label, child_label):
                    return _with_preserved_provision_index(state, new_ir)
                return state.with_ir(new_ir)
            if _op_type == "INSERT":
                new_children = list(node.children)
                new_children.insert((child_idx + 1) if child_idx is not None else len(new_children), child_ir)
                logger.debug("  %s → container child-section insert", ctx_label)
                return state.with_ir(_tops.replace_at(state.ir, path, _tops._with_children(node, new_children)))
        if _op_type == "REPEAL" and child_idx is not None and _target_item is None:
            new_children = [c for i, c in enumerate(node.children) if i != child_idx]
            logger.debug("  %s → container child-section repeal", ctx_label)
            return state.with_ir(_tops.replace_at(state.ir, path, _tops._with_children(node, new_children)))

    if _op_type == "REPLACE" and not _target_paragraph and not _target_item and not _target_special:
        if path is not None and muutos_ir is not None:
            if _target_unit_kind in {"chapter", "part"}:
                normalized_standalone_targets = _normalized_standalone_targets()
                node = _tops.resolve(state.ir, path)
                assert node is not None, f"resolve failed for {path}"
                live_section_labels = [
                    _norm_num_token(child.label)
                    for child in node.children
                    if child.kind is IRNodeKind.SECTION and child.label
                ]
                payload_section_labels = [
                    _norm_num_token(child.label)
                    for child in muutos_ir.children
                    if child.kind is IRNodeKind.SECTION and child.label
                ]
                payload_has_heading = any(child.kind is IRNodeKind.HEADING for child in muutos_ir.children)
                if (
                    _target_unit_kind == "chapter"
                    and _same_norm_label(node.label, muutos_ir.label)
                    and payload_has_heading
                    and payload_section_labels
                    and len(payload_section_labels) < len(live_section_labels)
                    and set(payload_section_labels).issubset(set(live_section_labels))
                ):
                    merged = _merge_same_numbered_container_insert_ir(node, muutos_ir)
                    if merged is None:
                        merged = _preserve_live_container_on_merge_duplicate(
                            source_pathologies_out=source_pathologies_out,
                            source_statute=view.source_statute or "",
                            target_unit_kind=_target_unit_kind,
                            target_label=f"{_target_section} {kind}",
                            recovery_kind="container_replace_fragmentary_heading_merge_duplicate_labels",
                            live_node=node,
                            payload_node=muutos_ir,
                        )
                    if source_pathologies_out is not None:
                        source_pathologies_out.append(
                            build_destructive_shape_loss_risk_pathology(
                                source_statute=view.source_statute or "",
                                target_unit_kind=_target_unit_kind,
                                target_label=f"{_target_section} {kind}",
                                recovery_kind="container_replace_fragmentary_heading_merge",
                                live_sibling_count=len(live_section_labels),
                                payload_sibling_count=len(payload_section_labels),
                            )
                        )
                    logger.debug("  %s → container replace-as-merge (fragmentary chapter payload)", ctx_label)
                    new_ir = _tops.replace_at(state.ir, path, merged)
                    return _with_preserved_provision_index(state, new_ir)
                live_member_labels = {
                    _norm_num_token(child.label)
                    for child in node.children
                    if child.kind is IRNodeKind.SECTION and child.label
                }
                current_container_label = _norm_num_token(_target_section)
                current_container_part = _norm_num_token(_target_part) if _target_part else None
                filtered_children: list[IRNode] = []
                filtered_shadowed = False
                for child in muutos_ir.children:
                    if child.kind is IRNodeKind.SECTION and child.label:
                        child_label = _norm_num_token(child.label)
                        shadowed_in_same_container = any(
                            standalone_label == child_label
                            and standalone_chapter is not None
                            and standalone_chapter == current_container_label
                            and standalone_part == current_container_part
                            for standalone_part, standalone_chapter, standalone_label in normalized_standalone_targets
                        )
                        if child_label not in live_member_labels and shadowed_in_same_container:
                            filtered_shadowed = True
                            continue
                    filtered_children.append(child)
                if filtered_shadowed:
                    muutos_ir = _tops._with_children(muutos_ir, filtered_children)
            logger.debug("  %s → container replace", ctx_label)
            return state.with_ir(_tops.replace_at(state.ir, path, muutos_ir))

    if _op_type == "REPEAL" and not _target_paragraph and not _target_item:
        if path is not None:
            logger.debug("  %s → container repeal", ctx_label)
            return state.with_ir(_tops.remove_at(state.ir, path))

    if _op_type == "INSERT" and not _target_paragraph and not _target_item and muutos_ir is not None:
        if profile.replace_same_numbered_container_insert and _target_unit_kind == "chapter" and path is not None:
            node = _tops.resolve(state.ir, path)
            assert node is not None, f"resolve failed for {path}"
            # If the chapter was not in the original base law it may exist only as
            # a VÄLIAIKAINEN scaffold whose sections have since expired.  Merging
            # the new amendment body with those expired sections would resurrect
            # content that should be gone.  Use REPLACE instead of MERGE for
            # non-base chapters so the fresh amendment body wins cleanly.
            _base_path = (
                _find_container_path_with_part_scope(
                    base_ir,
                    kind=kind,
                    label=_target_section,
                    target_part=_target_part,
                )
                if base_ir is not None
                else None
            )
            if _base_path is None:
                logger.debug("  %s → container insert replaces non-base scaffold (oracle mode)", ctx_label)
                if source_pathologies_out is not None:
                    source_pathologies_out.append(
                        build_destructive_shape_loss_risk_pathology(
                            source_statute=view.source_statute or "",
                            target_unit_kind=_target_unit_kind,
                            target_label=f"{_target_section} {kind}",
                            recovery_kind="container_insert_non_base_scaffold_consume",
                            live_sibling_count=len([c for c in node.children if c.kind is IRNodeKind.SECTION]),
                            payload_sibling_count=len([c for c in muutos_ir.children if c.kind is IRNodeKind.SECTION]),
                        )
                    )
                new_ir = _tops.replace_at(state.ir, path, muutos_ir)
                if _same_norm_label(node.label, muutos_ir.label):
                    return _with_preserved_provision_index(state, new_ir)
                return state.with_ir(new_ir)
            merged = _merge_same_numbered_container_insert_ir(node, muutos_ir)
            if merged is None:
                merged = _preserve_live_container_on_merge_duplicate(
                    source_pathologies_out=source_pathologies_out,
                    source_statute=view.source_statute or "",
                    target_unit_kind=_target_unit_kind,
                    target_label=f"{_target_section} {kind}",
                    recovery_kind="container_insert_base_chapter_merge_duplicate_labels",
                    live_node=node,
                    payload_node=muutos_ir,
                )
            logger.debug("  %s → container insert-as-merge", ctx_label)
            if source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_destructive_shape_loss_risk_pathology(
                        source_statute=view.source_statute or "",
                        target_unit_kind=_target_unit_kind,
                        target_label=f"{_target_section} {kind}",
                        recovery_kind="container_insert_base_chapter_merge",
                        live_sibling_count=len([c for c in node.children if c.kind is IRNodeKind.SECTION]),
                        payload_sibling_count=len([c for c in muutos_ir.children if c.kind is IRNodeKind.SECTION]),
                    )
                )
            return state.with_ir(_tops.replace_at(state.ir, path, merged))
        if not profile.replace_same_numbered_container_insert and path is not None:
            existing_node = _tops.resolve(state.ir, path)
            assert existing_node is not None, f"resolve failed for {path}"
            base_path = None
            if base_ir is not None:
                base_path = _find_container_path_with_part_scope(
                    base_ir,
                    kind=kind,
                    label=_target_section,
                    target_part=_target_part,
                )
            if base_path is None:
                logger.debug("  %s → container insert consumes non-base scaffold", ctx_label)
                if source_pathologies_out is not None:
                    source_pathologies_out.append(
                        build_destructive_shape_loss_risk_pathology(
                            source_statute=view.source_statute or "",
                            target_unit_kind=_target_unit_kind,
                            target_label=f"{_target_section} {kind}",
                            recovery_kind="container_insert_non_base_scaffold_consume",
                            live_sibling_count=len([c for c in existing_node.children if c.kind is IRNodeKind.SECTION]),
                            payload_sibling_count=len([c for c in muutos_ir.children if c.kind is IRNodeKind.SECTION]),
                        )
                    )
                new_ir = _tops.replace_at(state.ir, path, muutos_ir)
                if _same_norm_label(existing_node.label, muutos_ir.label):
                    return _with_preserved_provision_index(state, new_ir)
                return state.with_ir(new_ir)
            else:
                # Chapter exists in both live state and base text.  INSERT
                # targeting an already-present chapter must not create a
                # duplicate label — merge (overlay) the amendment content
                # instead.  This handles old amendments titled "muuttamisesta
                # X luvun" where the compiler emits INSERT rather than REPLACE
                # because the whole-chapter form is used.
                merged = _merge_same_numbered_container_insert_ir(existing_node, muutos_ir)
                if merged is None:
                    merged = _preserve_live_container_on_merge_duplicate(
                        source_pathologies_out=source_pathologies_out,
                        source_statute=view.source_statute or "",
                        target_unit_kind=_target_unit_kind,
                        target_label=f"{_target_section} {kind}",
                        recovery_kind="container_insert_base_chapter_merge_duplicate_labels",
                        live_node=existing_node,
                        payload_node=muutos_ir,
                    )
                logger.debug("  %s → container insert-as-merge (base chapter exists)", ctx_label)
                if source_pathologies_out is not None:
                    source_pathologies_out.append(
                        build_destructive_shape_loss_risk_pathology(
                            source_statute=view.source_statute or "",
                            target_unit_kind=_target_unit_kind,
                            target_label=f"{_target_section} {kind}",
                            recovery_kind="container_insert_base_chapter_merge",
                            live_sibling_count=len([c for c in existing_node.children if c.kind is IRNodeKind.SECTION]),
                            payload_sibling_count=len([c for c in muutos_ir.children if c.kind is IRNodeKind.SECTION]),
                        )
                    )
                return state.with_ir(_tops.replace_at(state.ir, path, merged))
        placeholder_labels_to_remove: list[str] = []
        if _target_unit_kind == "chapter" and muutos_ir.kind is IRNodeKind.CHAPTER:
            normalized_standalone_targets = _normalized_standalone_targets()
            new_ch_children = []
            current_container_label = _norm_num_token(muutos_ir.label or _target_section)
            current_container_part = _norm_num_token(_target_part) if _target_part else None
            for child in muutos_ir.children:
                if child.kind is IRNodeKind.SECTION and child.label:
                    if path is None:
                        _lbl = _norm_num_token(child.label)
                        _filter_this = False
                        for part_label, ch, lbl in normalized_standalone_targets:
                            if lbl != _lbl:
                                continue
                            if ch is None:
                                # Root-insert sections (ch=None) are always
                                # placed by their own standalone INSERT op.
                                # Strip them from the chapter body so they end
                                # up in the correct chapter (determined by
                                # find_family on the master), not under the
                                # newly-created chapter whose XML body happens
                                # to contain them (e.g. sections 20a-20h
                                # appearing inside chapter 5c in the amendment
                                # body, but belonging to chapter 6).
                                _filter_this = True
                                break
                            if part_label != current_container_part:
                                continue
                            if ch == current_container_label:
                                _filter_this = True
                                break
                        if _filter_this:
                            continue
                        existing_path = None
                    else:
                        _existing_path = state.find_section_path(child.label)
                        existing_path = _tops._as_path(_existing_path) if _existing_path is not None else None
                    if existing_path is not None:
                        existing_node = _tops.resolve(state.ir, existing_path)
                        is_placeholder = (
                            existing_node is not None and existing_node.attrs.get("lawvm_repeal_placeholder") == "1"
                        )
                        if not is_placeholder:
                            continue
                        placeholder_labels_to_remove.append(child.label)
                new_ch_children.append(child)
            muutos_ir = _tops._with_children(muutos_ir, new_ch_children)

        if path is None and muutos_ir.kind is IRNodeKind.CHAPTER and muutos_ir.label:
            _ch_part_hint = muutos_ir.attrs.get("lawvm_amendment_part_hint")
            _routing_part_hint = str(_ch_part_hint) if _ch_part_hint is not None else (_target_part or None)
            if _routing_part_hint is not None:
                _routing_arabic = _roman_label_to_arabic(_norm_num_token(_routing_part_hint).lower())
                if _routing_arabic is not None:
                    _routing_part_hint = str(_routing_arabic)
            if (
                _routing_part_hint is not None
                and _tops.find(state.ir, "part", _routing_part_hint) is None
            ):
                _sibling_ch = muutos_ir.attrs.get("lawvm_amendment_part_sibling_chapters") or ()
                state = state.with_ir(
                    _create_part_and_move_siblings(
                        state.ir,
                        _routing_part_hint,
                        tuple(_sibling_ch),
                        migration_ledger=migration_ledger,
                        effective=_op_source_effective,
                        source_statute=view.source_statute or "",
                    )
                )
            parent_path = _tops._as_path(
                _find_chapter_insert_parent_path(state.ir, muutos_ir.label, part_hint=_routing_part_hint)
            )
        else:
            parent_path = _tops.find_provisions_parent(state.ir)
        logger.debug("  %s → container insert (sorted)", ctx_label)
        new_ir = _tops.insert_sorted(state.ir, parent_path or (), muutos_ir)

        for lbl in placeholder_labels_to_remove:
            _ph_path = state.find_section_path(lbl)
            ph_path = _tops._as_path(_ph_path) if _ph_path is not None else None
            if ph_path is not None:
                ph_node = _tops.resolve(new_ir, ph_path)
                if ph_node is not None and ph_node.attrs.get("lawvm_repeal_placeholder") == "1":
                    new_ir = _tops.remove_at(new_ir, ph_path)

        return state.with_ir(new_ir)

    replay_print(f"  {ctx_label} → FAILED (unhandled non-section op)")
    return state


def _apply_whole_section_op(
    state,
    op: "_StructureApplyView | AmendmentOp | ResolvedOp",
    sec_path: Path | None,
    muutos_ir: Optional[IRNode],
    cross_ir: Optional[IRNode],
    profile: ReplayProfile,
    ctx_label: str,
    base_ir: Optional[IRNode] = None,
    replay_history_ops: Optional[List[_LegalOperation]] = None,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    mixed_sparse_insert: bool = False,
    migration_ledger=None,
):
    """Apply whole-section operation via tree_ops."""
    view = _coerce_structure_apply_view(op)
    rop = op if isinstance(op, ResolvedOp) else None
    _ts = view.target_section
    _target_unit_kind = view.target_unit_kind
    _op_type = view.op_type
    _target_paragraph = view.target_paragraph
    _target_special = view.target_special
    _target_chapter = view.target_chapter
    _target_part = view.target_part
    _source_statute = view.source_statute
    _source_issue_date = view.source_issue_date
    _source_title = view.source_title
    _scope_confidence = runtime_scope_confidence_for_op(op)

    _op_lo = getattr(op, "lo", None)
    _op_source_effective = _op_lo.source.effective if _op_lo is not None and _op_lo.source is not None else ""

    if (
        _op_type in {"INSERT", "REPLACE"}
        and sec_path is None
        and migration_ledger is not None
    ):
        source_address = (
            (
                rop.resolved_target_address
                or (
                    _rop_lo.target
                    if (_rop_lo := getattr(rop, "lo", None)) is not None
                    else None
                )
            )
            if rop is not None
            else view.target_address
        )
        migrated = (
            migration_ledger.current_address_with_prefix_migrations(source_address)
            if source_address is not None
            else None
        )
        if (
            migrated is not None
            and source_address is not None
            and migrated != source_address
            and migrated.path
            and migrated.path[-1][0] == "section"
        ):
            migrated_labels = {kind: label for kind, label in migrated.path}
            source_labels = {kind: label for kind, label in source_address.path}
            migrated_section = migrated_labels.get("section")
            if migrated_section:
                source_section = source_labels.get("section")
                if source_section:
                    source_path = state.find_section_path(
                        source_section,
                        source_labels.get("chapter"),
                        source_labels.get("part"),
                    )
                    if source_path is not None:
                        state = state.with_ir(_tops.remove_at(state.ir, source_path))
                        sec_path = None
                _ts = migrated_section
                _target_chapter = migrated_labels.get("chapter")
                _target_part = migrated_labels.get("part")
                if muutos_ir is not None and not _same_norm_label(muutos_ir.label, migrated_section):
                    muutos_ir = _relabel_section_ir(muutos_ir, migrated_section)

    if (
        _target_unit_kind != "section"
        or _target_paragraph
        or (_target_special and _target_special != "otsikko_edella")
    ):
        return None

    if _target_special == "otsikko_edella":
        if _op_type == "INSERT" and sec_path is not None and muutos_ir is not None:
            parent_path = sec_path[:-1]
            if len(parent_path) >= 1 and parent_path[-1][0] == "chapter":
                insert_path = parent_path[:-1]
            else:
                insert_path = parent_path
            new_chapter = IRNode(
                kind=IRNodeKind.CHAPTER,
                label="",
                children=tuple(c for c in muutos_ir.children if c.kind == IRNodeKind.HEADING),
            )
            logger.debug("  %s → otsikko_edella insert", ctx_label)
            return state.with_ir(_tops.insert_sorted(state.ir, insert_path, new_chapter))
        return state

    if _op_type == "REPLACE" and sec_path is not None and muutos_ir is not None:
        live_sec = _tops.resolve(state.ir, sec_path)
        assert live_sec is not None, f"resolve failed for {sec_path}"
        muutos_ir = _align_section_payload_subsection_labels_from_slot_assignment(
            muutos_ir,
            rop=rop,
            view=view,
        )
        merge_base_sec = _expired_temporary_section_merge_base(
            op=cast("AmendmentOp | ResolvedOp", op),
            section_path=sec_path,
            replay_history_ops=replay_history_ops,
            base_ir=base_ir,
            current_live_section=live_sec,
        )
        if merge_base_sec is not None:
            logger.debug("  %s → section merge rebased to non-temporary snapshot", ctx_label)
            rebase_kind, latest_snapshot_expires = _expired_temporary_section_merge_base_rebase_info(
                op=cast("AmendmentOp | ResolvedOp", op),
                section_path=sec_path,
                replay_history_ops=replay_history_ops,
                current_live_section=live_sec,
            )
            if rebase_kind is not None and source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_temporary_section_rebase_pathology(
                        source_statute=_source_statute or "",
                        target_section=_ts,
                        target_chapter=_target_chapter or "",
                        rebase_context="section_replace",
                        rebase_kind=rebase_kind,
                        latest_snapshot_expires=latest_snapshot_expires or "",
                    )
                )
        live_merge_sec = merge_base_sec or live_sec

        def _same_section_label(replacement: IRNode) -> bool:
            return (
                bool(live_sec.label)
                and bool(replacement.label)
                and _tops._norm(live_sec.label) == _tops._norm(replacement.label)
            )

        def _carry_heading_if_absent(replacement: IRNode) -> IRNode:
            """Preserve the existing section heading when the replacement lacks one.

            Finnish amendment payloads that replace subsection content often do
            not repeat the section heading.  Rather than losing the heading, we
            carry it forward from the live section.  This is safe because a
            heading change requires an explicit heading_replace op; a content-
            only section REPLACE never intends to delete the heading.
            """
            has_heading = any(c.kind == IRNodeKind.HEADING for c in replacement.children)
            if has_heading:
                return replacement
            live_heading = next(
                (c for c in live_sec.children if c.kind == IRNodeKind.HEADING), None
            )
            if live_heading is None:
                return replacement
            # Insert the heading after the num (if present), before everything else.
            new_children: list[IRNode] = []
            heading_placed = False
            for c in replacement.children:
                if not heading_placed and c.kind != IRNodeKind.NUM:
                    new_children.append(live_heading)
                    heading_placed = True
                new_children.append(c)
            if not heading_placed:
                new_children.append(live_heading)
            logger.debug(
                "  heading carry-forward: preserved %r on section %s",
                live_heading.text[:40] if live_heading.text else "",
                replacement.label or live_sec.label or "?",
            )
            return IRNode(
                kind=replacement.kind,
                label=replacement.label,
                text=replacement.text,
                attrs=replacement.attrs,
                children=tuple(new_children),
            )

        sparse_merge = _sparse_item_section_replace_merge_ir(live_merge_sec, muutos_ir)
        if sparse_merge is not None:
            live_sub = next((c for c in live_merge_sec.children if c.kind is IRNodeKind.SUBSECTION), None)
            amend_sub = next((c for c in muutos_ir.children if c.kind is IRNodeKind.SUBSECTION), None)
            if source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_destructive_shape_loss_risk_pathology(
                        source_statute=_source_statute or "",
                        target_unit_kind=view.target_unit_kind,
                        target_label=f"{_ts} §",
                        recovery_kind="sparse_item_replace_merge",
                        live_sibling_count=(
                            len([c for c in live_sub.children if c.kind is IRNodeKind.PARAGRAPH]) if live_sub is not None else 0
                        ),
                        payload_sibling_count=(
                            len([c for c in amend_sub.children if c.kind is IRNodeKind.PARAGRAPH])
                            if amend_sub is not None
                            else 0
                        ),
                    )
                )
            logger.debug("  %s → section replace-as-sparse-item-merge", ctx_label)
            sparse_merge = _carry_heading_if_absent(sparse_merge)
            sparse_merge = _preserve_unstated_live_subsection_tail(
                sparse_merge,
                live_sec=live_merge_sec,
                rop=rop,
                view=view,
            )
            sparse_merge = _apply_section_tail_policy_marker(sparse_merge, rop=rop, view=view)
            new_ir = _tops.replace_at(state.ir, sec_path, sparse_merge)
            if _same_section_label(sparse_merge):
                return _with_preserved_provision_index(state, new_ir)
            return state.with_ir(new_ir)
        intro_preserve = _heading_intro_replace_preserve_items_ir(live_merge_sec, muutos_ir)
        if intro_preserve is not None:
            logger.debug("  %s → section heading+intro replace, items preserved", ctx_label)
            intro_preserve = _preserve_unstated_live_subsection_tail(
                intro_preserve,
                live_sec=live_merge_sec,
                rop=rop,
                view=view,
            )
            intro_preserve = _apply_section_tail_policy_marker(intro_preserve, rop=rop, view=view)
            new_ir = _tops.replace_at(state.ir, sec_path, intro_preserve)
            if _same_section_label(intro_preserve):
                return _with_preserved_provision_index(state, new_ir)
            return state.with_ir(new_ir)
        multi_sparse_merge = _multi_subsection_sparse_item_section_replace_merge_ir(
            live_merge_sec,
            muutos_ir,
        )
        if multi_sparse_merge is not None:
            logger.debug("  %s → section replace with sparse subsection-item preservation", ctx_label)
            multi_sparse_merge = _carry_heading_if_absent(multi_sparse_merge)
            multi_sparse_merge = _preserve_unstated_live_subsection_tail(
                multi_sparse_merge,
                live_sec=live_merge_sec,
                rop=rop,
                view=view,
            )
            multi_sparse_merge = _apply_section_tail_policy_marker(multi_sparse_merge, rop=rop, view=view)
            new_ir = _tops.replace_at(state.ir, sec_path, multi_sparse_merge)
            if _same_section_label(multi_sparse_merge):
                return _with_preserved_provision_index(state, new_ir)
            return state.with_ir(new_ir)
        mixed_intro_preserve = _mixed_sparse_intro_replace_preserve_first_subsection_items_ir(
            live_merge_sec,
            muutos_ir,
        )
        if mixed_intro_preserve is not None:
            logger.debug("  %s → section replace with intro-only first subsection, items preserved", ctx_label)
            mixed_intro_preserve = _carry_heading_if_absent(mixed_intro_preserve)
            mixed_intro_preserve = _preserve_unstated_live_subsection_tail(
                mixed_intro_preserve,
                live_sec=live_merge_sec,
                rop=rop,
                view=view,
            )
            mixed_intro_preserve = _apply_section_tail_policy_marker(mixed_intro_preserve, rop=rop, view=view)
            new_ir = _tops.replace_at(state.ir, sec_path, mixed_intro_preserve)
            if _same_section_label(mixed_intro_preserve):
                return _with_preserved_provision_index(state, new_ir)
            return state.with_ir(new_ir)
        live_subsections = [c for c in live_merge_sec.children if c.kind is IRNodeKind.SUBSECTION]
        amend_subsections = [c for c in muutos_ir.children if c.kind is IRNodeKind.SUBSECTION]
        if mixed_sparse_insert and len(live_subsections) == 1 and len(amend_subsections) == 1:
            amend_heading = next((c for c in muutos_ir.children if c.kind == IRNodeKind.HEADING), None)
            if amend_heading is not None:
                new_children: list[IRNode] = []
                heading_replaced = False
                for child in live_sec.children:
                    if child.kind == IRNodeKind.HEADING and not heading_replaced:
                        new_children.append(amend_heading)
                        heading_replaced = True
                    else:
                        new_children.append(child)
                if not heading_replaced:
                    new_children.insert(0, amend_heading)
                logger.debug("  %s → section heading-only replace (mixed sparse insert)", ctx_label)
                return _with_preserved_provision_index(
                    state,
                    _tops.replace_at(state.ir, sec_path, _tops._with_children(live_sec, new_children)),
                )
        if _is_suspicious_partial_section_replace_ir(cast("AmendmentOp | ResolvedOp", view), live_sec, muutos_ir):
            logger.debug("  %s → section replace skipped (suspicious partial fallback fragment)", ctx_label)
            return state
            logger.debug("  %s → section replace", ctx_label)
        muutos_ir = _prepare_section_root_payload_for_replay(
            muutos_ir,
            live_sec=live_merge_sec,
            rop=rop,
            view=view,
        )
        new_ir = _tops.replace_at(state.ir, sec_path, muutos_ir)
        if _same_section_label(muutos_ir):
            return _with_preserved_provision_index(state, new_ir)
        return state.with_ir(new_ir)

    if _op_type == "REPLACE" and sec_path is None and muutos_ir is not None:
        if _target_chapter and _same_norm_label(muutos_ir.label, _ts):
            label_norm = _tops._norm(_ts)
            matches = [
                _tops._as_path(path)
                for path in state.provision_index.get(("section", label_norm), [])
                if _path_matches_target_part(_tops._as_path(path), _target_part)
            ]
            root_matches = [
                path
                for path in matches
                if not any(kind == "chapter" for kind, _label in path)
            ]
            candidate_paths = root_matches if len(root_matches) == 1 else ([matches[0]] if len(matches) == 1 else [])
            if candidate_paths:
                existing_path = candidate_paths[0]
                existing_chapter = next((lbl for kind, lbl in existing_path if kind == "chapter"), None)
                if not existing_chapter:
                    if _scope_confidence is not None and _scope_confidence.source == "carry_forward":
                        logger.debug(
                            "  %s → rejected root move+replace for carry-forward chapter scope",
                            ctx_label,
                        )
                        return None
                    logger.debug(
                        "  %s → section move+replace from root to %s",
                        ctx_label,
                        _target_chapter,
                    )
                    existing_node = _tops.resolve(state.ir, existing_path)
                    if source_pathologies_out is not None:
                        source_pathologies_out.append(
                            build_destructive_shape_loss_risk_pathology(
                                source_statute=_source_statute or "",
                                target_unit_kind=view.target_unit_kind,
                                target_label=f"{_ts} §",
                                recovery_kind="section_move_replace_destination_rebind",
                                live_sibling_count=len(
                                    [
                                        c
                                        for c in (existing_node.children if existing_node is not None else ())
                                        if c.kind is IRNodeKind.SUBSECTION
                                    ]
                                ),
                                payload_sibling_count=len(
                                    [c for c in muutos_ir.children if c.kind is IRNodeKind.SUBSECTION]
                                ),
                            )
                        )
                    moved_ir = _move_section_payload_to_target_chapter(
                        state.ir,
                        existing_path,
                        _target_chapter,
                        muutos_ir,
                        source_pathologies_out=source_pathologies_out,
                    )
                    return state.with_ir(moved_ir)
                if existing_chapter != _target_chapter:
                    logger.debug(
                        "  %s → section move+replace from chapter %s to %s",
                        ctx_label,
                        existing_chapter,
                        _target_chapter,
                    )
                    existing_node = _tops.resolve(state.ir, existing_path)
                    if source_pathologies_out is not None:
                        source_pathologies_out.append(
                            build_destructive_shape_loss_risk_pathology(
                                source_statute=_source_statute or "",
                                target_unit_kind=view.target_unit_kind,
                                target_label=f"{_ts} §",
                                recovery_kind="section_move_replace_destination_rebind",
                                live_sibling_count=len(
                                    [
                                        c
                                        for c in (existing_node.children if existing_node is not None else ())
                                        if c.kind is IRNodeKind.SUBSECTION
                                    ]
                                ),
                                payload_sibling_count=len(
                                    [c for c in muutos_ir.children if c.kind is IRNodeKind.SUBSECTION]
                                ),
                            )
                        )
                    moved_ir = _move_section_payload_to_target_chapter(
                        state.ir,
                        existing_path,
                        _target_chapter,
                        muutos_ir,
                    )
                    return state.with_ir(moved_ir)
        # Fall through to scoped materialization recovery. A bare root-level
        # whole-section REPLACE still won't synthesize an insert because
        # _apply_materialization rejects unscoped root-level replacements.
        if muutos_ir is not None and _target_unit_kind == "section":
            base_path = None
            if base_ir is not None:
                if _target_part:
                    part_path = _find_direct_body_part_path(base_ir, _target_part) or _tops.find(base_ir, "part", _target_part)
                    part_node = _tops.resolve(base_ir, part_path) if part_path is not None else None
                    if part_path is not None and part_node is not None:
                        if _target_chapter:
                            chapter_path = _tops.find(part_node, "chapter", _target_chapter)
                            chapter_node = _tops.resolve(part_node, chapter_path) if chapter_path is not None else None
                            if chapter_path is not None and chapter_node is not None:
                                base_path = _tops.find(chapter_node, "section", _ts)
                        else:
                            base_path = _tops.find(part_node, "section", _ts)
                else:
                    base_path = _tops.find(
                        base_ir,
                        "section",
                        _ts,
                        scope_kind="chapter" if _target_chapter else None,
                        scope_label=_target_chapter,
                    )
            if base_path is None:
                parent_path = _find_scoped_section_insert_parent_path(
                    state,
                    target_chapter=_target_chapter,
                    target_part=_target_part,
                )
                if parent_path is None:
                    logger.debug(
                        "  %s → section replace bootstrap gap rejected (missing scoped parent)",
                        ctx_label,
                    )
                    return None
                parent_supports_root_section_bootstrap = (
                    _target_chapter is not None
                    or any(kind == "hcontainer" for kind, _label in parent_path)
                )
                if not parent_supports_root_section_bootstrap:
                    logger.debug(
                        "  %s → section replace bootstrap gap rejected at plain root",
                        ctx_label,
                    )
                    return None
                if source_pathologies_out is not None:
                    source_pathologies_out.append(
                        build_destructive_shape_loss_risk_pathology(
                            source_statute=_source_statute or "",
                            target_unit_kind=view.target_unit_kind,
                            target_label=f"{_ts} §",
                            recovery_kind="section_replace_bootstrap_gap_establish",
                            live_sibling_count=0,
                            payload_sibling_count=len(
                                [c for c in muutos_ir.children if c.kind is IRNodeKind.SUBSECTION]
                            ),
                        )
                    )
                logger.debug("  %s → section replace-as-insert (bootstrap gap)", ctx_label)
                return state.with_ir(_tops.insert_sorted(state.ir, parent_path, muutos_ir))
        logger.debug("  %s → section replace deferred to materialization fallback", ctx_label)
        return None

    if _op_type == "REPEAL" and sec_path is not None:
        sec_node = _tops.resolve(state.ir, sec_path)
        assert sec_node is not None, f"resolve failed for {sec_path}"
        if profile.synthesize_repeal_placeholders:
            ph = _build_repeal_placeholder_ir(sec_node, _ts, _source_statute or "", _source_issue_date, _source_title)
            logger.debug("  %s → section repeal", ctx_label)
            return _with_preserved_provision_index(
                state,
                _tops.replace_at(state.ir, sec_path, ph),
            )
        logger.debug("  %s → section repeal", ctx_label)
        return state.with_ir(_tops.remove_at(state.ir, sec_path))

    if _op_type == "REPEAL" and sec_path is None and profile.synthesize_repeal_placeholders:
        parent_path = _find_scoped_section_insert_parent_path(
            state,
            target_chapter=_target_chapter,
            target_part=_target_part,
        )
        if parent_path is None:
            logger.debug("  %s → section repeal-as-placeholder-insert rejected (missing scoped parent)", ctx_label)
            return None
        ph = _build_repeal_placeholder_from_label_ir(
            _ts,
            _source_statute or "",
            _source_issue_date,
            _source_title,
        )
        logger.debug("  %s → section repeal-as-placeholder-insert", ctx_label)
        return state.with_ir(_tops.insert_sorted(state.ir, parent_path, ph))

    if _op_type == "INSERT" and muutos_ir is not None:
        if _target_chapter and _same_norm_label(muutos_ir.label, _ts):
            label_norm = _tops._norm(_ts)
            matches = [
                _tops._as_path(path)
                for path in state.provision_index.get(("section", label_norm), [])
                if _path_matches_target_part(_tops._as_path(path), _target_part)
            ]
            root_matches = [
                path
                for path in matches
                if not any(kind == "chapter" for kind, _label in path)
            ]
            candidate_paths = root_matches if len(root_matches) == 1 else ([matches[0]] if len(matches) == 1 else [])
            if candidate_paths:
                existing_path = candidate_paths[0]
                existing_node = _tops.resolve(state.ir, existing_path)
                existing_chapter = next((lbl for kind, lbl in existing_path if kind == "chapter"), None)
                if not existing_chapter:
                    logger.debug(
                        "  %s → section move+insert from root to %s",
                        ctx_label,
                        _target_chapter,
                    )
                    if source_pathologies_out is not None:
                        source_pathologies_out.append(
                            build_destructive_shape_loss_risk_pathology(
                                source_statute=_source_statute or "",
                                target_unit_kind=view.target_unit_kind,
                                target_label=f"{_ts} §",
                                recovery_kind="section_move_insert_destination_rebind",
                                live_sibling_count=len(
                                    [
                                        c
                                        for c in (existing_node.children if existing_node is not None else ())
                                        if c.kind is IRNodeKind.SUBSECTION
                                    ]
                                ),
                                payload_sibling_count=len(
                                    [c for c in muutos_ir.children if c.kind is IRNodeKind.SUBSECTION]
                                ),
                            )
                        )
                    moved_ir = _move_section_payload_to_target_chapter(
                        state.ir,
                        existing_path,
                        _target_chapter,
                        muutos_ir,
                        migration_ledger=migration_ledger,
                        effective=_op_source_effective,
                        source_statute=_source_statute or "",
                        source_pathologies_out=source_pathologies_out,
                    )
                    return state.with_ir(moved_ir)
                if existing_chapter and existing_chapter != _target_chapter:
                    is_placeholder = (
                        existing_node is not None
                        and existing_node.attrs.get("lawvm_repeal_placeholder") == "1"
                    )
                    if is_placeholder:
                        logger.debug(
                            "  %s → section move+insert (placeholder) from chapter %s to %s",
                            ctx_label,
                            existing_chapter,
                            _target_chapter,
                        )
                        if source_pathologies_out is not None:
                            source_pathologies_out.append(
                                build_destructive_shape_loss_risk_pathology(
                                    source_statute=_source_statute or "",
                                    target_unit_kind=view.target_unit_kind,
                                    target_label=f"{_ts} §",
                                    recovery_kind="section_move_insert_destination_rebind",
                                    live_sibling_count=len(
                                        [
                                            c
                                            for c in (existing_node.children if existing_node is not None else ())
                                            if c.kind is IRNodeKind.SUBSECTION
                                        ]
                                    ),
                                    payload_sibling_count=len(
                                        [c for c in muutos_ir.children if c.kind is IRNodeKind.SUBSECTION]
                                    ),
                                )
                            )
                        moved_ir = _move_section_payload_to_target_chapter(
                            state.ir,
                            existing_path,
                            _target_chapter,
                            muutos_ir,
                            migration_ledger=migration_ledger,
                            effective=_op_source_effective,
                            source_statute=_source_statute or "",
                        )
                        return state.with_ir(moved_ir)
                    elif re.fullmatch(
                        rf"{re.escape(existing_chapter)}[a-z]+", _target_chapter, re.I
                    ) is not None:
                        # Section exists in the "parent" chapter (e.g. §55 in ch "7")
                        # and is being INSERTed into a letter-suffix sub-chapter (e.g.
                        # ch "7c") via a REPLACE→INSERT pseudo-chapter restructuring
                        # conversion (see _compile_group body-chapter correction).
                        # MOVE the section from the parent chapter to the sub-chapter.
                        logger.debug(
                            "  %s → section move+insert (pseudo-chapter restructure) from %s to %s",
                            ctx_label,
                            existing_chapter,
                            _target_chapter,
                        )
                        if source_pathologies_out is not None:
                            source_pathologies_out.append(
                                build_destructive_shape_loss_risk_pathology(
                                    source_statute=_source_statute or "",
                                    target_unit_kind=view.target_unit_kind,
                                    target_label=f"{_ts} §",
                                    recovery_kind="section_move_insert_destination_rebind",
                                    live_sibling_count=len(
                                        [
                                            c
                                            for c in (existing_node.children if existing_node is not None else ())
                                            if c.kind is IRNodeKind.SUBSECTION
                                        ]
                                    ),
                                    payload_sibling_count=len(
                                        [c for c in muutos_ir.children if c.kind is IRNodeKind.SUBSECTION]
                                    ),
                                )
                            )
                        moved_ir = _move_section_payload_to_target_chapter(
                            state.ir,
                            existing_path,
                            _target_chapter,
                            muutos_ir,
                            migration_ledger=migration_ledger,
                            effective=_op_source_effective,
                            source_statute=_source_statute or "",
                            source_pathologies_out=source_pathologies_out,
                        )
                        return _with_preserved_provision_index(state, moved_ir)
        if _target_chapter:
            ch_path = None
            if _target_part:
                part_path = _find_direct_body_part_path(state.ir, _target_part) or state.find("part", _target_part)
                part_node = _tops.resolve(state.ir, part_path) if part_path is not None else None
                if part_path is not None and part_node is not None:
                    local_ch_path = _tops.find(part_node, "chapter", _target_chapter)
                    if local_ch_path is not None:
                        ch_path = part_path + local_ch_path
            else:
                _idx = state.provision_index
                ch_path = _tops.find(state.ir, "chapter", _target_chapter, label_index=_idx)
            if ch_path is not None:
                ch_node = _tops.resolve(state.ir, ch_path)
                assert ch_node is not None, f"resolve failed for chapter {_target_chapter}"
                prepared_muutos_ir = muutos_ir
                existing_sec_path = _parent_direct_child_path_with_same_label(
                    state.ir,
                    _tops._as_path(ch_path),
                    kind=IRNodeKind.SECTION,
                    label=muutos_ir.label or "",
                )
                if existing_sec_path is not None:
                    existing_sec = _tops.resolve(state.ir, existing_sec_path)
                    if existing_sec is not None:
                        prepared_muutos_ir = _prepare_section_root_payload_for_replay(
                            muutos_ir,
                            live_sec=existing_sec,
                            rop=rop,
                            view=view,
                        )
                temp_ch = IRNode(kind=IRNodeKind.CHAPTER, label=_target_chapter, children=(prepared_muutos_ir,))
                if source_pathologies_out is not None:
                    source_pathologies_out.append(
                        build_destructive_shape_loss_risk_pathology(
                            source_statute=_source_statute or "",
                            target_unit_kind=view.target_unit_kind,
                            target_label=f"{_ts} §",
                            recovery_kind="section_insert_chapter_merge_absorb",
                            live_sibling_count=len(
                                [c for c in ch_node.children if c.kind is IRNodeKind.SUBSECTION]
                            ),
                            payload_sibling_count=len(
                                [c for c in temp_ch.children if c.kind is IRNodeKind.SUBSECTION]
                            ),
                        )
                )
                merged = _merge_same_numbered_container_insert_ir(ch_node, temp_ch)
                if merged is None:
                    merged = _merge_unique_payload_sections_after_live_duplicate_skip(ch_node, temp_ch)
                    if merged is not None:
                        if source_pathologies_out is not None:
                            source_pathologies_out.append(
                                build_unique_payload_insert_under_live_duplicates_pathology(
                                    source_statute=_source_statute or "",
                                    target_unit_kind=view.target_unit_kind,
                                    target_label=f"{_ts} §",
                                    recovery_kind="section_insert_chapter_merge_live_duplicates_preserve_unique_payload",
                                    live_sibling_count=len(
                                        [c for c in ch_node.children if c.kind is IRNodeKind.SECTION]
                                    ),
                                    payload_sibling_count=len(
                                        [c for c in temp_ch.children if c.kind is IRNodeKind.SECTION]
                                    ),
                                )
                            )
                    else:
                        merged = _preserve_live_container_on_merge_duplicate(
                            source_pathologies_out=source_pathologies_out,
                            source_statute=_source_statute or "",
                            target_unit_kind=view.target_unit_kind,
                            target_label=f"{_ts} §",
                            recovery_kind="section_insert_chapter_merge_absorb_duplicate_labels",
                            live_node=ch_node,
                            payload_node=temp_ch,
                        )
                absorbed_ir = state.ir
                merged_for_replace = merged
                absorbed_paths: tuple[tuple[tuple[str, str], ...], ...] = ()
                if not any(child.kind is IRNodeKind.SECTION for child in ch_node.children):
                    absorbed_ir, merged_for_replace, absorbed_paths = _absorb_trailing_wrapper_sections_into_letter_suffix_chapter(
                        state,
                        chapter_path=_tops._as_path(ch_path),
                        merged_chapter=merged,
                    )
                    if absorbed_paths and source_pathologies_out is not None:
                        source_pathologies_out.append(
                            build_destructive_shape_loss_risk_pathology(
                                source_statute=_source_statute or "",
                                target_unit_kind=view.target_unit_kind,
                                target_label=f"{_ts} §",
                                recovery_kind="section_insert_chapter_merge_absorb_trailing_siblings",
                                live_sibling_count=len(absorbed_paths),
                                payload_sibling_count=len(
                                    [c for c in merged.children if c.kind is IRNodeKind.SECTION]
                                ),
                            )
                        )
                logger.debug("  %s → section insert via chapter merge (%s luku)", ctx_label, _target_chapter)
                return state.with_ir(_tops.replace_at(absorbed_ir, ch_path, merged_for_replace))

        if not profile.replace_same_numbered_section_insert:
            replace_path = None
            if muutos_ir.label:
                if _target_part:
                    replace_path = state.find_section_path(muutos_ir.label, _target_chapter, _target_part)
                else:
                    replace_path = state.find(
                        "section",
                        muutos_ir.label,
                        scope_kind=IRNodeKind.CHAPTER.value if _target_chapter else None,
                        scope_label=_target_chapter,
                    )
            if replace_path is None and sec_path is not None:
                replace_path = sec_path
            if replace_path is not None:
                existing_node = _tops.resolve(state.ir, replace_path)
                assert existing_node is not None, f"resolve failed for {replace_path}"
                base_path = None
                if base_ir is not None:
                    if _target_part:
                        part_path = _find_direct_body_part_path(base_ir, _target_part) or _tops.find(base_ir, "part", _target_part)
                        part_node = _tops.resolve(base_ir, part_path) if part_path is not None else None
                        if part_path is not None and part_node is not None:
                            if _target_chapter:
                                chapter_path = _tops.find(part_node, "chapter", _target_chapter)
                                chapter_node = _tops.resolve(part_node, chapter_path) if chapter_path is not None else None
                                if chapter_path is not None and chapter_node is not None:
                                    base_path = _tops.find(chapter_node, "section", _ts)
                            else:
                                base_path = _tops.find(part_node, "section", _ts)
                    else:
                        base_path = _tops.find(
                            base_ir,
                            "section",
                            _ts,
                            scope_kind=IRNodeKind.CHAPTER.value if _target_chapter else None,
                            scope_label=_target_chapter,
                        )
                if base_path is None:
                    logger.debug("  %s → section insert consumes non-base scaffold", ctx_label)
                    if source_pathologies_out is not None:
                        source_pathologies_out.append(
                            build_destructive_shape_loss_risk_pathology(
                                source_statute=_source_statute or "",
                                target_unit_kind=view.target_unit_kind,
                                target_label=f"{_ts} §",
                                recovery_kind="section_insert_non_base_scaffold_consume",
                                live_sibling_count=len(
                                    [c for c in existing_node.children if c.kind is IRNodeKind.SUBSECTION]
                                ),
                                payload_sibling_count=len(
                                    [c for c in muutos_ir.children if c.kind is IRNodeKind.SUBSECTION]
                                ),
                            )
                        )
                    new_ir = _tops.replace_at(state.ir, replace_path, muutos_ir)
                    if _same_norm_label(existing_node.label, muutos_ir.label):
                        return _with_preserved_provision_index(state, new_ir)
                    return state.with_ir(new_ir)

        if profile.replace_same_numbered_section_insert:
            replace_path = None
            if muutos_ir.label:
                # When the INSERT targets a specific chapter, scope the lookup
                # to that chapter to avoid replacing a same-numbered section in
                # a different chapter (cross-chapter misrouting prevention).
                if _target_part:
                    replace_path = state.find_section_path(muutos_ir.label, _target_chapter, _target_part)
                else:
                    replace_path = state.find(
                        "section",
                        muutos_ir.label,
                        scope_kind=IRNodeKind.CHAPTER.value if _target_chapter else None,
                        scope_label=_target_chapter,
                    )
            if replace_path is None and sec_path is not None:
                replace_path = sec_path
            if replace_path is not None:
                existing_node = _tops.resolve(state.ir, replace_path)
                assert existing_node is not None, f"resolve failed for {replace_path}"
                has_omissions = _has_section_omissions_ir(muutos_ir)
                if has_omissions:
                    merge_base_sec = _expired_temporary_section_merge_base(
                        op=cast("AmendmentOp | ResolvedOp", op),
                        section_path=replace_path,
                        replay_history_ops=replay_history_ops,
                        base_ir=base_ir,
                        current_live_section=existing_node,
                    )
                    if merge_base_sec is not None:
                        logger.debug("  %s → section insert omission merge rebased to non-temporary snapshot", ctx_label)
                        rebase_kind, latest_snapshot_expires = _expired_temporary_section_merge_base_rebase_info(
                            op=cast("AmendmentOp | ResolvedOp", op),
                            section_path=replace_path,
                            replay_history_ops=replay_history_ops,
                            current_live_section=existing_node,
                        )
                        if rebase_kind is not None and source_pathologies_out is not None:
                            source_pathologies_out.append(
                                build_temporary_section_rebase_pathology(
                                    source_statute=_source_statute or "",
                                    target_section=_ts,
                                    target_chapter=_target_chapter or "",
                                    rebase_context="section_insert_omission_merge",
                                    rebase_kind=rebase_kind,
                                    latest_snapshot_expires=latest_snapshot_expires or "",
                                )
                            )
                    merge_group_ops: list[AmendmentOp] | None = None
                    if isinstance(op, AmendmentOp):
                        merge_group_ops = [op]
                    elif isinstance(op, ResolvedOp):
                        merge_group_ops = [op.op]
                    try:
                        merged = _merge_section_with_omission_ir(
                            merge_base_sec or existing_node,
                            muutos_ir,
                            group_ops=merge_group_ops,
                        )
                    except TypeError as exc:
                        # Some tests monkeypatch the helper with the legacy two-arg
                        # signature; keep the omission-merge failure path observable
                        # instead of crashing on the compatibility-only keyword.
                        if "group_ops" not in str(exc):
                            raise
                        merged = _merge_section_with_omission_ir(
                            merge_base_sec or existing_node,
                            muutos_ir,
                        )
                    if merged is not None:
                        logger.debug("  %s → section insert-as-replace (omission merge)", ctx_label)
                        if source_pathologies_out is not None:
                            source_pathologies_out.append(
                                build_partial_whole_section_payload_pathology(
                                    source_statute=_source_statute or "",
                                    target_unit_kind=view.target_unit_kind,
                                    target_section=_ts,
                                    target_chapter=_target_chapter or "",
                                    live_paragraph_count=len(
                                        [c for c in existing_node.children if c.kind is IRNodeKind.SUBSECTION]
                                    ),
                                    amend_paragraph_count=len(
                                        [c for c in muutos_ir.children if c.kind is IRNodeKind.SUBSECTION]
                                    ),
                                    live_text_chars=len(irnode_to_text(existing_node)),
                                    amend_text_chars=len(irnode_to_text(muutos_ir)),
                                    diagnostic_reason="section_insert_omission_merge_applied",
                                )
                            )
                        new_ir = _tops.replace_at(state.ir, replace_path, merged)
                        if _same_norm_label(existing_node.label, merged.label):
                            return _with_preserved_provision_index(state, new_ir)
                        return state.with_ir(new_ir)
                    if source_pathologies_out is not None:
                        source_pathologies_out.append(
                            build_partial_whole_section_payload_pathology(
                                source_statute=_source_statute or "",
                                target_unit_kind=view.target_unit_kind,
                                target_section=_ts,
                                target_chapter=_target_chapter or "",
                                live_paragraph_count=len(
                                    [c for c in existing_node.children if c.kind is IRNodeKind.SUBSECTION]
                                ),
                                amend_paragraph_count=len(
                                    [c for c in muutos_ir.children if c.kind is IRNodeKind.SUBSECTION]
                                ),
                                live_text_chars=len(irnode_to_text(existing_node)),
                                amend_text_chars=len(irnode_to_text(muutos_ir)),
                                diagnostic_reason="section_insert_omission_merge_failed",
                            )
                        )
                    logger.debug("  %s → section insert-as-replace blocked (omission merge failed)", ctx_label)
                    return state
                logger.debug("  %s → section insert-as-replace", ctx_label)
                prepared_muutos_ir = _prepare_section_root_payload_for_replay(
                    muutos_ir,
                    live_sec=existing_node,
                    rop=rop,
                    view=view,
                )
                new_ir = _tops.replace_at(state.ir, replace_path, prepared_muutos_ir)
                if _same_norm_label(existing_node.label, muutos_ir.label):
                    return _with_preserved_provision_index(state, new_ir)
                return state.with_ir(new_ir)

        _idx = state.provision_index
        family_path = _tops.find_family(
            state.ir,
            "section",
            _ts,
            scope_kind=IRNodeKind.CHAPTER.value if _target_chapter else None,
            scope_label=_target_chapter,
            label_index=_idx,
        )
        if family_path is not None:
            parent_path = family_path[:-1]
            new_ir = state.ir
        else:
            parent_path = _tops._as_path(_find_insert_parent_path(state.ir, _target_chapter, label_index=_idx))
            new_ir = state.ir
            if cross_ir is not None:
                cross_same_path = _parent_direct_child_path_with_same_label(
                    new_ir,
                    parent_path,
                    kind=cross_ir.kind,
                    label=cross_ir.label or "",
                )
                if cross_same_path is not None:
                    existing_cross = _tops.resolve(new_ir, cross_same_path)
                    if existing_cross is not None and cross_ir.kind is IRNodeKind.SECTION:
                        cross_ir = _prepare_section_root_payload_for_replay(
                            cross_ir,
                            live_sec=existing_cross,
                            rop=rop,
                            view=view,
                        )
                new_ir, replaced = _insert_or_replace_same_labeled_child(new_ir, parent_path, cross_ir)
                if replaced and source_pathologies_out is not None:
                    parent_node = _tops.resolve(new_ir, parent_path)
                    source_pathologies_out.append(
                        build_destructive_shape_loss_risk_pathology(
                            source_statute=_source_statute or "",
                            target_unit_kind=view.target_unit_kind,
                            target_label=f"{_ts} §",
                            recovery_kind="section_insert_same_label_replace_cross",
                            live_sibling_count=len(
                                [c for c in (parent_node.children if parent_node is not None else ()) if c.kind is IRNodeKind.SECTION]
                            ),
                            payload_sibling_count=1,
                        )
                    )
        same_path = _parent_direct_child_path_with_same_label(
            new_ir,
            parent_path,
            kind=muutos_ir.kind,
            label=muutos_ir.label or "",
        )
        if same_path is not None:
            existing_node = _tops.resolve(new_ir, same_path)
            if existing_node is not None and muutos_ir.kind is IRNodeKind.SECTION:
                muutos_ir = _prepare_section_root_payload_for_replay(
                    muutos_ir,
                    live_sec=existing_node,
                    rop=rop,
                    view=view,
                )
        new_ir, replaced = _insert_or_replace_same_labeled_child(new_ir, parent_path, muutos_ir)
        if replaced and source_pathologies_out is not None:
            parent_node = _tops.resolve(new_ir, parent_path)
            source_pathologies_out.append(
                build_destructive_shape_loss_risk_pathology(
                    source_statute=_source_statute or "",
                    target_unit_kind=view.target_unit_kind,
                    target_label=f"{_ts} §",
                    recovery_kind="section_insert_same_label_replace",
                    live_sibling_count=len(
                        [c for c in (parent_node.children if parent_node is not None else ()) if c.kind is IRNodeKind.SECTION]
                    ),
                    payload_sibling_count=1,
                )
            )
        logger.debug("  %s → section insert (sorted)", ctx_label)
        return state.with_ir(new_ir)

    replay_print(f"  {ctx_label} → FAILED (section not found or unhandled op)")
    return state


def _apply_materialization(
    state,
    op: "_StructureApplyView | AmendmentOp | ResolvedOp",
    muutos_ir: Optional[IRNode],
    ctx_label: str,
    *,
    migration_ledger=None,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
):
    """Materialize missing sections only for scoped or child-target recovery.

    This is a fallback for cases where replay has enough structured context to
    place a missing section deterministically, such as chapter-scoped recovery
    or paragraph/item-targeted section reconstruction. A bare root-level whole-
    section REPLACE must not silently turn into an INSERT.
    """
    view = _coerce_structure_apply_view(op)
    rop = op if isinstance(op, ResolvedOp) else None
    _ts = view.target_section
    _target_unit_kind = view.target_unit_kind
    _target_chapter = view.target_chapter
    _target_paragraph = view.target_paragraph
    _target_item = view.target_item
    _target_special = view.target_special
    _op_type = view.op_type
    _scope_confidence = runtime_scope_confidence_for_op(op)
    if _target_unit_kind != "section":
        return None
    # For subsection-level ops (target_paragraph / target_item set): if the
    # section already exists *anywhere* in the tree, do not materialise a new
    # copy.  The previous chapter-scoped check allowed phantom insertion when a
    # section lived in a different part (e.g. part:4/section:51d) while the op
    # carried a chapter:1 scope from carry-forward, creating a duplicate.
    # Subsection ops should fall through to the existing-section dispatch path.
    #
    # For whole-section or special (otsikko, etc.) ops we still allow
    # materialisation even when the section exists elsewhere, because
    # pseudo-chapter restructuring legitimately moves sections between chapters.
    if (_target_paragraph or _target_item) and state.find_node("section", _ts) is not None:
        return None
    # For non-subsection ops: preserve the original chapter-scoped guard.
    if (
        state.find_node(
            "section", _ts, scope_kind=IRNodeKind.CHAPTER.value if _target_chapter else None, scope_label=_target_chapter
        )
        is not None
    ):
        return None
    if muutos_ir is None or muutos_ir.kind is not IRNodeKind.SECTION:
        return None
    if not muutos_ir.label:
        return None
    if not _target_chapter and not _target_paragraph and not _target_item and not _target_special:
        return None

    payload_label = muutos_ir.label
    target_label = _norm_num_token(_ts)

    if not _target_paragraph and not _target_item and not _target_special:
        if payload_label != target_label:
            m = re.fullmatch(r"(\d+)([a-z])", target_label, flags=re.I)
            if m is None or payload_label != m.group(1):
                return None

    if _target_paragraph or _target_item:
        m = re.fullmatch(r"(\d+)([a-z])", target_label, flags=re.I)
        if payload_label != target_label and (m is None or payload_label not in {m.group(1), target_label}):
            return None

    if _target_chapter and _same_norm_label(muutos_ir.label, _ts):
        label_norm = _tops._norm(_ts)
        matches = state.provision_index.get(("section", label_norm), [])
        root_matches = [
            _tops._as_path(path)
            for path in matches
            if not any(kind == "chapter" for kind, _label in _tops._as_path(path))
        ]
        if len(root_matches) == 1:
            existing_path = root_matches[0]
            if _scope_confidence is not None and _scope_confidence.source == "carry_forward":
                logger.debug(
                    "  %s → rejected section materialization via root move for carry-forward chapter scope",
                    ctx_label,
                )
                return None
            existing_node = _tops.resolve(state.ir, existing_path)
            moved_section_ir = muutos_ir
            payload_is_heading_only = all(
                child.kind in {IRNodeKind.NUM, IRNodeKind.HEADING, IRNodeKind.OMISSION}
                for child in muutos_ir.children
            )
            if existing_node is not None and payload_is_heading_only:
                amend_heading = next((child for child in muutos_ir.children if child.kind is IRNodeKind.HEADING), None)
                if amend_heading is not None:
                    new_children: list[IRNode] = []
                    heading_placed = False
                    for child in existing_node.children:
                        if child.kind is IRNodeKind.HEADING:
                            if not heading_placed:
                                new_children.append(amend_heading)
                                heading_placed = True
                        else:
                            new_children.append(child)
                    if not heading_placed:
                        insert_at = next(
                            (idx for idx, child in enumerate(new_children) if child.kind is not IRNodeKind.NUM),
                            len(new_children),
                        )
                        new_children.insert(insert_at, amend_heading)
                    moved_section_ir = IRNode(
                        kind=existing_node.kind,
                        label=existing_node.label,
                        text=existing_node.text,
                        attrs=existing_node.attrs,
                        children=tuple(new_children),
                    )
            logger.debug("  %s → section materialized via root move to %s", ctx_label, _target_chapter)
            if source_pathologies_out is not None:
                source_pathologies_out.append(
                    build_destructive_shape_loss_risk_pathology(
                        source_statute=view.source_statute or "",
                        target_unit_kind=_target_unit_kind,
                        target_label=f"{_ts} §",
                        recovery_kind="section_materialization_root_move_destination_rebind",
                        live_sibling_count=len(
                            [c for c in (existing_node.children if existing_node is not None else ()) if c.kind is IRNodeKind.SUBSECTION]
                        ),
                        payload_sibling_count=len(
                            [c for c in moved_section_ir.children if c.kind is IRNodeKind.SUBSECTION]
                        ),
                    )
                )
            moved_parent_path = _tops._as_path(
                _find_insert_parent_path(state.ir, _target_chapter, label_index=state.provision_index)
            )
            moved_ir = _move_section_payload_to_target_chapter(
                state.ir,
                existing_path,
                _target_chapter,
                moved_section_ir,
                source_pathologies_out=source_pathologies_out,
            )
            if migration_ledger is not None:
                from_path = tuple(
                    step
                    for step in existing_path
                    if step[0] in {"part", "chapter", "section", "subsection", "item"}
                )
                to_path = tuple(
                    step
                    for step in (moved_parent_path + (("section", moved_section_ir.label or ""),))
                    if step[0] in {"part", "chapter", "section", "subsection", "item"}
                )
                migration_ledger.record_move(
                    LegalAddress(path=from_path),
                    LegalAddress(path=to_path),
                    effective=(
                        op.resolved_op_source.effective
                        if isinstance(op, ResolvedOp) and op.resolved_op_source is not None
                        else ""
                    ),
                    source_statute=view.source_statute or "",
                )
            return state.with_ir(moved_ir)

    parent_path = _tops._as_path(_find_insert_parent_path(state.ir, _target_chapter, label_index=state.provision_index))
    logger.debug("  %s → section materialized", ctx_label)
    if source_pathologies_out is not None:
        source_pathologies_out.append(
            build_destructive_shape_loss_risk_pathology(
                source_statute=view.source_statute or "",
                target_unit_kind=_target_unit_kind,
                target_label=f"{_ts} §",
                recovery_kind="section_materialization_scoped_insert",
                live_sibling_count=len(
                    [c for c in state.ir.children if c.kind is IRNodeKind.SECTION and c.label == _ts]
                ),
                payload_sibling_count=len([c for c in muutos_ir.children if c.kind is IRNodeKind.SUBSECTION]),
            )
        )
    same_path = _parent_direct_child_path_with_same_label(
        state.ir,
        parent_path,
        kind=muutos_ir.kind,
        label=muutos_ir.label or "",
    )
    if same_path is not None:
        existing_node = _tops.resolve(state.ir, same_path)
        if existing_node is not None and muutos_ir.kind is IRNodeKind.SECTION:
            muutos_ir = _prepare_section_root_payload_for_replay(
                muutos_ir,
                live_sec=existing_node,
                rop=rop,
                view=view,
            )
    new_ir, replaced = _insert_or_replace_same_labeled_child(state.ir, parent_path, muutos_ir)
    if replaced and source_pathologies_out is not None:
        parent_node = _tops.resolve(new_ir, parent_path)
        source_pathologies_out.append(
            build_destructive_shape_loss_risk_pathology(
                source_statute=view.source_statute or "",
                target_unit_kind=_target_unit_kind,
                target_label=f"{_ts} §",
                recovery_kind="section_materialization_scoped_insert_same_label_replace",
                live_sibling_count=len(
                    [c for c in (parent_node.children if parent_node is not None else ()) if c.kind is IRNodeKind.SECTION]
                ),
                payload_sibling_count=1,
            )
        )
    return state.with_ir(new_ir)


def _normalize_subsection_target_hint_ir(
    op: AmendmentOp | ResolvedOp,
    master_subsecs: List[IRNode],
    amend_sub_ir: Optional[IRNode],
    ctx_label: str,
) -> AmendmentOp | ResolvedOp:
    """Normalize subsection target hint using IRNode data."""
    target_paragraph = op.effective_target_paragraph if isinstance(op, ResolvedOp) else op.target_paragraph
    target_item = op.effective_target_item_label if isinstance(op, ResolvedOp) else op.target_item
    if not target_paragraph or not master_subsecs:
        return op
    if target_paragraph > len(master_subsecs) and len(master_subsecs) == 1 and not target_item:
        if amend_sub_ir is not None:
            amend_paragraphs = [c for c in amend_sub_ir.children if c.kind is IRNodeKind.PARAGRAPH]
            # Keep true subsection payloads on the subsection lane. Only
            # reinterpret as an item fallback when the sparse payload itself
            # looks item-like rather than a whole inserted moment.
            if any(c.kind is IRNodeKind.INTRO for c in amend_sub_ir.children) or len(amend_paragraphs) != 1:
                return op
        sub = master_subsecs[0]
        if any(c.kind is IRNodeKind.PARAGRAPH for c in sub.children):
            logger.debug(
                "  %s → subsection hint: reinterpret mom %s as item in single subsection",
                ctx_label,
                target_paragraph,
            )
            if isinstance(op, ResolvedOp):
                return _rebind_resolved_target_address(
                    op,
                    target_paragraph=1,
                    target_item=str(target_paragraph),
                    target_special=None,
                )
            new_lo = _lo_with_path_update(op.lo, subsection="1", item=str(target_paragraph)) if op.lo else None
            updated_legacy = dc_replace(
                op,
                target_paragraph=1,
                target_item=str(target_paragraph),
                lo=new_lo,
            )
            return updated_legacy
    return op
