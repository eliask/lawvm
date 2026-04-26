"""Typed StructuralTransformPlan for large-restructure amendments.

When an amendment contains chapter/part inserts alongside cross-container
moves, renumber operations, or a high proportion of uncovered body, the
standard leaf-level replay is insufficient.  A StructuralTransformPlan
captures the restructure intent so that:

  1. Moves / relabels are identified first.
  2. Subtree claims under new chapter/part nodes are carried along.
  3. Leaf replacements are ordered after the structural scaffold is laid.

Plan building: ``build_restructure_plan()`` constructs the plan from
clause-surface data and body coverage.

Plan execution: ``execute_restructure_plan()`` applies MOVE and RELABEL
ops to the IR state tree via copy-on-write rebuilds.  Other op kinds
(INSERT_SUBTREE, REPLACE_LEAF, etc.) are handled by existing replay paths
and are skipped by the executor.

Types are Finland-specific and live in the finland frontend, not in core.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, List, Optional, Tuple

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.phase_result import Finding
from lawvm.finland.source_pathology import build_recodification_source_chain_gap_pathology
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core import tree_ops as _tops
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.ops import AmendmentOp

if TYPE_CHECKING:
    from lawvm.finland.migration_ledger import MigrationLedger

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RestructureSignal(Enum):
    """Observable signals that indicate a large-restructure amendment."""

    CHAPTER_INSERT = "chapter_insert"
    PART_INSERT = "part_insert"
    CROSS_CONTAINER_MOVE = "cross_container_move"
    RELABEL = "relabel"
    HIGH_UNCOVERED_BODY = "high_uncovered_body"


class TransformOpKind(Enum):
    """Ordered operation kinds for structural transform execution.

    Ordering contract (lower ordinal = must execute first):
      1. MOVE / RELABEL — reposition or rename structural containers
      2. INSERT_SUBTREE — insert new chapter/part with claimed children
      3. REPLACE_SUBTREE — replace an existing subtree wholesale
      4. REPLACE_LEAF — replace a single section/subsection leaf
      5. REPEAL_NODE — repeal a node (leaf or container)

    Callers that iterate ops to execute them should sort by
    ``TransformOpKind.execution_order()``.
    """

    MOVE = "move"
    RELABEL = "relabel"
    INSERT_SUBTREE = "insert_subtree"
    REPLACE_SUBTREE = "replace_subtree"
    REPLACE_LEAF = "replace_leaf"
    REPEAL_NODE = "repeal_node"

    def execution_order(self) -> int:
        """Return a stable execution-order key (lower = earlier)."""
        _ORDER: dict[str, int] = {
            "move": 0,
            "relabel": 0,
            "insert_subtree": 1,
            "replace_subtree": 2,
            "replace_leaf": 3,
            "repeal_node": 4,
        }
        return _ORDER.get(self.value, 99)


# ---------------------------------------------------------------------------
# StructuralTransformOp
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StructuralTransformOp:
    """One atomic operation within a StructuralTransformPlan.

    ``target`` is a normalized address string (e.g. "chapter:3" or
    "chapter:3/section:20").  ``destination`` is used for MOVE ops.
    ``payload_claim_ids`` lists the body units (ObservedBodyUnit.unit_id)
    claimed as payload for this op — used for subtree INSERT/REPLACE.
    """

    kind: TransformOpKind
    target: str
    destination: Optional[str] = None
    payload_claim_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "target": self.target,
            "destination": self.destination,
            "payload_claim_ids": list(self.payload_claim_ids),
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# StructuralTransformPlan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StructuralTransformPlan:
    """Plan for replaying a large-restructure amendment.

    ``signals`` records why this plan was built (the detected restructure
    evidence).  ``ops`` is the ordered sequence of operations to execute
    — callers should iterate in the order given or sort by
    ``op.kind.execution_order()``.  ``confidence`` is in [0, 1] and
    reflects confidence that the plan correctly models the restructure
    intent (lower = more speculative).

    The plan is built from clause-surface data and body coverage; it
    describes structural intent.  MOVE and RELABEL ops are executed by
    ``execute_restructure_plan()`` before leaf-level replay proceeds.
    Other op kinds (INSERT_SUBTREE, REPLACE_LEAF, etc.) are handled by
    the standard replay pipeline.
    """

    statute_id: str
    amendment_id: str
    signals: tuple[RestructureSignal, ...]
    ops: tuple[StructuralTransformOp, ...]
    confidence: float

    @property
    def has_unexecuted_ops(self) -> bool:
        """True if any ops require future execution (MOVE/RELABEL)."""
        return any(op.kind in (TransformOpKind.MOVE, TransformOpKind.RELABEL) for op in self.ops)

    @property
    def ops_ordered(self) -> tuple[StructuralTransformOp, ...]:
        """Ops sorted by execution order (stable sort, preserves tie order)."""
        return tuple(sorted(self.ops, key=lambda op: op.kind.execution_order()))

    def to_dict(self) -> dict[str, object]:
        return {
            "statute_id": self.statute_id,
            "amendment_id": self.amendment_id,
            "signals": [s.value for s in self.signals],
            "ops": [op.to_dict() for op in self.ops_ordered],
            "confidence": self.confidence,
            "has_unexecuted_ops": self.has_unexecuted_ops,
        }


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------

# Thresholds for restructure detection
_CHAPTER_INSERT_UNCOV_RATIO_THRESHOLD = 0.5
_CHAPTER_INSERT_TOTAL_UNITS_THRESHOLD = 10


def detect_restructure_signals(
    *,
    ops: list[AmendmentOp],
    uncov_ratio: float,
    total_units: int,
) -> tuple[RestructureSignal, ...]:
    """Detect which restructure signals are present for an amendment.

    Returns an empty tuple if no signals are detected.
    """
    signals: list[RestructureSignal] = []

    has_chapter_insert = any(op.target_unit_kind == "chapter" and op.op_type == "INSERT" for op in ops)
    has_part_insert = any(op.target_unit_kind == "part" and op.op_type == "INSERT" for op in ops)
    has_renumber = any(op.op_type == "RENUMBER" for op in ops)

    if has_chapter_insert:
        signals.append(RestructureSignal.CHAPTER_INSERT)
    if has_part_insert:
        signals.append(RestructureSignal.PART_INSERT)
    if has_renumber:
        signals.append(RestructureSignal.RELABEL)
    if (
        total_units > _CHAPTER_INSERT_TOTAL_UNITS_THRESHOLD
        and uncov_ratio > _CHAPTER_INSERT_UNCOV_RATIO_THRESHOLD
    ):
        signals.append(RestructureSignal.HIGH_UNCOVERED_BODY)

    return tuple(signals)


def build_restructure_plan(
    statute_id: str,
    amendment_id: str,
    *,
    ops: list[AmendmentOp],
    uncov_ratio: float,
    total_units: int,
    body_unit_ids_by_chapter: Optional[dict[tuple[str, str], list[str]]] = None,
) -> Optional[StructuralTransformPlan]:
    """Build a StructuralTransformPlan when restructure signals are present.

    Returns None if no signals are detected (not a restructure amendment).

    ``body_unit_ids_by_chapter`` maps ``(part_label, chapter_label)`` to the
    list of body-unit IDs that fall under that chapter. When provided, chapter
    INSERT ops are augmented with the body-unit IDs of their claimed sections
    as subtree payload claims.

    Op ordering in the returned plan: MOVE/RELABEL < INSERT_SUBTREE <
    REPLACE_SUBTREE < REPLACE_LEAF < REPEAL_NODE.
    """
    signals = detect_restructure_signals(
        ops=ops,
        uncov_ratio=uncov_ratio,
        total_units=total_units,
    )
    if not signals:
        return None

    body_unit_ids_by_chapter = body_unit_ids_by_chapter or {}

    transform_ops: list[StructuralTransformOp] = []
    seen_transform_ops: set[StructuralTransformOp] = set()

    def _normalize_label(kind: str, label: str) -> str:
        if not label:
            return label
        if kind in {"part", "chapter", "section"}:
            return _norm_num_token(label)
        return label

    def _address_to_string(path: tuple[tuple[str, str], ...] | list[tuple[str, str]]) -> str:
        return "/".join(
            f"{kind}:{_normalize_label(kind, label)}"
            for kind, label in path
            if label
        )

    for op in ops:
        op_type = op.op_type
        target_unit_kind = op.target_unit_kind
        target_section = _normalize_label("section", str(op.target_section or ""))
        target_chapter = _normalize_label("chapter", str(op.target_chapter or ""))
        target_part = _normalize_label("part", str(op.target_part or ""))

        is_chapter = target_unit_kind == "chapter"
        is_part = target_unit_kind == "part"
        is_section = target_unit_kind == "section"

        if op_type == "RENUMBER":
            is_root_relabel = (
                op.target_paragraph is None
                and not op.target_item
                and not op.target_special
            )
            if not is_root_relabel:
                continue
            destination_addr = None
            if op.lo is not None and op.lo.target is not None:
                target_addr = _address_to_string(op.lo.target.path)
            elif is_chapter:
                if target_section:
                    target_addr = (
                        f"part:{target_part}/chapter:{target_section}"
                        if target_part
                        else f"chapter:{target_section}"
                    )
                else:
                    target_addr = (
                        f"part:{target_part}/chapter:{target_chapter}"
                        if target_part and target_chapter
                        else f"chapter:{target_chapter}"
                    )
            elif is_part:
                target_addr = f"part:{target_section}" if target_section else f"part:{target_part}"
            elif is_section:
                target_addr = (
                    (
                        f"part:{target_part}/chapter:{target_chapter}/section:{target_section}"
                        if target_part
                        else f"chapter:{target_chapter}/section:{target_section}"
                    )
                    if target_chapter
                    else (
                        f"part:{target_part}/section:{target_section}"
                        if target_part
                        else f"section:{target_section}"
                    )
                )
            else:
                target_addr = target_section
            if op.lo is not None and op.lo.destination is not None:
                destination_addr = _address_to_string(op.lo.destination.path)
            op_out = StructuralTransformOp(
                kind=TransformOpKind.RELABEL,
                target=target_addr,
                destination=destination_addr,
                notes=("from_amendment_op",),
            )
            if op_out not in seen_transform_ops:
                seen_transform_ops.add(op_out)
                transform_ops.append(op_out)

        elif op_type == "INSERT":
            if is_chapter and target_section:
                chapter_label = target_section
                chapter_key = (target_part, chapter_label)
                # Gather body units under this chapter as subtree payload claims
                subtree_ids = tuple(body_unit_ids_by_chapter.get(chapter_key, []))
                target_addr = (
                    f"part:{target_part}/chapter:{chapter_label}"
                    if target_part
                    else f"chapter:{chapter_label}"
                )
                note = "chapter_insert_subtree"
                if subtree_ids:
                    note = f"chapter_insert_subtree:{len(subtree_ids)}_children"
                op_out = StructuralTransformOp(
                    kind=TransformOpKind.INSERT_SUBTREE,
                    target=target_addr,
                    payload_claim_ids=subtree_ids,
                    notes=(note,),
                )
                if op_out not in seen_transform_ops:
                    seen_transform_ops.add(op_out)
                    transform_ops.append(op_out)
            elif is_part and target_section:
                op_out = StructuralTransformOp(
                    kind=TransformOpKind.INSERT_SUBTREE,
                    target=f"part:{_normalize_label('part', target_section)}",
                    notes=("part_insert_subtree",),
                )
                if op_out not in seen_transform_ops:
                    seen_transform_ops.add(op_out)
                    transform_ops.append(op_out)
            elif is_section and target_section:
                target_addr = (
                    (
                        f"part:{target_part}/chapter:{target_chapter}/section:{target_section}"
                        if target_part
                        else f"chapter:{target_chapter}/section:{target_section}"
                    )
                    if target_chapter
                    else (
                        f"part:{target_part}/section:{target_section}"
                        if target_part
                        else f"section:{target_section}"
                    )
                )
                op_out = StructuralTransformOp(
                    kind=TransformOpKind.REPLACE_LEAF,
                    target=target_addr,
                    notes=("section_insert",),
                )
                if op_out not in seen_transform_ops:
                    seen_transform_ops.add(op_out)
                    transform_ops.append(op_out)

        elif op_type == "REPLACE":
            if is_chapter and target_section:
                op_out = StructuralTransformOp(
                    kind=TransformOpKind.REPLACE_SUBTREE,
                    target=(
                        f"part:{target_part}/chapter:{target_section}"
                        if target_part
                        else f"chapter:{target_section}"
                    ),
                    notes=("chapter_replace",),
                )
                if op_out not in seen_transform_ops:
                    seen_transform_ops.add(op_out)
                    transform_ops.append(op_out)
            elif is_section and target_section:
                target_addr = (
                    (
                        f"part:{target_part}/chapter:{target_chapter}/section:{target_section}"
                        if target_part
                        else f"chapter:{target_chapter}/section:{target_section}"
                    )
                    if target_chapter
                    else (
                        f"part:{target_part}/section:{target_section}"
                        if target_part
                        else f"section:{target_section}"
                    )
                )
                op_out = StructuralTransformOp(
                    kind=TransformOpKind.REPLACE_LEAF,
                    target=target_addr,
                    notes=("section_replace",),
                )
                if op_out not in seen_transform_ops:
                    seen_transform_ops.add(op_out)
                    transform_ops.append(op_out)

        elif op_type == "REPEAL":
            if is_chapter and target_section:
                op_out = StructuralTransformOp(
                    kind=TransformOpKind.REPEAL_NODE,
                    target=(
                        f"part:{target_part}/chapter:{target_section}"
                        if target_part
                        else f"chapter:{target_section}"
                    ),
                    notes=("chapter_repeal",),
                )
                if op_out not in seen_transform_ops:
                    seen_transform_ops.add(op_out)
                    transform_ops.append(op_out)
            elif is_section and target_section:
                target_addr = (
                    (
                        f"part:{target_part}/chapter:{target_chapter}/section:{target_section}"
                        if target_part
                        else f"chapter:{target_chapter}/section:{target_section}"
                    )
                    if target_chapter
                    else (
                        f"part:{target_part}/section:{target_section}"
                        if target_part
                        else f"section:{target_section}"
                    )
                )
                op_out = StructuralTransformOp(
                    kind=TransformOpKind.REPEAL_NODE,
                    target=target_addr,
                    notes=("section_repeal",),
                )
                if op_out not in seen_transform_ops:
                    seen_transform_ops.add(op_out)
                    transform_ops.append(op_out)

    # Coalesce exact duplicate transform ops while preserving first-seen order.
    # Recovery/restructure planning can observe the same normalized section
    # target through multiple fine-grained ops. The plan must carry one
    # structural leaf rewrite per exact target/op shape, not replay the same
    # structural rewrite repeatedly and rely on replay-fold dedup to recover.
    seen_transform_ops: set[StructuralTransformOp] = set()
    coalesced_transform_ops: list[StructuralTransformOp] = []
    for transform_op in transform_ops:
        if transform_op in seen_transform_ops:
            continue
        seen_transform_ops.add(transform_op)
        coalesced_transform_ops.append(transform_op)

    # Sort by execution order
    transform_ops_sorted = tuple(sorted(coalesced_transform_ops, key=lambda op: op.kind.execution_order()))

    # Confidence: lower when we have MOVE/RELABEL ops (future execution needed)
    # or when the uncov ratio is very high (may be wholesale replacement).
    has_relabel = any(op.kind == TransformOpKind.RELABEL for op in transform_ops_sorted)
    confidence: float
    if has_relabel and uncov_ratio > 0.8:
        confidence = 0.4
    elif has_relabel:
        confidence = 0.6
    elif uncov_ratio > 0.8:
        confidence = 0.5
    else:
        confidence = 0.75

    return StructuralTransformPlan(
        statute_id=statute_id,
        amendment_id=amendment_id,
        signals=signals,
        ops=transform_ops_sorted,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Address parsing
# ---------------------------------------------------------------------------


def _parse_address(address: str) -> List[Tuple[str, str]]:
    """Parse a plan address string into a path of (kind, label) pairs.

    Examples:
        "chapter:3"            → [("chapter", "3")]
        "chapter:3/section:20" → [("chapter", "3"), ("section", "20")]
        "section:5"            → [("section", "5")]
    """
    parts: List[Tuple[str, str]] = []
    for segment in address.split("/"):
        if ":" not in segment:
            continue
        kind, label = segment.split(":", 1)
        norm_kind = kind.strip()
        norm_label = label.strip()
        if norm_kind in {"part", "chapter", "section"}:
            norm_label = _norm_num_token(norm_label)
        parts.append((norm_kind, norm_label))
    return parts


def _find_path_by_suffix(tree: IRNode, target_path: List[Tuple[str, str]]) -> Optional[Tuple[Tuple[str, str], ...]]:
    """Find a live tree path whose suffix matches the plan-owned target path."""
    if not target_path:
        return None
    leaf_kind, leaf_label = target_path[-1]
    candidates = _tops.find_all(tree, leaf_kind, leaf_label)
    target_suffix = tuple(target_path)

    def _normalize_path(
        path: Tuple[Tuple[str, str], ...],
    ) -> Tuple[Tuple[str, str], ...]:
        return tuple(
            (
                kind,
                _norm_num_token(label) if kind in {"part", "chapter", "section"} else label,
            )
            for kind, label in path
        )

    normalized_target_suffix = _normalize_path(target_suffix)
    for candidate in candidates:
        if len(candidate) < len(target_suffix):
            continue
        if _normalize_path(tuple(candidate[-len(target_suffix):])) == normalized_target_suffix:
            return tuple(candidate)
    return None


def _tree_has_part_nodes(tree: IRNode) -> bool:
    stack = [tree]
    while stack:
        node = stack.pop()
        if node.kind is IRNodeKind.PART:
            return True
        stack.extend(reversed(node.children))
    return False


def _find_path_in_pre_partification_frame(
    tree: IRNode,
    target_path: List[Tuple[str, str]],
) -> Optional[Tuple[Tuple[str, str], ...]]:
    """Resolve part-scoped targets against a pre-partification live tree.

    Some large Finland restructure waves describe targets under newly created
    parts even though the live pre-amendment tree still has only root chapters.
    When the tree has no PART nodes yet, allow a bounded fallback that strips
    one leading part prefix and resolves the remainder in the old frame.
    """
    if _tree_has_part_nodes(tree):
        return None
    if not target_path or target_path[0][0] != "part":
        return None
    return _find_path_by_suffix(tree, target_path[1:])


def _find_path_in_loose_trailing_section_frame(
    tree: IRNode,
    target_path: List[Tuple[str, str]],
    *,
    part_relabel_sources: dict[str, str] | None = None,
) -> Optional[Tuple[Tuple[str, str], ...]]:
    """Resolve chapter-scoped section relabels against one loose trailing sibling.

    Some historical Finland pre-wave trees keep the last section of a chapter as
    a loose sibling immediately after the chapter wrapper even though the
    amendment text explicitly scopes the target under that chapter. When the
    chapter exists, the scoped section does not, and there is exactly one loose
    trailing section with the same label immediately after the chapter, allow a
    bounded recovery lookup to that pre-wrapper source leaf.
    """
    if len(target_path) < 2 or target_path[-1][0] != "section" or target_path[-2][0] != "chapter":
        return None
    parent_found = _resolve_relabel_lookup_path(
        tree,
        target_path[:-1],
        part_relabel_sources=part_relabel_sources,
    )
    if parent_found is None:
        return None
    parent_container_path = tuple(parent_found[:-1])
    parent_container = _tops.resolve(tree, parent_container_path) if parent_container_path else tree
    if parent_container is None:
        return None
    try:
        chapter_index = next(
            idx
            for idx, child in enumerate(parent_container.children)
            if child.kind is IRNodeKind.CHAPTER and _norm_num_token(child.label or "") == _norm_num_token(parent_found[-1][1])
        )
    except StopIteration:
        return None

    target_norm = _norm_num_token(target_path[-1][1])
    scan_index = chapter_index + 1
    candidate_path: Optional[Tuple[Tuple[str, str], ...]] = None
    while scan_index < len(parent_container.children):
        child = parent_container.children[scan_index]
        if child.kind in {IRNodeKind.CHAPTER, IRNodeKind.PART}:
            break
        if child.kind is not IRNodeKind.SECTION:
            return None
        if _norm_num_token(child.label or "") == target_norm:
            if candidate_path is not None:
                return None
            candidate_path = parent_container_path + (("section", child.label or ""),)
        scan_index += 1
    return candidate_path


def _find_path_in_pre_part_relabel_frame(
    tree: IRNode,
    target_path: List[Tuple[str, str]],
    *,
    part_relabel_sources: dict[str, str],
) -> Optional[Tuple[Tuple[str, str], ...]]:
    """Resolve a part-scoped target against the pre-part-relabel live tree.

    Large Finland restructure waves such as ``2019/371`` can express section
    relabel targets under the amendment's post-relabel part numbering even
    though plan execution still runs against the pre-wave tree. When the plan
    itself also carries a part relabel chain (for example ``IIa -> 3`` and
    ``3 -> 4``), prefer a bounded one-step rewrite of the leading part label
    back to its source frame before ordinary lookup. This prevents explicit
    post-wave targets from silently hijacking an unrelated live same-label
    provision that already exists under the destination part.
    """
    if not target_path or target_path[0][0] != "part":
        return None
    source_part = part_relabel_sources.get(_norm_num_token(target_path[0][1]))
    if not source_part:
        return None
    remapped_path = [("part", source_part), *target_path[1:]]
    return _find_path_by_suffix(tree, remapped_path)


def _candidate_source_paths_for_relabel_lookup(
    target_path: List[Tuple[str, str]],
    *,
    part_relabel_sources: dict[str, str] | None = None,
) -> tuple[tuple[tuple[str, str], ...], ...]:
    """Return bounded source-path candidates that relabel lookup may consume.

    This is used only for diagnosis when a later relabel cannot resolve its
    target because an earlier relabel in the same plan already consumed the
    relevant pre-wave source node. Candidates mirror the explicit source path
    and the one-step pre-part remap frame; we deliberately do not broaden this
    to arbitrary lookup fallbacks.
    """
    candidates: list[tuple[tuple[str, str], ...]] = []
    if target_path:
        candidates.append(tuple(target_path))
    if part_relabel_sources and target_path and target_path[0][0] == "part":
        source_part = part_relabel_sources.get(_norm_num_token(target_path[0][1]))
        if source_part:
            remapped = (("part", source_part), *tuple(target_path[1:]))
            if remapped not in candidates:
                candidates.append(remapped)
    return tuple(candidates)


def _resolve_relabel_lookup_path(
    tree: IRNode,
    target_path: List[Tuple[str, str]],
    *,
    part_relabel_sources: dict[str, str] | None = None,
) -> Optional[Tuple[Tuple[str, str], ...]]:
    """Resolve one relabel lookup path using the executor's bounded search rules."""
    found_path = None
    if part_relabel_sources and len(target_path) > 1:
        found_path = _find_path_in_pre_part_relabel_frame(
            tree,
            target_path,
            part_relabel_sources=part_relabel_sources,
        )
    if found_path is None:
        found_path = _find_path_by_suffix(tree, target_path)
    if found_path is None and len(target_path) == 1:
        found_path = _tops.find(tree, target_path[-1][0], target_path[-1][1])
    if found_path is None:
        found_path = _find_path_in_pre_partification_frame(tree, target_path)
    if found_path is None:
        found_path = _find_path_in_loose_trailing_section_frame(
            tree,
            target_path,
            part_relabel_sources=part_relabel_sources,
        )
    return found_path


