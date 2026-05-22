"""UK replay target-gap diagnostics and source-backed recovery helpers."""

from __future__ import annotations

import re
from dataclasses import replace as dc_replace
from typing import cast

from lawvm.core.ir import LegalAddress, LegalOperation, TextPatchSpec
from lawvm.core.semantic_types import IRNodeKind, TextPatchKindEnum
from lawvm.roman import roman_to_arabic as _shared_roman_to_arabic
from lawvm.uk_legislation.addressing import _addr_container, _addr_leaf_kind, _uk_kind_value
from lawvm.uk_legislation.canonicalize import uk_is_transparent_wrapper_kind, uk_kind_matches
from lawvm.uk_legislation.mutable_ir import UKMutableNode, uk_insert_child_sorted, uk_ir_node_kind, uk_replace_text_and_children
from lawvm.uk_legislation.ordering import _label_sort_key
from lawvm.uk_legislation.replay_records import (
    _append_uk_replay_adjudication,
    uk_replay_recovery_action_target_detail,
)
from lawvm.uk_legislation.replay_target_gaps import (
    uk_malformed_target_note_or_crossheading_gap,
    uk_malformed_target_placeholder_label_gap,
    uk_malformed_target_schedule_root_label_gap,
    uk_malformed_target_sectionlike_label_gap,
)
from lawvm.uk_legislation.replay_text import _node_text_contains_text, _subtree_text_match_count
from lawvm.uk_legislation.source_labeled_child_parts import (
    _source_carried_labeled_child_replacement_shape,
)
from lawvm.uk_legislation.source_text_reclassifications import source_following_anchor_structured_substitution_anchor
from lawvm.uk_legislation.text_matching import _text_match_has_word_punctuation_elision_candidate, _text_patch_pattern
from lawvm.uk_legislation.text_rewrite_fragments import _text_rewrite_rule_ids_for_op
from lawvm.uk_legislation.uk_grafter import _clean_num
from lawvm.uk_legislation.witness_sidecars import _witness_for_op


_UK_REPLAY_SOURCE_CARRIED_LABELED_CHILD_TEXT_SUBSTITUTION_RULE_ID = (
    "uk_replay_source_carried_labeled_child_text_substitution_recovered"
)


def _descendant_labels_by_kind(node: UKMutableNode, *, kinds: set[str]) -> list[str]:
    out: list[str] = []
    stack = list(getattr(node, "children", []) or [])
    while stack:
        curr = stack.pop()
        curr_kind = str(getattr(curr, "kind", "") or "").lower()
        if curr_kind in kinds:
            out.append(re.sub(r"[^0-9a-z]+", "", str(getattr(curr, "label", "") or "").lower()))
        stack.extend(list(getattr(curr, "children", []) or []))
    return out


def _local_alnum_suffix_key(text: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d+)([a-z])", text.strip().lower())
    if not match:
        return None
    return (int(match.group(1)), ord(match.group(2)) - ord("a") + 1)


def _alnum_multi_suffix_key(text: str) -> tuple[int, str] | None:
    match = re.fullmatch(r"(\d+)([a-z]{2,})", text.lower())
    if not match:
        return None
    return (int(match.group(1)), match.group(2))


def _alpha_num_suffix_key(text: str) -> tuple[str, int] | None:
    match = re.fullmatch(r"([a-z]+)(\d+)", text.lower())
    if not match:
        return None
    return (match.group(1), int(match.group(2)))


def _part_numeric_value(raw: str) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    text = re.sub(r"^(?:part)\s+", "", text, flags=re.I).strip()
    if text.isdigit():
        return int(text)
    return _shared_roman_to_arabic(text)


def _collect_sectionlike_descendant_labels(node: UKMutableNode | None) -> list[str]:
    if node is None:
        return []
    labels: list[str] = []
    stack = [node]
    while stack:
        curr = stack.pop()
        for child in getattr(curr, "children", []) or []:
            if str(getattr(child, "kind", "") or "").lower() in {"section", "article", "rule", "regulation"}:
                label = str(getattr(child, "label", "") or "").strip()
                if label:
                    labels.append(label)
            stack.append(child)
    return labels


