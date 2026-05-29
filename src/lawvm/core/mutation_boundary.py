"""Shared IR changed-path helpers for mutation-boundary checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence, Tuple, TypeAlias

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation
from lawvm.core.ir_helpers import _kind_str
from lawvm.core.semantic_types import StructuralAction

TreePathStep: TypeAlias = Tuple[str, str]
TreePath: TypeAlias = Tuple[TreePathStep, ...]
TreePaths: TypeAlias = Tuple[TreePath, ...]
RenumberedTreePaths: TypeAlias = Tuple[Tuple[TreePath, TreePath], ...]


def validate_tree_path(
    path: TreePath,
    *,
    field_name: str = "tree path",
    allow_root: bool = True,
) -> tuple[str, ...]:
    """Return validation issues for the shared mutation-boundary path shape."""

    if not path:
        if allow_root:
            return ()
        return (f"{field_name} must not be the root path",)

    issues: list[str] = []
    for index, step in enumerate(path):
        if len(step) != 2:
            issues.append(f"{field_name} step {index} must have kind and label")
            continue
        kind, _label = step
        if not str(kind).strip():
            issues.append(f"{field_name} step {index} requires a non-empty kind")
    return tuple(issues)


def tree_path_from_legal_address(address: LegalAddress) -> TreePath:
    """Project a legal node address into mutation-boundary path form."""

    return tuple((str(kind), str(label)) for kind, label in address.path)


def operation_storage_boundary_prefixes(
    op: LegalOperation,
    declared_extra_prefixes: Sequence[TreePath] = (),
) -> TreePaths:
    """Return storage paths an operation is allowed to change by action shape.

    Structural child-list edits are observed at the parent path by
    ``diff_ir_paths``. Text edits are observed at the target path. Replacement
    usually changes the target node, but a payload with a different leaf key
    changes the parent child-list shape and therefore needs the parent boundary.
    """

    target_path = tree_path_from_legal_address(op.target)
    if str(op.target.special or "") == "whole_act":
        return dedupe_tree_paths(((), *declared_extra_prefixes))

    action = op.action
    if action in {StructuralAction.TEXT_REPLACE, StructuralAction.TEXT_REPEAL, StructuralAction.HEADING_REPLACE}:
        return dedupe_tree_paths((target_path, *declared_extra_prefixes))
    if action is StructuralAction.INSERT:
        return dedupe_tree_paths((_parent_tree_path(target_path), *declared_extra_prefixes))
    if action is StructuralAction.REPEAL:
        return dedupe_tree_paths((_parent_tree_path(target_path), *declared_extra_prefixes))
    if action is StructuralAction.RENUMBER:
        prefixes = [_parent_tree_path(target_path)]
        if op.destination is not None:
            prefixes.append(_parent_tree_path(tree_path_from_legal_address(op.destination)))
        return dedupe_tree_paths((*prefixes, *declared_extra_prefixes))
    if action is StructuralAction.REPLACE:
        if _replace_payload_changes_target_key(op):
            return dedupe_tree_paths((_parent_tree_path(target_path), *declared_extra_prefixes))
        return dedupe_tree_paths((target_path, *declared_extra_prefixes))
    return dedupe_tree_paths((target_path, *declared_extra_prefixes))


def build_operation_mutation_boundary_report(
    before: IRNode,
    after: IRNode,
    op: LegalOperation,
    declared_extra_prefixes: Sequence[TreePath] = (),
) -> MutationBoundaryReport:
    """Build a mutation-boundary report from an operation's declared target."""

    return build_mutation_boundary_report(
        before,
        after,
        operation_storage_boundary_prefixes(op, declared_extra_prefixes),
    )


@dataclass(frozen=True)
class ChangedPathPartition:
    """Changed paths split by whether a declared boundary covers them."""

    covered_changed_paths: TreePaths
    unexplained_changed_paths: TreePaths

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "covered_changed_paths",
            _validated_tree_paths(self.covered_changed_paths, field_name="covered changed path", dedupe=False),
        )
        object.__setattr__(
            self,
            "unexplained_changed_paths",
            _validated_tree_paths(self.unexplained_changed_paths, field_name="unexplained changed path", dedupe=False),
        )


def partition_changed_paths(
    changed_paths: Sequence[TreePath],
    allowed_prefixes: Sequence[TreePath],
) -> ChangedPathPartition:
    """Partition changed paths into covered and unexplained paths."""

    issues = (
        *(
            issue
            for path in changed_paths
            for issue in validate_tree_path(path, field_name="changed path")
        ),
        *(
            issue
            for path in allowed_prefixes
            for issue in validate_tree_path(path, field_name="allowed prefix")
        ),
    )
    if issues:
        raise ValueError("; ".join(issues))
    allowed = tuple(allowed_prefixes)
    covered = tuple(path for path in changed_paths if path_has_prefix(path, allowed))
    unexplained = tuple(path for path in changed_paths if not path_has_prefix(path, allowed))
    return ChangedPathPartition(
        covered_changed_paths=covered,
        unexplained_changed_paths=unexplained,
    )


@dataclass(frozen=True)
class MutationBoundaryReport:
    """Changed-path accounting against declared legal mutation regions."""

    changed_paths: TreePaths
    allowed_prefixes: TreePaths
    covered_changed_paths: TreePaths
    unexplained_changed_paths: TreePaths

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "changed_paths",
            _validated_tree_paths(self.changed_paths, field_name="changed path", dedupe=False),
        )
        object.__setattr__(
            self,
            "allowed_prefixes",
            _validated_tree_paths(self.allowed_prefixes, field_name="allowed prefix"),
        )
        object.__setattr__(
            self,
            "covered_changed_paths",
            _validated_tree_paths(self.covered_changed_paths, field_name="covered changed path", dedupe=False),
        )
        object.__setattr__(
            self,
            "unexplained_changed_paths",
            _validated_tree_paths(self.unexplained_changed_paths, field_name="unexplained changed path", dedupe=False),
        )