def _restore_missing_source_part_alias(
    tree: IRNode,
    found_path: Tuple[Tuple[str, str], ...],
    *,
    target_part: str,
    source_part: str,
) -> Tuple[IRNode, Tuple[Tuple[str, str], ...]] | None:
    """Restore a named source-part alias before a post-frame leaf relabel.

    A recodification plan can describe descendant relabels in the destination
    part frame while also carrying a part relabel that proves the pre-wave
    source label. If the old source label is absent but the destination part is
    the node found, keep that recovery explicit by relabeling the ancestor part
    back to the source label for the descendant operation. This is intentionally
    bounded to symbolic source labels such as ``iia``; numeric-to-numeric part
    chains stay on the ordinary exact lookup path.
    """
    if not source_part or source_part.isdigit():
        return None
    part_index = next(
        (
            idx
            for idx, (kind, label) in enumerate(found_path)
            if kind == "part" and _norm_num_token(label) == _norm_num_token(target_part)
        ),
        None,
    )
    if part_index is None:
        return None
    part_path = found_path[: part_index + 1]
    part_node = _tops.resolve(tree, part_path)
    if part_node is None or part_node.kind is not IRNodeKind.PART:
        return None
    restored_part = _relabel_node(part_node, _norm_num_token(source_part))
    restored_tree = _tops.replace_at(tree, part_path, restored_part)
    restored_path = (
        found_path[:part_index]
        + (("part", _norm_num_token(source_part)),)
        + found_path[part_index + 1 :]
    )
    return restored_tree, restored_path


