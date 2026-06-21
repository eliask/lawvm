"""Finnish numbered-table target payload merging.

This module owns the narrow recovery for clauses such as
``13 §:n taulukko 4``.  The source target is a table inside a section, not the
section as a whole.  Until table facets are executable end to end, the safe
projection is to rebuild a section payload from the live section and replace
only the uniquely identified table-bearing direct child.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from lawvm.core import tree_ops as _tops
from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.helpers import _norm_num_token

_TABLE_MARKER_RE = re.compile(
    r"\btaulukko\s+(?P<label>\d{1,4}+\s{0,3}+[a-z]?)\s*[\.:]",
    re.I,
)
_TABLE_TARGET_PAYLOAD_RULE = "ELAB.NUMBERED_TABLE_TARGET_MERGE"
_DUPLICATE_TABLE_NOTE_BLOCK_RULE = "ELAB.DUPLICATE_TABLE_NOTE_BLOCK_PRUNED"
_PAYLOAD_NORMALIZATION_RULE_ATTR = "lawvm_payload_normalization_rule"


@dataclass(frozen=True, slots=True)
class NumberedTableMergeResult:
    """Result of applying explicit numbered-table target evidence."""

    node: IRNode | None
    rewritten: bool = False
    table_labels: tuple[str, ...] = ()


def _with_table_merge_rule(node: IRNode, extra_rules: tuple[str, ...] = ()) -> IRNode:
    existing = node.attrs.get(_PAYLOAD_NORMALIZATION_RULE_ATTR, ())
    rules = tuple(existing) if isinstance(existing, tuple) else ((str(existing),) if existing else ())
    return IRNode(
        kind=node.kind,
        label=node.label,
        text=node.text,
        attrs={
            **dict(node.attrs),
            _PAYLOAD_NORMALIZATION_RULE_ATTR: tuple(
                dict.fromkeys((*rules, _TABLE_TARGET_PAYLOAD_RULE, *extra_rules))
            ),
        },
        children=tuple(node.children),
    )


def _has_table_descendant(node: IRNode) -> bool:
    if node.kind is IRNodeKind.TABLE:
        return True
    return any(_has_table_descendant(child) for child in node.children)


def _mentioned_table_labels(node: IRNode) -> frozenset[str]:
    labels: set[str] = set()
    text = " ".join(irnode_to_text(node).split())
    # lawvm-regex: owning_parser own-subtree (irnode_to_text) numbered-table-label enumeration over the payload subtree the merge operator owns, not source-plane mint
    for match in _TABLE_MARKER_RE.finditer(text):
        label = _norm_num_token(match.group("label"))
        if label:
            labels.add(label)
    return frozenset(labels)


def mentioned_numbered_table_labels(node: IRNode) -> frozenset[str]:
    """Return numbered table labels mentioned in one payload subtree."""
    return _mentioned_table_labels(node)


def _table_child_indexes(section: IRNode, table_label: str) -> tuple[int, ...]:
    wanted = _norm_num_token(table_label)
    indexes: list[int] = []
    for idx, child in enumerate(section.children):
        if child.kind in {IRNodeKind.NUM, IRNodeKind.HEADING, IRNodeKind.OMISSION}:
            continue
        if wanted not in _mentioned_table_labels(child):
            continue
        if not _has_table_descendant(child):
            continue
        indexes.append(idx)
    return tuple(indexes)


def _relabel_replacement_child(replacement: IRNode, live_child: IRNode) -> IRNode:
    if replacement.kind is IRNodeKind.SUBSECTION and live_child.kind is IRNodeKind.SUBSECTION:
        return IRNode(
            kind=replacement.kind,
            label=live_child.label,
            text=replacement.text,
            attrs=dict(replacement.attrs),
            children=tuple(replacement.children),
        )
    return replacement


def _canonical_duplicate_note_text(node: IRNode) -> str:
    text = " ".join(irnode_to_text(node).lower().split())
    text = text.replace("q fi,k", "qfi,k")
    text = re.sub(r"mj/m\s+2\b", "mj/m2", text)
    text = re.sub(r"\s+([.,;:)])", r"\1", text)
    text = re.sub(r"([(])\s+", r"\1", text)
    return text.strip()


def _is_duplicate_table_note_block(rows: tuple[str, ...]) -> bool:
    return (
        len(rows) >= 4
        and rows[0].startswith("qfi,k on ")
        and any("kellarikerrokset mitoitetaan" in row for row in rows)
        and any(row.startswith("1) ylin kellarikerros") for row in rows)
        and any(row.startswith("2) ylimmän kellarikerroksen") for row in rows)
    )


def _prune_adjacent_duplicate_table_note_blocks(table: IRNode) -> tuple[IRNode, bool]:
    children = tuple(table.children)
    canonical = tuple(_canonical_duplicate_note_text(child) for child in children)
    max_block_len = min(8, len(children) // 2)
    for start in range(len(children)):
        for block_len in range(max_block_len, 1, -1):
            end = start + block_len
            next_end = end + block_len
            if next_end > len(children):
                continue
            left = canonical[start:end]
            right = canonical[end:next_end]
            if left == right and _is_duplicate_table_note_block(left):
                pruned_children = (*children[:start], *children[end:])
                return _tops._with_children(table, pruned_children), True
    return table, False


def _dedupe_duplicate_table_note_blocks(node: IRNode) -> tuple[IRNode, bool]:
    if node.kind is IRNodeKind.TABLE:
        return _prune_adjacent_duplicate_table_note_blocks(node)
    changed = False
    children: list[IRNode] = []
    for child in node.children:
        next_child, child_changed = _dedupe_duplicate_table_note_blocks(child)
        children.append(next_child)
        changed = changed or child_changed
    if not changed:
        return node, False
    return _tops._with_children(node, children), True


def merge_numbered_table_targets_into_live_section(
    live_section: IRNode | None,
    amendment_section: IRNode | None,
    table_labels: Iterable[str],
) -> NumberedTableMergeResult:
    """Replace only explicitly targeted numbered table children in a section.

    The merge is intentionally conservative.  Each requested table label must
    identify exactly one direct child in the live section and exactly one direct
    child in the amendment section.  The replacement child must not itself be an
    omission shell.  Ambiguous or missing evidence returns ``rewritten=False``.
    """
    if live_section is None or amendment_section is None:
        return NumberedTableMergeResult(amendment_section)
    if live_section.kind is not IRNodeKind.SECTION or amendment_section.kind is not IRNodeKind.SECTION:
        return NumberedTableMergeResult(amendment_section)
    if _norm_num_token(live_section.label or "") != _norm_num_token(amendment_section.label or ""):
        return NumberedTableMergeResult(amendment_section)

    normalized_labels = tuple(dict.fromkeys(_norm_num_token(label) for label in table_labels if _norm_num_token(label)))
    if not normalized_labels:
        return NumberedTableMergeResult(amendment_section)

    children = list(live_section.children)
    for table_label in normalized_labels:
        live_indexes = _table_child_indexes(live_section, table_label)
        amendment_indexes = _table_child_indexes(amendment_section, table_label)
        if len(live_indexes) != 1 or len(amendment_indexes) != 1:
            return NumberedTableMergeResult(amendment_section)
        replacement = amendment_section.children[amendment_indexes[0]]
        if replacement.kind is IRNodeKind.OMISSION:
            return NumberedTableMergeResult(amendment_section)
        live_child = live_section.children[live_indexes[0]]
        if replacement.kind is not live_child.kind:
            return NumberedTableMergeResult(amendment_section)
        replacement, duplicate_notes_pruned = _dedupe_duplicate_table_note_blocks(replacement)
        extra_rules = (
            (_DUPLICATE_TABLE_NOTE_BLOCK_RULE,)
            if duplicate_notes_pruned
            else ()
        )
        children[live_indexes[0]] = _with_table_merge_rule(
            _relabel_replacement_child(replacement, live_child),
            extra_rules=extra_rules,
        )

    rebuilt = _tops._with_children(live_section, children)
    return NumberedTableMergeResult(
        node=rebuilt,
        rewritten=True,
        table_labels=normalized_labels,
    )


__all__ = [
    "NumberedTableMergeResult",
    "merge_numbered_table_targets_into_live_section",
    "mentioned_numbered_table_labels",
]
