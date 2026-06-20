"""Finland-local scoped provision lookup helpers.

This module owns the small but load-bearing Finland policy for resolving a
section under an optional chapter/part scope. It is intentionally local to the
Finland frontend because Roman/Arabic part-label equivalence is a Finnish source
normalization rule, not a core tree-ops rule.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Literal

from lawvm.core import tree_ops as _tops
from lawvm.core.ir import IRNode
from lawvm.core.tree_ops import Path, normalized_label_key
from lawvm.finland.helpers import _norm_num_token

FindPath = Callable[[str, str, str | None, str | None], Path | None]
FindPartPath = Callable[[str], Path | None]
FindInsertParentPath = Callable[[str | None], Path | None]
MissingPartPolicy = Literal["fallback", "not_found"]
MissingChapterInPartPolicy = Literal["part", "not_found"]
ProvisionIndex = Mapping[tuple[str, str], Iterable[Path]]


def _kind_name(kind: object) -> str:
    return str(getattr(kind, "value", kind))


def unique_chapter_scoped_section_path(
    ir: IRNode,
    *,
    target_section: str,
    target_chapter: str,
    provision_index: ProvisionIndex | None = None,
) -> Path | None:
    """Return the unique ``.../chapter:X/section:Y`` path, ignoring part scope.

    Finnish source formulae often cite ``X luku Y §`` even when the live
    structure nests that chapter under an ``osa``.  That is not permission to
    drop or guess scope: this helper only succeeds when the requested
    chapter/section pair has exactly one direct live match across the tree.
    """

    target_chapter_norm = _norm_num_token(target_chapter)
    target_section_norm = normalized_label_key(target_section)
    if provision_index is not None:
        matches = _unique_canonical_legal_paths(
            ir,
            (
                path
                for path in section_paths_for_label(provision_index, target_section)
                if _path_matches_chapter_scope(path, target_chapter_norm)
            ),
        )
        if len(matches) == 1:
            return matches[0]
        return None

    matches: list[Path] = []

    def _walk(node: IRNode, path: Path, current_chapter: str | None) -> None:
        node_kind = _kind_name(node.kind)
        next_chapter = node.label if node_kind == "chapter" else current_chapter
        for child in node.children:
            child_kind = _kind_name(child.kind)
            child_path = path + ((child_kind, child.label or ""),)
            if (
                child_kind == "section"
                and next_chapter is not None
                and _norm_num_token(next_chapter) == target_chapter_norm
                and normalized_label_key(child.label) == target_section_norm
            ):
                matches.append(child_path)
            _walk(child, child_path, next_chapter)

    _walk(ir, (), None)
    if len(matches) == 1:
        return matches[0]
    return None


def find_scoped_section_path(
    ir: IRNode,
    *,
    target_section: str,
    target_chapter: str | None = None,
    target_part: str | None = None,
    find_path: FindPath,
    provision_index: ProvisionIndex | None = None,
) -> Path | None:
    """Find a Finland section path under optional chapter/part scope.

    Part labels accept either the source token or its Finnish normalized numeric
    form, so a Roman source reference like ``II`` can bind an Arabic IR part
    label ``2``. If a part scope is present, lookup never silently drops it.
    """

    if target_part:
        if provision_index is not None:
            candidates = section_paths_for_label(
                provision_index,
                target_section,
                target_part=target_part,
            )
            if target_chapter:
                target_chapter_norm = _norm_num_token(target_chapter)
                candidates = tuple(
                    path
                    for path in candidates
                    if _path_matches_chapter_scope(path, target_chapter_norm)
                )
            return candidates[0] if candidates else None

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

    if target_chapter:
        return unique_chapter_scoped_section_path(
            ir,
            target_section=target_section,
            target_chapter=target_chapter,
            provision_index=provision_index,
        )
    return find_path("section", target_section, None, None)


def find_scoped_section_insert_parent_path(
    ir: IRNode,
    *,
    chapter_label: str | None,
    part_label: str | None,
    find_part_path: FindPartPath,
    find_insert_parent_path: FindInsertParentPath,
    missing_part_policy: MissingPartPolicy,
    missing_chapter_in_part_policy: MissingChapterInPartPolicy,
    provision_index: ProvisionIndex | None = None,
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
                if provision_index is not None:
                    for candidate in provision_index.get(
                        ("chapter", normalized_label_key(chapter_label)),
                        (),
                    ):
                        chapter_path = _tops._as_path(candidate)
                        if len(chapter_path) > len(part_path) and chapter_path[: len(part_path)] == part_path:
                            return chapter_path
                else:
                    chapter_path = _tops.find(part_node, "chapter", chapter_label)
                    if chapter_path is not None:
                        return _tops._as_path(part_path + chapter_path)
                if missing_chapter_in_part_policy == "not_found":
                    return None
            if part_node is not None:
                return _tops._as_path(part_path)

    return find_insert_parent_path(chapter_label)


def path_matches_part_scope(path: Path, target_part: str | None) -> bool:
    """Return whether a path is inside the requested part scope.

    A declared part scope must bind an actual part step. Roman/Arabic
    equivalence is Finland-local and goes through ``_norm_num_token``.
    """

    if not target_part:
        return True
    parts = [label for kind, label in path if kind == "part" and label]
    if not parts:
        return False
    return _norm_num_token(parts[-1]) == _norm_num_token(target_part)


def _nearest_chapter_label(path: Path) -> str | None:
    for kind, label in reversed(path):
        if kind == "chapter":
            return label
    return None


def _path_matches_chapter_scope(path: Path, target_chapter_norm: str) -> bool:
    chapter_label = _nearest_chapter_label(path)
    return chapter_label is not None and _norm_num_token(chapter_label) == target_chapter_norm


def _canonical_legal_path(path: Path) -> Path:
    return tuple((kind, label) for kind, label in path if kind != "hcontainer")


def _unique_canonical_legal_paths(ir: IRNode, paths: Iterable[Path]) -> tuple[Path, ...]:
    seen: set[Path] = set()
    out: list[Path] = []
    for path in paths:
        canonical = _canonical_legal_path(path)
        if canonical != path and _tops.resolve(ir, canonical) is None:
            canonical = path
        if canonical in seen:
            continue
        seen.add(canonical)
        out.append(canonical)
    return tuple(out)


def section_paths_for_label(
    provision_index: ProvisionIndex,
    section_label: str,
    *,
    target_part: str | None = None,
) -> tuple[Path, ...]:
    """Return indexed same-label section paths, optionally restricted to a part."""

    label_norm = normalized_label_key(section_label)
    return tuple(
        path
        for path in (_tops._as_path(raw_path) for raw_path in provision_index.get(("section", label_norm), ()))
        if path_matches_part_scope(path, target_part)
    )


def unique_root_or_only_section_path(paths: Iterable[Path]) -> Path | None:
    """Prefer a unique root-level section, otherwise require a unique candidate."""

    candidates = tuple(paths)
    root_matches = tuple(
        path
        for path in candidates
        if not any(kind == "chapter" for kind, _label in path)
    )
    if len(root_matches) == 1:
        return root_matches[0]
    if len(candidates) == 1:
        return candidates[0]
    return None


def unique_same_part_different_chapter_section_path(
    paths: Iterable[Path],
    *,
    target_part: str | None,
    target_chapter: str | None,
) -> Path | None:
    """Select the unique same-part section outside the target chapter, if any."""

    if not target_part or not target_chapter:
        return None
    part_scoped = tuple(
        path
        for path in paths
        if path_matches_part_scope(path, target_part)
        and next((str(label) for kind, label in path if kind == "chapter"), None)
        not in (None, target_chapter)
    )
    if len(part_scoped) == 1:
        return part_scoped[0]
    return None