def _classify_missing_relabel_reason(
    tree: IRNode,
    target_path: List[Tuple[str, str]],
    *,
    part_relabel_sources: dict[str, str] | None = None,
    consumed_source_paths: set[tuple[tuple[str, str], ...]] | None = None,
) -> str:
    """Classify why a relabel target could not be resolved.

    The executor must keep missing-target reasons phase-local:
    - a consumed pre-wave source path is distinct from a generic miss
    - a missing leaf under an existing parent is distinct from an absent target
      container produced by a sparse earlier source chain
    """
    if consumed_source_paths:
        candidate_paths = _candidate_source_paths_for_relabel_lookup(
            target_path,
            part_relabel_sources=part_relabel_sources,
        )
        if any(candidate in consumed_source_paths for candidate in candidate_paths):
            return "source_consumed_by_prior_relabel"

    if len(target_path) == 1 and target_path[0][0] == "part":
        return "target_part_absent_in_pre_partification_frame"

    if len(target_path) > 1:
        parent_found = _resolve_relabel_lookup_path(
            tree,
            target_path[:-1],
            part_relabel_sources=part_relabel_sources,
        )
        if parent_found is not None:
            return "target_leaf_absent_under_existing_parent"

        for depth in range(len(target_path) - 2, 0, -1):
            ancestor_found = _resolve_relabel_lookup_path(
                tree,
                target_path[:depth],
                part_relabel_sources=part_relabel_sources,
            )
            if ancestor_found is not None:
                return "target_container_absent"

    return "target_not_found"


