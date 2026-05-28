"""UK replay target-shape gap predicates."""
from __future__ import annotations

import re

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


def _strip_order_kind_prefix(text: str, kind: str) -> str:
    return re.sub(rf"^(?:{re.escape(kind)})\s*", "", text.strip(), flags=re.I)


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


def uk_payload_shape_invariant_violations(op: LegalOperation) -> list[str]:
    payload = getattr(op, "payload", None)
    if payload is None or _action_name(op.action) not in {"insert", "replace"}:
        return []
    return [
        violation.message
        for violation in tree_ops.iter_tree_invariant_violations(
            payload,
            families=_PAYLOAD_SHAPE_INVARIANT_FAMILIES,
        )
    ]


def uk_payload_container_shape_gap(op: LegalOperation, scoped_violation: str) -> bool:
    if "duplicate part:" not in scoped_violation.lower():
        return False
    payload = getattr(op, "payload", None)
    if payload is None or _action_name(op.action) != "replace":
        return False
    target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
    if not target_path or str(target_path[-1][0] or "").lower() != "part":
        return False
    payload_kind = str(getattr(payload, "kind", "") or "").lower()
    payload_label = _clean_num(str(getattr(payload, "label", "") or ""))
    return payload_kind == "part" and payload_label in {"", "part"}


def uk_repeated_form_label_payload_shape_gap(op: LegalOperation, payload_violations: list[str]) -> bool:
    payload = getattr(op, "payload", None)
    if payload is None or _action_name(op.action) != "insert":
        return False
    target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
    if len(target_path) != 1 or str(target_path[0][0] or "").lower() != "schedule":
        return False
    if str(getattr(payload, "kind", "") or "").lower() != "schedule":
        return False
    if not payload_violations:
        return False
    allowed = (
        "duplicate item:",
        "item out of order:",
    )
    return all(any(token in violation.lower() for token in allowed) for violation in payload_violations)


def uk_replace_payload_kind_mismatch_gap(op: LegalOperation, scoped_violation: str) -> bool:
    if _action_name(op.action) != "replace" or op.payload is None:
        return False
    target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
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
            and "paragraph out of order:" in scoped_violation.lower()
        )
        or (
            target_kind == "paragraph"
            and payload_kind == "subparagraph"
            and "subparagraph out of order:" in scoped_violation.lower()
        )
        or (
            target_kind in {"subparagraph", "item", "point"}
            and payload_kind in {"item", "point"}
            and "duplicate " in scoped_violation.lower()
        )
    )


def uk_source_anchored_order_observation(op: LegalOperation, scoped_violation: str) -> bool:
    if _action_name(op.action) != "insert":
        return False
    if " out of order:" not in str(scoped_violation or "").lower():
        return False
    witness = _witness_for_op(op)
    insertion_anchor_witness = getattr(witness, "insertion_anchor_witness", None)
    if insertion_anchor_witness is None:
        return False
    if not (
        getattr(insertion_anchor_witness, "preceding_eid", None)
        or getattr(insertion_anchor_witness, "following_eid", None)
    ):
        return False
    target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
    if not target_path:
        return False
    target_kind = str(target_path[-1][0] or "").lower()
    target_label = _clean_num(str(target_path[-1][1] or ""))
    if not target_kind or not target_label:
        return False
    if f"{target_kind} out of order:" not in str(scoped_violation or "").lower():
        return False
    return target_label in _clean_num(scoped_violation)


def uk_missing_source_target_gap(op: LegalOperation) -> bool:
    witness = _witness_for_op(op)
    extraction = getattr(witness, "extraction_witness", None)
    authority_layer = str(getattr(extraction, "authority_layer", "") or "")
    extraction_failure_kind = str(getattr(extraction, "extraction_failure_kind", "") or "")
    extracted_source_present = bool(getattr(extraction, "extracted_source_present", False))
    return (
        authority_layer == "EFFECT_FEED_INDEX"
        and not extracted_source_present
        and extraction_failure_kind == "missing_extracted_source"
    )


def uk_part_order_shape_gap(op: LegalOperation, scoped_violation: str) -> bool:
    if "part out of order:" not in scoped_violation.lower():
        return False
    target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
    if not target_path:
        return False
    part_labels = [str(label or "") for kind, label in target_path if str(kind or "").lower() == "part"]
    if not part_labels:
        return False
    leaf_text = _clean_num(part_labels[-1])
    violation = str(scoped_violation or "")
    match = re.search(r"part out of order:\s*(.+?)\s*>\s*(.+)$", violation, re.I)
    schedule_labels = [
        _clean_num(str(label or "")) for kind, label in target_path if str(kind or "").lower() == "schedule"
    ]
    if schedule_labels and any(re.fullmatch(r"\d+[a-z]+", label, re.I) for label in schedule_labels if label):
        return True
    if re.fullmatch(r"(?:[a-z]+\d+[a-z0-9]*|\d+[a-z][a-z0-9]*)", leaf_text):
        return True
    if match is None:
        return False
    left = _clean_num(_strip_order_kind_prefix(match.group(1), "part"))
    right = _clean_num(_strip_order_kind_prefix(match.group(2), "part"))
    return (_is_numeric_order_label(left) and _is_roman_order_label(right)) or (
        _is_roman_order_label(left) and _is_numeric_order_label(right)
    )


