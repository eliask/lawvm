"""Duplicate-preserving indexes for source-tree paths."""

from __future__ import annotations

from collections import Counter
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")
SourcePath = tuple[str, ...]


def duplicate_preserving_source_path_index(
    items: Iterable[T],
    *,
    path_of: Callable[[T], SourcePath],
    duplicate_id_of: Callable[[T], str] | None = None,
    duplicate_segment_prefix: str = "source-duplicate",
) -> dict[SourcePath, T]:
    """Index items by source path without overwriting duplicate source paths."""

    item_tuple = tuple(items)
    paths = tuple(tuple(str(part) for part in path_of(item)) for item in item_tuple)
    path_counts: Counter[SourcePath] = Counter(paths)
    seen_by_path: Counter[SourcePath] = Counter()
    seen_keys: Counter[SourcePath] = Counter()
    indexed: dict[SourcePath, T] = {}
    for item, path in zip(item_tuple, paths, strict=True):
        if path_counts[path] == 1:
            key = path
        else:
            seen_by_path[path] += 1
            suffix = ""
            if duplicate_id_of is not None:
                suffix = str(duplicate_id_of(item) or "")
            if not suffix:
                suffix = f"ordinal:{seen_by_path[path]}"
            key = (*path, f"{duplicate_segment_prefix}:{suffix}")
        seen_keys[key] += 1
        if seen_keys[key] > 1:
            key = (*key, f"{duplicate_segment_prefix}:ordinal:{seen_by_path[path]}")
        indexed[key] = item
    return indexed