# ---------------------------------------------------------------------------
# Tree manipulation helpers (copy-on-write)
# ---------------------------------------------------------------------------


def _relabel_node(node: IRNode, new_label: str) -> IRNode:
    """Return a new IRNode with the label changed to ``new_label``.

    Finland replay products are rendered from the IR tree itself, so a relabel
    must retarget the displayed NUM child for structural roots rather than
    keeping a stale heading like ``5 b §`` under a node now labeled ``5 c``.
    """
    new_children = node.children
    if node.kind in {IRNodeKind.SECTION, IRNodeKind.CHAPTER} and node.children:
        replacement_text = f"{new_label} §" if node.kind is IRNodeKind.SECTION else f"{new_label} luku"
        rewritten_children: list[IRNode] = []
        rewrote_num = False
        for child in node.children:
            if not rewrote_num and child.kind is IRNodeKind.NUM:
                rewritten_children.append(
                    IRNode(
                        kind=child.kind,
                        label=child.label,
                        text=replacement_text,
                        attrs=dict(child.attrs),
                        children=child.children,
                    )
                )
                rewrote_num = True
            else:
                rewritten_children.append(child)
        new_children = tuple(rewritten_children)
    return IRNode(
        kind=node.kind,
        label=new_label,
        text=node.text,
        attrs=dict(node.attrs),
        children=new_children,
    )


