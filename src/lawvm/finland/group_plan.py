"""Grouping helpers for Finnish amendment compile planning."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from typing import Callable, Dict, Iterator, List, Mapping, Optional, Protocol, Tuple, TypeVar, cast, overload

from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.labels import leaf_label_identity_key
from lawvm.finland.ops import (
    AmendmentOp,
    _lo_with_path_update,
    normalize_scope_confidence,
    scope_confidence_from_tags,
)
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.target_selector_facades import replace_target

_GROUP_KEY_NON_WORD_RE = re.compile(r"[^\d\w]")


class SectionPathLookup(Protocol):
    def find_section_path(
        self,
        target_norm: str,
        target_chapter: Optional[str] = None,
        target_part: Optional[str] = None,
    ) -> Tuple[Tuple[str, str], ...] | None: ...


@dataclass(frozen=True, slots=True, eq=False)
class GroupTargetKey:
    """Normalized amendment-operation group key.

    Legacy callers historically used ``(unit_kind, target_norm, chapter, part)``
    tuples.  This carrier remains tuple-compatible while giving phase-boundary
    code named fields.
    """

    unit_kind: IRNodeKind
    target_norm: str
    target_chapter: Optional[str]
    target_part: Optional[str]

    def as_tuple(self) -> Tuple[IRNodeKind, str, Optional[str], Optional[str]]:
        return (self.unit_kind, self.target_norm, self.target_chapter, self.target_part)

    def __iter__(self) -> Iterator[IRNodeKind | str | None]:
        return iter(self.as_tuple())

    def __len__(self) -> int:
        return 4

    @overload
    def __getitem__(self, index: int) -> IRNodeKind | str | None: ...

    @overload
    def __getitem__(self, index: slice) -> Tuple[IRNodeKind, str, Optional[str], Optional[str]]: ...

    def __getitem__(
        self,
        index: int | slice,
    ) -> IRNodeKind | str | None | Tuple[IRNodeKind, str, Optional[str], Optional[str]]:
        if isinstance(index, slice):
            return cast(Tuple[IRNodeKind, str, Optional[str], Optional[str]], self.as_tuple()[index])
        return self.as_tuple()[index]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, GroupTargetKey):
            return self.as_tuple() == other.as_tuple()
        if isinstance(other, tuple):
            return self.as_tuple() == other
        return False

    def __hash__(self) -> int:
        return hash(self.as_tuple())


_GroupKeyType = TypeVar(
    "_GroupKeyType", GroupTargetKey, Tuple[IRNodeKind, str, Optional[str], Optional[str]]
)


def target_group_key(op: AmendmentOp) -> GroupTargetKey:
    def norm(s: str) -> str:
        return _GROUP_KEY_NON_WORD_RE.sub("", s).lower()

    # For osa/part targets, use _norm_num_token which converts Roman numerals
    # to Arabic (III→3, V→5). The master tree stores parts with Arabic labels
    # but the PEG parser emits Roman numeral labels from the johtolause.
    section_norm = (
        _norm_num_token(op.target_section)
        if op.target_unit_kind == "part" and op.target_section
        else norm(op.target_section)
    )
    chapter = norm(op.target_chapter) if op.target_unit_kind == "section" and op.target_chapter else None
    part = (
        _norm_num_token(op.target_part)
        if op.target_unit_kind in {"section", "chapter"} and op.target_part
        else None
    )
    return GroupTargetKey(IRNodeKind(op.target_unit_kind), section_norm, chapter, part)


def normalize_group_target_key(
    key: GroupTargetKey | Tuple[IRNodeKind, str, Optional[str], Optional[str]],
) -> GroupTargetKey:
    if isinstance(key, GroupTargetKey):
        return key
    unit_kind, target_norm, target_chapter, target_part = key
    return GroupTargetKey(unit_kind, target_norm, target_chapter, target_part)


def group_ops_by_target(ops: List[AmendmentOp]) -> Dict[GroupTargetKey, List[AmendmentOp]]:
    section_groups: Dict[GroupTargetKey, List[AmendmentOp]] = defaultdict(list)
    for op in ops:
        section_groups[target_group_key(op)].append(op)
    return section_groups


def coalesce_same_target_mixed_scope_section_groups(
    section_groups: Mapping[_GroupKeyType, List[AmendmentOp]],
    *,
    master: SectionPathLookup,
    find_body_section_chapter: Callable[[str], str | None],
) -> Dict[GroupTargetKey, List[AmendmentOp]]:
    """Merge mixed-scope section groups while preserving scope-upgrade evidence.

    A bare section group must not silently inherit scoped ownership from a
    sibling group. When coalescing is needed to keep one sparse section payload
    coherent, inherited bare ops are tagged so the scope upgrade survives as a
    first-class witness instead of disappearing inside group formation.
    """
    merged = {normalize_group_target_key(key): value for key, value in section_groups.items()}
    section_keys = [key for key in merged if key.unit_kind is IRNodeKind.SECTION]
    buckets: dict[tuple[str, Optional[str]], list[GroupTargetKey]] = defaultdict(list)

    def _op_merge_signature(op: AmendmentOp) -> tuple[object, ...]:
        return (
            op.op_type,
            op.target_unit_kind,
            _norm_num_token(op.target_section or ""),
            _norm_num_token(op.target_chapter or "") if op.target_chapter else "",
            _norm_num_token(op.target_part or "") if op.target_part else "",
            op.target_paragraph,
            leaf_label_identity_key(op.target_item or "") if op.target_item else "",
            str(op.target_special or "").strip(),
        )

    for key in section_keys:
        buckets[(key.target_norm, key.target_part)].append(key)

    for (target_norm, target_part), keys in buckets.items():
        unscoped_key = next((key for key in keys if not key.target_chapter), None)
        scoped_keys = [key for key in keys if key.target_chapter]
        if unscoped_key is None or len(scoped_keys) != 1:
            continue
        scoped_key = scoped_keys[0]
        scoped_chapter = scoped_key.target_chapter
        if scoped_chapter is None:
            continue

        live_path = master.find_section_path(target_norm, None, target_part)
        if live_path is None:
            continue
        live_chapter = next((label for kind, label in live_path if kind == "chapter"), None)
        if live_chapter != scoped_chapter:
            continue

        body_chapter = find_body_section_chapter(target_norm)
        if body_chapter not in (None, scoped_chapter):
            continue

        scoped_ops = merged.get(scoped_key)
        unscoped_ops = merged.get(unscoped_key)
        if not scoped_ops or not unscoped_ops:
            continue

        scoped_signatures = {_op_merge_signature(op) for op in scoped_ops}
        unique_tagged_unscoped_ops: list[AmendmentOp] = []
        for op in unscoped_ops:
            merged_scope_confidence = normalize_scope_confidence(
                scope_confidence_from_tags(
                    (*op.scope_provenance_tags, "mixed_scope_group_merge"),
                    resolved_chapter=scoped_chapter,
                ),
                resolved_chapter=scoped_chapter,
            )
            tagged_op = dc_replace(
                op,
                **replace_target(op, target_chapter=scoped_chapter),
                scope_provenance_tags=tuple(op.scope_provenance_tags) + ("mixed_scope_group_merge",),
                scope_confidence=merged_scope_confidence,
                lo=_lo_with_path_update(op.lo, chapter=scoped_chapter) if op.lo is not None else op.lo,
            )
            if _op_merge_signature(tagged_op) not in scoped_signatures:
                unique_tagged_unscoped_ops.append(tagged_op)

        if not unique_tagged_unscoped_ops:
            del merged[unscoped_key]
            continue

        merged[scoped_key] = sorted(
            [*scoped_ops, *unique_tagged_unscoped_ops],
            key=lambda op: (
                op.lo.sequence if op.lo is not None else 10**9,
                op.target_paragraph or 0,
            ),
        )
        del merged[unscoped_key]

    return merged


__all__ = [
    "GroupTargetKey",
    "SectionPathLookup",
    "coalesce_same_target_mixed_scope_section_groups",
    "target_group_key",
    "normalize_group_target_key",
    "group_ops_by_target",
]
