"""Finland-local scoped provision lookup helpers.

This module owns the small but load-bearing Finland policy for resolving a
section under an optional chapter/part scope. It is intentionally local to the
Finland frontend because Roman/Arabic part-label equivalence is a Finnish source
normalization rule, not a core tree-ops rule.
"""

from __future__ import annotations

from collections.abc import Callable

from lawvm.core import tree_ops as _tops
from lawvm.core.ir import IRNode
from lawvm.core.tree_ops import Path
from lawvm.finland.helpers import _norm_num_token

FindPath = Callable[[str, str, str | None, str | None], Path | None]


def find_scoped_section_path(
    ir: IRNode,
    *,
    target_section: str,
    target_chapter: str | None = None,
    target_part: str | None = None,
    find_path: FindPath,
) -> Path | None:
    """Find a Finland section path under optional chapter/part scope.

    Part labels accept either the source token or its Finnish normalized numeric
    form, so a Roman source reference like ``II`` can bind an Arabic IR part
    label ``2``. If a part scope is present, lookup never silently drops it.
    """

    if target_part:
        expected_part = _norm_num_token(target_part)
        part_path = find_path("part", target_part, None, None)
        if part_path is None:
            part_path = find_path("part", expected_part, None, None)
        if part_path is None or _norm_num_token(part_path[-1][1]) != expected_part:
            return None
        part_node = _tops.resolve(ir, part_path)
        if part_node is None:
            return None
        if target_chapter:
            chapter_path = _tops.find(part_node, "chapter", target_chapter)
            if chapter_path is None:
                return None
            chapter_node = _tops.resolve(part_node, chapter_path)
            if chapter_node is None:
                return None
            section_path = _tops.find(chapter_node, "section", target_section)
            if section_path is None:
                return None
            return part_path + chapter_path + section_path
        section_path = _tops.find(part_node, "section", target_section)
        if section_path is None:
            return None
        return part_path + section_path

    return find_path(
        "section",
        target_section,
        "chapter" if target_chapter else None,
        target_chapter,
    )