def build_mutation_boundary_report(
    before: IRNode,
    after: IRNode,
    allowed_prefixes: Sequence[TreePath],
) -> MutationBoundaryReport:
    """Diff two IR trees and classify changes by declared mutation boundaries."""

    changed_paths = diff_ir_paths(before, after)
    allowed = tuple(allowed_prefixes)
    partition = partition_changed_paths(changed_paths, allowed)
    return MutationBoundaryReport(
        changed_paths=changed_paths,
        allowed_prefixes=allowed,
        covered_changed_paths=partition.covered_changed_paths,
        unexplained_changed_paths=partition.unexplained_changed_paths,
    )


def diff_ir_paths(before: IRNode, after: IRNode) -> TreePaths:
    """Return structural paths whose node content or child shape differs."""

    return tuple(_diff_ir_paths(before, after, ()))


def unexplained_changed_paths(
    changed_paths: Sequence[TreePath],
    allowed_prefixes: Sequence[TreePath],
) -> TreePaths:
    """Return changed paths outside every declared mutation boundary prefix."""

    return partition_changed_paths(changed_paths, allowed_prefixes).unexplained_changed_paths


def path_has_prefix(path: TreePath, allowed_prefixes: Sequence[TreePath]) -> bool:
    """Return true when *path* is inside one of the allowed changed-path regions."""

    for prefix in allowed_prefixes:
        if len(path) >= len(prefix) and path[: len(prefix)] == prefix:
            return True
    return False


def path_is_strict_prefix(prefix: TreePath, full: TreePath) -> bool:
    """Return true when *prefix* is a proper ancestor path of *full*."""

    issues = (
        *validate_tree_path(prefix, field_name="prefix path"),
        *validate_tree_path(full, field_name="full path"),
    )
    if issues:
        raise ValueError("; ".join(issues))
    return len(prefix) < len(full) and full[: len(prefix)] == prefix


def normalize_tree_path_for_relation(
    path: TreePath,
    *,
    ignored_kinds: frozenset[str] = frozenset(),
) -> TreePath:
    """Drop relation-irrelevant path steps while preserving legal order."""

    issues = validate_tree_path(path)
    if issues:
        raise ValueError("; ".join(issues))
    return tuple(step for step in path if step[0] not in ignored_kinds)


def paths_related(
    left: TreePath,
    right: TreePath,
    *,
    ignored_kinds: frozenset[str] = frozenset(),
    special_labels: frozenset[str] = frozenset(),
) -> bool:
    """Return whether two paths identify overlapping or symbolic sibling regions."""

    left = normalize_tree_path_for_relation(left, ignored_kinds=ignored_kinds)
    right = normalize_tree_path_for_relation(right, ignored_kinds=ignored_kinds)
    if left == right:
        return True
    if len(left) <= len(right) and right[: len(left)] == left:
        return True
    if len(right) <= len(left) and left[: len(right)] == right:
        return True
    if not left or not right:
        return False
    left_kind, left_label = left[-1]
    right_kind, right_label = right[-1]
    if left_kind != right_kind:
        return False
    if left[:-1] != right[:-1]:
        return False
    return left_label in special_labels or right_label in special_labels


def _diff_ir_paths(before: IRNode, after: IRNode, path: TreePath) -> list[TreePath]:
    if _node_without_children(before) != _node_without_children(after):
        return [path]
    before_keys = tuple((_kind_str(child.kind), child.label or "") for child in before.children)
    after_keys = tuple((_kind_str(child.kind), child.label or "") for child in after.children)
    if before_keys != after_keys:
        return [path]
    out: list[TreePath] = []
    for before_child, after_child, key in zip(before.children, after.children, before_keys, strict=True):
        out.extend(_diff_ir_paths(before_child, after_child, path + (key,)))
    return out


def _node_without_children(node: IRNode) -> tuple[str, str | None, str, tuple[tuple[str, object], ...]]:
    return (_kind_str(node.kind), node.label, node.text, tuple(sorted(dict(node.attrs).items())))


def _parent_tree_path(path: TreePath) -> TreePath:
    if not path:
        return ()
    return path[:-1]


def _replace_payload_changes_target_key(op: LegalOperation) -> bool:
    if op.payload is None or not op.target.path:
        return False
    target_kind, target_label = op.target.path[-1]
    payload_key = (_kind_str(op.payload.kind), op.payload.label or "")
    return payload_key != (str(target_kind), str(target_label))


def dedupe_tree_paths(paths: Iterable[TreePath]) -> TreePaths:
    """Return tree paths in first-seen order after string-normalizing steps."""
    seen: set[TreePath] = set()
    result: list[TreePath] = []
    for path in paths:
        normalized = tuple((str(kind), str(label)) for kind, label in path)
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def _validated_tree_paths(paths: Sequence[TreePath], *, field_name: str, dedupe: bool = True) -> TreePaths:
    normalized = dedupe_tree_paths(paths) if dedupe else tuple(
        tuple((str(kind), str(label)) for kind, label in path) for path in paths
    )
    issues = tuple(
        issue
        for path in normalized
        for issue in validate_tree_path(path, field_name=field_name)
    )
    if issues:
        raise ValueError("; ".join(issues))
    return normalized


def _dedupe_tree_paths(paths: Iterable[TreePath]) -> TreePaths:
    return dedupe_tree_paths(paths)