# ---------------------------------------------------------------------------
# Execution result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutedOp:
    """Record of one executed structural transform operation."""

    op: StructuralTransformOp
    success: bool
    note: str = ""
    reason_code: str = ""


def relabel_skip_finding(
    executed: ExecutedOp,
    *,
    source_statute: str,
) -> Finding | None:
    """Convert a failed restructure-plan RELABEL ExecutedOp into a governed finding."""
    if executed.success or executed.op.kind is not TransformOpKind.RELABEL:
        return None
    raw_reason = executed.note.strip()
    if not raw_reason:
        return None

    reason_code = executed.reason_code or "unknown"

    return Finding(
        kind="APPLY.RELABEL_SKIP",
        role="observation",
        stage="restructure_plan",
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": "Restructure-plan relabel was skipped for a governed reason.",
            "reason_code": reason_code,
            "raw_reason": raw_reason,
            "target": executed.op.target,
            "destination": executed.op.destination or "",
            "plan_notes": list(executed.op.notes),
            "grouped": reason_code.startswith("group_"),
        },
    )


def relabel_skip_source_pathology_finding(
    executed: ExecutedOp,
    *,
    source_statute: str,
) -> Finding | None:
    """Project bounded relabel-skip recodification gaps into source-pathology."""
    if executed.success or executed.op.kind is not TransformOpKind.RELABEL:
        return None
    reason_code = executed.reason_code or ""
    if reason_code not in {
        "target_leaf_absent_under_existing_parent",
        "target_container_absent",
        "source_consumed_by_prior_relabel",
        "target_part_absent_in_pre_partification_frame",
    }:
        return None

    target_path = _parse_address(executed.op.target)
    if not target_path:
        return None
    leaf_kind, leaf_label = target_path[-1]
    if leaf_kind not in {"part", "chapter", "section"}:
        return None

    target_label = ""
    if leaf_kind == "section":
        chapter_label = ""
        for kind, label in target_path[:-1]:
            if kind == "chapter":
                chapter_label = label
        target_label = f"{chapter_label} luku {leaf_label} §".strip() if chapter_label else f"{leaf_label} §"
    elif leaf_kind == "chapter":
        target_label = f"{leaf_label} luku"
    else:
        target_label = f"{leaf_label} osa"

    pathology = build_recodification_source_chain_gap_pathology(
        source_statute=source_statute,
        target_unit_kind=leaf_kind,
        target_label=target_label,
        diagnostic_reason=reason_code,
    )
    return Finding(
        kind="ELAB.SOURCE_PATHOLOGY",
        role="observation",
        stage="restructure_plan",
        blocking=False,
        source_statute=source_statute,
        detail={
            "code": pathology.code,
            "message": pathology.message,
            "target_unit_kind": pathology.target_unit_kind,
            "target_label": pathology.target_label,
            "detail": dict(pathology.detail),
        },
    )


def move_skip_finding(
    executed: ExecutedOp,
    *,
    source_statute: str,
) -> Finding | None:
    """Convert a failed restructure-plan MOVE ExecutedOp into a governed finding."""
    if executed.success or executed.op.kind is not TransformOpKind.MOVE:
        return None
    raw_reason = executed.note.strip()
    if not raw_reason:
        return None

    reason_code = executed.reason_code or "unknown"

    return Finding(
        kind="APPLY.MOVE_SKIP",
        role="observation",
        stage="restructure_plan",
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": "Restructure-plan move was skipped for a governed reason.",
            "reason_code": reason_code,
            "raw_reason": raw_reason,
            "target": executed.op.target,
            "destination": executed.op.destination or "",
            "plan_notes": list(executed.op.notes),
        },
    )


# ---------------------------------------------------------------------------
# execute_restructure_plan
# ---------------------------------------------------------------------------

# Op kinds that the executor handles.  Other kinds (INSERT_SUBTREE,
# REPLACE_LEAF, etc.) are applied by the existing leaf-level replay path.
_EXECUTABLE_OP_KINDS = frozenset({TransformOpKind.MOVE, TransformOpKind.RELABEL})


def _stabilize_same_parent_relabel_exec_order(
    ops: tuple[StructuralTransformOp, ...],
) -> tuple[StructuralTransformOp, ...]:
    """Reorder same-parent RELABEL chains so consumers run before producers.

    This mirrors the replay-side relabel stabilization for cases like:

      9 § -> 10 §
      10 § -> 11 §
      11 § -> 12 §

    Applied naively in textual order, the first RELABEL can create the source
    label that the next RELABEL then consumes from the just-renamed node.
    The correct execution order is the reverse dependency order:

      11 -> 12, 10 -> 11, 9 -> 10

    Only genuine same-parent RELABEL chains are reordered; all non-RELABEL ops
    stay in their original positions.
    """

    def _relabel_key(op: StructuralTransformOp) -> tuple[str, tuple[tuple[str, str], ...]] | None:
        if op.kind is not TransformOpKind.RELABEL or op.destination is None:
            return None
        target_path = _parse_address(op.target)
        dest_path = _parse_address(op.destination)
        if not target_path or not dest_path:
            return None
        if target_path[:-1] != dest_path[:-1]:
            return None
        if target_path[-1][0] != dest_path[-1][0]:
            return None
        return target_path[-1][0], tuple(target_path[:-1])

    def _source_label(op: StructuralTransformOp) -> str | None:
        target_path = _parse_address(op.target)
        return target_path[-1][1] if target_path else None

    def _dest_label(op: StructuralTransformOp) -> str | None:
        if op.destination is None:
            return None
        dest_path = _parse_address(op.destination)
        return dest_path[-1][1] if dest_path else None

    keyed_positions: dict[tuple[str, tuple[tuple[str, str], ...]], list[int]] = {}
    keyed_ops: dict[tuple[str, tuple[tuple[str, str], ...]], list[StructuralTransformOp]] = {}
    for idx, op in enumerate(ops):
        key = _relabel_key(op)
        if key is None:
            continue
        keyed_positions.setdefault(key, []).append(idx)
        keyed_ops.setdefault(key, []).append(op)

    result = list(ops)
    for key, relabel_ops in keyed_ops.items():
        if len(relabel_ops) < 2:
            continue
        source_to_idx: dict[str, int] = {}
        dests: list[str] = []
        valid = True
        for rel_idx, op in enumerate(relabel_ops):
            src = _source_label(op)
            dst = _dest_label(op)
            if src is None or dst is None:
                valid = False
                break
            source_to_idx[src] = rel_idx
            dests.append(dst)
        if not valid:
            continue

        before: list[set[int]] = [set() for _ in range(len(relabel_ops))]
        has_chain = False
        for rel_idx, dest in enumerate(dests):
            consumer_idx = source_to_idx.get(dest)
            if consumer_idx is not None and consumer_idx != rel_idx:
                before[rel_idx].add(consumer_idx)
                has_chain = True
        if not has_chain:
            continue

        in_degree = [len(b) for b in before]
        unblocks: list[list[int]] = [[] for _ in range(len(relabel_ops))]
        for j in range(len(relabel_ops)):
            for k in before[j]:
                unblocks[k].append(j)

        queue = [j for j in range(len(relabel_ops)) if in_degree[j] == 0]
        topo_order: list[int] = []
        while queue:
            cur = queue.pop(0)
            topo_order.append(cur)
            for nxt in unblocks[cur]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if len(topo_order) != len(relabel_ops):
            continue

        for pos, rel_idx in zip(keyed_positions[key], topo_order):
            result[pos] = relabel_ops[rel_idx]

    return tuple(result)


