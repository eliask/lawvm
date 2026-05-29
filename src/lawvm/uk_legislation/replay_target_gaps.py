"""UK replay target-shape gap predicates."""
from __future__ import annotations

import re
from collections.abc import Sequence

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core import tree_ops
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
from lawvm.uk_legislation.witness_sidecars import _witness_for_op

_PAYLOAD_SHAPE_INVARIANT_FAMILIES: frozenset[tree_ops.TreeInvariantKind] = frozenset(
    {
        "duplicate_label",
        "sort_order",
    }
)
InvariantViolation = str | tree_ops.TreeInvariantViolation


def _strip_order_kind_prefix(text: str, kind: str) -> str:
    return re.sub(rf"^(?:{re.escape(kind)})\s*", "", text.strip(), flags=re.I)


def _violation_message(violation: InvariantViolation) -> str:
    if isinstance(violation, tree_ops.TreeInvariantViolation):
        return violation.message
    return str(violation or "")


def _is_order_violation(violation: InvariantViolation, kind: str) -> bool:
    if isinstance(violation, tree_ops.TreeInvariantViolation):
        return violation.kind == "sort_order" and violation.child_kind == kind
    return f"{kind} out of order:" in _violation_message(violation).lower()


def _is_duplicate_violation(violation: InvariantViolation, kind: str | None = None) -> bool:
    if isinstance(violation, tree_ops.TreeInvariantViolation):
        if violation.kind not in {"duplicate_label", "normalized_duplicate_label"}:
            return False
        return kind is None or violation.child_kind == kind
    text = _violation_message(violation).lower()
    return f"duplicate {kind}:" in text if kind is not None else "duplicate " in text


def _order_labels(violation: InvariantViolation, kind: str) -> tuple[str, str] | None:
    if isinstance(violation, tree_ops.TreeInvariantViolation):
        if not _is_order_violation(violation, kind):
            return None
        if violation.previous_label is None or violation.next_label is None:
            return None
        return violation.previous_label, violation.next_label
    match = re.search(
        rf"{re.escape(kind)} out of order:\s*(.+?)\s*>\s*(.+)$",
        _violation_message(violation),
        re.I,
    )
    if match is None:
        return None
    return match.group(1), match.group(2)


def _is_numeric_order_label(text: str) -> bool:
    return bool(re.fullmatch(r"\d+", text))


def _is_roman_order_label(text: str) -> bool:
    return bool(re.fullmatch(r"[ivxlcdm]+", text, re.I))


def _is_alpha_order_label(text: str) -> bool:
    return bool(re.fullmatch(r"[a-z]+", text, re.I))


def _is_single_alpha_order_label(text: str) -> bool:
    return bool(re.fullmatch(r"[a-z]", text, re.I))


def _is_alpha_suffix_order_label(text: str) -> bool:
    return bool(re.fullmatch(r"[a-z]{2,}", text, re.I))


def _is_mixed_chapter_order_label(text: str) -> bool:
    return bool(re.fullmatch(r"(?:[a-z]+\d+[a-z0-9]*|\d+[a-z][a-z0-9]*)", text, re.I))


def _is_mixed_section_order_label(text: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?:\d+[a-z]+\d+[a-z0-9]*|\d+[a-z]{2,}|\d+[a-z]\d[a-z0-9]*|[a-z]+\d+[a-z0-9]*)",
            text,
            re.I,
        )
    )


def _is_mixed_paragraph_order_label(text: str) -> bool:
    return bool(re.fullmatch(r"(?:\d+[a-z][a-z0-9]*|[a-z]+\d+[a-z0-9]*)", text, re.I))


def _is_mixed_subunit_order_label(text: str) -> bool:
    return bool(re.fullmatch(r"(?:\d+[a-z][a-z0-9]*|[a-z]+\d+[a-z0-9]*|[ivxlcdm]+[a-z]+)", text, re.I))


def uk_table_target_shape_gap(target: LegalAddress) -> bool:
    path = target.path
    if not path:
        return False
    return any(_clean_num(label or "") == "table" for _, label in path)


def uk_broad_schedule_table_shape_gap(target: LegalAddress, node: UKMutableNode) -> bool:
    path = target.path
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


def uk_payload_shape_invariant_violations(op: LegalOperation) -> list[str]:
    return [violation.message for violation in uk_payload_shape_invariant_violation_records(op)]


def uk_payload_shape_invariant_violation_records(op: LegalOperation) -> list[tree_ops.TreeInvariantViolation]:
    payload = getattr(op, "payload", None)
    if payload is None or _action_name(op.action) not in {"insert", "replace"}:
        return []
    return list(
        tree_ops.iter_tree_invariant_violations(
            payload,
            families=_PAYLOAD_SHAPE_INVARIANT_FAMILIES,
        )
    )


