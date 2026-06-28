"""Typed invariant detector adapters for debugging tools."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from lawvm.core.frozen_values import FrozenDict, freeze_mapping
from lawvm.core.ir import IRNode, LegalOperation
from lawvm.core.ir_helpers import _kind_str, irnode_content_hash
from lawvm.core.replay_lints import build_flattened_sublist_findings, build_label_sequence_gap_findings
from lawvm.core.semantic_types import StructuralAction
from lawvm.core.tree_ops import (
    TreeInvariantKind,
    default_label_sort_key,
    format_invariant_path,
    find_text_duplication_warnings,
    iter_tree_invariant_violations,
    normalized_label_key,
)

InvariantDetectorName = Literal[
    "duplicate_label",
    "label_normalization_collision",
    "illegal_edge",
    "sort_order",
    "mixed_hierarchy",
    "all_tree",
    "text_duplication",
    "flattened_sublist_family",
    "label_sequence_gap",
    "descendant_sibling_loss",
    "same_source_descendant_snapshot_shadow",
]
SUPPORTED_INVARIANT_DETECTORS: tuple[InvariantDetectorName, ...] = (
    "duplicate_label",
    "label_normalization_collision",
    "illegal_edge",
    "sort_order",
    "mixed_hierarchy",
    "all_tree",
    "text_duplication",
    "flattened_sublist_family",
    "label_sequence_gap",
    "descendant_sibling_loss",
    "same_source_descendant_snapshot_shadow",
)

LabelNormalizer = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class LabelNormalizationCollisionDetail:
    """Typed detail for labels that collide under a normalizer."""

    parent_path: str
    child_kind: str
    normalized_label: str
    labels: tuple[str, ...]
    count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "parent_path": self.parent_path,
            "child_kind": self.child_kind,
            "normalized_label": self.normalized_label,
            "labels": self.labels,
            "count": self.count,
        }


@dataclass(frozen=True, slots=True)
class MissingSiblingGroup:
    """Descendant sibling labels missing from a sparse replacement payload."""

    parent_path: tuple[tuple[str, str], ...]
    child_kind: str
    labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DescendantSiblingLossDetail:
    """Typed detail for sparse snapshots that drop live descendant siblings."""

    op_id: str
    op_target: str
    payload_kind: str
    payload_label: str | None
    parent_relative_path: str
    missing_child_kind: str
    missing_count: int
    missing_labels_sample: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "op_id": self.op_id,
            "op_target": self.op_target,
            "payload_kind": self.payload_kind,
            "payload_label": self.payload_label,
            "parent_relative_path": self.parent_relative_path,
            "missing_child_kind": self.missing_child_kind,
            "missing_count": self.missing_count,
            "missing_labels_sample": self.missing_labels_sample,
        }


@dataclass(frozen=True, slots=True)
class SameSourceDescendantSnapshotShadowDetail:
    """Typed detail for ancestor snapshots shadowing same-source descendant ops."""

    ancestor_op_id: str
    ancestor_target: str
    descendant_op_id: str
    descendant_target: str
    source_statute: str
    ancestor_descendant_hash: str
    descendant_payload_hash: str

    def to_dict(self) -> dict[str, object]:
        return {
            "ancestor_op_id": self.ancestor_op_id,
            "ancestor_target": self.ancestor_target,
            "descendant_op_id": self.descendant_op_id,
            "descendant_target": self.descendant_target,
            "source_statute": self.source_statute,
            "ancestor_descendant_hash": self.ancestor_descendant_hash,
            "descendant_payload_hash": self.descendant_payload_hash,
        }


@dataclass(frozen=True, slots=True)
class InvariantDetectorResult:
    """Typed detector result with a legacy message projection."""

    detector: str
    kind: str
    path_text: str
    message: str
    detail: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.detector:
            raise ValueError("InvariantDetectorResult.detector must be non-empty")
        if not self.kind:
            raise ValueError("InvariantDetectorResult.kind must be non-empty")
        if not self.message:
            raise ValueError("InvariantDetectorResult.message must be non-empty")
        if not isinstance(self.detail, Mapping):
            raise ValueError("InvariantDetectorResult.detail must be a mapping")
        if not isinstance(self.detail, FrozenDict):
            object.__setattr__(self, "detail", freeze_mapping(self.detail))


def _detail_sequence(value: object) -> list[object]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return list(value)
    return []


def path_matches_target(path_text: str, target_path: str) -> bool:
    """Return true when a detector path contains the requested target path."""
    if not target_path:
        return True
    path_parts = path_text.split("/")
    target_parts = target_path.split("/")
    n, m = len(path_parts), len(target_parts)
    for i in range(n - m + 1):
        if path_parts[i : i + m] == target_parts:
            return True
    return False


def _address_part_text(path: Sequence[tuple[str, str]]) -> str:
    return "/".join(f"{kind}:{label}" for kind, label in path)


def _path_text(path: Sequence[tuple[str, str | None]]) -> str:
    return format_invariant_path(tuple((kind, label) for kind, label in path))


def _child_label_collision_message(
    path_text: str,
    child_kind: str,
    normalized_label: str,
    labels: Sequence[str],
) -> str:
    label_text = ", ".join(labels)
    return (
        f"{path_text}: label-normalization collision {child_kind}:{normalized_label} "
        f"from labels {label_text}"
    )


def run_label_normalization_collision_detector(
    ir: IRNode,
    label_normalizer: LabelNormalizer = normalized_label_key,
    target_path: str = "",
    detector: str = "label_normalization_collision",
) -> list[InvariantDetectorResult]:
    """Flag sibling labels that collide under a caller-supplied normalizer.

    Core's built-in ``normalized_duplicate_label`` intentionally uses only the
    shared default label key.  Jurisdictions with stronger slot-identity rules
    can inject their own normalizer here without moving local semantics into
    core.
    """
    results: list[InvariantDetectorResult] = []

    def _walk(node: IRNode, path: tuple[tuple[str, str | None], ...]) -> None:
        grouped: dict[tuple[str, str], list[str]] = {}
        for child in node.children:
            if child.label is None:
                continue
            child_kind = _kind_str(child.kind)
            normalized = label_normalizer(child.label)
            grouped.setdefault((child_kind, normalized), []).append(child.label)

        parent_path = _path_text(path)
        for (child_kind, normalized), labels in grouped.items():
            distinct_labels = tuple(dict.fromkeys(labels))
            if len(distinct_labels) < 2:
                continue
            if not path_matches_target(parent_path, target_path):
                continue
            message = _child_label_collision_message(
                parent_path,
                child_kind,
                normalized,
                distinct_labels,
            )
            results.append(
                InvariantDetectorResult(
                    detector=detector,
                    kind="label_normalization_collision",
                    path_text=parent_path,
                    message=message,
                    detail=LabelNormalizationCollisionDetail(
                        parent_path=parent_path,
                        child_kind=child_kind,
                        normalized_label=normalized,
                        labels=distinct_labels,
                        count=len(labels),
                    ).to_dict(),
                )
            )

        for child in node.children:
            child_label = child.label
            child_path = path
            if child_label is not None:
                child_path = path + ((_kind_str(child.kind), child_label),)
            _walk(child, child_path)

    _walk(ir, ((_kind_str(ir.kind), ir.label),))
    return results


def _node_matches_step(node: IRNode, step: tuple[str, str]) -> bool:
    kind, label = step
    return _kind_str(node.kind) == kind and normalized_label_key(node.label or "") == normalized_label_key(label)


def _resolve_address_path(root: IRNode, path: Sequence[tuple[str, str]]) -> IRNode | None:
    """Resolve a LegalAddress path, transparently crossing unlabeled wrappers."""
    if not path:
        return root
    for child in root.children:
        if _node_matches_step(child, path[0]):
            resolved = _resolve_address_path(child, path[1:])
            if resolved is not None:
                return resolved
    for child in root.children:
        if not child.label and _kind_str(child.kind) in {"body", "hcontainer"}:
            resolved = _resolve_address_path(child, path)
            if resolved is not None:
                return resolved
    return None


def _resolve_payload_relative_path(
    payload: IRNode,
    ancestor_path: Sequence[tuple[str, str]],
    descendant_path: Sequence[tuple[str, str]],
) -> IRNode | None:
    """Resolve descendant_path inside an ancestor payload.

    LegalOperation payloads normally carry the target node itself, so a section
    snapshot payload starts at ``section:N`` while its target path also ends at
    ``section:N``.  Strip that shared ancestor prefix and resolve only the
    descendant suffix inside the payload.
    """
    if len(descendant_path) <= len(ancestor_path):
        return None
    ancestor_leaf = ancestor_path[-1] if ancestor_path else None
    node = payload
    if ancestor_leaf is not None and _node_matches_step(node, ancestor_leaf):
        relative_path = descendant_path[len(ancestor_path) :]
    else:
        relative_path = descendant_path
    return _resolve_address_path(node, relative_path)


def _operation_source_statute_id(op: LegalOperation) -> str:
    return op.source.statute_id if op.source is not None else ""


def _labelled_descendant_paths(node: IRNode) -> set[tuple[tuple[str, str], ...]]:
    paths: set[tuple[tuple[str, str], ...]] = set()

    def _walk(current: IRNode, prefix: tuple[tuple[str, str], ...]) -> None:
        for child in current.children:
            child_kind = _kind_str(child.kind)
            child_path = prefix
            if child.label:
                child_path = prefix + ((child_kind, child.label),)
                paths.add(child_path)
            _walk(child, child_path)

    _walk(node, ())
    return paths


def _missing_sibling_groups(
    live_node: IRNode,
    payload: IRNode,
) -> tuple[MissingSiblingGroup, ...]:
    live_paths = _labelled_descendant_paths(live_node)
    payload_paths = _labelled_descendant_paths(payload)
    missing_paths = sorted(live_paths - payload_paths)
    groups: dict[tuple[tuple[tuple[str, str], ...], str], list[str]] = {}
    for missing_path in missing_paths:
        if not missing_path:
            continue
        parent_path = missing_path[:-1]
        child_kind, child_label = missing_path[-1]
        if parent_path and parent_path not in payload_paths:
            continue
        groups.setdefault((parent_path, child_kind), []).append(child_label)
    return tuple(
        MissingSiblingGroup(
            parent_path=parent_path,
            child_kind=child_kind,
            labels=tuple(sorted(labels, key=default_label_sort_key)),
        )
        for (parent_path, child_kind), labels in groups.items()
        if len(labels) >= 2
    )


def _has_descendant_companion_op(
    op: LegalOperation,
    operations: Sequence[LegalOperation],
) -> bool:
    target_path = op.target.path
    if str(op.op_id or "").startswith("snapshot_"):
        return True
    return any(
        other is not op
        and len(other.target.path) > len(target_path)
        and other.target.path[: len(target_path)] == target_path
        for other in operations
    )


def run_descendant_sibling_loss_detector(
    before_ir: IRNode,
    operations: Sequence[LegalOperation],
    target_path: str = "",
) -> list[InvariantDetectorResult]:
    """Flag sparse broad snapshots that drop pre-existing descendant siblings.

    This is a transition detector for invariant-bisect. It does not judge the
    final tree alone; it compares broad emitted replacement snapshots against
    the pre-step live target so descendant-owned over-promotion remains visible
    even when ordinary structural invariants stay clean.
    """
    results: list[InvariantDetectorResult] = []
    for op in operations:
        if op.action is not StructuralAction.REPLACE:
            continue
        if op.payload is None:
            continue
        if not _has_descendant_companion_op(op, operations):
            continue
        live_node = _resolve_address_path(before_ir, op.target.path)
        if live_node is None:
            continue
        for group in _missing_sibling_groups(live_node, op.payload):
            issue_path = op.target.path + group.parent_path
            path_text = _path_text(issue_path)
            if not path_matches_target(path_text, target_path):
                continue
            sample = group.labels[:8]
            sample_text = ", ".join(sample)
            message = (
                f"{path_text}: descendant sibling loss in {group.child_kind} "
                f"children after {op.op_id} ({len(group.labels)} missing: {sample_text})"
            )
            results.append(
                InvariantDetectorResult(
                    detector="descendant_sibling_loss",
                    kind="descendant_sibling_loss",
                    path_text=path_text,
                    message=message,
                    detail=DescendantSiblingLossDetail(
                        op_id=str(op.op_id or ""),
                        op_target=_address_part_text(op.target.path),
                        payload_kind=_kind_str(op.payload.kind),
                        payload_label=op.payload.label,
                        parent_relative_path=_address_part_text(group.parent_path),
                        missing_child_kind=group.child_kind,
                        missing_count=len(group.labels),
                        missing_labels_sample=tuple(sample),
                    ).to_dict(),
                )
            )
    return results


def run_same_source_descendant_snapshot_shadow_detector(
    operations: Sequence[LegalOperation],
    target_path: str = "",
) -> list[InvariantDetectorResult]:
    """Flag same-source ancestor snapshots that conflict with descendant ops.

    This transition detector catches a parent snapshot emitted in the same
    amendment wave as a descendant INSERT/REPLACE where the parent already
    carries that descendant path but with different text.  That is the precise
    shape that can later shadow the descendant during PIT materialization.
    """
    results: list[InvariantDetectorResult] = []
    ancestor_ops = [
        op
        for op in operations
        if op.action is StructuralAction.REPLACE and op.payload is not None
    ]
    descendant_ops = [
        op
        for op in operations
        if op.action in {StructuralAction.INSERT, StructuralAction.REPLACE}
        and op.payload is not None
    ]
    for ancestor in ancestor_ops:
        ancestor_source = _operation_source_statute_id(ancestor)
        if not ancestor_source:
            continue
        ancestor_payload = ancestor.payload
        if ancestor_payload is None:
            continue
        for descendant in descendant_ops:
            if descendant is ancestor:
                continue
            if _operation_source_statute_id(descendant) != ancestor_source:
                continue
            if len(descendant.target.path) <= len(ancestor.target.path):
                continue
            if descendant.target.path[: len(ancestor.target.path)] != ancestor.target.path:
                continue
            shadow_node = _resolve_payload_relative_path(
                ancestor_payload,
                ancestor.target.path,
                descendant.target.path,
            )
            if shadow_node is None:
                continue
            ancestor_hash = irnode_content_hash(shadow_node)
            descendant_hash = irnode_content_hash(descendant.payload)
            if ancestor_hash == descendant_hash:
                continue
            path_text = _path_text(descendant.target.path)
            if not path_matches_target(path_text, target_path):
                continue
            message = (
                f"{path_text}: same-source descendant snapshot shadow "
                f"{ancestor.op_id} conflicts with {descendant.op_id}"
            )
            results.append(
                InvariantDetectorResult(
                    detector="same_source_descendant_snapshot_shadow",
                    kind="same_source_descendant_snapshot_shadow",
                    path_text=path_text,
                    message=message,
                    detail=SameSourceDescendantSnapshotShadowDetail(
                        ancestor_op_id=str(ancestor.op_id or ""),
                        ancestor_target=_address_part_text(ancestor.target.path),
                        descendant_op_id=str(descendant.op_id or ""),
                        descendant_target=_address_part_text(descendant.target.path),
                        source_statute=ancestor_source,
                        ancestor_descendant_hash=ancestor_hash,
                        descendant_payload_hash=descendant_hash,
                    ).to_dict(),
                )
            )
    return results


def run_invariant_detector(
    ir: Any,
    detector: str,
    target_path: str = "",
    *,
    definition_introducer_predicate: Callable[[IRNode], bool] | None = None,
) -> list[InvariantDetectorResult]:
    """Run a structural/lint detector and return typed results.

    The message field intentionally preserves the existing CLI string surface.

    ``definition_introducer_predicate`` (optional) is forwarded to
    ``build_flattened_sublist_findings`` for the ``flattened_sublist_family``
    detector. It is the frontend-supplied "is this parent a definition-list
    introducer?" predicate (Finland wires its FI predicate at the
    ``invariant-bisect`` CLI dispatch); other callers omit it (AGENTS.md §2.3 —
    core hosts the hook; it does not interpret frontend-local values).
    """
    if detector not in SUPPORTED_INVARIANT_DETECTORS:
        supported = ", ".join(SUPPORTED_INVARIANT_DETECTORS)
        raise ValueError(f"unsupported invariant detector {detector!r}; expected one of: {supported}")

    if detector in {
        "descendant_sibling_loss",
        "same_source_descendant_snapshot_shadow",
    }:
        return []

    if detector == "label_normalization_collision":
        return run_label_normalization_collision_detector(ir, target_path=target_path)

    if detector in ("duplicate_label", "illegal_edge", "sort_order", "mixed_hierarchy", "all_tree"):
        selected_families: set[TreeInvariantKind] | None = None
        if detector == "duplicate_label":
            selected_families = {"duplicate_label", "normalized_duplicate_label"}
        elif detector == "illegal_edge":
            selected_families = {"unexpected_child_kind"}
        elif detector == "sort_order":
            selected_families = {"sort_order"}
        elif detector == "mixed_hierarchy":
            selected_families = {"mixed_hierarchy_child"}
        return [
            InvariantDetectorResult(
                detector=detector,
                kind=violation.kind,
                path_text=violation.path_text,
                message=violation.message,
                detail={
                    "parent_kind": violation.parent_kind,
                    "child_kind": violation.child_kind,
                    "label": violation.label,
                    "normalized_label": violation.normalized_label,
                    "count": violation.count,
                    "previous_label": violation.previous_label,
                    "next_label": violation.next_label,
                    "container_kind": violation.container_kind,
                    "container_label": violation.container_label,
                },
            )
            for violation in iter_tree_invariant_violations(ir, families=selected_families)
            if path_matches_target(violation.path_text, target_path)
        ]

    if detector == "text_duplication":
        results: list[InvariantDetectorResult] = []
        for warning in find_text_duplication_warnings(ir):
            kind = str(warning.get("kind") or "?")
            path = str(warning.get("path") or "?")
            left = warning.get("left", "?")
            right = warning.get("right", "?")
            tokens = warning.get("shared_token_count", 0)
            excerpt = str(warning.get("excerpt") or "")[:60]
            message = f"{path}: {kind} {left!r} <-> {right!r} ({tokens} tokens) {excerpt!r}"
            if path_matches_target(path, target_path):
                results.append(
                    InvariantDetectorResult(
                        detector=detector,
                        kind=kind,
                        path_text=path,
                        message=message,
                        detail=dict(warning),
                    )
                )
        return results

    if detector == "flattened_sublist_family":
        results = []
        for finding in build_flattened_sublist_findings(
            ir,
            phase="diagnose_phase",
            definition_introducer_predicate=definition_introducer_predicate,
        ):
            warning = finding.detail
            kind = str(warning.get("kind") or "?")
            path = str(warning.get("path") or "?")
            node_kind = str(warning.get("node_kind") or "?")
            raw_sample = warning.get("label_sample")
            sample = _detail_sequence(raw_sample)
            sample_str = ", ".join(str(item) for item in sample[:8])
            if kind == "flattened_sublist_interleaved":
                raw_families = warning.get("repeated_families")
                families = ", ".join(str(item) for item in _detail_sequence(raw_families))
                message = f"{path}: flattened {node_kind} family interleaved ({families}) [{sample_str}]"
            elif kind == "flattened_sublist_reset":
                dominant = str(warning.get("dominant_family") or "?")
                max_before = str(warning.get("max_before_reset") or "?")
                reset_label = str(warning.get("reset_label") or "?")
                message = (
                    f"{path}: flattened {node_kind} {dominant}-family reset at "
                    f"{reset_label!r} (max was {max_before}) [{sample_str}]"
                )
            elif kind == "flattened_sublist_mixed_family":
                raw_families = warning.get("families")
                families = ", ".join(str(item) for item in _detail_sequence(raw_families))
                message = (
                    f"{path}: flattened {node_kind} mixed {families} families "
                    f"[{sample_str}]"
                )
            else:
                message = f"{path}: {kind} {node_kind} [{sample_str}]"
            if path_matches_target(path, target_path):
                results.append(
                    InvariantDetectorResult(
                        detector=detector,
                        kind=kind,
                        path_text=path,
                        message=message,
                        detail=dict(warning),
                    )
                )
        return results

    if detector == "label_sequence_gap":
        results = []
        for finding in build_label_sequence_gap_findings(ir, phase="diagnose_phase"):
            warning = finding.detail
            kind = str(warning.get("kind") or "?")
            path = str(warning.get("path") or "?")
            node_kind = str(warning.get("node_kind") or "?")
            previous_label = warning.get("previous_label")
            next_label = warning.get("next_label")
            missing = ", ".join(str(item) for item in _detail_sequence(warning.get("missing_labels"))[:8])
            if previous_label is None:
                message = f"{path}: {node_kind} sequence starts at {next_label!r}; missing {missing}"
            else:
                message = f"{path}: {node_kind} sequence gap {previous_label!r} -> {next_label!r}; missing {missing}"
            if path_matches_target(path, target_path):
                results.append(
                    InvariantDetectorResult(
                        detector=detector,
                        kind=kind,
                        path_text=path,
                        message=message,
                        detail=dict(warning),
                    )
                )
        return results

    raise AssertionError(f"unhandled invariant detector {detector!r}")


def run_invariant_detector_messages(
    ir: Any,
    detector: str,
    target_path: str = "",
    *,
    definition_introducer_predicate: Callable[[IRNode], bool] | None = None,
) -> list[str]:
    """Compatibility projection for legacy CLI output."""
    return [
        result.message
        for result in run_invariant_detector(
            ir,
            detector,
            target_path,
            definition_introducer_predicate=definition_introducer_predicate,
        )
    ]