class UKReplayTargetDiagnosticsMixin:

    def _schedule_unlabeled_paragraph_target_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if _addr_container(target) != "schedule" or len(path) < 3:
            return False
        root_kind, root_label = path[0]
        if str(root_kind or "").lower() != "schedule":
            return False
        paragraph_segments = [
            re.sub(r"[^0-9a-z]+", "", str(label or "").lower())
            for kind, label in path
            if str(kind or "").lower() == "paragraph"
        ]
        if not paragraph_segments or not any(label.isdigit() for label in paragraph_segments if label):
            return False
        want = _clean_num(root_label or "")
        root_node = None
        for schedule in getattr(self.statute, "supplements", []) or []:
            if str(getattr(schedule, "kind", "") or "").lower() != "schedule":
                continue
            have = _clean_num(getattr(schedule, "label", "") or "")
            if have == want or have.endswith(want):
                root_node = schedule
                break
        if root_node is None:
            return False
        paragraph_labels: list[str] = []
        subparagraph_labels: list[str] = []
        stack = list(getattr(root_node, "children", []) or [])
        while stack:
            curr = stack.pop()
            curr_kind = str(getattr(curr, "kind", "") or "").lower()
            if curr_kind == "paragraph":
                paragraph_labels.append(re.sub(r"[^0-9a-z]+", "", str(getattr(curr, "label", "") or "").lower()))
            elif curr_kind == "subparagraph":
                subparagraph_labels.append(re.sub(r"[^0-9a-z]+", "", str(getattr(curr, "label", "") or "").lower()))
            stack.extend(list(getattr(curr, "children", []) or []))
        leaf_kind = str(path[-1][0] or "").lower()
        return (
            bool(paragraph_labels)
            and not any(paragraph_labels)
            and bool(subparagraph_labels)
            and leaf_kind
            in {
                "subparagraph",
                "item",
                "point",
            }
        )

    def _malformed_target_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if not path:
            return False
        if any(
            str(kind or "").lower() in {"item", "point", "paragraph", "subparagraph"}
            and bool(re.fullmatch(r"\[[^\]]+\]", str(label or "").strip()))
            for kind, label in path
        ):
            return True
        if any(_clean_num(label or "").lower() == "note" for _, label in path):
            return True
        if any(
            re.sub(r"[^0-9a-z]+", "", _clean_num(label or "").lower()) in {"crossheading", "crossheadings"}
            for _, label in path
        ):
            return True
        if uk_malformed_target_sectionlike_label_gap(target):
            return True
        if _addr_container(target) == "schedule":
            first_kind, first_label = path[0]
            if first_kind == "schedule" and not _clean_num(first_label or ""):
                return True
        if len(path) >= 2:
            parent_target = LegalAddress(path=path[:-1], special=None)
            parent_node, _, _ = self._find_node_by_target(parent_target)
            leaf_kind, leaf_label = path[-1]
            textual_leaf = re.sub(r"[^0-9a-z]+", "", str(leaf_label or "").lower())
            is_roman = bool(re.fullmatch(r"[ivxlcdm]+", textual_leaf))
            is_alpha = bool(re.fullmatch(r"[a-z]+", textual_leaf))
            if (
                len(path) >= 2
                and str(path[-2][0] or "").lower() == "subsection"
                and re.fullmatch(r"[a-z]+", str(path[-2][1] or "").strip().lower())
                and str(path[-1][0] or "").lower() == "paragraph"
                and is_roman
            ):
                return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "paragraph"
                and is_roman
                and str(getattr(parent_node, "kind", "") or "").lower() == "subsection"
            ):
                for child in getattr(parent_node, "children", []) or []:
                    if str(getattr(child, "kind", "") or "").lower() != "paragraph":
                        continue
                    for grandchild in getattr(child, "children", []) or []:
                        if str(getattr(grandchild, "kind", "") or "").lower() not in {"subparagraph", "item", "point"}:
                            continue
                        grandchild_label = re.sub(
                            r"[^0-9a-z]+",
                            "",
                            str(getattr(grandchild, "label", "") or "").lower(),
                        )
                        if grandchild_label == textual_leaf:
                            return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "subparagraph"
                and is_alpha
                and str(getattr(parent_node, "kind", "") or "").lower() == "paragraph"
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() in {"subparagraph", "item", "point"}
                ]
                if child_labels and all(re.fullmatch(r"[ivxlcdm]+", label) for label in child_labels if label):
                    return True
                if child_labels and all(re.fullmatch(r"\d+", label) for label in child_labels if label):
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "subparagraph"
                and textual_leaf.isdigit()
                and str(getattr(parent_node, "kind", "") or "").lower() == "paragraph"
            ):
                child_kinds = {
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                }
                if child_kinds and child_kinds <= {"item", "point"}:
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() in {"item", "point"}
                and str(getattr(parent_node, "kind", "") or "").lower() in {"item", "point", "subparagraph"}
                and textual_leaf.isdigit()
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() in {"item", "point"}
                ]
                if child_labels and all(re.fullmatch(r"[ivxlcdm]+", label) for label in child_labels if label):
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() in {"item", "point"}
                and str(getattr(parent_node, "kind", "") or "").lower() in {"item", "point", "subparagraph"}
                and is_alpha
                and len(textual_leaf) > 1
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() in {"item", "point"}
                ]
                if child_labels and all(re.fullmatch(r"[a-z]", label) for label in child_labels if label):
                    return True
                if textual_leaf[:1] in child_labels:
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() in {"item", "point"}
                and str(getattr(parent_node, "kind", "") or "").lower() == "paragraph"
                and is_alpha
            ):
                child_kinds = {
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                }
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() == "subparagraph"
                ]
                if (
                    child_kinds
                    and child_kinds <= {"subparagraph"}
                    and child_labels
                    and all(re.fullmatch(r"\d+[a-z]?", label) for label in child_labels if label)
                ):
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "paragraph"
                and textual_leaf.isdigit()
                and str(getattr(parent_node, "kind", "") or "").lower() == "subsection"
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() == "paragraph"
                ]
                if child_labels and all(re.fullmatch(r"[a-z]+", label) for label in child_labels if label):
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "paragraph"
                and is_alpha
                and len(textual_leaf) > 1
                and str(getattr(parent_node, "kind", "") or "").lower() == "subsection"
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() == "paragraph"
                ]
                if child_labels and all(re.fullmatch(r"[a-z]", label) for label in child_labels if label):
                    return True
                first = textual_leaf[:1]
                rest = textual_leaf[1:]
                if rest and first in child_labels:
                    return True
                for child in getattr(parent_node, "children", []) or []:
                    if str(getattr(child, "kind", "") or "").lower() != "paragraph":
                        continue
                    child_label = re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    if child_label != first:
                        continue
                    descendant_labels = [
                        re.sub(r"[^0-9a-z]+", "", str(getattr(grandchild, "label", "") or "").lower())
                        for grandchild in getattr(child, "children", []) or []
                        if str(getattr(grandchild, "kind", "") or "").lower() in {"subparagraph", "item", "point"}
                    ]
                    if rest and rest in descendant_labels:
                        return True
                last = textual_leaf[-1:]
                prefix = textual_leaf[:-1]
                for child in getattr(parent_node, "children", []) or []:
                    if str(getattr(child, "kind", "") or "").lower() != "paragraph":
                        continue
                    child_label = re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    if child_label != last:
                        continue
                    descendant_labels = [
                        re.sub(r"[^0-9a-z]+", "", str(getattr(grandchild, "label", "") or "").lower())
                        for grandchild in getattr(child, "children", []) or []
                        if str(getattr(grandchild, "kind", "") or "").lower() in {"subparagraph", "item", "point"}
                    ]
                    if prefix and prefix in descendant_labels:
                        return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "subsection"
                and textual_leaf.isdigit()
                and str(getattr(parent_node, "kind", "") or "").lower() in {"section", "article", "rule", "regulation"}
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() == "subsection"
                ]
                if child_labels and any(label == "" for label in child_labels):
                    return True
                if any(re.fullmatch(rf"{re.escape(textual_leaf)}[a-z]+", label) for label in child_labels if label):
                    return True
            if (
                parent_node is not None
                and _addr_container(target) == "schedule"
                and len(path) == 2
                and str(leaf_kind or "").lower() == "paragraph"
                and str(getattr(parent_node, "kind", "") or "").lower() == "schedule"
            ):
                child_kinds = {
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                }
                if "part" in child_kinds:
                    return True
                if re.fullmatch(r"[a-z]+\d+", textual_leaf):
                    paragraph_labels = [
                        label for label in _descendant_labels_by_kind(parent_node, kinds={"paragraph"}) if label
                    ]
                    if paragraph_labels and all(re.fullmatch(r"\d+[a-z]?", label) for label in paragraph_labels):
                        return True
            if self._schedule_unlabeled_paragraph_target_gap(target):
                return True
            if (
                parent_node is not None
                and _addr_container(target) == "schedule"
                and len(path) == 2
                and str(leaf_kind or "").lower() in {"part", "chapter", "division"}
                and str(getattr(parent_node, "kind", "") or "").lower() == "schedule"
            ):
                child_kinds = {
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                }
                if child_kinds and child_kinds <= {"crossheading", "pblock"}:
                    return True
            if (
                parent_node is not None
                and _addr_container(target) == "schedule"
                and str(leaf_kind or "").lower() == "paragraph"
                and str(getattr(parent_node, "kind", "") or "").lower() in {"part", "chapter", "division"}
            ):
                child_kinds = {
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                }
                if child_kinds and child_kinds <= {"crossheading", "pblock"}:
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "subsection"
                and textual_leaf.isdigit()
                and str(getattr(parent_node, "kind", "") or "").lower() in {"section", "article", "rule", "regulation"}
            ):
                child_kinds = [
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                ]
                if child_kinds and "subsection" not in child_kinds and "paragraph" in child_kinds:
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "subsection"
                and is_alpha
                and str(getattr(parent_node, "kind", "") or "").lower() in {"section", "article", "rule", "regulation"}
            ):
                child_kinds = [
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                ]
                if child_kinds and "subsection" not in child_kinds and "paragraph" in child_kinds:
                    return True
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() == "subsection"
                ]
                if child_labels and all(re.fullmatch(r"\d+[a-z]?", label) for label in child_labels if label):
                    return True
            if (
                parent_node is not None
                and str(leaf_kind or "").lower() == "subsection"
                and re.fullmatch(r"\d+[a-z]{2,}", textual_leaf)
                and str(getattr(parent_node, "kind", "") or "").lower() in {"section", "article", "rule", "regulation"}
            ):
                child_labels = [
                    re.sub(r"[^0-9a-z]+", "", str(getattr(child, "label", "") or "").lower())
                    for child in getattr(parent_node, "children", []) or []
                    if str(getattr(child, "kind", "") or "").lower() == "subsection"
                ]
                if child_labels and all(re.fullmatch(r"\d+[a-z]?", label) for label in child_labels if label):
                    return True
            if (
                parent_node is not None
                and len(path) == 2
                and _addr_container(target) == "schedule"
                and str(leaf_kind or "").lower() in {"section", "article", "rule", "regulation"}
            ):
                child_kinds = {
                    str(getattr(child, "kind", "") or "").lower()
                    for child in getattr(parent_node, "children", []) or []
                }
                if child_kinds and child_kinds <= {"part", "chapter", "division", "crossheading", "pblock"}:
                    return True
        return any(_clean_num(label or "") == "and" for _, label in path)

    def _schedule_partition_target_gap(self, target: LegalAddress) -> bool:
        return bool(self._schedule_partition_target_gap_kind(target))

    def _schedule_partition_target_gap_kind(self, target: LegalAddress) -> str | None:
        path = tuple(getattr(target, "path", ()) or ())
        if _addr_container(target) != "schedule" or len(path) != 2:
            return None
        leaf_kind, _ = path[-1]
        if str(leaf_kind or "").lower() != "paragraph":
            return None
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None or str(getattr(parent_node, "kind", "") or "").lower() != "schedule":
            return None
        child_kinds = {
            str(getattr(child, "kind", "") or "").lower()
            for child in getattr(parent_node, "children", []) or []
        }
        if "part" in child_kinds:
            return "uk_replay_schedule_partition_part_target_gap"
        if child_kinds & {"chapter", "division"}:
            return "uk_replay_schedule_partition_target_gap"
        return None

    def _malformed_target_gap_kind(self, target: LegalAddress) -> str:
        if uk_malformed_target_placeholder_label_gap(target):
            return "uk_replay_malformed_target_placeholder_label_gap"
        if uk_malformed_target_note_or_crossheading_gap(target):
            return "uk_replay_malformed_target_note_or_crossheading_gap"
        if self._schedule_unlabeled_paragraph_target_gap(target):
            return "uk_replay_schedule_unlabeled_paragraph_target_gap"
        partition_kind = self._schedule_partition_target_gap_kind(target)
        if partition_kind is not None:
            return partition_kind
        if uk_malformed_target_sectionlike_label_gap(target):
            return "uk_replay_malformed_target_sectionlike_label_gap"
        if uk_malformed_target_schedule_root_label_gap(target):
            return "uk_replay_malformed_target_schedule_root_label_gap"
        if self._malformed_target_gap(target):
            return "uk_replay_malformed_target_granularity_collapse_gap"
        return "uk_replay_malformed_target_gap"

    def _empty_descendant_shape_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2:
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None:
            return False
        return not bool(getattr(parent_node, "children", []) or [])

    def _recover_text_patch_on_empty_descendant_parent(
        self,
        op: LegalOperation,
        target: LegalAddress,
        text_patch: TextPatchSpec,
        replacement: str,
    ) -> bool:
        if not self._empty_descendant_shape_gap(target):
            return False
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2:
            return False
        leaf_kind = str(path[-1][0] or "").lower()
        if leaf_kind not in {"paragraph", "subparagraph", "item", "point"}:
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None or getattr(parent_node, "children", None):
            return False
        match_text = text_patch.selector.match_text
        if not _node_text_contains_text(parent_node, match_text):
            return False
        rebuilt, applied = self._apply_text_replace_on_node_text_only(
            parent_node,
            match_text,
            replacement,
            text_patch.selector.occurrence,
            text_patch.selector.end_occurrence,
        )
        if not applied:
            return False
        self._log(
            f"  EXECUTOR: text_replace empty-descendant parent recovery in {rebuilt.kind} {rebuilt.label}: {match_text!r} -> {replacement!r}"
        )
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind="uk_replay_empty_descendant_parent_text_recovered",
            message=(
                "UK replay applied a text patch to an empty parent because the "
                "source-targeted descendant is not represented as a structural carrier."
            ),
            op=op,
            detail=uk_replay_recovery_action_target_detail(
                op,
                target,
                family="target_resolution_recovery",
                recovery_target=str(parent_target),
                text_match=match_text,
                replacement_text=replacement,
            ),
        )
        return True

    def _implicit_first_subparagraph_parent_text_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2:
            return False
        leaf_kind = str(path[-1][0] or "").lower()
        leaf_label = _clean_num(str(path[-1][1] or ""))
        if leaf_kind != "subparagraph" or leaf_label != "1":
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None or _uk_kind_value(parent_node.kind).lower() != "paragraph":
            return False
        for child in getattr(parent_node, "children", []) or []:
            child_kind = _uk_kind_value(child.kind).lower()
            child_label = _clean_num(str(child.label or ""))
            if child_kind == "subparagraph" and child_label == "1":
                return False
        return bool(parent_node.text or "")

    def _recover_text_patch_on_implicit_first_subparagraph_parent_text(
        self,
        op: LegalOperation,
        target: LegalAddress,
        text_patch: TextPatchSpec,
        replacement: str,
    ) -> bool:
        if not self._implicit_first_subparagraph_parent_text_gap(target):
            return False
        parent_target = LegalAddress(path=tuple(target.path[:-1]), special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None:
            return False
        match_text = text_patch.selector.match_text
        if not _node_text_contains_text(parent_node, match_text):
            return False
        rebuilt, applied = self._apply_text_replace_on_node_text_only(
            parent_node,
            match_text,
            replacement,
            text_patch.selector.occurrence,
            text_patch.selector.end_occurrence,
        )
        if not applied:
            return False
        self._log(
            f"  EXECUTOR: text_replace implicit first-subparagraph parent-text recovery in {rebuilt.kind} {rebuilt.label}: {match_text!r} -> {replacement!r}"
        )
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind="uk_replay_implicit_first_subparagraph_parent_text_recovered",
            message=(
                "UK replay applied a text patch to the paragraph intro text because "
                "the source-targeted first subparagraph is represented as parent text "
                "rather than a structural child."
            ),
            op=op,
            detail=uk_replay_recovery_action_target_detail(
                op,
                target,
                family="target_resolution_recovery",
                recovery_target=str(parent_target),
                text_match=match_text,
                replacement_text=replacement,
                source_shape="implicit_first_subparagraph_parent_text",
            ),
        )
        return True

    def _recover_source_carried_structured_tail_substitution(
        self,
        op: LegalOperation,
        target: LegalAddress,
        new_node: UKMutableNode,
    ) -> bool:
        witness = _witness_for_op(op)
        extraction = getattr(witness, "extraction_witness", None)
        source_text = str(getattr(extraction, "extracted_text", "") or getattr(op.source, "raw_text", "") or "")
        anchor = source_following_anchor_structured_substitution_anchor(source_text)
        if not anchor:
            return False
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2:
            return False
        leaf_kind = str(path[-1][0] or "").lower()
        leaf_label = _clean_num(str(path[-1][1] or ""))
        if leaf_kind not in {"paragraph", "subparagraph", "item", "point"} or not leaf_label:
            return False
        if not uk_kind_matches(
            node_kind=str(new_node.kind),
            target_kind=leaf_kind,
            node_label=_clean_num(new_node.label or ""),
            target_label=leaf_label,
        ):
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None:
            return False
        for child in getattr(parent_node, "children", []) or []:
            if str(child.kind).lower() == leaf_kind and _clean_num(str(child.label or "")) == leaf_label:
                return False

        parent_had_children = bool(getattr(parent_node, "children", []) or [])
        parent_tail_trimmed = False
        if not parent_had_children:
            parent_node, parent_tail_trimmed = self._apply_text_replace_on_node_text_only(
                parent_node,
                f"TEXT_AFTER_{anchor}_TO_END",
                "",
                occurrence=0,
            )
            if not parent_tail_trimmed:
                return False
            trimmed_parent_text = (parent_node.text or "").rstrip()
            if trimmed_parent_text != (parent_node.text or ""):
                old_parent_node = parent_node
                parent_node = dc_replace(parent_node, text=trimmed_parent_text)
                self._replace_node_in_statute(old_parent_node, parent_node)

        if not str(new_node.attrs.get("eId") or new_node.attrs.get("id") or ""):
            new_node.attrs["eId"] = self._derive_target_eid(target)
        uk_insert_child_sorted(parent_node, new_node)
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind="uk_replay_source_carried_structured_tail_substitution_recovered",
            message=(
                "UK replay materialized a source-carried structured substitution: "
                "the affecting text replaces the words after a quoted parent anchor "
                "with explicit child provisions."
            ),
            op=op,
            detail=uk_replay_recovery_action_target_detail(
                op,
                target,
                family="source_carried_structured_tail_substitution",
                recovery_target=str(parent_target),
                source_anchor=anchor,
                payload_kind=str(new_node.kind),
                payload_label=str(new_node.label or ""),
                parent_had_children_before=parent_had_children,
                parent_tail_trimmed=parent_tail_trimmed,
            ),
        )
        return True

    def _recover_source_carried_labeled_child_text_substitution(
        self,
        op: LegalOperation,
        target: LegalAddress,
        node: UKMutableNode,
        text_patch: TextPatchSpec,
        replacement: str,
    ) -> bool:
        if text_patch.kind is not TextPatchKindEnum.REPLACE:
            return False
        if text_patch.selector.end_occurrence:
            return False
        if (
            "uk_effect_source_carried_quoted_text_substitution_text_patch"
            not in _text_rewrite_rule_ids_for_op(op)
        ):
            return False
        if target.special is not None:
            return False
        parent_kind = _addr_leaf_kind(target) or ""
        child_shape = _source_carried_labeled_child_replacement_shape(
            replacement,
            parent_kind=parent_kind,
        )
        child_kind = child_shape.child_kind
        parts = child_shape.parts
        if not child_kind or not parts:
            return False
        if getattr(node, "children", None):
            return False
        text = node.text or ""
        if not text:
            return False
        match_text = text_patch.selector.match_text
        if not match_text or match_text.startswith("TEXT_"):
            return False

        ordinal = text_patch.selector.occurrence if text_patch.selector.occurrence > 0 else 1

        def _find_span(pattern: str, *, flags: int = 0) -> tuple[int, int] | None:
            matches = list(re.finditer(pattern, text, flags=flags))
            if text_patch.selector.occurrence == 0 and len(matches) != 1:
                return None
            if len(matches) < ordinal:
                return None
            selected = matches[ordinal - 1]
            return selected.start(), selected.end()

        literal_span = _find_span(re.escape(match_text))
        span = literal_span
        if span is None:
            span = _find_span(
                _text_patch_pattern(match_text, allow_punctuation_spacing=True),
                flags=re.I | re.S,
            )
        if span is None and _text_match_has_word_punctuation_elision_candidate(match_text):
            span = _find_span(
                _text_patch_pattern(match_text, allow_word_punctuation_elision=True),
                flags=re.I | re.S,
            )
        if span is None:
            return False

        before = text[: span[0]].rstrip()
        after = text[span[1] :].strip()
        # Do not smuggle unrelated parent-tail text into a child-materialization recovery.
        if after and not re.fullmatch(r"[\.,;:]+", after):
            return False
        rebuilt_text = before.rstrip(" ,;:")
        if child_shape.parent_prefix:
            rebuilt_text = " ".join(
                part for part in (rebuilt_text, child_shape.parent_prefix) if part
            )
        parent_eid = str(node.attrs.get("eId") or node.attrs.get("id") or "")
        children: list[UKMutableNode] = []
        for label, child_text in parts:
            child_target = LegalAddress(path=(*tuple(target.path), (child_kind, label)), special=None)
            child_eid = self._derive_target_eid(child_target)
            attrs = {"source_rule_id": _UK_REPLAY_SOURCE_CARRIED_LABELED_CHILD_TEXT_SUBSTITUTION_RULE_ID}
            if child_eid:
                attrs["eId"] = child_eid
            elif parent_eid:
                attrs["eId"] = f"{parent_eid}-{label}"
            children.append(
                UKMutableNode(
                    kind=uk_ir_node_kind(child_kind),
                    label=label,
                    text=child_text,
                    attrs=attrs,
                )
            )
        if not children:
            return False

        uk_replace_text_and_children(node, text=rebuilt_text, children=children)
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind=_UK_REPLAY_SOURCE_CARRIED_LABELED_CHILD_TEXT_SUBSTITUTION_RULE_ID,
            message=(
                "UK replay materialized visible labelled child provisions from a "
                "source-carried quoted substitution payload."
            ),
            op=op,
            detail=uk_replay_recovery_action_target_detail(
                op,
                target,
                family="source_carried_labeled_child_text_substitution",
                text_match=match_text,
                replacement_text=replacement,
                child_kind=child_kind,
                child_labels=tuple(label for label, _ in parts),
                source_parent_prefix=child_shape.parent_prefix,
                source_shape="flat_replacement_payload_with_visible_child_labels",
            ),
        )
        return True

    def _annex_schedule_mismatch_gap(self, op: LegalOperation) -> bool:
        target = getattr(op, "target", None)
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) != 1 or str(path[0][0] or "").lower() != "schedule":
            return False
        witness = _witness_for_op(op)
        extraction = getattr(witness, "extraction_witness", None)
        original_ref = str(getattr(extraction, "original_ref", "") or "")
        if "annex" not in original_ref.lower():
            for note in getattr(op, "provenance_tags", []) or []:
                if str(note or "").startswith("original_ref:") and "annex" in str(note or "").lower():
                    original_ref = str(note or "")
                    break
        if "annex" not in original_ref.lower():
            return False
        if target is None:
            return False
        node, _, _ = self._find_node_by_target(cast(LegalAddress, target))
        return node is None

    def _missing_parent_shape_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2:
            return False
        if self._schedule_paragraph_carrier_gap(target):
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        return parent_node is None

    def _missing_parent_grandparent_present_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 3:
            return False
        if not self._missing_parent_shape_gap(target):
            return False
        grandparent_target = LegalAddress(path=path[:-2], special=None)
        grandparent_node, _, _ = self._find_node_by_target(grandparent_target)
        return grandparent_node is not None

    def _missing_parent_shape_gap_kind(self, target: LegalAddress) -> str:
        if self._missing_schedule_branch_gap(target):
            return "uk_replay_missing_schedule_branch_gap"
        if self._missing_parent_grandparent_present_gap(target):
            return "uk_replay_missing_parent_grandparent_present_gap"
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) == 2:
            return "uk_replay_missing_root_parent_shape_gap"
        return "uk_replay_missing_parent_shape_gap"

    def _schedule_paragraph_carrier_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if _addr_container(target) != "schedule" or len(path) < 3:
            return False
        if not any(str(kind or "").lower() == "paragraph" for kind, _ in path):
            return False
        leaf_kind = str(path[-1][0] or "").lower()
        if leaf_kind not in {"subparagraph", "item", "point"}:
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is not None and str(getattr(parent_node, "kind", "") or "").lower() == "p1group":
            return True
        grandparent_target = LegalAddress(path=path[:-2], special=None)
        grandparent_node, _, _ = self._find_node_by_target(grandparent_target)
        return grandparent_node is not None and parent_node is None

    def _schedule_paragraph_carrier_gap_kind(self, target: LegalAddress) -> str:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) >= 2:
            parent_target = LegalAddress(path=path[:-1], special=None)
            parent_node, _, _ = self._find_node_by_target(parent_target)
            if parent_node is not None and str(getattr(parent_node, "kind", "") or "").lower() == "p1group":
                return "uk_replay_schedule_p1group_wrapper_carrier_gap"
        return "uk_replay_schedule_paragraph_carrier_gap"

    def _direct_section_paragraph_carrier_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) != 2:
            return False
        if str(path[0][0] or "").lower() != "section" or str(path[1][0] or "").lower() != "paragraph":
            return False
        label = re.sub(r"[^0-9a-z]+", "", str(path[1][1] or "").lower())
        if not re.fullmatch(r"[a-z]", label):
            return False
        parent_node, _, _ = self._find_node_by_target(LegalAddress(path=path[:1], special=None))
        if parent_node is None or str(getattr(parent_node, "kind", "") or "").lower() not in {
            "section",
            "article",
            "rule",
            "regulation",
        }:
            return False
        child_kinds = {
            str(getattr(child, "kind", "") or "").lower()
            for child in getattr(parent_node, "children", []) or []
        }
        return bool(child_kinds and "paragraph" not in child_kinds)

    def _recover_text_patch_on_direct_section_paragraph_child_text(
        self,
        op: LegalOperation,
        target: LegalAddress,
        text_patch: TextPatchSpec,
        replacement: str,
    ) -> bool:
        if not self._direct_section_paragraph_carrier_gap(target):
            return False
        path = tuple(getattr(target, "path", ()) or ())
        parent_target = LegalAddress(path=path[:1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None:
            return False
        match_text = text_patch.selector.match_text
        required_occurrence = text_patch.selector.occurrence if text_patch.selector.occurrence > 0 else 1
        candidates = [
            child
            for child in parent_node.children
            if _subtree_text_match_count(child, match_text) >= required_occurrence
        ]
        if len(candidates) != 1:
            return False
        recovered_target = candidates[0]
        rebuilt, applied = self._apply_text_replace_on_subtree(
            recovered_target,
            match_text,
            replacement,
            text_patch.selector.occurrence,
            text_patch.selector.end_occurrence,
        )
        if not applied:
            return False
        self._log(
            f"  EXECUTOR: text_replace direct section-paragraph child-text recovery in {rebuilt.kind} {rebuilt.label}: {match_text!r} -> {replacement!r}"
        )
        _append_uk_replay_adjudication(
            self.adjudications_out,
            kind="uk_replay_direct_section_paragraph_child_text_recovered",
            message=(
                "UK replay applied a direct section-paragraph text patch to a unique "
                "direct child because the source-targeted paragraph is not represented "
                "as an addressable carrier."
            ),
            op=op,
            detail=uk_replay_recovery_action_target_detail(
                op,
                target,
                family="target_resolution_recovery",
                recovery_target=str(
                    LegalAddress(
                        path=(
                            *parent_target.path,
                            (_uk_kind_value(recovered_target.kind), recovered_target.label or ""),
                        ),
                        special=None,
                    )
                ),
                text_match=match_text,
                replacement_text=replacement,
                source_shape="direct_section_paragraph_text_carried_by_unique_child",
            ),
        )
        return True

    def _leading_blank_subparagraph_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if not path:
            return False
        leaf_kind, leaf_label = path[-1]
        if str(leaf_kind or "").lower() != "subparagraph":
            return False
        text = str(leaf_label or "").strip().lower()
        want_pair = None
        if text.isdigit():
            want_num = int(text)
        elif re.fullmatch(r"\d+[a-z]", text):
            want_pair = _local_alnum_suffix_key(text)
            if want_pair is None:
                return False
            want_num = want_pair[0]
        else:
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None or str(getattr(parent_node, "kind", "") or "").lower() != "paragraph":
            return False
        blank_present = False
        numeric_labels: list[int] = []
        numeric_pairs: list[tuple[int, int]] = []
        for child in getattr(parent_node, "children", []) or []:
            if str(getattr(child, "kind", "") or "").lower() != "subparagraph":
                continue
            raw = str(getattr(child, "label", "") or "").strip().lower()
            if not raw:
                blank_present = True
                continue
            if raw.isdigit():
                numeric_labels.append(int(raw))
                continue
            pair = _local_alnum_suffix_key(raw)
            if pair is not None:
                numeric_pairs.append(pair)
        if not blank_present:
            return False
        if want_pair is not None:
            if any(pair[0] == want_pair[0] and pair[1] > want_pair[1] for pair in numeric_pairs):
                return True
        if numeric_labels and want_num < min(numeric_labels):
            return True
        if numeric_pairs and want_num < min(pair[0] for pair in numeric_pairs):
            return True
        return False

    def _missing_schedule_branch_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2 or str(path[0][0] or "").lower() != "schedule":
            return False
        schedule_target = LegalAddress(path=path[:1], special=None)
        schedule_node, _, _ = self._find_node_by_target(schedule_target)
        return schedule_node is None

    def _prior_same_target_gap_kind(self, target: LegalAddress) -> str | None:
        want = str(target)
        prior = getattr(self, "adjudications_out", None) or []
        preferred = {
            "uk_replay_annex_schedule_reference_gap",
            "uk_replay_empty_descendant_shape_gap",
            "uk_replay_missing_parent_shape_gap",
            "uk_replay_missing_parent_grandparent_present_gap",
            "uk_replay_missing_root_parent_shape_gap",
            "uk_replay_missing_schedule_branch_gap",
            "uk_replay_missing_schedule_range_gap",
            "uk_replay_missing_sectionlike_range_gap",
            "uk_replay_malformed_target_granularity_collapse_gap",
            "uk_replay_malformed_target_gap",
            "uk_replay_malformed_target_note_or_crossheading_gap",
            "uk_replay_malformed_target_placeholder_label_gap",
            "uk_replay_malformed_target_schedule_root_label_gap",
            "uk_replay_malformed_target_sectionlike_label_gap",
            "uk_replay_replace_payload_target_leaf_mismatch_gap",
            "uk_replay_repealed_target_gap",
            "uk_replay_absent_sibling_range_gap",
            "uk_replay_schedule_container_text_target_gap",
            "uk_replay_schedule_paragraph_carrier_gap",
            "uk_replay_schedule_p1group_wrapper_carrier_gap",
            "uk_replay_schedule_partition_target_gap",
            "uk_replay_schedule_partition_part_target_gap",
            "uk_replay_schedule_unlabeled_paragraph_target_gap",
            "uk_replay_subsection_descendant_target_collapse_gap",
            "uk_replay_table_shape_gap",
            "uk_replay_missing_source_target_gap",
        }
        for adjudication in reversed(prior):
            kind = str(getattr(adjudication, "kind", "") or "")
            if kind not in preferred:
                continue
            detail = getattr(adjudication, "detail", {}) or {}
            if str(detail.get("target", "") or "") == want:
                return kind
        return None

    def _missing_sibling_range_gap(self, target: LegalAddress) -> bool:
        # Roman numeral parser: shared implementation in lawvm.roman
        # rejects non-canonical spellings like "IIII" via round-trip
        # canonicalization.  The previous nested implementation had a
        # latent bug where ``prev`` only updated in the additive branch.
        _roman_to_int = _shared_roman_to_arabic

        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2:
            return False
        leaf_kind, leaf_label = path[-1]
        text = str(leaf_label or "").strip().lower()
        mode: str | None = None
        want: int
        want_pair: tuple[int, int] | None = None
        want_multi_pair: tuple[int, str] | None = None
        want_alpha_num_pair: tuple[str, int] | None = None
        if text.isdigit():
            mode = "numeric"
            want = int(text)
        elif re.fullmatch(r"[a-z]", text):
            mode = "alpha"
            want = ord(text) - ord("a") + 1
        elif re.fullmatch(r"[a-z]{2,}", text):
            mode = "alpha_suffix"
            want = ord(text[0]) - ord("a") + 1
        elif re.fullmatch(r"[ivxlcdm]+", text):
            roman = _roman_to_int(text)
            if roman is None:
                return False
            mode = "roman"
            want = roman
        elif re.fullmatch(r"\d+[a-z]", text):
            pair = _local_alnum_suffix_key(text)
            if pair is None:
                return False
            mode = "alnum_suffix"
            want = pair[0]
            want_pair = pair
        elif re.fullmatch(r"\d+[a-z]{2,}", text):
            pair = _alnum_multi_suffix_key(text)
            if pair is None:
                return False
            mode = "alnum_multi_suffix"
            want = pair[0]
            want_multi_pair = pair
        elif re.fullmatch(r"[a-z]+\d+", text):
            pair = _alpha_num_suffix_key(text)
            if pair is None:
                return False
            mode = "alpha_num_suffix"
            want = pair[1]
            want_alpha_num_pair = pair
        else:
            return False
        if len(path) == 1:
            parent_node = self.statute.body
        else:
            parent_target = LegalAddress(path=path[:-1], special=None)
            parent_node, _, _ = self._find_node_by_target(parent_target)
            if parent_node is None:
                return False
        if str(leaf_kind or "").lower() == "part" and text.isdigit():
            part_nums: list[int] = []
            for child in getattr(parent_node, "children", []) or []:
                if str(getattr(child, "kind", "") or "").lower() != "part":
                    continue
                num = _part_numeric_value(str(getattr(child, "label", "") or ""))
                if num is not None:
                    part_nums.append(num)
            if part_nums:
                part_nums = sorted(set(part_nums))
                want_num = int(text)
                lower = max((n for n in part_nums if n < want_num), default=None)
                upper = min((n for n in part_nums if n > want_num), default=None)
                if lower is not None and upper is not None and lower < want_num < upper:
                    return True
                if lower is None and part_nums and want_num < part_nums[0]:
                    return True
                if upper is None and part_nums and want_num > part_nums[-1]:
                    return True
        if str(leaf_kind or "").lower() == "part" and re.fullmatch(r"\d+[a-z]+", text):
            base_match = re.fullmatch(r"(\d+)[a-z]+", text)
            if base_match is not None:
                want_num = int(base_match.group(1))
                part_nums: list[int] = []
                for child in getattr(parent_node, "children", []) or []:
                    if str(getattr(child, "kind", "") or "").lower() != "part":
                        continue
                    raw = str(getattr(child, "label", "") or "").strip()
                    base_num = _part_numeric_value(raw)
                    if base_num is not None:
                        part_nums.append(base_num)
                        continue
                    m = re.fullmatch(r"part\s+(\d+)[a-z]+", raw, re.I)
                    if m is not None:
                        part_nums.append(int(m.group(1)))
                if part_nums:
                    part_nums = sorted(set(part_nums))
                    lower = max((n for n in part_nums if n < want_num), default=None)
                    upper = min((n for n in part_nums if n > want_num), default=None)
                    if lower is not None and upper is not None and lower < want_num < upper:
                        return True
                    if any(n == want_num for n in part_nums):
                        return True
        sibling_labels: list[int] = []
        sibling_pairs: list[tuple[int, int]] = []
        sibling_multi_pairs: list[tuple[int, str]] = []
        sibling_alpha_num_pairs: list[tuple[str, int]] = []
        alpha_raw_labels: list[str] = []
        numeric_suffix_labels: list[int] = []
        alpha_suffix_labels: list[str] = []
        blank_same_kind_present = False
        for child in getattr(parent_node, "children", []) or []:
            child_kind = str(getattr(child, "kind", "") or "").lower()
            if child_kind == str(leaf_kind or "").lower():
                label_text = str(getattr(child, "label", "") or "").strip()
                if not label_text:
                    blank_same_kind_present = True
                if mode == "numeric" and label_text.isdigit():
                    sibling_labels.append(int(label_text))
                elif mode == "numeric" and (pair := _local_alnum_suffix_key(label_text)) is not None:
                    numeric_suffix_labels.append(int(pair[0]))
                elif mode == "alpha" and re.fullmatch(r"[a-z]", label_text.lower()):
                    sibling_labels.append(ord(label_text.lower()) - ord("a") + 1)
                elif mode == "alpha":
                    alpha_raw_labels.append(label_text.lower())
                elif mode == "alpha_suffix":
                    lowered = label_text.lower()
                    if re.fullmatch(r"[a-z]", lowered):
                        sibling_labels.append(ord(lowered) - ord("a") + 1)
                    else:
                        alpha_suffix_labels.append(lowered)
                elif mode == "roman" and re.fullmatch(r"[ivxlcdm]+", label_text.lower()):
                    roman = _roman_to_int(label_text)
                    if roman is not None:
                        sibling_labels.append(roman)
                elif mode == "alnum_suffix":
                    pair = _local_alnum_suffix_key(label_text)
                    if pair is not None:
                        sibling_pairs.append(pair)
                    elif label_text.isdigit():
                        numeric_suffix_labels.append(int(label_text))
                elif mode == "alnum_multi_suffix":
                    pair = _alnum_multi_suffix_key(label_text)
                    if pair is not None:
                        sibling_multi_pairs.append(pair)
                    elif (pair1 := _local_alnum_suffix_key(label_text)) is not None:
                        sibling_multi_pairs.append((pair1[0], chr(ord("a") + pair1[1] - 1)))
                    elif label_text.isdigit():
                        numeric_suffix_labels.append(int(label_text))
                elif mode == "alpha_num_suffix":
                    pair = _alpha_num_suffix_key(label_text)
                    if pair is not None:
                        sibling_alpha_num_pairs.append(pair)
                    elif re.fullmatch(r"[a-z]+", label_text.lower()):
                        alpha_raw_labels.append(label_text.lower())
                continue
            if uk_is_transparent_wrapper_kind(child_kind):
                for grandchild in getattr(child, "children", []) or []:
                    if str(getattr(grandchild, "kind", "") or "").lower() != str(leaf_kind or "").lower():
                        continue
                    label_text = str(getattr(grandchild, "label", "") or "").strip()
                    if not label_text:
                        blank_same_kind_present = True
                    if mode == "numeric" and label_text.isdigit():
                        sibling_labels.append(int(label_text))
                    elif mode == "numeric" and (pair := _local_alnum_suffix_key(label_text)) is not None:
                        numeric_suffix_labels.append(int(pair[0]))
                    elif mode == "alpha" and re.fullmatch(r"[a-z]", label_text.lower()):
                        sibling_labels.append(ord(label_text.lower()) - ord("a") + 1)
                    elif mode == "alpha":
                        alpha_raw_labels.append(label_text.lower())
                    elif mode == "alpha_suffix":
                        lowered = label_text.lower()
                        if re.fullmatch(r"[a-z]", lowered):
                            sibling_labels.append(ord(lowered) - ord("a") + 1)
                        else:
                            alpha_suffix_labels.append(lowered)
                    elif mode == "roman" and re.fullmatch(r"[ivxlcdm]+", label_text.lower()):
                        roman = _roman_to_int(label_text)
                        if roman is not None:
                            sibling_labels.append(roman)
                    elif mode == "alnum_suffix":
                        pair = _local_alnum_suffix_key(label_text)
                        if pair is not None:
                            sibling_pairs.append(pair)
                        elif label_text.isdigit():
                            numeric_suffix_labels.append(int(label_text))
                    elif mode == "alnum_multi_suffix":
                        pair = _alnum_multi_suffix_key(label_text)
                        if pair is not None:
                            sibling_multi_pairs.append(pair)
                        elif (pair1 := _local_alnum_suffix_key(label_text)) is not None:
                            sibling_multi_pairs.append((pair1[0], chr(ord("a") + pair1[1] - 1)))
                        elif label_text.isdigit():
                            numeric_suffix_labels.append(int(label_text))
                    elif mode == "alpha_num_suffix":
                        pair = _alpha_num_suffix_key(label_text)
                        if pair is not None:
                            sibling_alpha_num_pairs.append(pair)
                        elif re.fullmatch(r"[a-z]+", label_text.lower()):
                            alpha_raw_labels.append(label_text.lower())
        if mode == "alnum_multi_suffix":
            if want_multi_pair is None:
                return False
            if sibling_multi_pairs:
                sibling_multi_pairs = sorted(set(sibling_multi_pairs))
                lower = max((pair for pair in sibling_multi_pairs if pair < want_multi_pair), default=None)
                upper = min((pair for pair in sibling_multi_pairs if pair > want_multi_pair), default=None)
                if lower is not None or upper is not None:
                    return True
                if any(pair[0] == want_multi_pair[0] for pair in sibling_multi_pairs):
                    return True
            numeric_base_present = any(
                str(getattr(child, "kind", "") or "").lower() == str(leaf_kind or "").lower()
                and str(getattr(child, "label", "") or "").strip().lower() == str(want_multi_pair[0])
                for child in getattr(parent_node, "children", []) or []
            )
            if numeric_base_present:
                return True
            if numeric_suffix_labels and want_multi_pair[0] in set(numeric_suffix_labels):
                return True
            return False
        if mode == "alpha_num_suffix":
            if want_alpha_num_pair is None:
                return False
            if sibling_alpha_num_pairs:
                sibling_alpha_num_pairs = sorted(set(sibling_alpha_num_pairs))
                same_prefix = [pair for pair in sibling_alpha_num_pairs if pair[0] == want_alpha_num_pair[0]]
                if same_prefix:
                    lower = max((pair for pair in same_prefix if pair[1] < want_alpha_num_pair[1]), default=None)
                    upper = min((pair for pair in same_prefix if pair[1] > want_alpha_num_pair[1]), default=None)
                    if lower is not None or upper is not None:
                        return True
            if any(label == want_alpha_num_pair[0] for label in alpha_raw_labels):
                return True
            return False
        if mode == "alnum_suffix":
            if not sibling_pairs or want_pair is None:
                # If the section still has the numeric base subsection (e.g. "6")
                # but the alpha extension (e.g. "6A") is absent, treat this as the
                # same stale/shape family as other missing sibling gaps.
                want_pair_base = want_pair[0] if want_pair is not None else None
                want_num = str(want_pair_base) if want_pair_base is not None else ""
                numeric_base_present = any(
                    str(getattr(child, "kind", "") or "").lower() == str(leaf_kind or "").lower()
                    and str(getattr(child, "label", "") or "").strip().lower() == want_num
                    for child in getattr(parent_node, "children", []) or []
                )
                if numeric_suffix_labels and want_pair_base is not None:
                    nums = sorted(set(numeric_suffix_labels))
                    lower_num = max((n for n in nums if n < want_pair_base), default=None)
                    upper_num = min((n for n in nums if n > want_pair_base), default=None)
                    if lower_num is not None and upper_num is not None and lower_num < want_pair_base < upper_num:
                        return True
                    if lower_num is None and nums and want_pair_base < nums[0]:
                        return True
                    if upper_num is None and nums and want_pair_base > nums[-1]:
                        return True
                return numeric_base_present
            sibling_pairs = sorted(set(sibling_pairs))
            lower = max((pair for pair in sibling_pairs if pair < want_pair), default=None)
            upper = min((pair for pair in sibling_pairs if pair > want_pair), default=None)
            if lower is not None and upper is not None and lower < want_pair < upper:
                return True
            if lower is None and sibling_pairs and want_pair < sibling_pairs[0]:
                return True
            if upper is None and sibling_pairs and want_pair > sibling_pairs[-1]:
                return True
            same_num = [pair for pair in sibling_pairs if pair[0] == want_pair[0]]
            if same_num:
                lower_same = max((pair for pair in same_num if pair[1] < want_pair[1]), default=None)
                upper_same = min((pair for pair in same_num if pair[1] > want_pair[1]), default=None)
                if lower_same is not None or upper_same is not None:
                    return True
            numeric_base_present = any(
                str(getattr(child, "kind", "") or "").lower() == str(leaf_kind or "").lower()
                and str(getattr(child, "label", "") or "").strip().lower() == str(want_pair[0])
                for child in getattr(parent_node, "children", []) or []
            )
            if numeric_base_present:
                return True
            if numeric_suffix_labels:
                nums = sorted(set(numeric_suffix_labels))
                lower_num = max((n for n in nums if n < want_pair[0]), default=None)
                upper_num = min((n for n in nums if n > want_pair[0]), default=None)
                if lower_num is not None and upper_num is not None and lower_num < want_pair[0] < upper_num:
                    return True
                if lower_num is None and nums and want_pair[0] < nums[0]:
                    return True
                if upper_num is None and nums and want_pair[0] > nums[-1]:
                    return True
            return False
        if mode == "alpha_suffix":
            if any(label.startswith(text) and len(label) > len(text) for label in alpha_suffix_labels):
                return True
            first = text[:1]
            if any(label == first for label in alpha_raw_labels):
                return True
            lower = max((n for n in sibling_labels if n < want), default=None)
            upper = min((n for n in sibling_labels if n > want), default=None)
            if lower is not None and upper is not None and lower < want < upper:
                return True
            if any(label.startswith(first) and len(label) > 1 for label in alpha_suffix_labels):
                return True
            return False
        if not sibling_labels:
            if mode == "numeric" and numeric_suffix_labels:
                nums = sorted(set(numeric_suffix_labels))
                lower_num = max((n for n in nums if n < want), default=None)
                upper_num = min((n for n in nums if n > want), default=None)
                if lower_num is not None and upper_num is not None and lower_num < want < upper_num:
                    return True
                if lower_num is None and nums and want < nums[0]:
                    return True
                if upper_num is None and nums and want > nums[-1]:
                    return True
            if mode == "alpha" and any(label.startswith(text) and len(label) > 1 for label in alpha_raw_labels):
                return True
            if mode == "alpha":
                repeated = sorted(label for label in alpha_raw_labels if re.fullmatch(r"([a-z])\1+", label))
                if repeated and any(rep < text for rep in repeated) and any(rep > text for rep in repeated):
                    return True
            return False
        if mode == "alpha":
            repeated = sorted(label for label in alpha_raw_labels if re.fullmatch(r"([a-z])\1+", label))
            if repeated and any(rep < text for rep in repeated) and any(rep > text for rep in repeated):
                return True
        sibling_labels = sorted(set(sibling_labels))
        if mode == "numeric" and blank_same_kind_present and sibling_labels and want < sibling_labels[0]:
            return True
        lower = max((label for label in sibling_labels if label < want), default=None)
        upper = min((label for label in sibling_labels if label > want), default=None)
        if lower is not None and upper is not None and lower < want < upper:
            return True
        if lower is None and sibling_labels and want < sibling_labels[0]:
            return True
        if upper is None and sibling_labels and want > sibling_labels[-1]:
            return True
        return False

    def _container_text_target_gap(self, op: LegalOperation) -> bool:
        target = getattr(op, "target", None)
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) != 2:
            return False
        if _addr_container(cast(LegalAddress, target)) != "schedule":
            return False
        leaf_kind, _ = path[-1]
        if str(leaf_kind or "").lower() not in {"part", "chapter"}:
            return False
        schedule_node, _, _ = self._find_node_by_target(LegalAddress(path=path[:1], special=None))
        if schedule_node is None:
            return False
        if any(
            str(getattr(child, "kind", "") or "").lower() == str(leaf_kind or "").lower()
            for child in getattr(schedule_node, "children", []) or []
        ):
            return False
        witness = _witness_for_op(op)
        extraction = getattr(witness, "extraction_witness", None)
        raw_text = str(getattr(extraction, "raw_text", "") or "")
        original_ref = str(getattr(extraction, "original_ref", "") or "")
        if not raw_text or not original_ref:
            for note in getattr(op, "provenance_tags", []) or []:
                note_text = str(note or "")
                if not raw_text and note_text.startswith("raw_text:"):
                    raw_text = note_text.partition(":")[2]
                elif not original_ref and note_text.startswith("original_ref:"):
                    original_ref = note_text.partition(":")[2]
        combined = f"{original_ref} {raw_text}".lower()
        return any(token in combined for token in ("paragraph", "sub-paragraph", "subparagraph", "item"))

    def _subsection_alpha_text_target_gap(self, op: LegalOperation) -> bool:
        target = getattr(op, "target", None)
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) != 2:
            return False
        if str(path[0][0] or "").lower() not in {"section", "article", "rule", "regulation"}:
            return False
        if str(path[1][0] or "").lower() != "subsection":
            return False
        leaf_label = str(path[1][1] or "").strip().lower()
        if not re.fullmatch(r"[a-z]+", leaf_label):
            return False
        parent_node, _, _ = self._find_node_by_target(LegalAddress(path=path[:1], special=None))
        if parent_node is None:
            return False
        subsection_labels = [
            str(getattr(child, "label", "") or "").strip().lower()
            for child in getattr(parent_node, "children", []) or []
            if str(getattr(child, "kind", "") or "").lower() == "subsection"
        ]
        if not subsection_labels or not all(re.fullmatch(r"\d+[a-z]?", label) for label in subsection_labels if label):
            return False
        witness = _witness_for_op(op)
        extraction = getattr(witness, "extraction_witness", None)
        raw_text = str(getattr(extraction, "raw_text", "") or "")
        original_ref = str(getattr(extraction, "original_ref", "") or "")
        if not raw_text or not original_ref:
            for note in getattr(op, "provenance_tags", []) or []:
                note_text = str(note or "")
                if not raw_text and note_text.startswith("raw_text:"):
                    raw_text = note_text.partition(":")[2]
                elif not original_ref and note_text.startswith("original_ref:"):
                    original_ref = note_text.partition(":")[2]
        combined = f"{original_ref} {raw_text}".lower()
        return bool(re.search(r"subsection\s*\(\d+[a-z]?\)\s*\([a-z]+\)", combined))

    def _missing_sectionlike_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) != 1:
            return False
        leaf_kind, leaf_label = path[0]
        if str(leaf_kind or "").lower() not in {"section", "article", "rule", "regulation"}:
            return False
        want_label = str(leaf_label or "").strip()
        if not want_label:
            return False
        want_key = _label_sort_key(want_label)
        labels = _collect_sectionlike_descendant_labels(self.statute.body)
        if not labels:
            return False
        existing = sorted({_label_sort_key(label): label for label in labels}.keys())
        if want_key in existing:
            return False
        lower = max((key for key in existing if key < want_key), default=None)
        upper = min((key for key in existing if key > want_key), default=None)
        return lower is not None and upper is not None

    def _doubled_alpha_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) < 2:
            return False
        leaf_kind, leaf_label = path[-1]
        text = str(leaf_label or "").strip().lower()
        if not re.fullmatch(r"([a-z])\1+", text):
            return False
        parent_target = LegalAddress(path=path[:-1], special=None)
        parent_node, _, _ = self._find_node_by_target(parent_target)
        if parent_node is None:
            return False
        labels = [
            str(getattr(child, "label", "") or "").strip().lower()
            for child in getattr(parent_node, "children", []) or []
            if str(getattr(child, "kind", "") or "").lower() == str(leaf_kind or "").lower()
        ]
        repeated = sorted(label for label in labels if re.fullmatch(r"([a-z])\1+", label))
        if not repeated:
            return False
        return any(rep < text for rep in repeated) and any(rep > text for rep in repeated)

    def _missing_schedule_root_gap(self, target: LegalAddress) -> bool:
        path = tuple(getattr(target, "path", ()) or ())
        if len(path) != 1 or str(path[0][0] or "").lower() != "schedule":
            return False
        want_label = str(path[0][1] or "").strip()
        if not want_label:
            return False
        want_key = _label_sort_key(want_label)
        labels = [str(getattr(sched, "label", "") or "").strip() for sched in self.statute.supplements]
        labels = [label for label in labels if label]
        if not labels:
            return False
        existing = sorted({_label_sort_key(label): label for label in labels}.keys())
        if want_key in existing:
            return False
        lower = max((key for key in existing if key < want_key), default=None)
        upper = min((key for key in existing if key > want_key), default=None)
        if lower is not None and upper is not None:
            return True
        if lower is None and existing and want_key < existing[0]:
            return True
        if upper is None and existing and want_key > existing[-1]:
            return True
        return False