def uk_payload_container_shape_gap(op: LegalOperation, scoped_violation: InvariantViolation) -> bool:
    if not _is_duplicate_violation(scoped_violation, "part"):
        return False
    payload = getattr(op, "payload", None)
    if payload is None or _action_name(op.action) != "replace":
        return False
    target_path = op.target.path
    if not target_path or str(target_path[-1][0] or "").lower() != "part":
        return False
    payload_kind = str(getattr(payload, "kind", "") or "").lower()
    payload_label = _clean_num(str(getattr(payload, "label", "") or ""))
    return payload_kind == "part" and payload_label in {"", "part"}


def uk_repeated_form_label_payload_shape_gap(
    op: LegalOperation,
    payload_violations: Sequence[InvariantViolation],
) -> bool:
    payload = getattr(op, "payload", None)
    if payload is None or _action_name(op.action) != "insert":
        return False
    target_path = op.target.path
    if len(target_path) != 1 or str(target_path[0][0] or "").lower() != "schedule":
        return False
    if str(getattr(payload, "kind", "") or "").lower() != "schedule":
        return False
    if not payload_violations:
        return False
    return all(
        _is_duplicate_violation(violation, "item") or _is_order_violation(violation, "item")
        for violation in payload_violations
    )


def uk_replace_payload_kind_mismatch_gap(op: LegalOperation, scoped_violation: InvariantViolation) -> bool:
    if _action_name(op.action) != "replace" or op.payload is None:
        return False
    target_path = op.target.path
    if not target_path:
        return False
    target_kind = str(target_path[-1][0] or "").lower()
    payload_kind = str(getattr(op.payload, "kind", "") or "").lower()
    if payload_kind == target_kind:
        return False
    return (
        (
            target_kind == "subsection"
            and payload_kind == "paragraph"
            and _is_order_violation(scoped_violation, "paragraph")
        )
        or (
            target_kind == "paragraph"
            and payload_kind == "subparagraph"
            and _is_order_violation(scoped_violation, "subparagraph")
        )
        or (
            target_kind in {"subparagraph", "item", "point"}
            and payload_kind in {"item", "point"}
            and _is_duplicate_violation(scoped_violation)
        )
    )


def uk_source_anchored_order_observation(op: LegalOperation, scoped_violation: InvariantViolation) -> bool:
    if _action_name(op.action) != "insert":
        return False
    witness = _witness_for_op(op)
    if witness is None:
        return False
    insertion_anchor_witness = witness.insertion_anchor_witness
    if insertion_anchor_witness is None:
        return False
    if not (insertion_anchor_witness.preceding_eid or insertion_anchor_witness.following_eid):
        return False
    target_path = op.target.path
    if not target_path:
        return False
    target_kind = str(target_path[-1][0] or "").lower()
    target_label = _clean_num(str(target_path[-1][1] or ""))
    if not target_kind or not target_label:
        return False
    if not _is_order_violation(scoped_violation, target_kind):
        return False
    return target_label in _clean_num(_violation_message(scoped_violation))


def uk_missing_source_target_gap(op: LegalOperation) -> bool:
    witness = _witness_for_op(op)
    if witness is None:
        return False
    extraction = witness.extraction_witness
    return (
        extraction.authority_layer == "EFFECT_FEED_INDEX"
        and not extraction.extracted_source_present
        and extraction.extraction_failure_kind == "missing_extracted_source"
    )


def uk_part_order_shape_gap(op: LegalOperation, scoped_violation: InvariantViolation) -> bool:
    if not _is_order_violation(scoped_violation, "part"):
        return False
    target_path = op.target.path
    if not target_path:
        return False
    part_labels = [str(label or "") for kind, label in target_path if str(kind or "").lower() == "part"]
    if not part_labels:
        return False
    leaf_text = _clean_num(part_labels[-1])
    labels = _order_labels(scoped_violation, "part")
    schedule_labels = [
        _clean_num(str(label or "")) for kind, label in target_path if str(kind or "").lower() == "schedule"
    ]
    if schedule_labels and any(re.fullmatch(r"\d+[a-z]+", label, re.I) for label in schedule_labels if label):
        return True
    if re.fullmatch(r"(?:[a-z]+\d+[a-z0-9]*|\d+[a-z][a-z0-9]*)", leaf_text):
        return True
    if labels is None:
        return False
    left = _clean_num(_strip_order_kind_prefix(labels[0], "part"))
    right = _clean_num(_strip_order_kind_prefix(labels[1], "part"))
    return (_is_numeric_order_label(left) and _is_roman_order_label(right)) or (
        _is_roman_order_label(left) and _is_numeric_order_label(right)
    )