def _prioritize_descendant_relabels(
    ops: tuple[StructuralTransformOp, ...],
) -> tuple[StructuralTransformOp, ...]:
    """Run deeper relabels before ancestor relabels while preserving relabel slots.

    Large Finland restructure waves can relabel a part, then a chapter inside
    that part, then sections inside that chapter. Descendant relabels must run
    against the pre-ancestor snapshot; otherwise the later lookup chases an old
    chapter/part address that has already been renamed away.
    """
    relabel_positions: list[int] = []
    relabel_ops: list[tuple[int, StructuralTransformOp]] = []
    for idx, op in enumerate(ops):
        if op.kind is not TransformOpKind.RELABEL:
            continue
        relabel_positions.append(idx)
        relabel_ops.append((idx, op))
    if len(relabel_ops) < 2:
        return ops

    ordered_relabels = [
        op
        for _idx, op in sorted(
            relabel_ops,
            key=lambda item: (-len(_parse_address(item[1].target)), item[0]),
        )
    ]
    result = list(ops)
    for pos, op in zip(relabel_positions, ordered_relabels):
        result[pos] = op
    return tuple(result)


def _execute_same_parent_relabel_group(
    tree: IRNode,
    ops: tuple[StructuralTransformOp, ...],
    *,
    part_relabel_sources: dict[str, str] | None = None,
    consumed_source_paths: set[tuple[tuple[str, str], ...]] | None = None,
    migration_ledger: "MigrationLedger | None" = None,
    effective_date: str = "",
    source_statute: str = "",
) -> Tuple[IRNode, List[ExecutedOp]]:
    """Execute a same-parent RELABEL chain atomically against one parent snapshot."""

    if not ops:
        return tree, []

    found_paths: dict[StructuralTransformOp, Tuple[Tuple[str, str], ...]] = {}
    op_by_source: dict[tuple[str, str], StructuralTransformOp] = {}
    dest_by_source: dict[tuple[str, str], str] = {}
    missing_executed: list[ExecutedOp] = []
    for op in ops:
        target_path = _parse_address(op.target)
        dest_path = _parse_address(op.destination or "")
        if not target_path or not dest_path:
            return tree, [
                ExecutedOp(
                    op=o,
                    success=False,
                    note="could not parse grouped relabel address",
                    reason_code="group_parse_failed",
                )
                for o in ops
            ]
        found_path = _resolve_relabel_lookup_path(
            tree,
            target_path,
            part_relabel_sources=part_relabel_sources,
        )
        if found_path is None:
            consumed_reason = _classify_missing_relabel_reason(
                tree,
                target_path,
                part_relabel_sources=part_relabel_sources,
                consumed_source_paths=consumed_source_paths,
            )
            logger.warning(
                "[%s] RELABEL target not found: %s (reason=%s, plan %s/%s)",
                source_statute or "-",
                op.target,
                consumed_reason,
                op.notes,
                op.destination,
            )
            missing_executed.append(
                ExecutedOp(
                    op=op,
                    success=False,
                    note=f"target not found: {op.target}",
                    reason_code=consumed_reason,
                )
            )
            continue
        found_paths[op] = tuple(found_path)
        source_key = found_path[-1]
        op_by_source[source_key] = op
        dest_by_source[source_key] = dest_path[-1][1]

    if not found_paths:
        return tree, missing_executed

    parent_paths = {path[:-1] for path in found_paths.values()}
    if len(parent_paths) != 1:
        return tree, missing_executed + [
            ExecutedOp(
                op=o,
                success=False,
                note="grouped relabel paths do not share one parent",
                reason_code="group_parent_mismatch",
            )
            for o in found_paths
        ]
    parent_path = next(iter(parent_paths))
    parent_node = _tops.resolve(tree, parent_path) if parent_path else tree
    if parent_node is None:
        return tree, missing_executed + [
            ExecutedOp(
                op=op,
                success=False,
                note=f"parent not found: {parent_path!r}",
                reason_code="group_parent_not_found",
            )
            for op in found_paths
        ]

    parent_child_keys = {
        (child.kind.value, child.label)
        for child in parent_node.children
        if child.label is not None
    }
    found_source_keys = set(op_by_source)
    collision_ops: list[ExecutedOp] = []
    for source_key, op in op_by_source.items():
        dest_label = dest_by_source[source_key]
        dest_key = (source_key[0], dest_label)
        if dest_key in parent_child_keys and dest_key not in found_source_keys:
            collision_ops.append(
                ExecutedOp(
                    op=op,
                    success=False,
                    note=f"grouped relabel destination occupied: {dest_key[0]}:{dest_key[1]}",
                    reason_code="group_destination_collision",
                )
            )
    if collision_ops:
        return tree, missing_executed + collision_ops

    new_children: list[IRNode] = []
    executed: list[ExecutedOp] = list(missing_executed)
    seen_sources: set[tuple[str, str]] = set()
    for child in parent_node.children:
        if child.label is None:
            new_children.append(child)
            continue
        source_key = (child.kind.value, child.label)
        if source_key in dest_by_source:
            seen_sources.add(source_key)
            relabeled_child = _relabel_node(child, dest_by_source[source_key])
            new_children.append(relabeled_child)
            if migration_ledger is not None:
                ledger = migration_ledger
                found_path = found_paths[op_by_source[source_key]]
                from_addr = LegalAddress(path=found_path)
                to_addr = LegalAddress(path=found_path[:-1] + ((found_path[-1][0], dest_by_source[source_key]),))
                ledger.record_renumber(
                    from_addr,
                    to_addr,
                    effective=effective_date,
                    source_statute=source_statute,
                )
            executed.append(
                ExecutedOp(
                    op=op_by_source[source_key],
                    success=True,
                    note=f"relabeled to {dest_by_source[source_key]}",
                )
            )
        else:
            new_children.append(child)

    missing_sources = [source_key for source_key in op_by_source if source_key not in seen_sources]
    if missing_sources:
        for source_key in missing_sources:
            op = op_by_source[source_key]
            logger.warning(
                "[%s] RELABEL target not found: %s (reason=%s, plan %s/%s)",
                source_statute or "-",
                op.target,
                "target_not_found",
                op.notes,
                op.destination,
            )
            executed.append(
                ExecutedOp(
                    op=op,
                    success=False,
                    note=f"target not found: {op.target}",
                    reason_code="target_not_found",
                )
            )

    rebuilt_parent = IRNode(
        kind=parent_node.kind,
        label=parent_node.label,
        text=parent_node.text,
        attrs=dict(parent_node.attrs),
        children=tuple(new_children),
    )
    if parent_path:
        tree = _tops.replace_at(tree, parent_path, rebuilt_parent)
    else:
        tree = rebuilt_parent
    tree = _tops.resort_children(tree)
    return tree, executed


