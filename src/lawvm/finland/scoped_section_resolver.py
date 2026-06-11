"""Finland-local scoped provision lookup helpers.

This module owns the small but load-bearing Finland policy for resolving a
section under an optional chapter/part scope. It is intentionally local to the
Finland frontend because Roman/Arabic part-label equivalence is a Finnish source
normalization rule, not a core tree-ops rule.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from lawvm.core import tree_ops as _tops
from lawvm.core.ir import IRNode
from lawvm.core.tree_ops import Path
from lawvm.finland.helpers import _norm_num_token

FindPath = Callable[[str, str, str | None, str | None], Path | None]
FindPartPath = Callable[[str], Path | None]
FindInsertParentPath = Callable[[str | None], Path | None]
MissingPartPolicy = Literal["fallback", "not_found"]
MissingChapterInPartPolicy = Literal["part", "not_found"]


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


def find_scoped_section_insert_parent_path(
    ir: IRNode,
    *,
    chapter_label: str | None,
    part_label: str | None,
    find_part_path: FindPartPath,
    find_insert_parent_path: FindInsertParentPath,
    missing_part_policy: MissingPartPolicy,
    missing_chapter_in_part_policy: MissingChapterInPartPolicy,
) -> Path | None:
    """Resolve the parent path for inserting a section under optional scope.

    Existing FI call sites disagree intentionally on two recovery policies.
    Typed relabel dispatch may fall back when a part scope cannot be found and
    may insert directly under the part if the requested chapter is absent.
    Structure bootstrap paths are stricter: a declared part/chapter scope must
    exist. The policies are explicit here so future resolver adoption does not
    smuggle either behavior through copy-pasted branches.
    """

    if part_label:
        part_path = find_part_path(part_label)
        if part_path is None:
            if missing_part_policy == "not_found":
                return None
        else:
            part_node = _tops.resolve(ir, part_path)
            if part_node is None:
                if missing_part_policy == "not_found":
                    return None
            elif chapter_label:
                chapter_path = _tops.find(part_node, "chapter", chapter_label)
                if chapter_path is not None:
                    return _tops._as_path(part_path + chapter_path)
                if missing_chapter_in_part_policy == "not_found":
                    return None
            if part_node is not None:
                return _tops._as_path(part_path)

    return find_insert_parent_path(chapter_label)
