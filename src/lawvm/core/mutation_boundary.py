"""Shared IR changed-path helpers for mutation-boundary checks."""

from __future__ import annotations

from typing import Sequence, Tuple

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import _kind_str

TreePath = Tuple[Tuple[str, str], ...]


def diff_ir_paths(before: IRNode, after: IRNode) -> Tuple[TreePath, ...]:
    """Return structural paths whose node content or child shape differs."""

    return tuple(_diff_ir_paths(before, after, ()))


def unexplained_changed_paths(
    changed_paths: Sequence[TreePath],
    allowed_prefixes: Sequence[TreePath],
) -> Tuple[TreePath, ...]:
    """Return changed paths outside every declared mutation boundary prefix."""

    return tuple(path for path in changed_paths if not path_has_prefix(path, allowed_prefixes))


def path_has_prefix(path: TreePath, allowed_prefixes: Sequence[TreePath]) -> bool:
    """Return true when *path* is inside one of the allowed changed-path regions."""

    for prefix in allowed_prefixes:
        if len(path) >= len(prefix) and path[: len(prefix)] == prefix:
            return True
    return False


def _diff_ir_paths(before: IRNode, after: IRNode, path: TreePath) -> list[TreePath]:
    if _node_without_children(before) != _node_without_children(after):
        return [path]
    before_keys = tuple((_kind_str(child.kind), child.label or "") for child in before.children)
    after_keys = tuple((_kind_str(child.kind), child.label or "") for child in after.children)
    if before_keys != after_keys:
        return [path]
    out: list[TreePath] = []
    for before_child, after_child, key in zip(before.children, after.children, before_keys):
        out.extend(_diff_ir_paths(before_child, after_child, path + (key,)))
    return out


def _node_without_children(node: IRNode) -> tuple[str, str | None, str, tuple[tuple[str, object], ...]]:
    return (_kind_str(node.kind), node.label, node.text, tuple(sorted(dict(node.attrs).items())))
