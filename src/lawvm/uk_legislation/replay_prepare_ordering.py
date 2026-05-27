from __future__ import annotations

from typing import Any, NamedTuple, Optional, Sequence

from lawvm.core.ir import LegalOperation
from lawvm.uk_legislation.addressing import _action_name


class UKSameSourceTextPatchOverlapClassification(NamedTuple):
    overlapping_op_ids: set[str]
    disjoint_overlap_op_ids: set[str]
    disjoint_before_edges: dict[str, set[str]]


def _literal_text_spans_in_subtree(
    node: Any,
    needle: str,
) -> list[tuple[tuple[int, ...], int, int]]:
    spans: list[tuple[tuple[int, ...], int, int]] = []
    if not needle:
        return spans

    def _walk(current: Any, path: tuple[int, ...] = ()) -> None:
        text = current.text or ""
        start = 0
        while True:
            pos = text.find(needle, start)
            if pos == -1:
                break
            end = pos + len(needle)
            spans.append((path, pos, end))
            start = end
        for index, child in enumerate(current.children):
            _walk(child, path + (index,))

    _walk(node)
    return spans


def _spans_overlap(
    left: tuple[tuple[int, ...], int, int],
    right: tuple[tuple[int, ...], int, int],
) -> bool:
    left_path, left_start, left_end = left
    right_path, right_start, right_end = right
    return left_path == right_path and left_start < right_end and right_start < left_end


def _same_source_ordinal_text_patch_overlap_status(
    op: LegalOperation,
    broader_ops: Sequence[LegalOperation],
    *,
    base_executor: Optional[Any],
) -> str:
    """Classify whether an ordinal text patch overlaps broader same-source patches."""
    if base_executor is None or op.text_patch is None:
        return "unknown"
    occurrence = op.text_patch.selector.occurrence
    if occurrence <= 0:
        return "unknown"
    node, _, _ = base_executor._find_node_by_target(op.target)
    if node is None:
        return "unknown"
    match_text = op.text_patch.selector.match_text
    match_spans = _literal_text_spans_in_subtree(node, match_text)
    if len(match_spans) < occurrence:
        return "unknown"
    claimed_span = match_spans[occurrence - 1]
    saw_unknown = False
    for broader_op in broader_ops:
        if broader_op.text_patch is None:
            saw_unknown = True
            continue
        broader_match = broader_op.text_patch.selector.match_text
        broader_spans = _literal_text_spans_in_subtree(node, broader_match)
        if not broader_spans:
            saw_unknown = True
            continue
        if any(_spans_overlap(claimed_span, broader_span) for broader_span in broader_spans):
            return "overlap"
    if saw_unknown:
        return "unknown"
    return "disjoint"


def _classify_same_source_text_patch_overlaps(
    ops: Sequence[LegalOperation],
    *,
    base_executor: Optional[Any],
) -> UKSameSourceTextPatchOverlapClassification:
    """Classify same-source text patch overlaps before replay ordering."""
    overlapping_text_patch_op_ids: set[str] = set()
    disjoint_text_patch_overlap_op_ids: set[str] = set()
    disjoint_text_patch_before_edges: dict[str, set[str]] = {}
    grouped_text_ops: dict[tuple[str, str, str, tuple[tuple[str, str], ...]], list[LegalOperation]] = {}
    for op in ops:
        if _action_name(op.action) != "text_replace" or op.text_patch is None:
            continue
        match_text = op.text_patch.selector.match_text
        if match_text.startswith(("TEXT_", "FROM_")):
            continue
        source = op.source
        group_key = (
            source.statute_id if source else "",
            source.effective if source else "",
            str(op.target.special or ""),
            op.target.path,
        )
        grouped_text_ops.setdefault(group_key, []).append(op)

    for group_ops in grouped_text_ops.values():
        if len(group_ops) < 2:
            continue
        for op in group_ops:
            if op.text_patch is None or op.text_patch.selector.occurrence == 0:
                continue
            match_text = op.text_patch.selector.match_text
            if len(match_text.strip()) < 3:
                continue
            broader_ops: list[LegalOperation] = []
            for other in group_ops:
                if other is op or other.text_patch is None:
                    continue
                other_match = other.text_patch.selector.match_text
                if other_match.startswith(("TEXT_", "FROM_")):
                    continue
                if len(other_match) <= len(match_text):
                    continue
                if match_text.strip() in other_match:
                    broader_ops.append(other)
            if not broader_ops:
                continue
            overlap_status = _same_source_ordinal_text_patch_overlap_status(
                op,
                broader_ops,
                base_executor=base_executor,
            )
            if overlap_status == "disjoint":
                disjoint_text_patch_overlap_op_ids.add(op.op_id)
                disjoint_text_patch_before_edges.setdefault(op.op_id, set()).update(
                    broader_op.op_id for broader_op in broader_ops
                )
            else:
                overlapping_text_patch_op_ids.add(op.op_id)
    return UKSameSourceTextPatchOverlapClassification(
        overlapping_op_ids=overlapping_text_patch_op_ids,
        disjoint_overlap_op_ids=disjoint_text_patch_overlap_op_ids,
        disjoint_before_edges=disjoint_text_patch_before_edges,
    )


def _order_ops_by_before_edges(
    ops: Sequence[LegalOperation],
    before_edges: dict[str, set[str]],
) -> list[LegalOperation]:
    if not before_edges:
        return list(ops)
    op_indices_by_id: dict[str, list[int]] = {}
    for index, op in enumerate(ops):
        op_indices_by_id.setdefault(op.op_id, []).append(index)
    successors: dict[int, set[int]] = {index: set() for index, _op in enumerate(ops)}
    predecessors: dict[int, set[int]] = {index: set() for index, _op in enumerate(ops)}
    for before_id, after_ids in before_edges.items():
        before_indices = op_indices_by_id.get(before_id)
        if not before_indices:
            continue
        for after_id in after_ids:
            after_indices = op_indices_by_id.get(after_id)
            if not after_indices:
                continue
            for before_index in before_indices:
                for after_index in after_indices:
                    if after_index == before_index:
                        continue
                    successors[before_index].add(after_index)
                    predecessors[after_index].add(before_index)

    ready = [index for index in range(len(ops)) if not predecessors[index]]
    ordered_indices: list[int] = []
    while ready:
        ready.sort()
        index = ready.pop(0)
        ordered_indices.append(index)
        for successor in sorted(successors[index]):
            predecessors[successor].discard(index)
            if not predecessors[successor]:
                ready.append(successor)
    if len(ordered_indices) != len(ops):
        return list(ops)
    return [ops[index] for index in ordered_indices]