def uk_chapter_order_shape_gap(op: LegalOperation, scoped_violation: str) -> bool:
    if "chapter out of order:" not in scoped_violation.lower():
        return False
    target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
    if not target_path or str(target_path[-1][0] or "").lower() != "chapter":
        return False
    violation = str(scoped_violation or "")
    match = re.search(r"chapter out of order:\s*(.+?)\s*>\s*(.+)$", violation, re.I)
    if match is None:
        return False
    left = _clean_num(_strip_order_kind_prefix(match.group(1), "chapter"))
    right = _clean_num(_strip_order_kind_prefix(match.group(2), "chapter"))

    return (
        (_is_numeric_order_label(left) and _is_mixed_chapter_order_label(right))
        or (_is_mixed_chapter_order_label(left) and _is_numeric_order_label(right))
        or (_is_mixed_chapter_order_label(left) and _is_mixed_chapter_order_label(right))
        or left == right
    )


def uk_section_order_shape_gap(op: LegalOperation, scoped_violation: str) -> bool:
    if "section out of order:" not in scoped_violation.lower():
        return False
    target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
    if not target_path or str(target_path[-1][0] or "").lower() != "section":
        return False
    leaf_text = _clean_num(str(target_path[-1][1] or ""))
    violation = str(scoped_violation or "")
    if _is_mixed_section_order_label(leaf_text):
        return True
    if leaf_text and not re.fullmatch(r"\d+[a-z]*", leaf_text, re.I):
        return True
    match = re.search(r"section out of order:\s*(.+?)\s*>\s*(.+)$", violation, re.I)
    if match is None:
        return False
    left = _clean_num(match.group(1))
    right = _clean_num(match.group(2))
    return (
        (_is_numeric_order_label(left) and _is_mixed_section_order_label(right))
        or (_is_mixed_section_order_label(left) and _is_numeric_order_label(right))
        or (_is_mixed_section_order_label(left) and _is_mixed_section_order_label(right))
    )


def uk_paragraph_order_shape_gap(op: LegalOperation, scoped_violation: str) -> bool:
    if "paragraph out of order:" not in scoped_violation.lower():
        return False
    target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
    if not target_path:
        return False
    paragraph_labels = [str(label or "") for kind, label in target_path if str(kind or "").lower() == "paragraph"]
    if not paragraph_labels:
        return False
    leaf_text = _clean_num(paragraph_labels[-1])
    if _is_mixed_paragraph_order_label(leaf_text) or _is_alpha_suffix_order_label(leaf_text):
        return True
    violation = str(scoped_violation or "")
    match = re.search(r"paragraph out of order:\s*(.+?)\s*>\s*(.+)$", violation, re.I)
    if match is None:
        return False
    left = _clean_num(match.group(1))
    right = _clean_num(match.group(2))
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


def uk_subparagraph_order_shape_gap(op: LegalOperation, scoped_violation: str) -> bool:
    if "subparagraph out of order:" not in scoped_violation.lower():
        return False
    target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
    if not target_path or str(target_path[-1][0] or "").lower() != "subparagraph":
        return False
    leaf_text = _clean_num(str(target_path[-1][1] or ""))
    if _is_mixed_subunit_order_label(leaf_text) or _is_alpha_suffix_order_label(leaf_text):
        return True
    violation = str(scoped_violation or "")
    match = re.search(r"subparagraph out of order:\s*(.+?)\s*>\s*(.+)$", violation, re.I)
    if match is None:
        return False
    left = _clean_num(match.group(1))
    right = _clean_num(match.group(2))
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


def uk_item_order_shape_gap(op: LegalOperation, scoped_violation: str) -> bool:
    if "item out of order:" not in scoped_violation.lower():
        return False
    target_path = tuple(getattr(getattr(op, "target", None), "path", ()) or ())
    if not target_path or str(target_path[-1][0] or "").lower() not in {"subparagraph", "item", "point"}:
        return False
    in_schedule = any(str(kind or "").lower() == "schedule" for kind, _ in target_path)
    raw_leaf_text = str(target_path[-1][1] or "").strip().lower()
    leaf_text = _clean_num(raw_leaf_text)
    if _is_mixed_subunit_order_label(leaf_text) or _is_alpha_suffix_order_label(leaf_text):
        return True
    violation = str(scoped_violation or "")
    match = re.search(r"item out of order:\s*(.+?)\s*>\s*(.+)$", violation, re.I)
    if match is None:
        return False
    raw_left = str(match.group(1) or "").strip().lower()
    raw_right = str(match.group(2) or "").strip().lower()
    left = _clean_num(match.group(1))
    right = _clean_num(match.group(2))
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
