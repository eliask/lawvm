"""UK replay target-shape gap predicates."""
from __future__ import annotations

import re

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.uk_legislation.addressing import (
    _action_name,
    _addr_container,
    _addr_leaf_kind,
    _addr_leaf_label,
)
from lawvm.uk_legislation.canonicalize import uk_kind_matches
from lawvm.uk_legislation.mutable_ir import UKMutableNode
from lawvm.uk_legislation.replay_text import _normalized_replay_subtree_text
from lawvm.uk_legislation.uk_grafter import _clean_num


def uk_table_target_shape_gap(target: LegalAddress) -> bool:
    path = tuple(getattr(target, "path", ()) or ())
    if not path:
        return False
    return any(_clean_num(label or "") == "table" for _, label in path)


def uk_broad_schedule_table_shape_gap(target: LegalAddress, node: UKMutableNode) -> bool:
    path = tuple(getattr(target, "path", ()) or ())
    if _addr_container(target) != "schedule" or not path:
        return False
    leaf_kind = str(path[-1][0] or "").lower()
    if leaf_kind not in {"schedule", "part"}:
        return False
    node_kind = str(getattr(node, "kind", "") or "").lower()
    if node_kind not in {"schedule", "part"}:
        return False
    descendant_kinds: set[str] = set()
    stack = list(getattr(node, "children", []) or [])
    while stack:
        curr = stack.pop()
        curr_kind = str(getattr(curr, "kind", "") or "").lower()
        descendant_kinds.add(curr_kind)
        stack.extend(list(getattr(curr, "children", []) or []))
    if descendant_kinds & {"table", "row", "cell", "header_cell"}:
        return False
    provision_kinds = {"paragraph", "subparagraph", "item", "point", "p1group", "section"}
    return not bool(descendant_kinds & provision_kinds)


def uk_malformed_target_placeholder_label_gap(target: LegalAddress) -> bool:
    path = tuple(getattr(target, "path", ()) or ())
    return any(
        str(kind or "").lower() in {"item", "point", "paragraph", "subparagraph"}
        and bool(re.fullmatch(r"\[[^\]]+\]", str(label or "").strip()))
        for kind, label in path
    )


def uk_malformed_target_note_or_crossheading_gap(target: LegalAddress) -> bool:
    path = tuple(getattr(target, "path", ()) or ())
    if any(_clean_num(label or "").lower() == "note" for _, label in path):
        return True
    return any(
        re.sub(r"[^0-9a-z]+", "", _clean_num(label or "").lower()) in {"crossheading", "crossheadings"}
        for _, label in path
    )


def uk_malformed_target_sectionlike_label_gap(target: LegalAddress) -> bool:
    path = tuple(getattr(target, "path", ()) or ())
    if not path:
        return False
    root_kind, root_label = path[0]
    if str(root_kind or "").lower() not in {"section", "article", "rule", "regulation"}:
        return False
    normalized = re.sub(r"[^0-9a-z]+", "", str(root_label or "").strip().lower())
    if not normalized:
        return True
    if any(ch.isdigit() for ch in normalized):
        return False
    return True


def uk_malformed_target_schedule_root_label_gap(target: LegalAddress) -> bool:
    path = tuple(getattr(target, "path", ()) or ())
    if _addr_container(target) != "schedule" or not path:
        return False
    first_kind, first_label = path[0]
    return str(first_kind or "").lower() == "schedule" and not _clean_num(first_label or "")


def uk_existing_target_insert_gap(
    target: LegalAddress,
    node: UKMutableNode | None,
    op: LegalOperation,
) -> bool:
    if _action_name(op.action) != "insert" or node is None:
        return False
    payload = getattr(op, "payload", None)
    if payload is None:
        return True
    payload_kind = str(getattr(payload, "kind", "") or "")
    payload_label = _clean_num(str(getattr(payload, "label", "") or ""))
    target_kind = _addr_leaf_kind(target) or ""
    target_label = _addr_leaf_label(target) or ""
    if not (
        uk_kind_matches(
            node_kind=payload_kind,
            target_kind=target_kind,
            node_label=payload_label,
            target_label=_clean_num(target_label),
        )
        and payload_label == _clean_num(target_label)
    ):
        return False
    return uk_kind_matches(
        node_kind=str(getattr(node, "kind", "") or ""),
        target_kind=target_kind,
        node_label=_clean_num(str(getattr(node, "label", "") or "")),
        target_label=_clean_num(target_label),
    ) and _clean_num(str(getattr(node, "label", "") or "")) == _clean_num(target_label)


def uk_existing_target_insert_already_materialized(
    node: UKMutableNode | None,
    op: LegalOperation,
) -> bool:
    payload = getattr(op, "payload", None)
    if node is None or payload is None:
        return False
    existing_text = _normalized_replay_subtree_text(node)
    payload_text = _normalized_replay_subtree_text(payload)
    return bool(existing_text and payload_text and existing_text == payload_text)


def uk_existing_target_insert_conflict_detail(
    node: UKMutableNode | None,
    op: LegalOperation,
) -> dict[str, str] | None:
    payload = getattr(op, "payload", None)
    if node is None or payload is None:
        return None
    existing_text = _normalized_replay_subtree_text(node)
    payload_text = _normalized_replay_subtree_text(payload)
    if not existing_text or not payload_text or existing_text == payload_text:
        return None
    return {
        "existing_text_preview": existing_text[:240],
        "payload_text_preview": payload_text[:240],
    }