def execute_restructure_plan(
    plan: StructuralTransformPlan,
    tree: IRNode,
    *,
    migration_ledger: "MigrationLedger | None" = None,
    effective_date: str = "",
) -> Tuple[IRNode, List[ExecutedOp]]:
    """Execute MOVE and RELABEL ops from a StructuralTransformPlan.

    Other op kinds are silently skipped (they are handled elsewhere in
    the replay pipeline).

    Args:
        plan: The plan to execute.
        tree: The current IR state tree (body-level IRNode).

    Returns:
        (modified_tree, executed_ops) — the tree after applying all
        executable ops, and a list of ExecutedOp records for auditing.
    """
    executed: List[ExecutedOp] = []
    part_relabel_sources: dict[str, str] = {}
    for op in plan.ops:
        if op.kind is not TransformOpKind.RELABEL:
            continue
        target_path = _parse_address(op.target)
        dest_path = _parse_address(op.destination or "")
        if (
            target_path
            and dest_path
            and len(target_path) == 1
            and len(dest_path) == 1
            and target_path[0][0] == "part"
            and dest_path[0][0] == "part"
        ):
            part_relabel_sources[_norm_num_token(dest_path[0][1])] = _norm_num_token(target_path[0][1])

    ordered_ops = _stabilize_same_parent_relabel_exec_order(plan.ops_ordered)
    ordered_ops = _prioritize_descendant_relabels(ordered_ops)
    consumed_relabel_sources: set[tuple[tuple[str, str], ...]] = set()
    i = 0
    while i < len(ordered_ops):
        op = ordered_ops[i]
        if op.kind is TransformOpKind.RELABEL:
            target_path = _parse_address(op.target)
            if target_path:
                parent_path = tuple(target_path[:-1])
                leaf_kind = target_path[-1][0]
                group: list[StructuralTransformOp] = [op]
                j = i + 1
                while j < len(ordered_ops):
                    next_op = ordered_ops[j]
                    if next_op.kind is not TransformOpKind.RELABEL:
                        break
                    next_target = _parse_address(next_op.target)
                    next_dest = _parse_address(next_op.destination or "")
                    if (
                        not next_target
                        or not next_dest
                        or tuple(next_target[:-1]) != parent_path
                        or next_target[-1][0] != leaf_kind
                        or tuple(next_dest[:-1]) != parent_path
                        or next_dest[-1][0] != leaf_kind
                    ):
                        break
                    group.append(next_op)
                    j += 1
                if len(group) > 1:
                    tree, group_exec = _execute_same_parent_relabel_group(
                        tree,
                        tuple(group),
                        part_relabel_sources=part_relabel_sources,
                        consumed_source_paths=consumed_relabel_sources,
                        migration_ledger=migration_ledger,
                        effective_date=effective_date,
                        source_statute=plan.amendment_id,
                    )
                    executed.extend(group_exec)
                    for exec_op in group_exec:
                        if exec_op.success:
                            target_path = tuple(_parse_address(exec_op.op.target))
                            if target_path:
                                consumed_relabel_sources.add(target_path)
                    i = j
                    continue

        if op.kind not in _EXECUTABLE_OP_KINDS:
            i += 1
            continue

        if op.kind == TransformOpKind.RELABEL:
            tree, exec_op = _execute_relabel(
                tree,
                op,
                part_relabel_sources=part_relabel_sources,
                consumed_source_paths=consumed_relabel_sources,
                migration_ledger=migration_ledger,
                effective_date=effective_date,
                source_statute=plan.amendment_id,
            )
            executed.append(exec_op)
            if exec_op.success:
                target_path = tuple(_parse_address(exec_op.op.target))
                if target_path:
                    consumed_relabel_sources.add(target_path)

        elif op.kind == TransformOpKind.MOVE:
            tree, exec_op = _execute_move(
                tree,
                op,
                migration_ledger=migration_ledger,
                effective_date=effective_date,
                source_statute=plan.amendment_id,
            )
            executed.append(exec_op)
        i += 1
    return tree, executed