def uk_chapter_order_shape_gap(op: LegalOperation, scoped_violation: InvariantViolation) -> bool:
    if not _is_order_violation(scoped_violation, "chapter"):
        return False
    target_path = op.target.path
    if not target_path or str(target_path[-1][0] or "").lower() != "chapter":
        return False
    labels = _order_labels(scoped_violation, "chapter")
    if labels is None:
        return False
    left = _clean_num(_strip_order_kind_prefix(labels[0], "chapter"))
    right = _clean_num(_strip_order_kind_prefix(labels[1], "chapter"))

    return (
        (_is_numeric_order_label(left) and _is_mixed_chapter_order_label(right))
        or (_is_mixed_chapter_order_label(left) and _is_numeric_order_label(right))
        or (_is_mixed_chapter_order_label(left) and _is_mixed_chapter_order_label(right))
        or left == right
    )


def uk_section_order_shape_gap(op: LegalOperation, scoped_violation: InvariantViolation) -> bool:
    if not _is_order_violation(scoped_violation, "section"):
        return False
    target_path = op.target.path
    if not target_path or str(target_path[-1][0] or "").lower() != "section":
        return False
    leaf_text = _clean_num(str(target_path[-1][1] or ""))
    if _is_mixed_section_order_label(leaf_text):
        return True
    if leaf_text and not re.fullmatch(r"\d+[a-z]*", leaf_text, re.I):
        return True
    labels = _order_labels(scoped_violation, "section")
    if labels is None:
        return False
    left = _clean_num(labels[0])
    right = _clean_num(labels[1])
    return (
        (_is_numeric_order_label(left) and _is_mixed_section_order_label(right))
        or (_is_mixed_section_order_label(left) and _is_numeric_order_label(right))
        or (_is_mixed_section_order_label(left) and _is_mixed_section_order_label(right))
    )


def uk_paragraph_order_shape_gap(op: LegalOperation, scoped_violation: InvariantViolation) -> bool:
    if not _is_order_violation(scoped_violation, "paragraph"):
        return False
    target_path = op.target.path
    if not target_path:
        return False
    paragraph_labels = [str(label or "") for kind, label in target_path if str(kind or "").lower() == "paragraph"]
    if not paragraph_labels:
        return False
    leaf_text = _clean_num(paragraph_labels[-1])
    if _is_mixed_paragraph_order_label(leaf_text) or _is_alpha_suffix_order_label(leaf_text):
        return True
    labels = _order_labels(scoped_violation, "paragraph")
    if labels is None:
        return False
    left = _clean_num(labels[0])
    right = _clean_num(labels[1])
    return (
        (_is_mixed_paragraph_order_label(left) and _is_alpha_order_label(right))
        or (_is_alpha_order_label(left) and _is_mixed_paragraph_order_label(right))
        or (_is_mixed_paragraph_order_label(left) and _is_numeric_order_label(right))
        or (_is_numeric_order_label(left) and _is_mixed_paragraph_order_label(right))
        or (_is_mixed_paragraph_order_label(left) and _is_mixed_paragraph_order_label(right))
        or (_is_numeric_order_label(left) and _is_alpha_order_label(right))
        or (_is_alpha_order_label(left) and _is_numeric_order_label(right))
        or (_is_alpha_suffix_order_label(left) and _is_alpha_order_label(right))
        or (_is_alpha_order_label(left) and _is_alpha_suffix_order_label(right))
        or (_is_roman_order_label(left) and _is_alpha_order_label(right))
        or (_is_alpha_order_label(left) and _is_roman_order_label(right))
        or (_is_alpha_suffix_order_label(left) and _is_roman_order_label(right))
        or (_is_roman_order_label(left) and _is_alpha_suffix_order_label(right))
    )


def uk_subparagraph_order_shape_gap(op: LegalOperation, scoped_violation: InvariantViolation) -> bool:
    if not _is_order_violation(scoped_violation, "subparagraph"):
        return False
    target_path = op.target.path
    if not target_path or str(target_path[-1][0] or "").lower() != "subparagraph":
        return False
    leaf_text = _clean_num(str(target_path[-1][1] or ""))
    if _is_mixed_subunit_order_label(leaf_text) or _is_alpha_suffix_order_label(leaf_text):
        return True
    labels = _order_labels(scoped_violation, "subparagraph")
    if labels is None:
        return False
    left = _clean_num(labels[0])
    right = _clean_num(labels[1])
    return bool(
        (_is_mixed_subunit_order_label(left) and _is_roman_order_label(right))
        or (_is_roman_order_label(left) and _is_mixed_subunit_order_label(right))
        or (_is_numeric_order_label(left) and _is_mixed_subunit_order_label(right))
        or (_is_mixed_subunit_order_label(left) and _is_numeric_order_label(right))
        or (_is_mixed_subunit_order_label(left) and _is_mixed_subunit_order_label(right))
        or (_is_alpha_suffix_order_label(left) and _is_roman_order_label(right))
        or (_is_roman_order_label(left) and _is_alpha_suffix_order_label(right))
        or (_is_alpha_suffix_order_label(left) and _is_alpha_suffix_order_label(right))
        or (_is_numeric_order_label(left) and _is_alpha_suffix_order_label(right))
        or (_is_alpha_suffix_order_label(left) and _is_numeric_order_label(right))
    )


