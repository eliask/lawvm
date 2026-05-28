"""Merge and omission-resolution functions for the Finnish amendment pipeline.

All functions operate on IRNode trees.  Group A functions have no XMLStatute
dependency at all; Group B functions reference XMLStatute only under
TYPE_CHECKING so there is no circular import with grafter.py.

grafter.py re-exports every public symbol here for backward compatibility.

CONSTITUTION: No ambient master access in this module.
All live-state reads go through PayloadElaborationContext.
See notes/LAWVM_CONSTITUTION.md §3 (Phase Ownership Rules).

Typed merge operator architecture (PRO_RESPONSE05 §6, PRO_RESPONSE4_1 Query 2):

    Every merge is classified by a ReplaceMode that determines the invariant
    contract.  After each merge, ``validate_merge_invariants`` checks:

    1. No omission markers survive in the output tree.
    2. If replace_mode != omission_merge but omission markers found, emit violation.
    3. All payload descendants expected by contract appear in result.
    4. Preserved residue is explicitly tracked (labels kept vs labels replaced).

    Merge functions emit structured ``MergeEvent`` metadata describing what
    labels were preserved, which came from the payload, which omission slots
    were expanded, and whether trailing-omission dedup fired.

    TODO: upstream ReplaceMode into ExecutionContract.coverage once the
    canonical intent migration reaches Step 2 (apply reads typed contract).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Dict, List, Literal, Optional, Set, Tuple, TYPE_CHECKING, cast

from lawvm.core.compile_result import SourcePathology
from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.payload_surface import TargetUnitKind
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import normalized_label_key
from lawvm.finland.helpers import (
    _is_omission_ir,
    _section_sort_key,
    _previous_item_token,
)
from lawvm.finland.ops import AmendmentOp, FailedOp, ReplayProfile, ResolvedOp
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.source_pathology import (
    build_malformed_broad_replace_body_pathology,
    build_partial_whole_section_payload_pathology,
)

if TYPE_CHECKING:
    from lawvm.core.elaboration_context import PayloadElaborationContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed merge operator — ReplaceMode, MergeEvent, invariant checks
# ---------------------------------------------------------------------------


class ReplaceMode(StrEnum):
    """Typed replace contract for merge operations.

    Determines which invariant contract applies after a merge.
    See PRO_RESPONSE05 §6 and PRO_RESPONSE4_1 Query 2.
    """

    EXACT_REPLACE = "exact_replace"
    OMISSION_MERGE = "omission_merge"
    SPARSE_MERGE = "sparse_merge"
    PLACEHOLDER_REPLACE = "placeholder_replace"


@dataclass(frozen=True)
class MergeInvariantViolation:
    """A single invariant violation detected after a merge operation."""

    code: str
    severity: Literal["hard", "warning", "signal"]
    message: str
    detail: dict[str, object] = field(default_factory=dict)


@dataclass
class MergeEvent:
    """Structured metadata emitted by each merge operation.

    Captures what happened during the merge so that downstream layers
    can audit, diff, and diagnose without re-inspecting the tree.
    """

    replace_mode: ReplaceMode
    preserved_labels: Tuple[str, ...] = ()
    payload_labels: Tuple[str, ...] = ()
    omission_slots_expanded: int = 0
    master_items_per_omission_slot: Tuple[int, ...] = ()
    trailing_omission_dedup_fired: bool = False
    violations: Tuple[MergeInvariantViolation, ...] = ()

    @property
    def has_violations(self) -> bool:
        return len(self.violations) > 0

    @property
    def hard_violations(self) -> Tuple[MergeInvariantViolation, ...]:
        return tuple(v for v in self.violations if v.severity == "hard")


def _collect_labels(node: IRNode) -> List[str]:
    """Collect all non-empty labels from addressable children of a node."""
    labels: List[str] = []
    for child in node.children:
        if child.label:
            labels.append(child.label)
    return labels


def _tree_has_omission(node: IRNode) -> bool:
    """Recursively check if any descendant is an omission marker."""
    if _is_omission_ir(node):
        return True
    return any(_tree_has_omission(c) for c in node.children)


def validate_merge_invariants(
    result: IRNode,
    master: IRNode,
    payload: IRNode,
    replace_mode: ReplaceMode,
    *,
    source_statute: str = "",
    op_id: str = "",
) -> Tuple[MergeInvariantViolation, ...]:
    """Validate post-merge invariants and return any violations.

    Invariants checked (from PRO_RESPONSE4_1 Query 2):

    1. No omission markers survive in the output tree.
    2. If replace_mode != omission_merge but omission markers found, hard fail.
    3. All payload descendants expected by contract appear in result.
    4. Preserved residue is explicitly tracked.
    """
    violations: List[MergeInvariantViolation] = []

    # Invariant 1 & 2: No omission markers survive
    has_surviving_omission = _tree_has_omission(result)
    if has_surviving_omission:
        if replace_mode != ReplaceMode.OMISSION_MERGE:
            violations.append(
                MergeInvariantViolation(
                    code="OMISSION_SURVIVES_NON_MERGE",
                    severity="hard",
                    message=(
                        f"Omission marker survives in output tree but replace_mode="
                        f"{replace_mode.value}; expected no omissions outside omission_merge"
                    ),
                    detail={"source_statute": source_statute, "op_id": op_id},
                )
            )
        else:
            violations.append(
                MergeInvariantViolation(
                    code="OMISSION_SURVIVES_MERGE",
                    severity="hard",
                    message="Omission marker survives in output tree after omission merge",
                    detail={"source_statute": source_statute, "op_id": op_id},
                )
            )

    # Invariant 3: All payload addressable descendants appear in result
    # (only for omission_merge and sparse_merge where payload is partial)
    if replace_mode in (ReplaceMode.OMISSION_MERGE, ReplaceMode.SPARSE_MERGE):
        payload_labels = {c.label for c in payload.children if c.label and not _is_omission_ir(c)}
        result_labels = {c.label for c in result.children if c.label}
        missing = payload_labels - result_labels
        if missing:
            violations.append(
                MergeInvariantViolation(
                    code="PAYLOAD_DESCENDANTS_MISSING",
                    severity="hard",
                    message=(f"Payload descendants missing from merge result: {sorted(missing)}"),
                    detail={
                        "missing_labels": sorted(missing),
                        "source_statute": source_statute,
                        "op_id": op_id,
                    },
                )
            )

    return tuple(violations)


def build_merge_event(
    result: IRNode,
    master: IRNode,
    payload: IRNode,
    replace_mode: ReplaceMode,
    *,
    omission_slots_expanded: int = 0,
    master_items_per_omission_slot: Tuple[int, ...] = (),
    trailing_omission_dedup_fired: bool = False,
    source_statute: str = "",
    op_id: str = "",
) -> MergeEvent:
    """Build a MergeEvent with invariant validation for a completed merge.

    This is the single entry point for constructing merge metadata with
    invariant checks.
    """
    master_labels = set(_collect_labels(master))
    payload_labels_set = {c.label for c in payload.children if c.label and not _is_omission_ir(c)}
    result_labels = _collect_labels(result)

    preserved = tuple(l for l in result_labels if l in master_labels and l not in payload_labels_set)
    from_payload = tuple(l for l in result_labels if l in payload_labels_set)

    violations = validate_merge_invariants(
        result,
        master,
        payload,
        replace_mode,
        source_statute=source_statute,
        op_id=op_id,
    )

    if violations:
        for v in violations:
            logger.warning(
                "MERGE_INVARIANT_VIOLATION [%s] %s: %s",
                v.code,
                v.severity,
                v.message,
            )

    return MergeEvent(
        replace_mode=replace_mode,
        preserved_labels=preserved,
        payload_labels=from_payload,
        omission_slots_expanded=omission_slots_expanded,
        master_items_per_omission_slot=master_items_per_omission_slot,
        trailing_omission_dedup_fired=trailing_omission_dedup_fired,
        violations=violations,
    )


@dataclass
class MergeResult:
    """Result of a typed merge operation: the merged tree plus structured metadata.

    Callers that need only the IRNode can access ``.node``; callers that need
    audit metadata can inspect ``.event``.
    """

    node: IRNode
    event: MergeEvent


def merge_section_with_invariants(
    master_sec: IRNode,
    amend_sec: IRNode,
    *,
    source_statute: str = "",
    op_id: str = "",
) -> Optional[MergeResult]:
    """High-level typed merge: section-level omission merge with invariant checks.

    Wraps ``_merge_section_with_omission_ir`` with structured event emission
    and post-merge invariant validation.  Returns None when the underlying
    merge returns None (no omission markers present).
    """
    result = _merge_section_with_omission_ir(master_sec, amend_sec)
    if result is None:
        return None

    # Count omission slots expanded and items per slot
    amend_slots = [c for c in amend_sec.children if c.kind is IRNodeKind.SUBSECTION or _is_omission_ir(c)]
    omission_count = sum(1 for c in amend_slots if _is_omission_ir(c))
    master_subsecs = [c for c in master_sec.children if c.kind is IRNodeKind.SUBSECTION]

    # Rough per-slot count: total master subs minus amendment real subs,
    # divided across omission slots
    amend_real = sum(1 for c in amend_slots if c.kind is IRNodeKind.SUBSECTION)
    total_expanded = max(0, len(master_subsecs) - amend_real)
    per_slot = (
        tuple([total_expanded] if omission_count == 1 else [total_expanded // omission_count] * omission_count)
        if omission_count > 0
        else ()
    )

    event = build_merge_event(
        result,
        master_sec,
        amend_sec,
        ReplaceMode.OMISSION_MERGE,
        omission_slots_expanded=omission_count,
        master_items_per_omission_slot=per_slot,
        source_statute=source_statute,
        op_id=op_id,
    )
    return MergeResult(node=result, event=event)


def merge_subsection_with_invariants(
    master_sub: IRNode,
    amend_sub: IRNode,
    *,
    source_statute: str = "",
    op_id: str = "",
) -> Optional[MergeResult]:
    """High-level typed merge: subsection-level omission merge with invariant checks.

    Wraps ``_merge_subsection_with_omission_ir`` with structured event emission
    and post-merge invariant validation.  Returns None when the underlying
    merge returns None (no omission markers present).
    """
    result = _merge_subsection_with_omission_ir(master_sub, amend_sub)
    if result is None:
        return None

    # Detect trailing-omission dedup
    children = amend_sub.children
    omission_idx = next(
        (i for i, child in enumerate(children) if _is_omission_ir(child)),
        None,
    )
    trailing_dedup = False
    if omission_idx is not None:
        trailing = children[omission_idx + 1 :]
        if not trailing and omission_idx == len(children) - 1 and omission_idx > 0:
            # Check if dedup would have fired
            master_children = master_sub.children
            splice_start = omission_idx
            if splice_start < len(master_children):

                def _strip_num_prefix(s: str) -> str:
                    return re.sub(r"^\d+[a-z]?\s*[\)\.]\s*", "", s, count=1, flags=re.I)

                last_amend_text = " ".join(irnode_to_text(children[omission_idx - 1]).split())
                first_splice_text = " ".join(irnode_to_text(master_children[splice_start]).split())
                if last_amend_text and _strip_num_prefix(last_amend_text) == _strip_num_prefix(first_splice_text):
                    trailing_dedup = True

    event = build_merge_event(
        result,
        master_sub,
        amend_sub,
        ReplaceMode.OMISSION_MERGE,
        omission_slots_expanded=1,
        trailing_omission_dedup_fired=trailing_dedup,
        source_statute=source_statute,
        op_id=op_id,
    )
    return MergeResult(node=result, event=event)


def merge_container_with_invariants(
    master_node: IRNode,
    amend_node: IRNode,
    *,
    source_statute: str = "",
    op_id: str = "",
) -> MergeResult:
    """High-level typed merge: container insert-merge with invariant checks.

    Wraps ``_merge_same_numbered_container_insert_ir`` with structured event
    emission and post-merge invariant validation.
    """
    result = _merge_same_numbered_container_insert_ir(master_node, amend_node)
    if result is None:
        violation = MergeInvariantViolation(
            code="DUPLICATE_SECTION_LABELS",
            severity="hard",
            message="Duplicate SECTION labels after same-numbered container insert",
            detail={"source_statute": source_statute, "op_id": op_id},
        )
        return MergeResult(
            node=master_node,
            event=MergeEvent(replace_mode=ReplaceMode.SPARSE_MERGE, violations=(violation,)),
        )

    event = build_merge_event(
        result,
        master_node,
        amend_node,
        ReplaceMode.SPARSE_MERGE,
        source_statute=source_statute,
        op_id=op_id,
    )
    return MergeResult(node=result, event=event)


def merge_sparse_section_with_invariants(
    master_sec: IRNode,
    amend_sec: IRNode,
    *,
    source_statute: str = "",
    op_id: str = "",
) -> Optional[MergeResult]:
    """High-level typed merge: sparse item section replace with invariant checks.

    Wraps ``_sparse_item_section_replace_merge_ir`` with structured event
    emission and post-merge invariant validation.  Returns None when the
    underlying merge returns None.
    """
    result = _sparse_item_section_replace_merge_ir(master_sec, amend_sec)
    if result is None:
        return None

    event = build_merge_event(
        result,
        master_sec,
        amend_sec,
        ReplaceMode.SPARSE_MERGE,
        source_statute=source_statute,
        op_id=op_id,
    )
    return MergeResult(node=result, event=event)


# ---------------------------------------------------------------------------
# Group A: pure IRNode transforms (no XMLStatute dependency)
# ---------------------------------------------------------------------------


def _has_section_omissions_ir(sec: IRNode) -> bool:
    """Check if IRNode section has omission markers at any depth."""

    def _check(node: IRNode) -> bool:
        if _is_omission_ir(node):
            return True
        return any(_check(c) for c in node.children)

    return any(_check(c) for c in sec.children)


def _pre_omission_is_context_carried(pre_omission_children: tuple) -> bool:
    """Return True if the pre-omission slice is johdantokappale context (CONTEXT_CARRIED).

    Per drafting-guide rules (Lainkirjoittajan opas §14-5), when items of a
    subsection are changed the preceding johdantokappale is included in the
    amendment body even if it is UNCHANGED.  Such context-carried nodes must
    NOT overwrite prior law.

    A pre-omission slice is classified as CONTEXT_CARRIED when ALL nodes in
    the slice are unlabeled structural nodes (kind in intro, content, p) — i.e.
    they carry no labeled address and therefore cannot be claimed by a specific
    clause target.  Labeled paragraphs ARE potentially new law and fall through
    to the normal CANDIDATE_PAYLOAD path.
    """
    if not pre_omission_children:
        return False
    context_kinds = {IRNodeKind.INTRO, IRNodeKind.CONTENT, IRNodeKind.PARAGRAPH}
    return all(
        c.kind in context_kinds and not c.label
        for c in pre_omission_children
    )


def _merge_subsection_with_omission_ir(master_sub: IRNode, amend_sub: IRNode) -> Optional[IRNode]:
    """IRNode version: merge amendment subsection with omission markers.

    Handles two layouts:
    - CONTEXT_CARRIED pre-omission: intro/content nodes before omission are
      johdantokappale context (unchanged, per Lainkirjoittajan opas §14-5).
      Replace them with master's equivalent pre-omission content so the
      context text from the amendment body does NOT overwrite prior law.
    - CANDIDATE_PAYLOAD pre-omission: labeled paragraph nodes before omission
      are treated as genuine new law (existing behaviour).
    """
    children = amend_sub.children
    omission_idx = next(
        (i for i, child in enumerate(children) if _is_omission_ir(child)),
        None,
    )
    if omission_idx is None:
        return None

    trailing = children[omission_idx + 1 :]
    master_children = master_sub.children

    pre_omission = children[:omission_idx]

    # Omission-aware payload claim: if all pre-omission nodes are unlabeled
    # intro/content (johdantokappale context), treat them as CONTEXT_CARRIED
    # and use master's pre-omission content instead (PRO_RESPONSE_5_1 §5).
    if trailing and _pre_omission_is_context_carried(pre_omission):
        # Use master's pre-omission content (the real, unchanged law) rather
        # than the amendment's context text.
        new_children = list(master_children[:omission_idx])
    else:
        new_children = list(pre_omission)

    if trailing:
        suffix = [c for c in master_children[omission_idx:] if not _is_omission_ir(c)]
        suffix_by_label: Dict[str, int] = {}
        for idx, child in enumerate(suffix):
            if not child.label:
                continue
            if child.label in suffix_by_label:
                logger.warning("MERGE_DUPLICATE_PARAGRAPH_LABELS [%s]", child.label)
                return None
            suffix_by_label[child.label] = idx
        seen_trailing_labels: Set[str] = set()
        for child in trailing:
            if not child.label:
                suffix.append(child)
                continue
            if child.label in seen_trailing_labels:
                logger.warning("MERGE_DUPLICATE_PARAGRAPH_LABELS [%s]", child.label)
                return None
            seen_trailing_labels.add(child.label)
            if child.label in suffix_by_label:
                suffix[suffix_by_label[child.label]] = child
            else:
                suffix.append(child)
        new_children.extend(suffix)
    else:
        splice_start = omission_idx
        if omission_idx == len(children) - 1 and omission_idx > 0 and splice_start < len(master_children):

            def _strip_num_prefix(s: str) -> str:
                """Remove leading item number like '7)' or '8.' from text."""
                return re.sub(r"^\d+[a-z]?\s*[\)\.]\s*", "", s, count=1, flags=re.I)

            last_amend_child = children[omission_idx - 1]
            first_splice_child = master_children[splice_start]
            amend_label = normalized_label_key(last_amend_child.label)
            splice_label = normalized_label_key(first_splice_child.label)
            if (
                last_amend_child.kind is first_splice_child.kind
                and amend_label.isdigit()
                and splice_label.isdigit()
                and int(splice_label) == int(amend_label) + 1
            ):
                last_amend_text = " ".join(irnode_to_text(last_amend_child).split())
                first_splice_text = " ".join(irnode_to_text(first_splice_child).split())
                if last_amend_text and _strip_num_prefix(last_amend_text) == _strip_num_prefix(first_splice_text):
                    splice_start += 1
        suffix = [c for c in master_children[splice_start:] if not _is_omission_ir(c)]
        suffix_seen_labels: Set[str] = set()
        for child in suffix:
            if not child.label:
                continue
            if child.label in suffix_seen_labels:
                logger.warning("MERGE_DUPLICATE_PARAGRAPH_LABELS [%s]", child.label)
                return None
            suffix_seen_labels.add(child.label)
        amend_labels = {child.label for child in new_children if child.label}
        new_children.extend(child for child in suffix if not child.label or child.label not in amend_labels)

    return _tops._with_children(amend_sub, new_children)


def _merge_subsection_accumulate_inner_omission_ir(master_sub: IRNode, amend_sub: IRNode) -> Optional[IRNode]:
    """IRNode version: merge subsection with internal omission between paragraphs."""
    children = amend_sub.children
    omission_idx = next(
        (i for i, child in enumerate(children) if _is_omission_ir(child)),
        None,
    )
    if omission_idx is None:
        return None

    trailing = [c for c in children[omission_idx + 1 :] if not _is_omission_ir(c)]
    if not trailing:
        return None

    trailing_para_map: Dict[str, IRNode] = {}
    trailing_non_para: List[IRNode] = []
    for child in trailing:
        if child.kind is IRNodeKind.PARAGRAPH and child.label:
            trailing_para_map[child.label] = child
        else:
            trailing_non_para.append(child)

    if not trailing_para_map:
        new_children = list(children[:omission_idx]) + trailing
        return _tops._with_children(amend_sub, new_children)

    new_children = list(children[:omission_idx])
    replaced_labels: Set[str] = set()
    for child in master_sub.children[omission_idx:]:
        if child.kind is IRNodeKind.PARAGRAPH and child.label and child.label in trailing_para_map:
            new_children.append(trailing_para_map[child.label])
            replaced_labels.add(child.label)
        else:
            new_children.append(child)

    for label, child in trailing_para_map.items():
        if label not in replaced_labels:
            new_children.append(child)

    new_children.extend(trailing_non_para)
    return _tops._with_children(amend_sub, new_children)


def _mark_targeted_subsections_in_place(
    section: IRNode,
    target_paragraphs: set,
) -> IRNode:
    """Return *section* with each targeted subsection tagged lawvm_in_place_merge="1".

    Used when a whole-section INSERT merged payload contains subsections that
    should be replaced in-place (not renumbered) at apply-time.  The
    *target_paragraphs* set contains the (integer) paragraph numbers that are
    targeted by item-level ops in the group; those subsection nodes are marked.
    """
    new_children = list(section.children)
    changed = False
    for i, child in enumerate(new_children):
        if child.kind is not IRNodeKind.SUBSECTION or not child.label:
            continue
        try:
            child_num = int(child.label)
        except (ValueError, TypeError):
            continue
        if child_num in target_paragraphs and child.attrs.get("lawvm_in_place_merge") != "1":
            new_children[i] = IRNode(
                kind=child.kind,
                label=child.label,
                text=child.text,
                attrs={**dict(child.attrs), "lawvm_in_place_merge": "1"},
                children=child.children,
            )
            changed = True
    if not changed:
        return section
    return _tops._with_children(section, new_children)


def _merge_section_inner_subsection_omission_ir(
    master_sec: IRNode,
    amend_sec: IRNode,
    *,
    mark_in_place: bool = False,
) -> Optional[IRNode]:
    """IRNode version: merge section when amendment subsections have inner omissions.

    When *mark_in_place* is True the merged subsection nodes are tagged with
    ``lawvm_in_place_merge="1"`` so that :func:`_apply_subsection_insert` knows
    to replace the existing subsection rather than shifting it upward (renumber).
    Use this when the amendment is inserting items *into* an existing subsection,
    not inserting an entirely new subsection.
    """
    amend_subsecs = [c for c in amend_sec.children if c.kind is IRNodeKind.SUBSECTION]
    master_subsecs = [c for c in master_sec.children if c.kind is IRNodeKind.SUBSECTION]

    has_inner = any(any(_is_omission_ir(gc) for gc in sub.children) for sub in amend_subsecs)
    if not has_inner:
        return None

    # Start from master, overlay amendment subsections
    new_children = list(master_sec.children)
    # Replace heading if amendment provides one
    amend_heading = next((c for c in amend_sec.children if c.kind is IRNodeKind.HEADING), None)
    if amend_heading is not None:
        new_children = [amend_heading if c.kind is IRNodeKind.HEADING else c for c in new_children]

    # Replace master subsections with merged versions
    master_sub_indices = [i for i, c in enumerate(new_children) if c.kind is IRNodeKind.SUBSECTION]
    for j, amend_sub in enumerate(amend_subsecs):
        if j >= len(master_sub_indices):
            break
        idx = master_sub_indices[j]
        has_sub_omission = any(_is_omission_ir(gc) for gc in amend_sub.children)
        if has_sub_omission:
            omission_idx = next((i for i, gc in enumerate(amend_sub.children) if _is_omission_ir(gc)), None)
            trailing = (
                []
                if omission_idx is None
                else [gc for gc in amend_sub.children[omission_idx + 1 :] if not _is_omission_ir(gc)]
            )
            if omission_idx is not None and not trailing:
                # Section-shell family: subsection-level trailing omission means
                # "preserve later subsections of the section", not "splice the
                # old tail back into this replaced subsection". This keeps
                # 1979/864 §6 correct without regressing whole-subsection
                # replaces like 2002/973 §9 mom 1.
                stripped_children = [gc for gc in amend_sub.children if not _is_omission_ir(gc)]
                result = _tops._with_children(amend_sub, stripped_children)
            else:
                result = _merge_subsection_accumulate_inner_omission_ir(master_subsecs[j], amend_sub)
                if result is None:
                    result = _merge_subsection_with_omission_ir(master_subsecs[j], amend_sub)
            if result is not None:
                if mark_in_place and result.attrs.get("lawvm_in_place_merge") != "1":
                    result = IRNode(
                        kind=result.kind,
                        label=result.label,
                        text=result.text,
                        attrs={**dict(result.attrs), "lawvm_in_place_merge": "1"},
                        children=result.children,
                    )
                new_children[idx] = result
        else:
            new_children[idx] = amend_sub

    return _tops._with_children(master_sec, new_children)

def _strip_leading_text_prefix(text: str, prefix: str) -> Optional[str]:
    """Strip a whitespace-flexible prefix from text if it matches exactly.

    This is intentionally conservative: it only removes a prefix when the
    normalized amendment text matches the start of the preserved text.  That
    lets sparse section merges split carried prose back into a later subsection
    without inventing statute-specific handling or broad text similarity logic.
    """
    norm_prefix = " ".join(prefix.split())
    if not norm_prefix:
        return None
    pattern = r"^\s*" + r"\s+".join(re.escape(part) for part in norm_prefix.split())
    match = re.match(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    remainder = text[match.end():].lstrip()
    return remainder


def _relabel_subsection(child: IRNode, label: str) -> IRNode:
    if child.label == label:
        return child
    return IRNode(
        kind=child.kind,
        label=label,
        text=child.text,
        attrs=dict(child.attrs),
        children=tuple(child.children),
    )


def _renumber_subsections_in_order(children: List[IRNode]) -> List[IRNode]:
    result: List[IRNode] = []
    next_label = 1
    for child in children:
        if child.kind is IRNodeKind.SUBSECTION:
            result.append(_relabel_subsection(child, str(next_label)))
            next_label += 1
        else:
            result.append(child)
    return result


def _planned_section_subsection_targets(
    group_ops: List[AmendmentOp],
    explicit_subsection_count: int,
    *,
    has_trailing_omission: bool,
) -> List[Tuple[Literal["REPLACE", "INSERT"], int]] | None:
    plain_ops = [
        op
        for op in group_ops
        if op.target_paragraph is not None
        and not op.target_item
        and not op.target_special
        and op.op_type in ("REPLACE", "INSERT")
    ]
    if not plain_ops:
        return None

    direct_plan: List[Tuple[Literal["REPLACE", "INSERT"], int]] = [
        (cast(Literal["REPLACE", "INSERT"], op.op_type), int(op.target_paragraph or 0))
        for op in plain_ops
    ]
    if len(direct_plan) == explicit_subsection_count:
        return direct_plan

    # Some "uusi 1 momentti, jolloin muutettu 1 momentti ja nykyinen 2 momentti..."
    # shapes only surface the true INSERT in johtolause. The remaining carried
    # subsection payload still belongs to the immediately following shifted slots.
    if (
        has_trailing_omission
        and len(direct_plan) < explicit_subsection_count
        and all(op_type == "INSERT" for op_type, _ in direct_plan)
    ):
        expected_prefix = list(range(1, len(direct_plan) + 1))
        actual_prefix = [target for _op_type, target in direct_plan]
        if actual_prefix == expected_prefix:
            plan = list(direct_plan)
            next_target = len(plan) + 1
            while len(plan) < explicit_subsection_count:
                plan.append(("REPLACE", next_target))
                next_target += 1
            return plan

    return None


def _merge_section_with_targeted_ops_ir(
    master_sec: IRNode,
    amend_sec: IRNode,
    *,
    group_ops: List[AmendmentOp],
) -> Optional[IRNode]:
    master_subsecs = [c for c in master_sec.children if c.kind is IRNodeKind.SUBSECTION]
    amend_subsecs = [c for c in amend_sec.children if c.kind is IRNodeKind.SUBSECTION]
    has_trailing_omission = bool(amend_sec.children and _is_omission_ir(amend_sec.children[-1]))
    plan = _planned_section_subsection_targets(
        group_ops,
        len(amend_subsecs),
        has_trailing_omission=has_trailing_omission,
    )
    if plan is None:
        return None

    prefix_children = [
        c
        for c in amend_sec.children
        if c.kind is not IRNodeKind.SUBSECTION and not _is_omission_ir(c)
    ]
    explicit_repeal_targets = {
        int(op.target_paragraph)
        for op in group_ops
        if (
            op.target_paragraph is not None
            and not op.target_item
            and not op.target_special
            and op.op_type == "REPEAL"
        )
    }
    preserve_sparse_labels = bool(explicit_repeal_targets)
    merged_subsecs: List[IRNode] = []
    master_idx = 0
    inserts_so_far = 0

    def _master_subsection_is_repealed(subsection: IRNode) -> bool:
        if subsection.label is None or not str(subsection.label).isdigit():
            return False
        return int(str(subsection.label)) in explicit_repeal_targets

    def _relabel_to_target(child: IRNode, target_paragraph: int) -> IRNode:
        desired_label = str(target_paragraph)
        if child.label == desired_label:
            return child
        return _relabel_subsection(child, desired_label)

    for amend_sub, (op_type, target_paragraph) in zip(amend_subsecs, plan, strict=False):
        target_idx = max(0, target_paragraph - 1 - inserts_so_far)
        while master_idx < min(target_idx, len(master_subsecs)):
            master_sub = master_subsecs[master_idx]
            if not _master_subsection_is_repealed(master_sub):
                merged_subsecs.append(master_sub)
            master_idx += 1

        if op_type == "INSERT":
            merged_subsecs.append(
                _relabel_to_target(amend_sub, target_paragraph) if preserve_sparse_labels else amend_sub
            )
            inserts_so_far += 1
            continue

        if master_idx < len(master_subsecs):
            merged_subsecs.append(
                _relabel_to_target(amend_sub, target_paragraph) if preserve_sparse_labels else amend_sub
            )
            master_idx += 1
        else:
            merged_subsecs.append(
                _relabel_to_target(amend_sub, target_paragraph) if preserve_sparse_labels else amend_sub
            )

    if master_idx < len(master_subsecs) and amend_subsecs and not has_trailing_omission:
        # Only consider skipping the immediately-following master subsection when
        # there is NO trailing omission in the amendment body.  A trailing omission
        # explicitly instructs "preserve all following master subsections here"; in
        # that case every remaining master slot must be kept, not heuristically
        # skipped as a possible redundant johdantokappale tail.
        trailing_master = master_subsecs[master_idx]
        last_amend_sub = amend_subsecs[-1]
        trailing_text = " ".join(irnode_to_text(trailing_master).split())
        if (
            trailing_master.kind is IRNodeKind.SUBSECTION
            and not any(child.kind is IRNodeKind.PARAGRAPH for child in trailing_master.children)
            and trailing_text
            and trailing_text[:1].isalpha()
            and trailing_text[:1].isupper()
            and any(
                child.kind in (IRNodeKind.CONTENT, IRNodeKind.INTRO) and irnode_to_text(child).strip()
                for child in last_amend_sub.children
            )
        ):
            master_idx += 1

    while master_idx < len(master_subsecs):
        master_sub = master_subsecs[master_idx]
        if not _master_subsection_is_repealed(master_sub):
            merged_subsecs.append(master_sub)
        master_idx += 1

    merged_children = merged_subsecs if preserve_sparse_labels else _renumber_subsections_in_order(merged_subsecs)
    return _tops._with_children(amend_sec, prefix_children + merged_children)


def _merge_section_with_omission_ir(
    master_sec: IRNode,
    amend_sec: IRNode,
    *,
    group_ops: List[AmendmentOp] | None = None,
) -> Optional[IRNode]:
    """IRNode version: merge amendment section with omission markers against master."""
    has_omission = any(_is_omission_ir(c) for c in amend_sec.children)
    if group_ops:
        targeted = _merge_section_with_targeted_ops_ir(master_sec, amend_sec, group_ops=group_ops)
        if targeted is not None:
            return targeted

    if not has_omission:
        return _merge_section_inner_subsection_omission_ir(master_sec, amend_sec)

    master_subsecs = [c for c in master_sec.children if c.kind is IRNodeKind.SUBSECTION]
    M = len(master_subsecs)

    leading_omissions = 0
    for child in amend_sec.children:
        if child.kind is IRNodeKind.SUBSECTION:
            break
        if _is_omission_ir(child):
            leading_omissions += 1

    def _relabel_subsection_to_master_slot(child: IRNode, slot_idx: int) -> IRNode:
        """Clone a subsection with the label of the master slot it replaces.

        Section-level omission merges preserve the master tail and overlay
        explicit amendment subsections in source order. Bind each explicit
        subsection to the master slot it replaces so numbering remains aligned.
        """
        master_label = master_subsecs[slot_idx].label
        assert master_label is not None, f"missing master subsection label for slot {slot_idx}"
        if child.label == master_label:
            return child
        return _relabel_subsection(child, master_label)

    amend_subsecs = [c for c in amend_sec.children if c.kind is IRNodeKind.SUBSECTION]
    if amend_subsecs and master_subsecs:
        first_amend_label = normalized_label_key(amend_subsecs[0].label)
        if first_amend_label:
            matched_master_idx = next(
                (
                    idx
                    for idx, master_sub in enumerate(master_subsecs)
                    if normalized_label_key(master_sub.label) == first_amend_label
                ),
                None,
            )
            if matched_master_idx is not None:
                leading_omissions = max(leading_omissions, matched_master_idx)
    new_children = [
        c
        for c in amend_sec.children
        if c.kind is not IRNodeKind.SUBSECTION and not _is_omission_ir(c)
    ]
    master_subsec_is_replaced: List[bool] = [False] * len(master_subsecs)

    amend_texts = [
        " ".join(irnode_to_text(sub).split())
        for sub in amend_subsecs
        if " ".join(irnode_to_text(sub).split())
    ]

    for slot_idx, amend_sub in enumerate(amend_subsecs):
        master_slot_idx = leading_omissions + slot_idx
        if master_slot_idx < M:
            master_subsecs[master_slot_idx] = _relabel_subsection_to_master_slot(amend_sub, master_slot_idx)
            master_subsec_is_replaced[master_slot_idx] = True
        else:
            master_subsecs.append(amend_sub)
            master_subsec_is_replaced.append(True)

    if leading_omissions > 0 and amend_texts:
        for idx in range(min(leading_omissions, len(master_subsecs))):
            sub = master_subsecs[idx]
            if not sub.children:
                continue
            first_child = sub.children[0]
            if first_child.kind not in {IRNodeKind.CONTENT, IRNodeKind.INTRO} or not first_child.text:
                continue
            for amend_text in amend_texts:
                trimmed = _strip_leading_text_prefix(first_child.text, amend_text)
                if trimmed is None:
                    continue
                trimmed_children = list(sub.children)
                if trimmed:
                    trimmed_children[0] = IRNode(
                        kind=first_child.kind,
                        label=first_child.label,
                        text=trimmed,
                        attrs=dict(first_child.attrs),
                        children=tuple(first_child.children),
                    )
                else:
                    trimmed_children.pop(0)
                master_subsecs[idx] = _tops._with_children(sub, trimmed_children)
                break

    chosen_positions: Dict[str, int] = {}
    for idx, child in enumerate(master_subsecs):
        if child.kind is not IRNodeKind.SUBSECTION or not child.label:
            continue

        prev_idx = chosen_positions.get(child.label)
        if prev_idx is None:
            chosen_positions[child.label] = idx
            continue

        prev_is_replaced = master_subsec_is_replaced[prev_idx]
        curr_is_replaced = master_subsec_is_replaced[idx]
        if curr_is_replaced and not prev_is_replaced:
            chosen_positions[child.label] = idx
            continue
        if curr_is_replaced == prev_is_replaced:
            return None
        # Existing chosen occurrence is amended, so keep it over stale residue.

    deduped_master_subsecs = [
        child
        for idx, child in enumerate(master_subsecs)
        if child.kind is not IRNodeKind.SUBSECTION
        or not child.label
        or chosen_positions.get(child.label) == idx
    ]

    new_children.extend(deduped_master_subsecs)

    return _tops._with_children(amend_sec, new_children)


def _merge_same_numbered_container_insert_ir(master_node: IRNode, amend_node: IRNode) -> Optional[IRNode]:
    """IRNode version: overlay partial container onto live container."""
    # Start from master, update heading if provided
    new_children = list(master_node.children)
    amend_heading = next((c for c in amend_node.children if c.kind is IRNodeKind.HEADING), None)
    if amend_heading is not None:
        heading_idx = next((i for i, c in enumerate(new_children) if c.kind is IRNodeKind.HEADING), None)
        if heading_idx is not None:
            new_children[heading_idx] = amend_heading
        else:
            insert_idx = 1 if any(c.kind is IRNodeKind.NUM for c in new_children) else 0
            new_children.insert(insert_idx, amend_heading)

    for amend_child in amend_node.children:
        if amend_child.kind is not IRNodeKind.SECTION or not amend_child.label:
            continue
        # Find matching section in master
        live_idx = next(
            (i for i, c in enumerate(new_children) if c.kind is IRNodeKind.SECTION and c.label == amend_child.label),
            None,
        )
        if live_idx is not None:
            has_omission = any(_is_omission_ir(gc) for gc in amend_child.children)
            if has_omission:
                live_child = new_children[live_idx]
                live_sub_texts = {c.text or "" for c in live_child.children if c.kind is IRNodeKind.SUBSECTION}
                for ac in amend_child.children:
                    if ac.kind is IRNodeKind.SUBSECTION and (ac.text or "") not in live_sub_texts:
                        live_child = _tops._with_children(live_child, list(live_child.children) + [ac])
                new_children[live_idx] = live_child
            else:
                # Check for inner-subsection omissions
                merged = _merge_section_inner_subsection_omission_ir(new_children[live_idx], amend_child)
                new_children[live_idx] = merged if merged is not None else amend_child
        else:
            # Insert at sorted position
            target_key = _section_sort_key(amend_child.label)
            insert_idx = len(new_children)
            for idx, c in enumerate(new_children):
                if c.kind is IRNodeKind.SECTION and c.label:
                    if _section_sort_key(c.label) > target_key:
                        insert_idx = idx
                        break
            new_children.insert(insert_idx, amend_child)

    seen_section_labels: Set[str] = set()
    for child in new_children:
        if child.kind is not IRNodeKind.SECTION or not child.label:
            continue
        if child.label in seen_section_labels:
            logger.warning("MERGE_DUPLICATE_SECTION_LABELS [%s]", child.label)
            return None
        seen_section_labels.add(child.label)
    return _tops._with_children(master_node, new_children)


def _paragraph_signatures_ir(node: IRNode) -> List[str]:
    subsections = [c for c in node.children if c.kind is IRNodeKind.SUBSECTION]
    if len(subsections) != 1:
        return []
    out: List[str] = []
    for para in subsections[0].children:
        if para.kind is not IRNodeKind.PARAGRAPH:
            continue
        text = irnode_to_text(para)
        norm = " ".join(text.split())
        if norm:
            out.append(norm)
    return out


def _single_subsection_paragraph_map_ir(node: IRNode) -> tuple[Optional[IRNode], dict[str, IRNode]]:
    """Return (subsection, paragraph_map) for one-subsection numbered-item sections."""
    subsections = [c for c in node.children if c.kind is IRNodeKind.SUBSECTION]
    if len(subsections) != 1:
        return None, {}
    sub = subsections[0]
    para_map: dict[str, IRNode] = {}
    for para in sub.children:
        if para.kind is not IRNodeKind.PARAGRAPH or not para.label:
            continue
        norm = normalized_label_key(para.label)
        if norm:
            para_map[norm] = para
    return sub, para_map


def _item_label_from_intro_like_ir(node: IRNode) -> Optional[str]:
    """Return a normalized item label from leading intro/content text like `2.` or `2)`."""
    for child in node.children:
        if child.kind not in {IRNodeKind.INTRO, IRNodeKind.CONTENT}:
            continue
        text = (child.text or "").lstrip()
        compact = re.sub(r"(\d+)\s+([a-z])", r"\1\2", text, flags=re.I)
        m = re.match(r"^(\d+[a-z]?)\s*[\).]", compact, flags=re.I)
        if m:
            return normalized_label_key(m.group(1))
    return None


def _letter_label_from_intro_like_ir(node: IRNode) -> Optional[str]:
    """Return a normalized single-letter item label from leading text like `a)` or `b)`."""
    for child in node.children:
        if child.kind not in {IRNodeKind.INTRO, IRNodeKind.CONTENT}:
            continue
        text = (child.text or "").lstrip()
        m = re.match(r"^([a-z])\s*[\).]", text)
        if m:
            return normalized_label_key(m.group(1))
    return None


def _sparse_section_item_update_map_ir(node: IRNode) -> tuple[Optional[IRNode], dict[str, IRNode]]:
    """Return amendment intro subsection + sparse item replacements for section-level list payloads."""
    sub, para_map = _single_subsection_paragraph_map_ir(node)
    if sub is not None and para_map:
        return sub, para_map

    subsections = [c for c in node.children if c.kind is IRNodeKind.SUBSECTION]
    if len(subsections) < 2:
        return None, {}
    intro_sub = subsections[0]
    if any(c.kind is IRNodeKind.PARAGRAPH for c in intro_sub.children):
        return None, {}

    sparse_map: dict[str, IRNode] = {}
    last_digit_label: Optional[str] = None
    for sub in subsections[1:]:
        label = _item_label_from_intro_like_ir(sub)
        if label:
            last_digit_label = label
            sparse_map[label] = IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label=label,
                text=sub.text,
                attrs=dict(sub.attrs),
                children=tuple(sub.children),
            )
            continue
        # Check for letter-labeled subsection (a), b) etc.) — treat as
        # subparagraph of the preceding digit-labeled item.
        letter_label = _letter_label_from_intro_like_ir(sub)
        if letter_label and last_digit_label and last_digit_label in sparse_map:
            parent = sparse_map[last_digit_label]
            sp_node = IRNode(
                kind=IRNodeKind.SUBPARAGRAPH,
                label=letter_label,
                text=sub.text,
                attrs=dict(sub.attrs),
                children=tuple(sub.children),
            )
            sparse_map[last_digit_label] = IRNode(
                kind=parent.kind,
                label=parent.label,
                text=parent.text,
                attrs=dict(parent.attrs),
                children=tuple(parent.children) + (sp_node,),
            )
            continue
        # Unrecognized subsection — bail out
        return None, {}
    if not sparse_map:
        return None, {}
    return intro_sub, sparse_map


def _sparse_item_section_replace_merge_ir(
    master_sec: IRNode,
    amend_sec: IRNode,
) -> Optional[IRNode]:
    """Merge sparse paragraph updates into a list-shaped section instead of replacing it.

    Some amendment XML carries only the changed numbered list entries inside a
    section body even though PEG extracts a whole-section REPLACE. When both the
    live section and amendment payload are single-subsection numbered-item lists
    with the same intro, replace matching paragraph labels in place and keep the
    untouched siblings.
    """
    master_sub, master_map = _single_subsection_paragraph_map_ir(master_sec)
    amend_sub, amend_map = _sparse_section_item_update_map_ir(amend_sec)
    if master_sub is None or amend_sub is None:
        return None
    if len(master_map) < 3 or not amend_map or len(amend_map) >= len(master_map):
        return None

    master_intro = next((c for c in master_sub.children if c.kind is IRNodeKind.INTRO), None)
    amend_intro = next((c for c in amend_sub.children if c.kind is IRNodeKind.INTRO), None)
    if master_intro is None or amend_intro is None:
        return None

    amend_labels = set(amend_map)
    if not amend_labels.issubset(set(master_map)):
        return None
    amend_seq = [normalized_label_key(c.label) for c in amend_sub.children if c.kind is IRNodeKind.PARAGRAPH and c.label]
    if amend_seq:
        contiguous_prefix = [str(i) for i in range(1, len(amend_seq) + 1)]
        if amend_seq == contiguous_prefix:
            return None

    # Extended full-replacement check for "a-suffix" items (e.g. "2a", "5b").
    #
    # When an amendment rewriting a definitions section covers all integers 1..N
    # but intentionally drops an "a-suffix" item (like "2a") that falls within
    # that integer range, the non-contiguous-prefix check above cannot detect it
    # because a "6a" or similar label in the amendment breaks the pure-integer
    # sequence.
    #
    # Gate: if the amendment's PURE-INTEGER labels form a complete contiguous spine
    # 1..max AND the master has "a-suffix" items within that range that are NOT in
    # the amendment, treat the amendment as a full section replacement (not sparse).
    #
    # Pure integers missing from the amendment (e.g. items 9, 10 beyond max) are
    # always a genuine sparse-update signal and are not covered by this check.
    # "a-suffix" label: e.g. "2a", "5b", "13a" — digits followed only by letters.
    # A trailing dot ("1.") is NOT an a-suffix label and must not match here.
    _a_suffix_re = re.compile(r"^(\d+)[a-zA-Z]+$")
    amend_pure_ints: set[int] = set()
    for lbl in amend_labels:
        if lbl.isdigit():
            amend_pure_ints.add(int(lbl))
    if amend_pure_ints:
        max_amend_int = max(amend_pure_ints)
        if amend_pure_ints == set(range(1, max_amend_int + 1)):
            # Amendment has a complete contiguous spine of pure integers 1..max.
            # Check whether any master "a-suffix" items within this range are absent.
            missing_from_amend = set(master_map) - amend_labels
            for lbl in missing_from_amend:
                if lbl.isdigit():
                    continue  # Pure integers outside max are a genuine sparse signal
                m = _a_suffix_re.match(lbl)
                if m and int(m.group(1)) <= max_amend_int:
                    # An "a-suffix" item (e.g. "2a") inside the amendment's integer
                    # range is missing — the amendment intentionally removes it.
                    return None

    new_sub_children: List[IRNode] = [amend_intro]
    for child in master_sub.children:
        if child.kind is IRNodeKind.INTRO:
            continue
        if child.kind is IRNodeKind.PARAGRAPH and child.label:
            norm = normalized_label_key(child.label)
            replacement = amend_map.get(norm)
            if replacement is not None:
                new_sub_children.append(replacement)
                continue
        new_sub_children.append(child)

    merged_sub = _tops._with_children(master_sub, new_sub_children)

    def _tail_norm(text: str) -> str:
        return (
            text.replace("―", "-")
            .replace("–", "-")
            .replace("§", "")
            .replace(" ", "")
            .strip(" .;,:")
            .lower()
        )

    absorbed_tail_text = ""
    last_para = next((child for child in reversed(merged_sub.children) if child.kind is IRNodeKind.PARAGRAPH), None)
    if last_para is not None:
        last_sub = next((child for child in reversed(last_para.children) if child.kind is IRNodeKind.SUBPARAGRAPH), None)
        if last_sub is not None:
            absorbed_tail_text = " ".join(irnode_to_text(last_sub).split())
        else:
            content_children = [child for child in last_para.children if child.kind is IRNodeKind.CONTENT]
            if len(content_children) >= 2:
                absorbed_tail_text = " ".join(irnode_to_text(content_children[-1]).split())

    new_sec_children: List[IRNode] = []
    replaced_sub = False
    dropped_first_carried_subsection = False
    for child in master_sec.children:
        if child.kind is IRNodeKind.SUBSECTION and not replaced_sub:
            new_sec_children.append(merged_sub)
            replaced_sub = True
        elif (
            replaced_sub
            and not dropped_first_carried_subsection
            and child.kind is IRNodeKind.SUBSECTION
            and absorbed_tail_text
        ):
            carried_text = " ".join(irnode_to_text(child).split())
            absorbed_norm = _tail_norm(absorbed_tail_text)
            carried_norm = _tail_norm(carried_text)
            if carried_norm and absorbed_norm and carried_norm == absorbed_norm:
                dropped_first_carried_subsection = True
                continue
            new_sec_children.append(child)
            dropped_first_carried_subsection = True
        else:
            new_sec_children.append(child)
    return _tops._with_children(master_sec, new_sec_children)


def _merge_sparse_item_subsection_ir(
    master_sub: IRNode,
    amend_sub: IRNode,
) -> Optional[IRNode]:
    """Merge sparse numbered-item replacements inside one subsection.

    This is the subsection-scoped analogue of
    ``_sparse_item_section_replace_merge_ir`` for whole-section replace shells
    that still carry multiple subsections. Only explicit replacement item
    labels from the amendment own changes; untouched live siblings stay visible.
    """
    if master_sub.kind is not IRNodeKind.SUBSECTION or amend_sub.kind is not IRNodeKind.SUBSECTION:
        return None

    master_map: dict[str, IRNode] = {}
    for para in master_sub.children:
        if para.kind is not IRNodeKind.PARAGRAPH or not para.label:
            continue
        norm = normalized_label_key(para.label)
        if norm:
            master_map[norm] = para

    amend_map: dict[str, IRNode] = {}
    amend_seq: list[str] = []
    for para in amend_sub.children:
        if para.kind is not IRNodeKind.PARAGRAPH or not para.label:
            continue
        norm = normalized_label_key(para.label)
        if norm:
            amend_map[norm] = para
            amend_seq.append(norm)

    if len(master_map) < 3 or not amend_map or len(amend_map) >= len(master_map):
        return None

    master_intro = next((c for c in master_sub.children if c.kind is IRNodeKind.INTRO), None)
    amend_intro = next((c for c in amend_sub.children if c.kind is IRNodeKind.INTRO), None)
    if master_intro is None or amend_intro is None:
        return None

    amend_labels = set(amend_map)
    if not amend_labels.issubset(set(master_map)):
        return None
    if amend_seq:
        contiguous_prefix = [str(i) for i in range(1, len(amend_seq) + 1)]
        if amend_seq == contiguous_prefix:
            return None

    new_children: List[IRNode] = [amend_intro]
    for child in master_sub.children:
        if child.kind is IRNodeKind.INTRO:
            continue
        if child.kind is IRNodeKind.PARAGRAPH and child.label:
            norm = normalized_label_key(child.label)
            replacement = amend_map.get(norm)
            if replacement is not None:
                new_children.append(replacement)
                continue
        new_children.append(child)

    return _tops._with_children(master_sub, new_children)


def _multi_subsection_sparse_item_section_replace_merge_ir(
    master_sec: IRNode,
    amend_sec: IRNode,
) -> Optional[IRNode]:
    """Preserve untouched item siblings inside sparse subsection payloads.

    Some whole-section replace shells carry multiple subsections, but only one
    subsection contains sparse item-level changes. Merge that subsection
    against the live sibling instead of letting the whole-section shell delete
    unchanged numbered items.
    """
    master_subs = [c for c in master_sec.children if c.kind is IRNodeKind.SUBSECTION]
    amend_children = list(amend_sec.children)
    if len(master_subs) < 2:
        return None

    master_by_label = {
        normalized_label_key(sub.label): sub
        for sub in master_subs
        if normalized_label_key(sub.label)
    }
    master_sub_by_index = {idx: sub for idx, sub in enumerate(master_subs)}
    changed = False
    new_children: List[IRNode] = []
    amend_sub_idx = 0
    for child in amend_children:
        if child.kind is not IRNodeKind.SUBSECTION:
            new_children.append(child)
            continue
        child_norm = normalized_label_key(child.label)
        if child_norm:
            master_sub = master_by_label.get(child_norm)
        else:
            # Some Finland omission-shaped section payloads carry unlabeled
            # subsection shells. In that family, preserve sparse item siblings
            # by matching the subsection positionally instead of giving up.
            master_sub = master_sub_by_index.get(amend_sub_idx)
        amend_sub_idx += 1
        if master_sub is None:
            new_children.append(child)
            continue
        merged_sub = _merge_sparse_item_subsection_ir(master_sub, child)
        if merged_sub is None:
            new_children.append(child)
            continue
        new_children.append(merged_sub)
        changed = True

    if not changed:
        return None
    return _tops._with_children(amend_sec, new_children)


def _heading_intro_replace_preserve_items_ir(
    master_sec: IRNode,
    amend_sec: IRNode,
) -> Optional[IRNode]:
    """Handle heading + intro replacement when the amendment carries no item paragraphs.

    Some amendments change only the heading and/or the introductory sentence of a
    list-shaped section.  The amendment XML then contains a single subsection with
    an <intro> element but zero <paragraph> items — because the items are unchanged
    and omitted.

    Signal: amendment subsection has an INTRO and no PARAGRAPHs; live subsection has
    an INTRO and ≥2 PARAGRAPHs.  The amendment intends to replace only the
    heading/intro; the items must be preserved.

    Returns the merged section, or None if the pattern does not apply.
    """
    master_subs = [c for c in master_sec.children if c.kind is IRNodeKind.SUBSECTION]
    amend_subs = [c for c in amend_sec.children if c.kind is IRNodeKind.SUBSECTION]
    if not master_subs or not amend_subs:
        return None

    master_sub = master_subs[0]
    amend_sub = amend_subs[0]

    # Amendment first subsection must have an intro but NO paragraph items.
    amend_intro = next((c for c in amend_sub.children if c.kind is IRNodeKind.INTRO), None)
    if amend_intro is None and len(amend_sub.children) == 1:
        only_child = amend_sub.children[0]
        only_text = " ".join(irnode_to_text(only_child).split()).strip()
        if only_child.kind is IRNodeKind.CONTENT and only_text.endswith(":"):
            amend_intro = IRNode(
                kind=IRNodeKind.INTRO,
                label=only_child.label,
                text=only_child.text,
                attrs=dict(only_child.attrs),
                children=tuple(only_child.children),
            )
    if amend_intro is None:
        return None
    if any(c.kind is IRNodeKind.PARAGRAPH for c in amend_sub.children):
        return None
    amend_intro_text = " ".join(irnode_to_text(amend_intro).split())

    # Live first subsection must have intro + at least 2 paragraph items.
    master_paras = [c for c in master_sub.children if c.kind is IRNodeKind.PARAGRAPH]
    if len(master_paras) < 2:
        return None

    # Intro text may stay the same when the amendment only changes the heading or
    # carries later subsection payload; still allow the preservation merge in that
    # family.
    master_intro = next((c for c in master_sub.children if c.kind is IRNodeKind.INTRO), None)
    if master_intro is not None and len(amend_subs) == 1:
        m_text = " ".join(irnode_to_text(master_intro).split())
        if m_text == amend_intro_text:
            return None

    # Build merged subsection: amendment intro + live paragraph items (in order).
    new_sub_children: List[IRNode] = [amend_intro]
    for child in master_sub.children:
        if child.kind is IRNodeKind.PARAGRAPH:
            new_sub_children.append(child)

    merged_sub = IRNode(
        kind=amend_sub.kind,
        label=amend_sub.label,
        text=amend_sub.text,
        attrs=dict(amend_sub.attrs),
        children=tuple(new_sub_children),
    )

    # Rebuild the section with the merged first subsection.
    # If the amendment supplies later subsections, trust those explicit payloads.
    # Otherwise preserve the later live subsections so same-group repeals can own
    # any removals explicitly.
    new_sec_children: List[IRNode] = []
    sub_placed = False
    for child in amend_sec.children:
        if child.kind is IRNodeKind.SUBSECTION and not sub_placed:
            new_sec_children.append(merged_sub)
            sub_placed = True
        elif child.kind is IRNodeKind.SUBSECTION and sub_placed and len(amend_subs) == 1:
            continue
        else:
            new_sec_children.append(child)
    if not sub_placed:
        new_sec_children.append(merged_sub)
    if len(amend_subs) == 1:
        for child in master_sec.children:
            if child.kind is IRNodeKind.SUBSECTION and child is not master_sub:
                new_sec_children.append(child)

    return IRNode(
        kind=amend_sec.kind,
        label=amend_sec.label,
        text=amend_sec.text,
        attrs=dict(amend_sec.attrs),
        children=tuple(new_sec_children),
    )


def _mixed_sparse_intro_replace_preserve_first_subsection_items_ir(
    master_sec: IRNode,
    amend_sec: IRNode,
) -> Optional[IRNode]:
    """Preserve first-subsection items in mixed sparse section-replace shells.

    Some Finland section-level replace shells travel together with explicit
    subsection INSERT ops in the same group. In that family the amendment
    section often carries:

    - subsection 1 with only the unchanged/updated intro, and
    - later new subsections as real payload.

    Replacing the whole section would silently delete the existing numbered
    items under live subsection 1. Preserve those items and keep the later
    amendment subsections intact.
    """
    master_subs = [c for c in master_sec.children if c.kind is IRNodeKind.SUBSECTION]
    amend_subs = [c for c in amend_sec.children if c.kind is IRNodeKind.SUBSECTION]
    if len(master_subs) != 1 or len(amend_subs) < 2:
        return None

    master_sub = master_subs[0]
    amend_first = amend_subs[0]
    if master_sub.label and amend_first.label and normalized_label_key(master_sub.label) != normalized_label_key(amend_first.label):
        return None

    amend_intro = next((c for c in amend_first.children if c.kind is IRNodeKind.INTRO), None)
    if amend_intro is None and len(amend_first.children) == 1:
        only_child = amend_first.children[0]
        only_text = " ".join(irnode_to_text(only_child).split()).strip()
        if only_child.kind is IRNodeKind.CONTENT and only_text.endswith(":"):
            amend_intro = IRNode(
                kind=IRNodeKind.INTRO,
                label=only_child.label,
                text=only_child.text,
                attrs=dict(only_child.attrs),
                children=tuple(only_child.children),
            )
    if amend_intro is None:
        return None
    if any(c.kind is IRNodeKind.PARAGRAPH for c in amend_first.children):
        return None

    master_paras = [c for c in master_sub.children if c.kind is IRNodeKind.PARAGRAPH]
    if len(master_paras) < 2:
        return None

    merged_first = IRNode(
        kind=amend_first.kind,
        label=amend_first.label,
        text=amend_first.text,
        attrs=dict(amend_first.attrs),
        children=(amend_intro, *master_paras),
    )

    new_sec_children: List[IRNode] = []
    first_sub_placed = False
    for child in amend_sec.children:
        if child.kind is IRNodeKind.SUBSECTION and not first_sub_placed:
            new_sec_children.append(merged_first)
            first_sub_placed = True
        else:
            new_sec_children.append(child)
    if not first_sub_placed:
        new_sec_children.append(merged_first)

    return IRNode(
        kind=amend_sec.kind,
        label=amend_sec.label,
        text=amend_sec.text,
        attrs=dict(amend_sec.attrs),
        children=tuple(new_sec_children),
    )


def _paragraph_to_subparagraph_ir(paragraph: IRNode, new_label: Optional[str] = None) -> IRNode:
    """Convert a paragraph node into a subparagraph while preserving visible label text."""
    label = new_label or paragraph.label or ""
    display_label = re.sub(r"^(\d+)([a-z])$", r"\1 \2", label, flags=re.I)
    new_children: List[IRNode] = []
    num_updated = False
    for child in paragraph.children:
        if child.kind is IRNodeKind.NUM and not num_updated:
            child_text = child.text or ""
            if child_text:
                new_text = re.sub(
                    r"^(\s*)(?:\d+\s*[a-z]?|[a-z])",
                    lambda m: f"{m.group(1)}{display_label}",
                    child_text,
                    count=1,
                    flags=re.I,
                )
            else:
                new_text = f"{display_label})"
            new_children.append(
                IRNode(
                    kind=IRNodeKind.NUM,
                    label=child.label,
                    text=new_text,
                    attrs=dict(child.attrs),
                    children=tuple(child.children),
                )
            )
            num_updated = True
        else:
            new_children.append(child)
    if not num_updated:
        new_children = [IRNode(kind=IRNodeKind.NUM, text=f"{display_label})")] + new_children
    return IRNode(
        kind=IRNodeKind.SUBPARAGRAPH,
        label=label,
        text=paragraph.text,
        attrs=dict(paragraph.attrs),
        children=tuple(new_children),
    )


def _merge_sparse_alakohta_insert_ir(
    master_para: IRNode,
    amend_sub: IRNode,
    item_norm: str,
) -> Optional[IRNode]:
    """Merge sparse ``b alakohta`` style amendment payloads into an existing item paragraph.

    Some amendments target ``N kohtaan`` but encode only the changed alakohta in the
    body as a sparse subsection:
    - paragraph ``N)``
    - omission
    - paragraph ``b)``
    - omission

    In those cases the op model only retains target_item ``N``.  Replaying that as a
    normal item insert duplicates the whole parent item.  Detect the sparse body shape
    and splice the letter-labelled sibling paragraphs in as subparagraphs under the
    existing master paragraph instead.
    """
    if not any(_is_omission_ir(c) for c in amend_sub.children):
        return None
    anchor = next(
        (c for c in amend_sub.children if c.kind is IRNodeKind.PARAGRAPH and c.label and normalized_label_key(c.label) == item_norm),
        None,
    )
    if anchor is None:
        return None
    sparse_letters: List[IRNode] = [
        c
        for c in anchor.children
        if c.kind is IRNodeKind.SUBPARAGRAPH and c.label and re.fullmatch(r"[a-z]", normalized_label_key(c.label))
    ]
    if not sparse_letters:
        sparse_letters = [
            c
            for c in amend_sub.children
            if c.kind is IRNodeKind.PARAGRAPH and c.label and re.fullmatch(r"[a-z]", normalized_label_key(c.label))
        ]
    if not sparse_letters:
        return None

    new_children = list(master_para.children)
    existing_sps = [c for c in new_children if c.kind is IRNodeKind.SUBPARAGRAPH]
    for sparse_para in sparse_letters:
        sp_label = normalized_label_key(sparse_para.label)
        replacement = _paragraph_to_subparagraph_ir(sparse_para, sp_label)
        existing = next(
            (c for c in existing_sps if c.label and normalized_label_key(c.label) == sp_label),
            None,
        )
        if existing is not None:
            new_children = [replacement if c is existing else c for c in new_children]
            existing_sps = [c for c in new_children if c.kind is IRNodeKind.SUBPARAGRAPH]
            continue

        insert_at = len(new_children)
        prev_label = _previous_item_token(sp_label)
        if prev_label is not None:
            prev_sp = next(
                (c for c in existing_sps if c.label and normalized_label_key(c.label) == prev_label),
                None,
            )
            if prev_sp is not None:
                for i, child in enumerate(new_children):
                    if child is prev_sp:
                        insert_at = i + 1
                        break
        new_children.insert(insert_at, replacement)
        existing_sps = [c for c in new_children if c.kind is IRNodeKind.SUBPARAGRAPH]

    return IRNode(
        kind=master_para.kind,
        label=master_para.label,
        text=master_para.text,
        attrs=dict(master_para.attrs),
        children=tuple(new_children),
    )


def _merge_sparse_alakohta_replace_ir(
    master_para: IRNode,
    amend_sub: IRNode,
    item_norm: str,
) -> Optional[IRNode]:
    """Merge sparse ``h alakohta`` style REPLACE payloads into an existing item paragraph.

    Some amendments target ``N kohdan X alakohta`` but the PEG/frontend degrades the
    target to item ``N`` because subparagraph granularity is not represented in ops.
    The amendment body then carries:
    - paragraph ``N)`` with updated intro text
    - one or more letter-labelled sibling paragraphs like ``h)``
    - optionally other numbered sibling paragraphs for separate item replaces

    Replacing the whole master paragraph ``N`` would drop untouched existing
    subparagraphs ``a``..``g``. Detect that sparse shape and merge only the updated
    intro + letter-labelled subparagraphs into the live paragraph.
    """
    anchor = next(
        (c for c in amend_sub.children if c.kind is IRNodeKind.PARAGRAPH and c.label and normalized_label_key(c.label) == item_norm),
        None,
    )
    if anchor is None:
        return None

    sparse_letters: List[IRNode] = [
        c
        for c in anchor.children
        if c.kind is IRNodeKind.SUBPARAGRAPH and c.label and re.fullmatch(r"[a-z]", normalized_label_key(c.label))
    ]
    if not sparse_letters:
        sparse_letters = [
            c
            for c in amend_sub.children
            if c.kind is IRNodeKind.PARAGRAPH and c.label and re.fullmatch(r"[a-z]", normalized_label_key(c.label))
        ]
    if not sparse_letters:
        return None

    existing_sps = [c for c in master_para.children if c.kind is IRNodeKind.SUBPARAGRAPH]
    if not existing_sps:
        return None

    master_non_sp = [c for c in master_para.children if c.kind is not IRNodeKind.SUBPARAGRAPH]
    master_num = [c for c in master_non_sp if c.kind is IRNodeKind.NUM]
    master_other = [c for c in master_non_sp if c.kind not in (IRNodeKind.NUM, IRNodeKind.INTRO, IRNodeKind.CONTENT)]
    master_intro_like = [c for c in master_non_sp if c.kind in (IRNodeKind.INTRO, IRNodeKind.CONTENT)]
    anchor_intro_like = [c for c in anchor.children if c.kind in (IRNodeKind.INTRO, IRNodeKind.CONTENT)]

    if anchor_intro_like:
        first_master_intro = next((c for c in master_intro_like), None)
        intro_like_children: List[IRNode] = []
        for child in anchor_intro_like:
            intro_kind = first_master_intro.kind if first_master_intro is not None else child.kind
            intro_like_children.append(
                IRNode(
                    kind=intro_kind,
                    label=child.label,
                    text=child.text,
                    attrs=dict(child.attrs),
                    children=tuple(child.children),
                )
            )
    else:
        intro_like_children = list(master_intro_like)

    new_children = list(master_num) + intro_like_children + list(master_other) + list(existing_sps)
    merged_sps = [c for c in new_children if c.kind is IRNodeKind.SUBPARAGRAPH]
    for sparse_para in sparse_letters:
        sp_label = normalized_label_key(sparse_para.label)
        replacement = (
            sparse_para if sparse_para.kind is IRNodeKind.SUBPARAGRAPH else _paragraph_to_subparagraph_ir(sparse_para, sp_label)
        )
        existing = next(
            (c for c in merged_sps if c.label and normalized_label_key(c.label) == sp_label),
            None,
        )
        if existing is not None:
            new_children = [replacement if c is existing else c for c in new_children]
            merged_sps = [c for c in new_children if c.kind is IRNodeKind.SUBPARAGRAPH]
            continue

        insert_at = len(new_children)
        prev_label = _previous_item_token(sp_label)
        if prev_label is not None:
            prev_sp = next(
                (c for c in merged_sps if c.label and normalized_label_key(c.label) == prev_label),
                None,
            )
            if prev_sp is not None:
                for i, child in enumerate(new_children):
                    if child is prev_sp:
                        insert_at = i + 1
                        break
        new_children.insert(insert_at, replacement)
        merged_sps = [c for c in new_children if c.kind is IRNodeKind.SUBPARAGRAPH]

    return IRNode(
        kind=master_para.kind,
        label=master_para.label,
        text=master_para.text,
        attrs=dict(master_para.attrs),
        children=tuple(new_children),
    )


def _merge_letter_item_into_content_only_subsection_ir(
    sub: IRNode,
    amend_para: IRNode,
    item_norm: str,
) -> Optional[IRNode]:
    """Replace a single letter-labelled row inside a flattened content-only subsection.

    Old tabular amendments sometimes target rows like ``H kohta`` but both the live
    section and the amendment subtree are flattened to text/content rather than
    structural paragraph nodes. Replacing the whole subsection drops untouched rows.
    Instead, splice the amended ``H. ...`` row into the existing text.
    """
    if not re.fullmatch(r"[a-z]", item_norm):
        return None

    master_text = " ".join(irnode_to_text(sub).split())
    amend_text = " ".join(irnode_to_text(amend_para).split())
    if not master_text or not amend_text:
        return None

    label = item_norm.upper()
    row_re = re.compile(
        rf"(?<!\w){re.escape(label)}\.\s.*?(?=(?:\s+[A-ZÅÄÖ]\.)|$)",
        re.S,
    )
    if not row_re.search(master_text):
        return None

    replacement = amend_text
    row_start = re.search(rf"(?<!\w){re.escape(label)}\.\s", replacement)
    if row_start is not None:
        replacement = replacement[row_start.start() :]
    elif not replacement.startswith(f"{label}."):
        replacement = f"{label}. {replacement}"
    merged_text = row_re.sub(replacement, master_text, count=1)

    rebuilt_children: List[IRNode] = []
    replaced = False
    for child in sub.children:
        if child.kind in (IRNodeKind.CONTENT, IRNodeKind.INTRO) and not replaced:
            rebuilt_children.append(
                IRNode(
                    kind=child.kind,
                    label=child.label,
                    text=merged_text,
                    attrs=dict(child.attrs),
                    children=(),
                )
            )
            replaced = True
        else:
            rebuilt_children.append(child)
    if not replaced:
        rebuilt_children.append(IRNode(kind=IRNodeKind.CONTENT, text=merged_text))
    return _tops._with_children(sub, rebuilt_children)


def _merge_letter_item_from_content_subsection_ir(
    sub: IRNode,
    amend_sub: IRNode,
    item_norm: str,
) -> Optional[IRNode]:
    """Replace a single letter-labelled row using a flattened amendment subsection."""
    if not re.fullmatch(r"[a-z]", item_norm):
        return None
    amend_text = " ".join(irnode_to_text(amend_sub).split())
    if not amend_text:
        return None
    synthetic_para = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=item_norm,
        children=(IRNode(kind=IRNodeKind.CONTENT, text=amend_text),),
    )
    return _merge_letter_item_into_content_only_subsection_ir(sub, synthetic_para, item_norm)


# ---------------------------------------------------------------------------
# Group B: light XMLStatute dependency (TYPE_CHECKING only)
# ---------------------------------------------------------------------------


def _partial_section_replace_diagnostics_ir(
    op: "AmendmentOp | ResolvedOp",
    master_sec: IRNode,
    amend_sec: IRNode,
) -> dict[str, object]:
    op_type = op.resolved_action_type if isinstance(op, ResolvedOp) else op.op_type
    if op_type != "REPLACE":
        return {}
    # Uncovered-body recovery ops are pre-validated by _recover_uncovered_body_ops
    # (no-omission guard, no-subsection-loss guard).  Skip the suspicious-partial
    # heuristic for them so routing through apply_op does not change semantics.
    if op.uses_uncovered_body_recovery if isinstance(op, ResolvedOp) else op.uncovered_body_recovery:
        return {}
    master_paras = _paragraph_signatures_ir(master_sec)
    amend_paras = _paragraph_signatures_ir(amend_sec)
    if len(master_paras) < 8:
        return {}

    def _intro_text(node: IRNode) -> str:
        subsections = [c for c in node.children if c.kind is IRNodeKind.SUBSECTION]
        if len(subsections) != 1:
            return ""
        intro = next((c for c in subsections[0].children if c.kind is IRNodeKind.INTRO), None)
        return " ".join(irnode_to_text(intro).split()) if intro is not None else ""

    def _heading_text(node: IRNode) -> str:
        heading = next((c for c in node.children if c.kind is IRNodeKind.HEADING), None)
        return " ".join(irnode_to_text(heading).split()) if heading is not None else ""

    master_set = set(master_paras)
    diag: dict[str, object] = {}
    if amend_paras and len(amend_paras) < len(master_paras) and all(sig in master_set for sig in amend_paras):
        diag = {
            "suspicious": True,
            "reason": "subset_paragraph_signatures",
            "master_paragraph_count": len(master_paras),
            "amend_paragraph_count": len(amend_paras),
            "para_ratio": (len(amend_paras) / len(master_paras)) if master_paras else 0.0,
            "malformed_body": False,
        }
    master_intro = _intro_text(master_sec)
    amend_intro = _intro_text(amend_sec)
    master_text = " ".join(irnode_to_text(master_sec).split())
    amend_text = " ".join(irnode_to_text(amend_sec).split())
    if not master_text or not amend_text:
        return diag
    para_ratio = (len(amend_paras) / len(master_paras)) if amend_paras else 0.0
    text_ratio = len(amend_text) / len(master_text)
    if master_intro and master_intro == amend_intro:
        if para_ratio <= 0.35 and text_ratio <= 0.35:
            diag.update(
                {
                    "suspicious": True,
                    "reason": str(diag.get("reason") or "shared_intro_tiny_payload"),
                    "master_paragraph_count": len(master_paras),
                    "amend_paragraph_count": len(amend_paras),
                    "para_ratio": para_ratio,
                    "text_ratio": text_ratio,
                    "malformed_body": True,
                }
            )
            return diag
    master_heading = _heading_text(master_sec)
    amend_heading = _heading_text(amend_sec)
    if not amend_paras and master_heading and master_heading == amend_heading and text_ratio <= 0.2:
        return {
            "suspicious": True,
            "reason": "shared_heading_tiny_payload",
            "master_paragraph_count": len(master_paras),
            "amend_paragraph_count": len(amend_paras),
            "para_ratio": para_ratio,
            "text_ratio": text_ratio,
            "malformed_body": True,
        }
    return diag


def _is_suspicious_partial_section_replace_ir(
    op: "AmendmentOp | ResolvedOp",
    master_sec: IRNode,
    amend_sec: IRNode,
) -> bool:
    return bool(_partial_section_replace_diagnostics_ir(op, master_sec, amend_sec).get("suspicious"))


def _drop_suspicious_partial_whole_section_replaces(
    live_section: Optional[IRNode],
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    muutos_ir: Optional[IRNode],
    group_ops: List["AmendmentOp"],
) -> tuple[List["AmendmentOp"], List[SourcePathology], List[FailedOp]]:
    if target_unit_kind != "section" or muutos_ir is None:
        return group_ops, [], []
    live_sec = live_section
    if live_sec is None:
        return group_ops, [], []
    filtered: List[AmendmentOp] = []
    pathologies: List[SourcePathology] = []
    rejected_ops: List[FailedOp] = []
    for op in group_ops:
        if op.target_paragraph is not None or op.target_item is not None or op.target_special:
            filtered.append(op)
            continue
        diag = _partial_section_replace_diagnostics_ir(op, live_sec, muutos_ir)
        if diag.get("suspicious"):
            logger.debug(
                "  [%s] %s → SKIP (suspicious partial whole-section fallback replace)",
                op.source_statute,
                op.description(),
            )
            rejected_ops.append(
                FailedOp.from_scope(
                    amendment_id=op.source_statute or "",
                    description=op.description(),
                    reason="_drop_suspicious_partial_whole_section_replaces: suspicious partial whole-section fallback replace",
                    reason_code="PARTIAL_WHOLE_SECTION_REPLACE_REJECTED",
                    target_section=op.target_section or target_norm,
                    target_unit_kind=op.target_unit_kind,
                    target_chapter=op.target_chapter or target_chapter,
                )
            )
            diag_reason = str(diag.get("reason") or "")
            live_para_count = len(_paragraph_signatures_ir(live_sec))
            amend_para_count = len(_paragraph_signatures_ir(muutos_ir))
            live_text_chars = len(" ".join(irnode_to_text(live_sec).split()))
            amend_text_chars = len(" ".join(irnode_to_text(muutos_ir).split()))
            pathologies.append(
                build_partial_whole_section_payload_pathology(
                    source_statute=op.source_statute,
                    target_unit_kind=op.target_unit_kind,
                    target_section=target_norm,
                    target_chapter=target_chapter or "",
                    live_paragraph_count=live_para_count,
                    amend_paragraph_count=amend_para_count,
                    live_text_chars=live_text_chars,
                    amend_text_chars=amend_text_chars,
                    diagnostic_reason=diag_reason,
                )
            )
            if bool(diag.get("malformed_body")):
                pathologies.append(
                    build_malformed_broad_replace_body_pathology(
                        source_statute=op.source_statute,
                        target_unit_kind=op.target_unit_kind,
                        target_section=target_norm,
                        target_chapter=target_chapter or "",
                        live_paragraph_count=live_para_count,
                        amend_paragraph_count=amend_para_count,
                        live_text_chars=live_text_chars,
                        amend_text_chars=amend_text_chars,
                        diagnostic_reason=diag_reason,
                    )
                )
            continue
        filtered.append(op)
    return filtered, pathologies, rejected_ops


def _drop_suspicious_partial_subsection_shell_replaces(
    live_section: Optional[IRNode],
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    muutos_ir: Optional[IRNode],
    group_ops: List["AmendmentOp"],
) -> tuple[List["AmendmentOp"], List[SourcePathology], List[FailedOp]]:
    """Drop paragraph-targeted replaces that carry a stale whole-section shell.

    Some Finland amendment bodies target a single subsection but embed an
    outdated whole-section wrapper copied from an older consolidation. When the
    wrapper heading disagrees with the live section heading, carrying that shell
    into apply will overwrite the current section family with stale metadata and
    stale subsection text.
    """
    if target_unit_kind != "section" or muutos_ir is None or live_section is None:
        return group_ops, [], []

    live_heading = next((c for c in live_section.children if c.kind is IRNodeKind.HEADING), None)
    amend_heading = next((c for c in muutos_ir.children if c.kind is IRNodeKind.HEADING), None)
    amend_subsections = [c for c in muutos_ir.children if c.kind is IRNodeKind.SUBSECTION]
    if live_heading is None or amend_heading is None or live_heading.text == amend_heading.text:
        return group_ops, [], []
    if len(amend_subsections) != 1:
        return group_ops, [], []

    filtered: List["AmendmentOp"] = []
    pathologies: List[SourcePathology] = []
    rejected_ops: List[FailedOp] = []
    live_para_count = len(_paragraph_signatures_ir(live_section))
    amend_para_count = len(_paragraph_signatures_ir(muutos_ir))
    live_text_chars = len(" ".join(irnode_to_text(live_section).split()))
    amend_text_chars = len(" ".join(irnode_to_text(muutos_ir).split()))

    for op in group_ops:
        if (
            op.op_type != "REPLACE"
            or op.target_paragraph is None
            or op.target_item is not None
            or op.target_special
        ):
            filtered.append(op)
            continue
        target_label = str(op.target_paragraph)
        amend_sub = amend_subsections[0]
        if amend_sub.label and _norm_num_token(amend_sub.label) != target_label:
            filtered.append(op)
            continue
        logger.debug(
            "  [%s] %s → SKIP (stale whole-section shell for subsection-targeted replace)",
            op.source_statute,
            op.description(),
        )
        rejected_ops.append(
            FailedOp.from_scope(
                amendment_id=op.source_statute or "",
                description=op.description(),
                reason="_drop_suspicious_partial_subsection_shell_replaces: stale whole-section shell for subsection-targeted replace",
                reason_code="STALE_WHOLE_SECTION_SHELL_REJECTED",
                target_section=op.target_section or target_norm,
                target_unit_kind=op.target_unit_kind,
                target_chapter=op.target_chapter or target_chapter,
            )
        )
        pathologies.append(
            build_partial_whole_section_payload_pathology(
                source_statute=op.source_statute,
                target_unit_kind=op.target_unit_kind,
                target_section=target_norm,
                target_chapter=target_chapter or "",
                live_paragraph_count=live_para_count,
                amend_paragraph_count=amend_para_count,
                live_text_chars=live_text_chars,
                amend_text_chars=amend_text_chars,
                diagnostic_reason="stale_whole_section_shell_heading_mismatch",
            )
        )
    return filtered, pathologies, rejected_ops


def _is_compact_first_subsection_replace_shell_ir(
    muutos_ir: IRNode,
    group_ops: List["AmendmentOp"],
) -> bool:
    """Return true for a tight headed section shell carrying one 1 mom replace.

    This is the safe omission-pre-resolution family behind 1979/864 §6:
    the amendment targets `REPLACE section § 1 mom`, but the source XML wraps
    the payload in a compact section shell:
    - one section heading
    - one subsection shell (label `1` or unlabeled)
    - no top-level omission/container siblings
    - omission appears inside the subsection body, meaning "preserve tail"

    We intentionally keep this narrow. Broader sparse section shells such as
    1977/18 must remain unresolved at this phase.

    FIXME: this family should eventually be selected by a typed payload witness
    emitted by acquisition/elaboration, not rediscovered from IR shell shape.
    For now we keep the predicate explicitly narrow because broader section-
    shell omission merging regressed whole-subsection replace families where
    stale trailing items must *not* be spliced back in.
    """
    plain_subsection_ops = [
        op
        for op in group_ops
        if (
            op.target_paragraph is not None
            and not op.target_item
            and not op.target_special
            and op.op_type == "REPLACE"
        )
    ]
    if len(plain_subsection_ops) != 1:
        return False
    if str(plain_subsection_ops[0].target_paragraph) != "1":
        return False
    if muutos_ir.kind is not IRNodeKind.SECTION:
        return False

    non_shell_children = [
        child
        for child in muutos_ir.children
        if child.kind not in {IRNodeKind.NUM, IRNodeKind.HEADING, IRNodeKind.SUBSECTION}
    ]
    if non_shell_children:
        return False

    amend_subsections = [child for child in muutos_ir.children if child.kind is IRNodeKind.SUBSECTION]
    if len(amend_subsections) != 1:
        return False
    amend_sub = amend_subsections[0]
    if amend_sub.label and normalized_label_key(amend_sub.label) != "1":
        return False

    has_heading = any(child.kind is IRNodeKind.HEADING for child in muutos_ir.children)
    has_num = any(child.kind is IRNodeKind.NUM for child in muutos_ir.children)
    if not has_heading or not has_num:
        return False

    return any(_is_omission_ir(child) for child in amend_sub.children)


def _is_single_subsection_insert_item_shell_ir(
    muutos_ir: IRNode,
    group_ops: List["AmendmentOp"],
) -> bool:
    """Return true when the amendment inserts items INTO an existing subsection.

    This covers the family where fi.insertion_sub_target records an INSERT into
    a specific subsection (target_paragraph set) and the source XML carries:
    - one section shell (no section-level omissions)
    - one subsection shell containing inner omissions (meaning "preserve the rest")

    Example: 2012/80 inserting item 13b into subsection 1 of 1996/627 §1.
    The inner omissions mean the full existing subsection content should be
    preserved, with only the new items added.
    """
    insert_sub_ops = [
        op
        for op in group_ops
        if op.op_type == "INSERT" and op.target_paragraph is not None
    ]
    if not insert_sub_ops:
        return False
    if muutos_ir.kind is not IRNodeKind.SECTION:
        return False
    # Must not have section-level omissions (those signal a different family)
    if any(_is_omission_ir(child) for child in muutos_ir.children):
        return False
    amend_subsections = [child for child in muutos_ir.children if child.kind is IRNodeKind.SUBSECTION]
    if len(amend_subsections) != 1:
        return False
    # The subsection must have inner omissions — the key signal that existing
    # content should be preserved
    return any(_is_omission_ir(gc) for gc in amend_subsections[0].children)


def _is_single_subsection_replace_section_omission_shell_ir(
    muutos_ir: IRNode,
    group_ops: List["AmendmentOp"],
) -> bool:
    """Return true for a valid subsection replace wrapped in a section shell.

    This covers the narrow family where source XML carries:
    - one section NUM + HEADING shell
    - one subsection shell matching the targeted paragraph
    - a section-level omission marker preserving the live tail

    The section heading may be stale in the copied shell; the caller must keep
    the live heading authoritative when resolving the omission merge.
    """
    plain_subsection_ops = [
        op
        for op in group_ops
        if (
            op.target_paragraph is not None
            and not op.target_item
            and not op.target_special
            and op.op_type == "REPLACE"
        )
    ]
    if len(plain_subsection_ops) != 1:
        return False
    target_label = str(plain_subsection_ops[0].target_paragraph or "")
    if not target_label:
        return False
    if muutos_ir.kind is not IRNodeKind.SECTION:
        return False

    non_shell_children = [
        child
        for child in muutos_ir.children
        if child.kind not in {IRNodeKind.NUM, IRNodeKind.HEADING, IRNodeKind.SUBSECTION, IRNodeKind.OMISSION}
    ]
    if non_shell_children:
        return False

    amend_subsections = [child for child in muutos_ir.children if child.kind is IRNodeKind.SUBSECTION]
    if len(amend_subsections) != 1:
        return False
    amend_sub = amend_subsections[0]
    if amend_sub.label and normalized_label_key(amend_sub.label) != normalized_label_key(target_label):
        return False

    has_heading = any(child.kind is IRNodeKind.HEADING for child in muutos_ir.children)
    has_num = any(child.kind is IRNodeKind.NUM for child in muutos_ir.children)
    has_section_level_omission = any(_is_omission_ir(child) for child in muutos_ir.children)
    return has_heading and has_num and has_section_level_omission


def _is_single_subsection_insert_section_omission_shell_ir(
    muutos_ir: IRNode,
    group_ops: List["AmendmentOp"],
) -> bool:
    """True for a section-shell INSERT that carries the full new subsection content.

    Covers the family where:
    - op is INSERT targeting a specific subsection (target_paragraph set, no target_item)
    - source XML carries a section shell with: NUM + HEADING + section-level omission
      + one full subsection (no inner omissions — amendment provides complete new content)
    - the amendment subsection label matches the op's target_paragraph

    Example: 2022/525 inserting item 6 into subsection 2 of 2007/1024 §2.
    The amendment XML contains the full revised subsection:2 (all 6 items) plus an
    omission marker to preserve subsection:1.  The johtolause parser failed to
    extract target_item, so the op only carries target_paragraph=2.

    This is distinct from _is_single_subsection_insert_item_shell_ir where the
    subsection has inner omissions (signalling "preserve existing items").
    """
    insert_sub_ops = [
        op
        for op in group_ops
        if op.op_type == "INSERT"
        and op.target_paragraph is not None
        and not op.target_item
        and not op.target_special
    ]
    if len(insert_sub_ops) != 1:
        return False
    if muutos_ir.kind is not IRNodeKind.SECTION:
        return False
    # Must have section-level omission (distinguishes from _is_single_subsection_insert_item_shell_ir)
    if not any(_is_omission_ir(child) for child in muutos_ir.children):
        return False
    amend_subsections = [child for child in muutos_ir.children if child.kind is IRNodeKind.SUBSECTION]
    if len(amend_subsections) != 1:
        return False
    # Full subsection content — no inner omissions
    # (inner omissions would mean the other family: _is_single_subsection_insert_item_shell_ir)
    if any(_is_omission_ir(gc) for gc in amend_subsections[0].children):
        return False
    # No trailing omission: the subsection must come last among non-structural
    # children.  A trailing omission (omission AFTER the subsection) signals a
    # genuine new-subsection INSERT that pushes existing subsections upward (e.g.
    # "lisätään § uusi 3 momentti, jolloin nykyinen 3 momentti siirtyy 4
    # momentiksi").  An in-place replacement has the form OMISSION + SUBSECTION
    # with nothing after the subsection.
    children_list = list(muutos_ir.children)
    last_subsec_idx = max(
        i for i, c in enumerate(children_list) if c.kind is IRNodeKind.SUBSECTION
    )
    if any(_is_omission_ir(children_list[j]) for j in range(last_subsec_idx + 1, len(children_list))):
        return False
    has_heading = any(child.kind is IRNodeKind.HEADING for child in muutos_ir.children)
    has_num = any(child.kind is IRNodeKind.NUM for child in muutos_ir.children)
    return has_heading and has_num


def _preserve_live_heading_for_targeted_section_shell_ir(
    master_sec: IRNode,
    amend_sec: IRNode,
) -> IRNode:
    """Keep the live heading authoritative for targeted section-shell replaces."""
    live_heading = next((child for child in master_sec.children if child.kind is IRNodeKind.HEADING), None)
    amend_heading = next((child for child in amend_sec.children if child.kind is IRNodeKind.HEADING), None)
    if live_heading is None or amend_heading is None or amend_heading.text == live_heading.text:
        return amend_sec

    rebuilt_children: list[IRNode] = []
    replaced = False
    for child in amend_sec.children:
        if child.kind is IRNodeKind.HEADING and not replaced:
            rebuilt_children.append(live_heading)
            replaced = True
        else:
            rebuilt_children.append(child)
    return _tops._with_children(amend_sec, rebuilt_children)


def _pre_resolve_omissions(
    ctx: "PayloadElaborationContext",
    muutos_ir: Optional[IRNode],
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    group_ops: List["AmendmentOp"],
    profile: "ReplayProfile",
) -> Optional[IRNode]:
    """Pre-resolve omission markers in amendment IRNode against live snapshot.

    Uses ``ctx.live_node`` (Class 2: local subtree) for section/container
    resolution.  For the INSERT fallback (section with payload label different
    from target), walks ``ctx.parent_node`` children as a bounded lookup.
    """
    if muutos_ir is None or not _has_section_omissions_ir(muutos_ir):
        return muutos_ir

    def _has_whole_op(op_type: str) -> bool:
        return any(
            op.op_type == op_type
            and not op.target_paragraph
            and not op.target_item
            and not op.target_special
            for op in group_ops
        )

    # Section-level: REPLACE or INSERT with omissions
    if target_unit_kind == "section":
        master_ref = None
        if ctx.live_node is not None and _is_compact_first_subsection_replace_shell_ir(muutos_ir, group_ops):
            resolved_inner = _merge_section_inner_subsection_omission_ir(ctx.live_node, muutos_ir)
            if resolved_inner is not None:
                return resolved_inner
        elif ctx.live_node is not None and _is_single_subsection_replace_section_omission_shell_ir(muutos_ir, group_ops):
            # When the group contains an explicit heading op (target_special="otsikko")
            # the johtolause explicitly changes the heading — trust the amendment
            # heading and do NOT overwrite it with the stale live heading.
            has_explicit_heading_op = any(
                str(getattr(op, "target_special", "") or "").strip() == "otsikko"
                for op in group_ops
            )
            shell_for_merge = (
                muutos_ir
                if has_explicit_heading_op
                else _preserve_live_heading_for_targeted_section_shell_ir(ctx.live_node, muutos_ir)
            )
            resolved = _merge_section_with_omission_ir(
                ctx.live_node,
                shell_for_merge,
                group_ops=group_ops,
            )
            if resolved is not None:
                return resolved
        elif ctx.live_node is not None and _is_single_subsection_insert_item_shell_ir(muutos_ir, group_ops):
            # INSERT of items INTO an existing subsection: the inner omissions
            # signal that the full existing subsection content must be preserved.
            # Merge the new items in; do not create a new subsection.
            # mark_in_place=True so _apply_subsection_insert replaces the existing
            # subsection in-place instead of renumbering it upward.
            resolved_inner = _merge_section_inner_subsection_omission_ir(
                ctx.live_node, muutos_ir, mark_in_place=True
            )
            if resolved_inner is not None:
                return resolved_inner
        elif ctx.live_node is not None and _is_single_subsection_insert_section_omission_shell_ir(
            muutos_ir, group_ops
        ):
            # INSERT op targeting a subsection but amendment carries the full new
            # subsection content (no inner omissions).  This happens when the
            # johtolause parser fails to extract target_item (item-level INSERT),
            # leaving only target_paragraph.  Merge the section preserving the live
            # heading and mark the targeted subsection in-place so
            # _apply_subsection_insert replaces it without renumbering.
            #
            # Guard: only apply in-place merge when the target subsection ALREADY
            # EXISTS in the live section.  If it doesn't exist, the op is a genuine
            # new-subsection INSERT and must follow the normal INSERT path (which
            # will insert at the right position).
            live_subsecs = [c for c in ctx.live_node.children if c.kind is IRNodeKind.SUBSECTION]
            target_paragraphs = {
                op.target_paragraph
                for op in group_ops
                if op.op_type == "INSERT" and op.target_paragraph is not None and not op.target_item
            }
            target_exists_in_live = any(
                op_para is not None
                and any(
                    normalized_label_key(sub.label) == normalized_label_key(str(op_para))
                    and sub.attrs.get("lawvm_repeal_placeholder") != "1"
                    for sub in live_subsecs
                )
                for op_para in target_paragraphs
            )
            if target_exists_in_live:
                shell_for_merge = _preserve_live_heading_for_targeted_section_shell_ir(ctx.live_node, muutos_ir)
                resolved = _merge_section_with_omission_ir(ctx.live_node, shell_for_merge)
                if resolved is not None:
                    if target_paragraphs:
                        resolved = _mark_targeted_subsections_in_place(resolved, target_paragraphs)
                    return resolved
        elif _has_whole_op("REPLACE"):
            if any(bool(op.target_item) for op in group_ops):
                return muutos_ir
            master_ref = ctx.live_node
        elif _has_whole_op("INSERT") and profile.replace_same_numbered_section_insert:
            master_ref = ctx.live_node
            if master_ref is None and muutos_ir.label:
                # Fallback: find a section by the payload's own label within the
                # bounded parent subtree.  This covers the case where a same-
                # numbered INSERT targets a section that doesn't exist yet but
                # another section with the payload's label does exist nearby.
                if ctx.parent_node is not None:
                    master_ref = next(
                        (
                            child
                            for child in ctx.parent_node.children
                            if child.kind is IRNodeKind.SECTION and child.label == muutos_ir.label
                        ),
                        None,
                    )
        if master_ref is not None:
            resolved = _merge_section_with_omission_ir(master_ref, muutos_ir, group_ops=group_ops)
            if resolved is not None:
                # When at least one op in the group has a target_item (item-level
                # INSERT), the whole-subsection INSERT ops are artifacts of the
                # XML structure — they update existing subsections, not insert
                # new ones.  Mark the targeted subsections with lawvm_in_place_merge
                # so _apply_subsection_insert replaces them in-place instead of
                # renumbering them upward.
                item_target_paragraphs = {
                    op.target_paragraph
                    for op in group_ops
                    if op.target_paragraph is not None and bool(op.target_item)
                }
                if item_target_paragraphs:
                    resolved = _mark_targeted_subsections_in_place(resolved, item_target_paragraphs)
                return resolved

        # Container-level: REPLACE with omissions
        if target_unit_kind in {"chapter", "part"} and _has_whole_op("REPLACE"):
            # For container targets (L/O), ctx.live_node IS the container
            if ctx.live_node is not None:
                merged = _merge_same_numbered_container_insert_ir(ctx.live_node, muutos_ir)
                return merged if merged is not None else ctx.live_node

    return muutos_ir