def _execute_relabel(
    tree: IRNode,
    op: StructuralTransformOp,
    *,
    part_relabel_sources: dict[str, str] | None = None,
    consumed_source_paths: set[tuple[tuple[str, str], ...]] | None = None,
    migration_ledger: "MigrationLedger | None" = None,
    effective_date: str = "",
    source_statute: str = "",
) -> Tuple[IRNode, ExecutedOp]:
    """Execute a RELABEL op: find target node and change its label.

    The ``target`` address identifies the node to relabel.
    The ``destination`` address provides the new label (leaf label of the
    destination path).  If destination is None, the op is skipped.
    """
    if op.destination is None:
        return tree, ExecutedOp(
            op=op,
            success=False,
            note="RELABEL op has no destination",
            reason_code="missing_destination",
        )

    target_path = _parse_address(op.target)
    dest_path = _parse_address(op.destination)
    if not target_path or not dest_path:
        return tree, ExecutedOp(
            op=op,
            success=False,
            note=f"could not parse target={op.target!r} or destination={op.destination!r}",
            reason_code="parse_failed",
        )

    new_label = dest_path[-1][1]  # leaf label of destination

    # Find the target node in the tree.
    found_path = _resolve_relabel_lookup_path(
        tree,
        target_path,
        part_relabel_sources=part_relabel_sources,
    )
    restored_source_alias = False
    if (
        found_path is not None
        and part_relabel_sources
        and consumed_source_paths is None
        and target_path
        and target_path[0][0] == "part"
    ):
        target_part = _norm_num_token(target_path[0][1])
        source_part = part_relabel_sources.get(target_part)
        if source_part:
            source_path = [("part", source_part), *target_path[1:]]
            source_found = _find_path_by_suffix(tree, source_path)
            if source_found is None:
                restored = _restore_missing_source_part_alias(
                    tree,
                    found_path,
                    target_part=target_part,
                    source_part=source_part,
                )
                if restored is not None:
                    tree, found_path = restored
                    restored_source_alias = True

    if found_path is None:
        reason_code = _classify_missing_relabel_reason(
            tree,
            target_path,
            part_relabel_sources=part_relabel_sources,
            consumed_source_paths=consumed_source_paths,
        )
        logger.warning(
            "[%s] RELABEL target not found: %s (reason=%s, plan %s/%s)",
            source_statute or "-",
            op.target,
            reason_code,
            op.notes,
            op.destination,
        )
        return tree, ExecutedOp(
            op=op,
            success=False,
            note=f"target not found: {op.target}",
            reason_code=reason_code,
        )

    # Resolve the node and create a relabeled copy.
    target_node = _tops.resolve(tree, found_path)
    if target_node is None:
        return tree, ExecutedOp(
            op=op,
            success=False,
            note=f"target resolved to None: {op.target}",
            reason_code="target_resolved_none",
        )

    relabeled = _relabel_node(target_node, new_label)
    explicit_parent_found = None
    if len(target_path) > 1:
        explicit_parent_found = _resolve_relabel_lookup_path(
            tree,
            target_path[:-1],
            part_relabel_sources=part_relabel_sources,
        )

    if (
        explicit_parent_found is not None
        and tuple(found_path[:-1]) != tuple(explicit_parent_found)
        and found_path[-1][0] == target_path[-1][0]
    ):
        tree = _tops.remove_at(tree, found_path)
        parent_node = _tops.resolve(tree, explicit_parent_found) if explicit_parent_found else tree
        if parent_node is None:
            return tree, ExecutedOp(
                op=op,
                success=False,
                note=f"parent not found after loose-leaf recovery: {explicit_parent_found!r}",
                reason_code="recovered_parent_missing",
            )
        rebuilt_parent = IRNode(
            kind=parent_node.kind,
            label=parent_node.label,
            text=parent_node.text,
            attrs=dict(parent_node.attrs),
            children=tuple(list(parent_node.children) + [relabeled]),
        )
        rebuilt_parent = _tops.resort_children(rebuilt_parent)
        tree = _tops.replace_at(tree, explicit_parent_found, rebuilt_parent)
        applied_from = tuple(found_path)
        applied_to = tuple(explicit_parent_found) + ((target_path[-1][0], new_label),)
        note = f"reparented loose trailing leaf and relabeled to {new_label}"
    else:
        tree = _tops.replace_at(tree, found_path, relabeled)
        applied_from = tuple(found_path)
        applied_to = tuple(found_path[:-1]) + ((found_path[-1][0], new_label),)
        note = f"relabeled to {new_label}"
    if restored_source_alias:
        note = f"{note}; restored missing source part alias"
    if migration_ledger is not None:
        ledger = migration_ledger
        ledger.record_renumber(
            LegalAddress(path=applied_from),
            LegalAddress(path=applied_to),
            effective=effective_date,
            source_statute=source_statute,
        )

    logger.info(
        "RELABEL executed: %s → %s",
        op.target, new_label,
    )
    return tree, ExecutedOp(
        op=op,
        success=True,
        note=note,
    )


def _execute_move(
    tree: IRNode,
    op: StructuralTransformOp,
    *,
    migration_ledger: "MigrationLedger | None" = None,
    effective_date: str = "",
    source_statute: str = "",
) -> Tuple[IRNode, ExecutedOp]:
    """Execute a MOVE op: remove source node and insert at destination.

    The ``target`` address identifies the node to move.
    The ``destination`` address identifies the container to move it into.
    """
    if op.destination is None:
        return tree, ExecutedOp(
            op=op,
            success=False,
            note="MOVE op has no destination",
            reason_code="missing_destination",
        )

    target_path = _parse_address(op.target)
    dest_path = _parse_address(op.destination)
    if not target_path or not dest_path:
        return tree, ExecutedOp(
            op=op,
            success=False,
            note=f"could not parse target={op.target!r} or destination={op.destination!r}",
            reason_code="parse_failed",
        )

    # Find the source node.
    source_found = _tops.find(tree, target_path[-1][0], target_path[-1][1],
                              scope_kind=target_path[0][0] if len(target_path) > 1 else None,
                              scope_label=target_path[0][1] if len(target_path) > 1 else None)
    if source_found is None and len(target_path) == 1:
        source_found = _tops.find(tree, target_path[-1][0], target_path[-1][1])

    if source_found is None:
        logger.warning(
            "MOVE source not found: %s (plan %s)",
            op.target, op.notes,
        )
        return tree, ExecutedOp(
            op=op,
            success=False,
            note=f"source not found: {op.target}",
            reason_code="source_not_found",
        )

    source_node = _tops.resolve(tree, source_found)
    if source_node is None:
        return tree, ExecutedOp(
            op=op,
            success=False,
            note=f"source resolved to None: {op.target}",
            reason_code="source_resolved_none",
        )

    # Find the destination container.
    dest_container_found = _tops.find(tree, dest_path[-1][0], dest_path[-1][1],
                                      scope_kind=dest_path[0][0] if len(dest_path) > 1 else None,
                                      scope_label=dest_path[0][1] if len(dest_path) > 1 else None)
    if dest_container_found is None:
        dest_container_found = _tops.find(tree, dest_path[-1][0], dest_path[-1][1])

    if dest_container_found is None:
        logger.warning(
            "MOVE destination not found: %s (plan %s)",
            op.destination, op.notes,
        )
        return tree, ExecutedOp(
            op=op,
            success=False,
            note=f"destination not found: {op.destination}",
            reason_code="destination_not_found",
        )

    # Step 1: Remove node from current location.
    tree = _tops.remove_at(tree, source_found)

    # Step 2: Insert node at destination (sorted position).
    # Re-find dest after removal (tree changed).
    dest_container_found = _tops.find(tree, dest_path[-1][0], dest_path[-1][1],
                                      scope_kind=dest_path[0][0] if len(dest_path) > 1 else None,
                                      scope_label=dest_path[0][1] if len(dest_path) > 1 else None)
    if dest_container_found is None:
        dest_container_found = _tops.find(tree, dest_path[-1][0], dest_path[-1][1])

    if dest_container_found is None:
        # Destination disappeared after removal — this should not happen
        # in well-formed plans.  Log and return tree without the source.
        logger.warning(
            "MOVE destination disappeared after source removal: %s (plan %s)",
            op.destination, op.notes,
        )
        return tree, ExecutedOp(
            op=op,
            success=False,
            note=f"destination disappeared after source removal: {op.destination}",
            reason_code="destination_disappeared",
        )

    tree = _tops.insert_sorted(tree, dest_container_found, source_node)

    if migration_ledger is not None:
        ledger = migration_ledger
        ledger.record_move(
            LegalAddress(path=tuple(source_found)),
            LegalAddress(path=tuple(dest_container_found) + ((source_found[-1][0], source_found[-1][1]),)),
            effective=effective_date,
            source_statute=source_statute,
        )

    logger.info(
        "MOVE executed: %s → %s",
        op.target, op.destination,
    )
    return tree, ExecutedOp(
        op=op,
        success=True,
        note=f"moved to {op.destination}",
    )