def uk_item_order_shape_gap(op: LegalOperation, scoped_violation: InvariantViolation) -> bool:
    if not _is_order_violation(scoped_violation, "item"):
        return False
    target_path = op.target.path
    if not target_path or str(target_path[-1][0] or "").lower() not in {"subparagraph", "item", "point"}:
        return False
    in_schedule = any(str(kind or "").lower() == "schedule" for kind, _ in target_path)
    raw_leaf_text = str(target_path[-1][1] or "").strip().lower()
    leaf_text = _clean_num(raw_leaf_text)
    if _is_mixed_subunit_order_label(leaf_text) or _is_alpha_suffix_order_label(leaf_text):
        return True
    labels = _order_labels(scoped_violation, "item")
    if labels is None:
        return False
    raw_left = str(labels[0] or "").strip().lower()
    raw_right = str(labels[1] or "").strip().lower()
    left = _clean_num(labels[0])
    right = _clean_num(labels[1])
    return bool(
        (_is_mixed_subunit_order_label(left) and _is_roman_order_label(right))
        or (_is_roman_order_label(left) and _is_mixed_subunit_order_label(right))
        or (_is_numeric_order_label(left) and _is_mixed_subunit_order_label(right))
        or (_is_mixed_subunit_order_label(left) and _is_numeric_order_label(right))
        or (_is_mixed_subunit_order_label(left) and _is_mixed_subunit_order_label(right))
        or (_is_alpha_suffix_order_label(left) and _is_alpha_order_label(right))
        or (_is_alpha_order_label(left) and _is_alpha_suffix_order_label(right))
        or (_is_alpha_suffix_order_label(left) and _is_roman_order_label(right))
        or (_is_roman_order_label(left) and _is_alpha_suffix_order_label(right))
        or (_is_alpha_suffix_order_label(left) and _is_alpha_suffix_order_label(right))
        or (
            in_schedule
            and _is_single_alpha_order_label(raw_leaf_text)
            and _is_single_alpha_order_label(raw_left)
            and _is_single_alpha_order_label(raw_right)
        )
        or (
            in_schedule
            and _is_single_alpha_order_label(raw_leaf_text)
            and _is_single_alpha_order_label(raw_left)
            and _is_alpha_suffix_order_label(raw_right)
        )
        or (
            in_schedule
            and _is_single_alpha_order_label(raw_leaf_text)
            and _is_alpha_suffix_order_label(raw_left)
            and _is_single_alpha_order_label(raw_right)
        )
    )


def uk_malformed_target_placeholder_label_gap(target: LegalAddress) -> bool:
    path = target.path
    return any(
        str(kind or "").lower() in {"item", "point", "paragraph", "subparagraph"}
        and bool(re.fullmatch(r"\[[^\]]+\]", str(label or "").strip()))
        for kind, label in path
    )


def uk_malformed_target_note_or_crossheading_gap(target: LegalAddress) -> bool:
    path = target.path
    if any(_clean_num(label or "").lower() == "note" for _, label in path):
        return True
    return any(
        re.sub(r"[^0-9a-z]+", "", _clean_num(label or "").lower()) in {"crossheading", "crossheadings"}
        for _, label in path
    )


def uk_malformed_target_sectionlike_label_gap(target: LegalAddress) -> bool:
    path = target.path
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
    path = target.path
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


def uk_crossheading_insert_target_gap(target: LegalAddress, op: LegalOperation) -> bool:
    payload = getattr(op, "payload", None)
    return (
        _action_name(op.action) == "insert"
        and _addr_leaf_kind(target) == "crossheading"
        and not _clean_num(_addr_leaf_label(target) or "")
        and payload is not None
        and str(getattr(payload, "kind", "") or "").lower() == "crossheading"
    )


def uk_is_explicit_direct_section_paragraph_target(target: LegalAddress) -> bool:
    path = tuple(target.path or ())
    return len(path) >= 2 and path[0][0] == "section" and path[1][0] == "paragraph"
